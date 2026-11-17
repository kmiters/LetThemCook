#!/usr/bin/env python3
"""Read one project's task registry and inspect recorded Herdr terminals."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def inspect_run(run, token):
    result = dict(run)
    if result.pop('_verified_closed', False):
        result['terminal_status'] = {'state': 'closed', 'reason': 'verified_close'}
        return result
    result['terminal_status'] = {'state': 'unknown', 'reason': 'no_recorded_terminal'}
    if not run['terminal']:
        return result
    if not token:
        result['terminal_status']['reason'] = 'no_registry_identity'
        return result
    env = dict(os.environ, HC_SESSION=run['hc_session'])
    env.pop('HC_SOCKET', None)
    try:
        output = subprocess.run(['hc', 'get', run['terminal']], text=True, capture_output=True,
                                env=env, check=True, timeout=5)
        terminal = json.loads(output.stdout)
        labels = terminal.get('labels') or {}
        expected = {'ltc.registry': token, 'ltc.run': str(run['id']),
                    'ltc.task': str(run['task_id']), 'ltc.project': str(run['project_id']),
                    'herdr.space': run['space']}
        if terminal.get('address') != run['terminal'] or any(labels.get(k) != v for k, v in expected.items()):
            result['terminal_status']['reason'] = 'identity_mismatch'
        elif terminal.get('state') not in ('live', 'finished'):
            result['terminal_status']['reason'] = 'unrecognized_state'
        else:
            status = terminal.get('status')
            result['terminal_status'] = {'state': terminal['state'],
                                         'agent': status.get('state') if isinstance(status, dict) else None}
    except subprocess.TimeoutExpired:
        result['terminal_status']['reason'] = 'herdr_timeout'
    except (OSError, subprocess.CalledProcessError):
        result['terminal_status']['reason'] = 'herdr_unavailable_or_terminal_missing'
    except (ValueError, TypeError, AttributeError):
        result['terminal_status']['reason'] = 'invalid_herdr_response'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.environ.get('LTC_DB', str(ROOT / '.letthemcook' / 'registry.sqlite3')))
    parser.add_argument('--all', action='store_true', help='Include complete task and run history')
    parser.add_argument('project', help='Registered project name')
    args = parser.parse_args()
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise ValueError('Registry not found')
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute('SELECT * FROM projects WHERE name = ?', (args.project,)).fetchone()
        if project is None:
            raise ValueError(f'Project not found: {args.project}')
        project = dict(project)
        tasks = [dict(row) for row in conn.execute('SELECT id, title, status, updated_at FROM tasks WHERE project_id = ? ORDER BY id', (project['id'],))]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        runs = ([dict(row) for row in conn.execute('''SELECT id, task_id, project_id, space, worktree, base,
                   terminal, hc_session, name, profile, launch_state, created_at FROM cook_runs
                   WHERE project_id = ? ORDER BY id''', (project['id'],))] if 'cook_runs' in tables else [])
        identity = conn.execute('SELECT token FROM registry_identity WHERE id = 1').fetchone() if 'registry_identity' in tables else None
        token = identity[0] if identity else None
        closed = {row[0] for row in conn.execute('SELECT run_id FROM closed_runs')} if 'closed_runs' in tables else set()
        for run in runs:
            run['_verified_closed'] = run['id'] in closed
    with ThreadPoolExecutor(max_workers=8) as pool:
        inspected = list(pool.map(lambda run: inspect_run(run, token), runs))
    if not args.all:
        latest = {run['task_id']: run['id'] for run in inspected}
        active = {task['id'] for task in tasks if task['status'] not in ('done', 'cancelled')}
        # Never suppress uncertainty, live terminals, or launch failures.
        retained = [run for run in inspected if (run['task_id'] in active and run['id'] == latest[run['task_id']])
                    or run['terminal_status']['state'] not in ('finished', 'closed')
                    or run['launch_state'] != 'started']
        relevant = {run['task_id'] for run in retained}
        visible_tasks = [task for task in tasks if task['status'] not in ('done', 'cancelled') or task['id'] in relevant]
        omitted = {'tasks': len(tasks) - len(visible_tasks), 'runs': len(inspected) - len(retained)}
        tasks, inspected = visible_tasks, retained
    else:
        omitted = {'tasks': 0, 'runs': 0}
    print(json.dumps({'project': project, 'tasks': tasks, 'runs': inspected, 'omitted': omitted}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f'ltc-status: {error}', file=sys.stderr)
        sys.exit(1)
