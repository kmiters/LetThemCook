#!/usr/bin/env python3
"""Watch a project's Cooks and deliver actionable events to its Chef."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from ltc_status import inspect_run

ROOT = Path(__file__).resolve().parent.parent


def schema(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS watch_targets (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id), chef TEXT NOT NULL,
        session TEXT NOT NULL, space TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS watch_observations (
        run_id INTEGER PRIMARY KEY REFERENCES cook_runs(id), state TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS watch_launch_observations (
        run_id INTEGER PRIMARY KEY REFERENCES cook_runs(id), state TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS watch_events (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        run_id INTEGER NOT NULL REFERENCES cook_runs(id), state TEXT NOT NULL,
        delivered INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    ''')


def call_hc(session, *args, timeout=10):
    env = dict(os.environ, HC_SESSION=session)
    env.pop('HC_SOCKET', None)
    return subprocess.run(['hc', *args], env=env, text=True, capture_output=True, check=True, timeout=timeout)


def info(session, terminal):
    result = json.loads(call_hc(session, 'get', terminal).stdout)
    if not isinstance(result, dict):
        raise ValueError('Invalid Herdr response')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.environ.get('LTC_DB', str(ROOT / '.letthemcook' / 'registry.sqlite3')))
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start', help='Register this Chef and start its watcher')
    start.add_argument('project')
    run = commands.add_parser('run', help='Run the watcher loop')
    run.add_argument('project')
    run.add_argument('--interval', type=int, default=15)
    run.add_argument('--once', action='store_true', help='Observe and deliver once, then exit')
    args = parser.parse_args()
    if args.command == 'run' and args.interval < 1:
        parser.error('--interval must be positive')
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise ValueError('Registry not found')
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    schema(conn)
    conn.commit()
    project = conn.execute('SELECT id, name, space FROM projects WHERE name = ?', (args.project,)).fetchone()
    identity = conn.execute('SELECT token FROM registry_identity WHERE id = 1').fetchone()
    if project is None or identity is None or not project['space']:
        raise ValueError('Project, Space, or registry identity not found')
    token = identity['token']
    project_id = project['id']
    suffix = hashlib.sha256((str(db) + ':' + str(project_id)).encode()).hexdigest()[:16]
    watcher_name = 'ltc-watch-' + suffix
    watcher_labels = {'ltc.watch.registry': token, 'ltc.watch.project': str(project_id)}
    chef_labels = {'ltc.chef.registry': token, 'ltc.chef.project': str(project_id), 'herdr.space': project['space']}
    if args.command == 'start':
        chef = os.environ.get('HC_TERMINAL_ID')
        session = os.environ.get('HC_SESSION') or 'default'
        if not chef or os.environ.get('HC_SOCKET'):
            raise ValueError('Start from the Chef Herdr terminal without an HC_SOCKET override')
        current = info(session, chef)
        labels = current.get('labels') or {}
        if current.get('address') != chef or current.get('state') != 'live' or labels.get('herdr.space') != project['space']:
            raise ValueError('Chef terminal is not live in the registered Space')
        if any(labels.get(k) not in (None, v) for k, v in chef_labels.items()):
            raise ValueError('Chef terminal is already assigned to another project or registry')
        call_hc(session, 'label', chef, *(k + '=' + v for k, v in chef_labels.items()))
        conn.execute('''INSERT INTO watch_targets (project_id, chef, session, space) VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET chef=excluded.chef, session=excluded.session, space=excluded.space''',
                     (project_id, chef, session, project['space']))
        conn.commit()
        try:
            existing = info(session, watcher_name)
        except subprocess.CalledProcessError as error:
            if error.returncode != 3:
                raise
            existing = None
        if existing is not None:
            labels = existing.get('labels') or {}
            argv = existing.get('argv') or []
            foreground = existing.get('foreground') or {}
            running_argv = foreground.get('argv') or []
            if existing.get('state') != 'live' or str(Path(__file__).resolve()) not in argv + running_argv or any(labels.get(k) != v for k, v in watcher_labels.items()):
                raise ValueError('Watcher name is already in use by another terminal')
            print(json.dumps({'status': 'already_running', 'watcher': existing.get('address'), 'chef': chef}))
            return 0
        cmd = ['run', '--bg', '--no-parent', '--outlive-parent', '--no-notify', '--name', watcher_name,
               '--label', 'ltc.watch.registry=' + token, '--label', 'ltc.watch.project=' + str(project_id),
               '--', sys.executable, str(Path(__file__).resolve()), '--db', str(db), 'run', args.project]
        response = json.loads(call_hc(session, *cmd, timeout=30).stdout)
        print(json.dumps({'status': 'started', 'watcher': response, 'chef': chef}))
        return 0

    lock_path = db.parent / ('.ltc-watch-' + suffix + '.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another watcher already holds the project lock')
        while True:
            try:
                observe_and_deliver(conn, project_id, token)
            except (OSError, sqlite3.Error, subprocess.SubprocessError) as error:
                if args.once:
                    raise
                print(f'ltc-watch: observation failed: {error}', file=sys.stderr, flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    return 0


def observe_and_deliver(conn, project_id, token):
    runs = [dict(row) for row in conn.execute('''SELECT cook_runs.* FROM cook_runs
        JOIN tasks ON tasks.id = cook_runs.task_id WHERE cook_runs.project_id = ?
        AND tasks.status NOT IN ('done', 'cancelled') ORDER BY cook_runs.id''', (project_id,))]
    for run in runs:
        if run['launch_state'] in ('failed', 'unknown', 'delivery_failed'):
            launch_state = 'launch_' + run['launch_state']
            previous_launch = conn.execute('SELECT state FROM watch_launch_observations WHERE run_id = ?', (run['id'],)).fetchone()
            if previous_launch is None or previous_launch['state'] != launch_state:
                conn.execute('''INSERT INTO watch_launch_observations (run_id, state) VALUES (?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET state=excluded.state''', (run['id'], launch_state))
                conn.execute('INSERT INTO watch_events (project_id, run_id, state) VALUES (?, ?, ?)', (project_id, run['id'], launch_state))
                conn.commit()
        if not run['terminal']:
            continue
        status = inspect_run(run, token)['terminal_status']
        if status['state'] == 'unknown':
            observation = 'unknown:' + status['reason']
        elif status['state'] == 'finished':
            observation = 'terminal_finished'
        elif status.get('agent') in ('done', 'blocked', 'error'):
            observation = 'agent_' + status['agent']
        else:
            observation = 'active'
        previous = conn.execute('SELECT state FROM watch_observations WHERE run_id = ?', (run['id'],)).fetchone()
        if previous is None or previous['state'] != observation:
            conn.execute('''INSERT INTO watch_observations (run_id, state) VALUES (?, ?)
                ON CONFLICT(run_id) DO UPDATE SET state=excluded.state''', (run['id'], observation))
            if observation != 'active':
                conn.execute('INSERT INTO watch_events (project_id, run_id, state) VALUES (?, ?, ?)', (project_id, run['id'], observation))
            conn.commit()
    events = conn.execute('SELECT * FROM watch_events WHERE project_id = ? AND delivered = 0 ORDER BY id', (project_id,)).fetchall()
    for event in events:
        target = conn.execute('SELECT * FROM watch_targets WHERE project_id = ?', (project_id,)).fetchone()
        if not target:
            break
        try:
            chef = info(target['session'], target['chef'])
            labels = chef.get('labels') or {}
            expected = {'ltc.chef.registry': token, 'ltc.chef.project': str(project_id), 'herdr.space': target['space']}
            if chef.get('address') != target['chef'] or chef.get('state') != 'live' or any(labels.get(k) != v for k, v in expected.items()):
                break
            message = (f"Cook event: run {event['run_id']}, {event['state']}. "
                       f"Check ltc-status and the Cook's report; this event does not confirm task completion.")
            call_hc(target['session'], 'send', target['chef'], message, timeout=75)
        except (OSError, ValueError, subprocess.SubprocessError):
            break
        conn.execute('UPDATE watch_events SET delivered = 1 WHERE id = ?', (event['id'],))
        conn.commit()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f'ltc-watch: {error}', file=sys.stderr)
        sys.exit(1)
