#!/usr/bin/env python3
"""Collect a bounded, read-only snapshot of current Cook sessions for one project."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
from time import monotonic
import unicodedata

from ltc_status import ROOT, current_runs, inspect_terminal, read_project_status, terminal_env
from ltc_whatsup_summary import SUMMARY_MODELS, render_summary

try:
    from ltc_workspace import matching_project_ids
except ModuleNotFoundError as error:
    if error.name != 'ltc_workspace':
        raise
    matching_project_ids = None


HISTORY_LINES = 24
READ_TIMEOUT = 5
MAX_READ_BYTES = 256 * 1024
MAX_EXCERPT_CHARS = 5000
MAX_EXCERPT_LINES = 45


def resolve_project(conn, name, cwd):
    if name is not None:
        project = conn.execute('SELECT id, name FROM projects WHERE name = ?', (name,)).fetchone()
        if project is None:
            raise ValueError(f'Project not found: {name}')
        return project

    if matching_project_ids is not None:
        ids = matching_project_ids(conn, cwd)
        projects = [conn.execute('SELECT id, name FROM projects WHERE id = ?', (id_,)).fetchone() for id_ in ids]
    else:
        # Legacy checkouts do not have the workspace resolver yet.
        try:
            output = subprocess.run(['git', 'rev-parse', '--path-format=absolute',
                                     '--show-toplevel', '--git-common-dir'], cwd=cwd, text=True,
                                    capture_output=True, check=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('Current directory has no registered project; specify PROJECT') from None
        root, git_dir = map(Path, output.stdout.splitlines())
        projects = conn.execute('SELECT id, name FROM projects WHERE path = ?', (str(root.resolve()),)).fetchall()
        if not projects and git_dir.name == '.git':
            projects = conn.execute('SELECT id, name FROM projects WHERE path = ?',
                                    (str(git_dir.resolve().parent),)).fetchall()

    if len(projects) == 1:
        return projects[0]
    if projects:
        raise ValueError('Multiple projects match the current directory; specify PROJECT')
    raise ValueError('Current directory has no registered project; specify PROJECT')


def bounded_read(run):
    command = ['hc', 'read', '--json', '--lines', str(HISTORY_LINES), '--', run['terminal']]
    deadline = monotonic() + READ_TIMEOUT
    with subprocess.Popen(command, env=terminal_env(run), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                data = bytearray()
                while selector.select(max(0, deadline - monotonic())) and monotonic() < deadline:
                    chunk = os.read(process.stdout.fileno(), min(16384, MAX_READ_BYTES + 1 - len(data)))
                    if not chunk:
                        if process.wait(timeout=max(0, deadline - monotonic())):
                            return None, 'screen_unavailable'
                        return data, None
                    data.extend(chunk)
                    if len(data) > MAX_READ_BYTES:
                        return None, 'screen_output_limit'
            return None, 'screen_timeout'
        finally:
            # Bound the lifetime of the read client. The Cook stays untouched.
            if process.poll() is None:
                process.kill()


def read_screen(run):
    try:
        data, reason = bounded_read(run)
        if reason:
            return {'state': 'unavailable', 'reason': reason}
        screen = json.loads(data)
        text = screen['text']
        if not isinstance(text, str):
            raise ValueError('Screen text is not a string')
        # Keep the excerpt as JSON data even when it contains shell commands or markup.
        text = ''.join(char for char in text if char in '\n\t' or unicodedata.category(char) not in ('Cc', 'Cf')).strip()
        lines = text.splitlines()
        excerpt = '\n'.join(lines[-MAX_EXCERPT_LINES:])[-MAX_EXCERPT_CHARS:]
        if not excerpt:
            return {'state': 'unavailable', 'reason': 'empty_screen'}
        return {'state': 'available', 'excerpt': excerpt,
                'partial': screen.get('complete') is not True or excerpt != text}
    except subprocess.TimeoutExpired:
        return {'state': 'unavailable', 'reason': 'screen_timeout'}
    except OSError:
        return {'state': 'unavailable', 'reason': 'screen_unavailable'}
    except (ValueError, TypeError, KeyError, RecursionError):
        return {'state': 'unavailable', 'reason': 'invalid_screen_response'}


def launch_settings(run, terminal):
    settings = {key: run[key] for key in ('harness', 'model', 'reasoning_effort')}
    settings['source'] = 'registry' if settings['harness'] else 'unknown'
    if settings['harness'] or terminal is None:
        return settings

    argv = terminal.get('argv')
    if not isinstance(argv, list) or not argv or argv[0] != 'codex':
        return settings
    # Older launchers recorded just the profile. Their verified launch argv can
    # establish the executable and an explicitly requested model.
    settings.update(harness='codex', source='verified_terminal_argv')
    for option, value in zip(argv[1:], argv[2:]):
        if option == '-m' and isinstance(value, str):
            settings['model'] = value
            break
    return settings


def describe_run(run, task, status, terminal, screen):
    state = status['state']
    if state == 'live':
        state = {'working': 'working', 'idle': 'idle', 'blocked': 'blocked', 'error': 'blocked',
                 'done': 'response_ready'}.get(status['agent'], 'unknown')
    native = terminal.get('status') if terminal else None
    reason = status.get('reason') or ('unrecognized_agent_state' if state == 'unknown' else None)
    result = {
        'id': run['id'], 'task_id': task['id'], 'task_title': task['title'], 'task_status': task['status'],
        'terminal': run['terminal'], 'hc_session': run['hc_session'], 'worktree': run['worktree'],
        'launch_state': run['launch_state'], 'profile': run['profile'],
        'terminal_status': status, 'cook_state': state, 'cook_state_reason': reason,
        'observed_app': native.get('app') if isinstance(native, dict) else None,
        'exit_code': terminal.get('exit_code') if terminal else None,
        'launch_settings': launch_settings(run, terminal), 'screen_untrusted': screen,
    }
    if 'member_id' in run:
        result.update(member_id=run['member_id'], member=run['member'])
    return result


def add_screen(run, status, terminal, token):
    if terminal is None:
        return status, None, {'state': 'unavailable', 'reason': status['reason']}
    screen = read_screen(run)
    if screen['state'] == 'available':
        # hc read does not return labels. Discard the excerpt if the same
        # address is no longer verifiable after the read.
        status, latest = inspect_terminal(run, token)
        if latest is None:
            return status, None, {'state': 'unavailable', 'reason': status['reason']}
        if latest.get('created_ms') != terminal.get('created_ms'):
            status = {'state': 'unknown', 'reason': 'terminal_replaced'}
            return status, None, {'state': 'unavailable', 'reason': status['reason']}
        terminal = latest
    return status, terminal, screen


def snapshot(project, tasks, runs, token):
    tasks_by_id = {task['id']: task for task in tasks}
    terminals = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for run, (status, terminal) in zip(runs, pool.map(lambda run: inspect_terminal(run, token), runs)):
            run['terminal_status'] = status
            terminals[run['id']] = terminal
        relevant = current_runs(tasks, runs)

        def summarize(run):
            status, terminal, screen = add_screen(run, run['terminal_status'], terminals[run['id']], token)
            return describe_run(run, tasks_by_id[run['task_id']], status, terminal, screen)

        inspected = list(pool.map(summarize, relevant))
    return {'project': dict(project), 'runs': inspected, 'omitted_runs': len(runs) - len(relevant)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.environ.get('LTC_DB', str(ROOT / '.letthemcook' / 'registry.sqlite3')))
    parser.add_argument('--summarize', choices=SUMMARY_MODELS, help='Use the selected CLI to summarize with its fast model')
    parser.add_argument('--language', default='English', help='Language of the model summary')
    parser.add_argument('project', nargs='?', help='Registered project name (default: current repository)')
    args = parser.parse_args()
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        raise ValueError('Registry not found; pass --db or set LTC_DB')
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        project = resolve_project(conn, args.project, Path.cwd())
        tasks, runs, token, _ = read_project_status(conn, project['id'], include_launch_settings=True)
    report = snapshot(project, tasks, runs, token)
    if args.summarize:
        print(render_summary(report, args.summarize, args.language, db, ROOT / 'bin' / 'ltc-tasks'))
    else:
        print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f'ltc-whatsup: {error}', file=sys.stderr)
        sys.exit(1)
