#!/usr/bin/env python3
"""Operate on a Cook only after checking its recorded identity in Herdr."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from ltc_worktree import inspect
from ltc_workspace import member_for_run, member_label_matches

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.environ.get('LTC_DB', str(ROOT / '.letthemcook' / 'registry.sqlite3')))
    commands = parser.add_subparsers(dest='command', required=True)
    send = commands.add_parser('send')
    send.add_argument('run_id', type=int)
    send.add_argument('message')
    close = commands.add_parser('close')
    close.add_argument('run_id', type=int)
    close.add_argument('--discard-changes', action='store_true', help='Discard unlanded work only after the user explicitly approves this worktree')
    check = commands.add_parser('check')
    check.add_argument('run_id', type=int)
    args = parser.parse_args()
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise ValueError('Registry not found')
    # Serialize cleanup with resume/launch reservations for this registry.
    # Non-mutating operations use a read-only connection.
    mode = 'rw' if args.command == 'close' else 'ro'
    conn = sqlite3.connect(db.as_uri() + '?mode=' + mode, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    if args.command == 'close':
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS closed_runs (
            run_id INTEGER PRIMARY KEY REFERENCES cook_runs(id),
            closed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
    row = conn.execute('''SELECT cook_runs.*, tasks.status AS task_status, projects.path AS project_path, registry_identity.token
        FROM cook_runs JOIN tasks ON tasks.id = cook_runs.task_id
        JOIN projects ON projects.id = cook_runs.project_id AND tasks.project_id = projects.id
        CROSS JOIN registry_identity WHERE cook_runs.id = ? AND registry_identity.id = 1''', (args.run_id,)).fetchone()
    if row is None:
        raise ValueError('Run or registry identity not found')
    run = dict(row)
    member = member_for_run(conn, run)
    checkout = member['path'] if member else run['project_path']
    if args.command == 'close':
        # A verified removal ends the lifetime of this worktree, including its
        # resumed runs. Old receipts only identify the selected run.
        columns = {r[1] for r in conn.execute('PRAGMA table_info(closed_runs)')}
        if 'last_run_id' not in columns:
            conn.execute('ALTER TABLE closed_runs ADD COLUMN last_run_id INTEGER')
        boundary = conn.execute('''SELECT COALESCE(MAX(COALESCE(c.last_run_id, c.run_id)), 0)
            FROM closed_runs c JOIN cook_runs r ON r.id=c.run_id WHERE r.worktree=?''',
            (run['worktree'],)).fetchone()[0]
        if run['id'] <= boundary:
            raise ValueError('This worktree lifecycle has already been closed')
        siblings = [dict(item) for item in conn.execute(
            'SELECT * FROM cook_runs WHERE worktree=? AND id>? AND id!=?',
            (run['worktree'], boundary, run['id']))]
    address = run['terminal']
    if not address:
        raise ValueError('Run has no recorded terminal; inspect the launch before acting')
    if args.command == 'close' and run['task_status'] not in (('done', 'cancelled') if args.discard_changes else ('done',)):
        raise ValueError('Task is not marked done; user acceptance is required before closing')
    if args.command == 'send' and not args.message.strip():
        raise ValueError('Message must not be empty')
    env = dict(os.environ, HC_SESSION=run['hc_session'])
    env.pop('HC_SOCKET', None)

    def checked_terminal(record):
        if not record['terminal']:
            raise ValueError('Run has no recorded terminal; cannot verify its state')
        endpoint_env = dict(os.environ, HC_SESSION=record['hc_session'])
        endpoint_env.pop('HC_SOCKET', None)
        output = subprocess.run(['hc', 'get', record['terminal']], env=endpoint_env, text=True, capture_output=True, check=True, timeout=10)
        terminal = json.loads(output.stdout)
        labels = terminal.get('labels') or {}
        identity = {'ltc.registry': run['token'], 'ltc.run': str(record['id']),
                    'ltc.task': str(record['task_id']), 'ltc.project': str(record['project_id']),
                    'herdr.space': record['space']}
        if (terminal.get('address') != record['terminal'] or not member_label_matches(record, labels)
                or any(labels.get(k) != v for k, v in identity.items())):
            raise ValueError('Terminal identity does not match the recorded run; no action taken')
        if terminal.get('state') not in ('live', 'finished'):
            raise ValueError('Terminal state is unknown; no action taken')
        return terminal

    terminal = checked_terminal(run)
    if args.command == 'send':
        if terminal['state'] != 'live':
            raise ValueError('Cook terminal is not live')
        subprocess.run(['hc', 'send', address, args.message], env=env, check=True)
    elif args.command == 'check':
        report = inspect(checkout, run['worktree'], run['base'])
        if member:
            report['member'] = member['name']
        print(json.dumps(report, ensure_ascii=False))
    else:
        native_status = terminal.get('status')
        if terminal['state'] == 'live' and isinstance(native_status, dict) and native_status.get('state') in ('working', 'blocked'):
            raise ValueError('Cook is working or blocked; inspect it before closing')
        for sibling in siblings:
            if not sibling['terminal'] and sibling['launch_state'] == 'failed':
                continue
            if checked_terminal(sibling)['state'] != 'finished':
                raise ValueError('Another Cook for this worktree is still live; no action taken')
        report = inspect(checkout, run['worktree'], run['base'])
        if not report['safe_to_remove'] and not args.discard_changes:
            print(json.dumps({'error': 'work_not_confirmed_on_main', 'report': report}, ensure_ascii=False), file=sys.stderr)
            return 1
        if terminal['state'] == 'live':
            subprocess.run(['hc', 'kill', address], env=env, check=True)
            terminal = checked_terminal(run)
            if terminal['state'] != 'finished':
                raise ValueError('Cook terminal did not finish; worktree was not removed')
        report = inspect(checkout, run['worktree'], run['base'])
        if not report['safe_to_remove'] and not args.discard_changes:
            print(json.dumps({'error': 'work_changed_during_close', 'report': report}, ensure_ascii=False), file=sys.stderr)
            return 1
        for sibling in siblings:
            if not sibling['terminal'] and sibling['launch_state'] == 'failed':
                continue
            if checked_terminal(sibling)['state'] != 'finished':
                raise ValueError('Another Cook for this worktree is still live; worktree was not removed')
        remove = ['git', '-C', report['project'], 'worktree', 'remove']
        if args.discard_changes and not report['safe_to_remove']:
            remove.append('--force')
        remove += ['--', report['worktree']]
        subprocess.run(remove, check=True)
        terminal = checked_terminal(run)
        if terminal['state'] != 'finished':
            raise ValueError('Terminal state changed; it was not removed')
        subprocess.run(['hc', 'rm', address], env=env, check=True)
        last_run = max([run['id'], *(item['id'] for item in siblings)])
        conn.execute('INSERT INTO closed_runs (run_id,last_run_id) VALUES (?,?)', (run['id'], last_run))
        conn.commit()
        print(json.dumps({'closed': run['id'], 'removed_worktree': report['worktree'], 'discarded_changes': bool(args.discard_changes and not report['safe_to_remove'])}))
    conn.close()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, sqlite3.Error, ValueError, subprocess.SubprocessError) as error:
        print(f'ltc-cook-control: {error}', file=sys.stderr)
        sys.exit(1)
