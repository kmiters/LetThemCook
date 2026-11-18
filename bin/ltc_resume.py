#!/usr/bin/env python3
"""Resume a recorded Cook in the same worktree after its terminal has finished."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path

from ltc_harness import CookRoute, resolve_route
from ltc_workspace import member_for_run, member_label_matches, member_prompt
from ltc_registry import ensure_runs, format_task, get_brief, start_run, terminal_name
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.environ.get('LTC_DB', str(ROOT / '.letthemcook' / 'registry.sqlite3')))
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    parser.add_argument('run_id', type=int, help='Most recent recorded run for the worktree')
    args = parser.parse_args()
    chef = os.environ.get('HC_TERMINAL_ID')
    if not chef:
        raise ValueError('Run from the Chef Herdr terminal (HC_TERMINAL_ID missing)')
    if os.environ.get('HC_SOCKET'):
        raise ValueError('Cannot verify the Chef session with HC_SOCKET override; inspect it first')
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise ValueError('Registry not found')
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text())
    prompt_base = (config_path.parent / config['prompts']['cook']).resolve().read_text()
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('BEGIN IMMEDIATE')
    row = conn.execute('''SELECT cook_runs.*, tasks.title, tasks.description, tasks.status AS task_status,
        projects.path AS project_path, projects.space AS current_space, registry_identity.token
        FROM cook_runs JOIN tasks ON tasks.id = cook_runs.task_id
        JOIN projects ON projects.id = cook_runs.project_id AND tasks.project_id = projects.id
        CROSS JOIN registry_identity WHERE cook_runs.id = ? AND registry_identity.id = 1''', (args.run_id,)).fetchone()
    if row is None:
        raise ValueError('Run or registry identity not found')
    run = dict(row)
    member = member_for_run(conn, run)
    task_brief = format_task({'id': run['task_id'], 'title': run['title'], 'description': run['description'], 'brief': get_brief(conn, run['task_id'])})
    if run['task_status'] in ('done', 'cancelled'):
        raise ValueError('A finished or cancelled task cannot be resumed')
    if run['space'] != run['current_space']:
        raise ValueError('Project Space changed; inspect before resuming')
    if (os.environ.get('HC_SESSION') or 'default') != run['hc_session']:
        raise ValueError('Chef is not in the recorded Herdr session')
    latest = conn.execute('SELECT MAX(id) FROM cook_runs WHERE worktree = ?', (run['worktree'],)).fetchone()[0]
    if latest != run['id']:
        raise ValueError('A newer run exists for this worktree; inspect it instead')
    if not run['terminal']:
        raise ValueError('No recorded terminal; cannot prove the previous Cook stopped')
    env = dict(os.environ, HC_SESSION=run['hc_session'])
    previous = json.loads(subprocess.run(['hc', 'get', run['terminal']], env=env, capture_output=True,
                                          text=True, check=True, timeout=10).stdout)
    expected = {'ltc.registry': run['token'], 'ltc.run': str(run['id']), 'ltc.task': str(run['task_id']),
                'ltc.project': str(run['project_id']), 'herdr.space': run['space']}
    labels = previous.get('labels') or {}
    if (previous.get('address') != run['terminal'] or not member_label_matches(run, labels)
            or any(labels.get(key) != value for key, value in expected.items())):
        raise ValueError('Previous terminal identity does not match the run')
    if previous.get('state') != 'finished':
        raise ValueError('Previous terminal is not confirmed finished')
    project = Path(member['path'] if member else run['project_path']).resolve(strict=True)
    worktree = Path(run['worktree']).resolve(strict=True)
    if worktree == project or not worktree.is_dir():
        raise ValueError('Recorded worktree is invalid')
    listed = subprocess.run(['git', '-C', str(project), 'worktree', 'list', '--porcelain', '-z'],
                            capture_output=True, check=True).stdout.split(b'\0')
    worktrees = [Path(os.fsdecode(field[9:])).resolve() for field in listed if field.startswith(b'worktree ')]
    if worktree not in worktrees:
        raise ValueError('Recorded worktree is not registered to this project')
    current_root = subprocess.run(['git', '-C', str(worktree), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True).stdout.strip()
    if Path(current_root).resolve() != worktree:
        raise ValueError('Recorded worktree is not its own Git root')
    name = terminal_name(run['title'])
    if run.get('harness') is None:
        # Old runs recorded only the Codex profile name.
        route = resolve_route(config, run['profile'], 'codex')
    else:
        route = CookRoute(run['profile'], run['harness'], run['model'], run['reasoning_effort'])
    prompt = (prompt_base + '\n\n# Resumed task\n' + task_brief
              + '\n\nThis is a fresh session continuing the same task in the existing worktree. Inspect existing changes and prior context before editing; preserve the work. Do not assume the task is complete.'
              + '\nPrevious run ID: ' + str(run['id']) + '\nYour worktree: ' + str(worktree) + '\nRegistry: ' + str(db)
              + '\nLetThemCook directory: ' + str(ROOT) + '\nReport the outcome or blocker to the Chef using hc send ' + chef + '.')
    if member:
        prompt += member_prompt(member, run['project_path'])
    harness_cmd = route.command(prompt)
    ensure_runs(conn)
    new_id = start_run(conn, run['task_id'], run['project_id'], run['space'], worktree,
                       run['base'], run['hc_session'], name, route)
    if member:
        conn.execute('UPDATE cook_runs SET member_id = ? WHERE id = ?', (member['id'], new_id))
    conn.commit()

    def record(state, terminal=None, detail=None):
        conn.execute('UPDATE cook_runs SET launch_state = ?, terminal = ?, detail = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (state, terminal, detail, new_id))
        conn.commit()

    cmd = ['hc', 'run', '--bg', '--name', name, '--cwd', str(worktree), '--parent', chef,
           '--label', 'herdr.space=' + run['space'], '--label', 'ltc.registry=' + run['token'],
           '--label', 'ltc.run=' + str(new_id), '--label', 'ltc.task=' + str(run['task_id']),
           '--label', 'ltc.project=' + str(run['project_id'])]
    if member:
        cmd += ['--label', 'ltc.member=' + str(member['id'])]
    cmd += ['--', *harness_cmd]
    try:
        launch = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        state = 'failed' if isinstance(error, FileNotFoundError) else 'unknown'
        record(state, detail=str(error))
        print(json.dumps({'error': 'launch_failed', 'run_id': new_id, 'worktree': str(worktree), 'detail': str(error), 'stderr': getattr(error, 'stderr', None)}), file=sys.stderr)
        return 1
    try:
        herdr = json.loads(launch.stdout)
        if not isinstance(herdr, dict) or not isinstance(herdr.get('address'), str) or not herdr['address']:
            raise ValueError('Launch response has no terminal address')
    except (ValueError, TypeError) as error:
        record('unknown', detail=str(error))
        print(json.dumps({'error': 'launch_response_unknown', 'run_id': new_id, 'worktree': str(worktree), 'response': launch.stdout}), file=sys.stderr)
        return 1
    first = herdr.get('first_message')
    state = 'delivery_failed' if isinstance(first, dict) and first.get('sent') is False else 'started'
    try:
        record(state, herdr['address'], json.dumps(first) if first is not None else None)
    except sqlite3.Error as error:
        print(json.dumps({'error': 'registry_update_failed', 'run_id': new_id, 'worktree': str(worktree), 'herdr': herdr, 'detail': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'run_id': new_id, 'previous_run_id': run['id'], 'member': member['name'] if member else None, **asdict(route), 'worktree': str(worktree), 'herdr': herdr}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f'ltc-resume: {error}', file=sys.stderr)
        sys.exit(1)
