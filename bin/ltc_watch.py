#!/usr/bin/env python3
"""Record Cook transitions and let the bound Chef retrieve project updates."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from ltc_endpoint import chef_binding, verify_callback
from ltc_registry import has_reports
from ltc_status import inspect_run, stale_start

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
    run.add_argument('--once', action='store_true', help='Record one observation cycle, then exit')
    inbox = commands.add_parser('inbox', help='Read new Cook report IDs and transitions from the bound Chef terminal')
    inbox.add_argument('project')
    inbox.add_argument('--recent', action='store_true', help='Re-read recent entries without acknowledging them')
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
    if args.command == 'inbox':
        print(json.dumps(read_inbox(conn, project_id, args.recent)))
        return 0
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
                observe(conn, project_id, token)
            except (OSError, sqlite3.Error, subprocess.SubprocessError) as error:
                if args.once:
                    raise
                print(f'ltc-watch: observation failed: {error}', file=sys.stderr, flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    return 0


def observe(conn, project_id, token):
    runs = [dict(row) for row in conn.execute('''SELECT cook_runs.* FROM cook_runs
        JOIN tasks ON tasks.id = cook_runs.task_id AND tasks.project_id = cook_runs.project_id
        WHERE cook_runs.project_id = ?
        AND tasks.status NOT IN ('done', 'cancelled') ORDER BY cook_runs.id''', (project_id,))]
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda run: inspect_run(run, token)['terminal_status'] if run['terminal'] else None, runs))
    for run, status in zip(runs, statuses):
        launch_state = None
        if stale_start(run):
            launch_state = 'stale_start_without_terminal'
        elif run['launch_state'] in ('failed', 'unknown', 'delivery_failed'):
            launch_state = 'launch_' + run['launch_state']
        if launch_state:
            previous_launch = conn.execute('SELECT state FROM watch_launch_observations WHERE run_id = ?', (run['id'],)).fetchone()
            if previous_launch is None or previous_launch['state'] != launch_state:
                conn.execute('''INSERT INTO watch_launch_observations (run_id, state) VALUES (?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET state=excluded.state''', (run['id'], launch_state))
                conn.execute('INSERT INTO watch_events (project_id, run_id, state) VALUES (?, ?, ?)', (project_id, run['id'], launch_state))
                conn.commit()
        if not run['terminal']:
            continue
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


def read_inbox(conn, project_id, recent):
    chef = os.environ.get('HC_TERMINAL_ID')
    if not chef:
        raise ValueError('Read the inbox from the bound Chef terminal')
    binding = verify_callback(conn, project_id, chef)
    # Claim only if the verified endpoint still owns this project.
    with conn:
        conn.execute('BEGIN' if recent else 'BEGIN IMMEDIATE')
        if chef_binding(conn, project_id) != binding:
            raise ValueError('Chef binding changed during inbox verification')
        scope = """JOIN cook_runs r ON r.id=item.run_id
            JOIN tasks t ON t.id=r.task_id AND t.project_id=r.project_id
            JOIN projects p ON p.id=r.project_id
            WHERE p.id=?"""
        if not recent:
            scope += """ AND r.space=p.space AND t.status NOT IN ('done','cancelled')
                AND NOT EXISTS (SELECT 1 FROM cook_runs newer WHERE newer.project_id=r.project_id
                    AND newer.worktree=r.worktree AND newer.id>r.id)"""

        def entries(table, flag, extra):
            where = scope if recent else scope + f' AND item.{flag}=0'
            if table == 'watch_events':
                where += ' AND item.project_id=r.project_id'
            order = 'DESC' if recent else 'ASC'
            rows = conn.execute(f"""SELECT item.id, r.id AS run_id, t.id AS task_id {extra}
                FROM {table} item {where} ORDER BY item.id {order} LIMIT 20""", (project_id,)).fetchall()
            if not recent:
                conn.executemany(f'UPDATE {table} SET {flag}=1 WHERE id=?', [(row['id'],) for row in rows])
            return [dict(row) for row in rows]

        events = entries('watch_events', 'delivered', ', item.state')
        reports = entries('cook_reports', 'notified', '') if has_reports(conn) else []
        return {'events': events, 'reports': reports}


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f'ltc-watch: {error}', file=sys.stderr)
        sys.exit(1)
