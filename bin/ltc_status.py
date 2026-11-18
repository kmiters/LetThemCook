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

try:
    from ltc_workspace import member_label_matches, members_for
except ModuleNotFoundError as error:
    if error.name != 'ltc_workspace':
        raise
    members_for = None

    def member_label_matches(run, labels):
        # A standalone checkout can verify unlabeled legacy runs. Member
        # identity requires the workspace module's inspection rules.
        return run.get('member_id') is None and 'ltc.member' not in labels


def terminal_env(run):
    env = dict(os.environ, HC_SESSION=run['hc_session'])
    env.pop('HC_SOCKET', None)
    return env


def inspect_terminal(run, token):
    """Return status and terminal metadata only when the terminal identity matches."""
    if run.get('_member_mismatch'):
        return {'state': 'unknown', 'reason': 'task_member_mismatch'}, None
    if run.get('_verified_closed'):
        return {'state': 'closed', 'reason': 'verified_close'}, None
    if not run['terminal']:
        return {'state': 'unknown', 'reason': 'no_recorded_terminal'}, None
    if not token:
        return {'state': 'unknown', 'reason': 'no_registry_identity'}, None
    try:
        output = subprocess.run(['hc', 'get', run['terminal']], text=True, capture_output=True,
                                env=terminal_env(run), check=True, timeout=5)
        terminal = json.loads(output.stdout)
        labels = terminal.get('labels') or {}
        expected = {'ltc.registry': token, 'ltc.run': str(run['id']),
                    'ltc.task': str(run['task_id']), 'ltc.project': str(run['project_id']),
                    'herdr.space': run['space']}
        if (terminal.get('address') != run['terminal']
                or any(labels.get(k) != v for k, v in expected.items())
                or not member_label_matches(run, labels)):
            reason = 'identity_mismatch'
        elif terminal.get('state') not in ('live', 'finished'):
            reason = 'unrecognized_state'
        else:
            status = terminal.get('status')
            agent = status.get('state') if isinstance(status, dict) else None
            return {'state': terminal['state'],
                    'agent': agent if isinstance(agent, str) else None}, terminal
    except subprocess.TimeoutExpired:
        reason = 'herdr_timeout'
    except (OSError, subprocess.CalledProcessError):
        reason = 'herdr_unavailable_or_terminal_missing'
    except (ValueError, TypeError, AttributeError, RecursionError):
        reason = 'invalid_herdr_response'
    return {'state': 'unknown', 'reason': reason}, None


def inspect_run(run, token):
    result = dict(run)
    result.pop('_verified_closed', None)
    result.pop('_member_mismatch', None)
    result['terminal_status'], _ = inspect_terminal(run, token)
    return result


def read_project_status(conn, project_id, *, include_launch_settings=False):
    """Read the registry inputs for a single project, including legacy databases."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    has_members = 'project_members' in tables
    members = members_for(conn, project_id) if has_members and members_for is not None else []
    task_member = ', member_id' if has_members and any(
        row[1] == 'member_id' for row in conn.execute('PRAGMA table_info(tasks)')) else ''
    tasks = [dict(row) for row in conn.execute(
        f'SELECT id, title, status, updated_at{task_member} FROM tasks WHERE project_id = ? ORDER BY id',
        (project_id,))]
    runs = []
    if 'cook_runs' in tables:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(cook_runs)')}
        optional = ', member_id' if 'member_id' in columns else ''
        if include_launch_settings:
            optional += ', ' + ', '.join(column if column in columns else f'NULL AS {column}'
                                        for column in ('harness', 'model', 'reasoning_effort'))
        verified_closed = ('EXISTS (SELECT 1 FROM closed_runs WHERE run_id = cook_runs.id)'
                           if 'closed_runs' in tables else '0')
        runs = [dict(row) for row in conn.execute(f'''SELECT id, task_id, project_id, space, worktree, base,
            terminal, hc_session, name, profile, launch_state, created_at, {verified_closed} AS _verified_closed
            {optional} FROM cook_runs WHERE project_id = ?
            AND task_id IN (SELECT id FROM tasks WHERE project_id = ?) ORDER BY id''', (project_id, project_id))]
    member_names = {member['id']: member['name'] for member in members}
    for row in tasks + runs:
        if 'member_id' in row:
            row['member'] = member_names.get(row['member_id'])
    if members:
        assigned = {task['id']: task.get('member_id') for task in tasks}
        for run in runs:
            run['_member_mismatch'] = (run.get('member_id') not in member_names
                                       or assigned[run['task_id']] != run.get('member_id'))
    identity = conn.execute('SELECT token FROM registry_identity WHERE id = 1').fetchone() if 'registry_identity' in tables else None
    return tasks, runs, identity[0] if identity else None, members


def current_runs(tasks, runs):
    latest = {run['task_id']: run['id'] for run in runs}
    active = {task['id'] for task in tasks if task['status'] not in ('done', 'cancelled')}
    # Never suppress uncertainty, live terminals, or launch failures.
    return [run for run in runs if (run['task_id'] in active and run['id'] == latest[run['task_id']])
            or run['terminal_status']['state'] not in ('finished', 'closed')
            or run['launch_state'] != 'started']


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
        tasks, runs, token, members = read_project_status(conn, project['id'])
        if members:
            project['members'] = members
    with ThreadPoolExecutor(max_workers=8) as pool:
        inspected = list(pool.map(lambda run: inspect_run(run, token), runs))
    if not args.all:
        retained = current_runs(tasks, inspected)
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
