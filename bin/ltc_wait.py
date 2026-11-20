#!/usr/bin/env python3
"""Wait for bounded, replayable project updates in the bound Chef terminal."""
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

from ltc_endpoint import chef_binding, verify_callback
from ltc_registry import has_reports

try:
    import ltc_lavish_inbox as lavish
except ModuleNotFoundError as error:
    if error.name != 'ltc_lavish_inbox':
        raise
    lavish = None


LIMIT = 10
POLL = 2
VERIFY_INTERVAL = 15
IDLE_GRACE = 20
SOURCES = {'event': ('watch_events', 'delivered'), 'report': ('cook_reports', 'notified')}
PROJECT_RUNS = '''FROM cook_runs r
    JOIN projects p ON p.id=r.project_id AND p.space=r.space'''
CURRENT = '''JOIN tasks t ON t.id=r.task_id AND t.project_id=p.id AND t.status NOT IN ('done','cancelled')
    WHERE p.id=? AND NOT EXISTS (SELECT 1 FROM cook_runs newer WHERE newer.project_id=r.project_id
        AND newer.space=r.space AND newer.worktree=r.worktree AND newer.id>r.id)'''


def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def acknowledge(conn, project, refs):
    if any(kind == 'live' for kind, _ in refs):
        conn.execute('''CREATE TABLE IF NOT EXISTS wait_live_acks (
            event_id INTEGER PRIMARY KEY REFERENCES watch_events(id))''')
    for kind, item_id in refs:
        table, flag = SOURCES.get(kind, SOURCES['event'])
        row = conn.execute(f'''SELECT item.id {PROJECT_RUNS} JOIN {table} item ON item.run_id=r.id
            WHERE p.id=? AND item.id=?''' + (' AND item.project_id=p.id' if kind != 'report' else ''),
                           (project, item_id)).fetchone() if has_table(conn, table) else None
        if row is None:
            raise ValueError('Acknowledgment does not match the project and Space')
        if kind == 'live':
            conn.execute('INSERT OR IGNORE INTO wait_live_acks VALUES (?)', (item_id,))
        else:
            conn.execute(f'UPDATE {table} SET {flag}=1 WHERE id=?', (item_id,))


def snapshot(conn, project, binding):
    events = conn.execute(f'''SELECT item.id, r.id AS run_id, r.task_id, substr(item.state,1,80) AS state
        {PROJECT_RUNS} JOIN watch_events item ON item.run_id=r.id AND item.project_id=p.id
        {CURRENT} AND item.delivered=0 ORDER BY item.id LIMIT ?''', (project, LIMIT))
    reports = conn.execute(f'''SELECT item.id, r.id AS run_id, r.task_id
        {PROJECT_RUNS} JOIN cook_reports item ON item.run_id=r.id
        WHERE p.id=? AND item.notified=0 ORDER BY item.id LIMIT ?''',
                           (project, LIMIT)) if has_reports(conn) else ()
    # An older watcher already records the current observation. Use its last
    # terminal event as a stable reference for the subsequent return to live.
    unacked = ('AND NOT EXISTS (SELECT 1 FROM wait_live_acks WHERE event_id=item.id)'
               if has_table(conn, 'wait_live_acks') else '')
    resumed = conn.execute(f'''SELECT item.id, r.id AS run_id, r.task_id
        {PROJECT_RUNS} JOIN watch_observations o ON o.run_id=r.id AND o.state='active'
        JOIN watch_events item ON item.id=(SELECT max(id) FROM watch_events
            WHERE run_id=r.id AND project_id=p.id AND state NOT LIKE 'launch_%'
            AND state != 'stale_start_without_terminal')
        {CURRENT} {unacked} ORDER BY item.id LIMIT ?''', (project, LIMIT))
    result = [{'ref': f'{kind}:{row["id"]}', 'task_id': row['task_id'], 'run_id': row['run_id'],
               'state': row['state'] if kind == 'event' else kind}
              for kind, rows in (('event', events), ('report', reports), ('live', resumed)) for row in rows]

    closed = ('AND NOT EXISTS (SELECT 1 FROM closed_runs WHERE run_id=r.id)'
              if has_table(conn, 'closed_runs') else '')
    busy, unknown, has_runs = conn.execute(f'''SELECT
        coalesce(max((r.terminal IS NOT NULL AND
            (o.state IS NULL OR o.state IN ('active','agent_blocked'))) OR
            (r.launch_state='starting' AND r.terminal IS NULL AND r.created_at>datetime('now','-5 minutes'))),0),
        coalesce(max(o.state LIKE 'unknown:%'),0), count(*) > 0
        {PROJECT_RUNS} LEFT JOIN watch_observations o ON o.run_id=r.id {CURRENT} {closed}''', (project,)).fetchone()

    batches, feedback = [], False
    if lavish is not None and lavish.has_inbox(conn):
        address, session, space, _ = binding
        batches = lavish.pending(conn, project, None, lavish.Agent(session, address, space),
                                 all_members=True, offer=False)[:LIMIT]
        if has_table(conn, 'lavish_listeners'):
            feedback = conn.execute('''SELECT 1 FROM lavish_reviews r
                JOIN lavish_owners o ON o.review=r.id JOIN lavish_listeners l ON l.review=r.id
                WHERE r.project_id=? AND o.session=? AND o.space=? AND o.run_id IS NULL
                AND r.phase IN ('open','ending') AND l.state IN ('starting','receiving','reconnecting')
                LIMIT 1''', (project, session, space)).fetchone() is not None
    return result, batches, busy, unknown, has_runs, feedback


def watcher_running(lock_path):
    try:
        with lock_path.open('rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except FileNotFoundError:
        pass
    return False


def registry_busy(error):
    code = getattr(error, 'sqlite_errorcode', None)
    if code is not None:
        return code & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    # Python 3.9 and 3.10 expose the SQLite result only in the exception text.
    return str(error).split(':', 1)[0] in ('database is locked', 'database table is locked',
                                           'database schema is locked')


def wait(conn, db, project, args, *, now=time.monotonic, sleep=time.sleep):
    address = os.environ.get('HC_TERMINAL_ID')
    if not address:
        raise ValueError('Run from the bound Chef terminal')
    binding = verify_callback(conn, project, address)
    deadline = now() + args.timeout if args.timeout else None
    next_verify = now() + VERIFY_INTERVAL
    suffix = hashlib.sha256((str(db) + ':' + str(project)).encode()).hexdigest()[:16]
    lock = db.parent / ('.ltc-watch-' + suffix + '.lock')

    refs = args.ack
    failures = 0
    idle_since = None
    while True:
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE' if refs else 'BEGIN')
                if chef_binding(conn, project) != binding:
                    raise ValueError('Chef binding changed. Retry from the current bound Chef terminal.')
                if refs:
                    acknowledge(conn, project, refs)
                updates, batches, busy, unknown, has_runs, feedback = snapshot(conn, project, binding)
        except sqlite3.OperationalError as error:
            if not registry_busy(error) or failures == 2:
                raise
            failures += 1
        else:
            failures = 0
            refs = []
            result = None
            if busy or feedback:
                idle_since = None
            elif idle_since is None:
                # A follow-up can make a previously done Cook live before the
                # watcher has recorded its next observation.
                idle_since = now()
            if updates or batches:
                result = {'status': 'updates', 'updates': updates, 'lavish_batches': batches}
            elif (busy or unknown or not feedback) and not watcher_running(lock):
                result = {'status': 'watcher_unavailable'}
            elif idle_since is not None and (args.check or not has_runs or now() - idle_since >= IDLE_GRACE):
                result = {'status': 'unverified' if unknown else 'idle'}
            elif args.check:
                result = {'status': 'waiting'}
            elif deadline is not None and now() >= deadline:
                result = {'status': 'deadline'}
            if result or now() >= next_verify:
                if verify_callback(conn, project, address) != binding or chef_binding(conn, project) != binding:
                    raise ValueError('Chef binding changed. Retry from the current bound Chef terminal.')
                next_verify = now() + VERIFY_INTERVAL
            if result:
                return result
        sleep(POLL if deadline is None else min(POLL, max(0, deadline - now())))


def reference(value):
    kind, _, number = value.partition(':')
    if (kind not in (*SOURCES, 'live') or not number.isascii() or not number.isdecimal()
            or len(number) > 19 or not 0 < int(number) < 2**63):
        raise argparse.ArgumentTypeError('Use event:ID, report:ID or live:ID')
    return kind, int(number)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--db', default=os.environ.get('LTC_DB', str(root / '.letthemcook/registry.sqlite3')))
    parser.add_argument('--ack', action='append', type=reference, default=[], metavar='REF')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='Check once without waiting for an update')
    modes.add_argument('--timeout', type=int, default=0, metavar='SECONDS', help='Optional overall deadline')
    args = parser.parse_args()
    if not 0 <= args.timeout <= 3600 or len(args.ack) > 3 * LIMIT:
        parser.error('Use a timeout from 0 to 3600 seconds and at most 30 acknowledgment references')
    db = Path(args.db).expanduser().resolve()
    mode = 'rw' if args.ack else 'ro'
    with sqlite3.connect(db.as_uri() + '?mode=' + mode, uri=True, timeout=1) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute('SELECT id FROM projects WHERE name=?', (args.project,)).fetchone()
        if project is None:
            raise ValueError('Project is not registered')
        print(json.dumps(wait(conn, db, project['id'], args)), flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        sys.exit(f'ltc-wait: {error}')
