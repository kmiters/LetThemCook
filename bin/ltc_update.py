"""Check the GitHub main ref without fetching objects or updating Git refs."""
import argparse
from contextlib import closing, suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
INTERVAL = 24 * 60 * 60
MAIN = 'refs/heads/main'
ORIGIN = re.compile(r'https://github\.com/kmiters/LetThemCook(?:\.git)?', re.IGNORECASE | re.ASCII)
OID = r'(?:[0-9a-f]{40}|[0-9a-f]{64})'
HINT = 'LetThemCook update check (GitHub main {tip}): {detail} See bin/ltc-update in the LetThemCook checkout.'
STATE_ERRORS = (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError)
SCHEMA = """CREATE TABLE IF NOT EXISTS update_check (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    last_attempt REAL NOT NULL,
    last_success REAL,
    outcome TEXT NOT NULL,
    local_main TEXT,
    remote_main TEXT,
    relation TEXT
)"""


def git(root, *args, timeout=0.5):
    env = os.environ.copy()
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_NAMESPACE',
                 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_SHALLOW_FILE'):
        env.pop(name, None)
    env.update(GIT_ALLOW_PROTOCOL='https', GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/usr/bin/false',
               GCM_INTERACTIVE='never', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1',
               GIT_GRAFT_FILE=os.devnull)
    command = ['git', '-C', str(root), *args]
    with subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, start_new_session=True) as child:
        try:
            stdout, _ = child.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise
        return child.returncode, stdout.decode('utf-8', errors='replace').rstrip('\n')


def origin_status(root):
    if not (root / '.git').exists():
        return 'unsupported_origin'
    try:
        code, url = git(root, 'remote', 'get-url', '--all', 'origin')
    except (OSError, subprocess.TimeoutExpired):
        return 'git_unavailable'
    return 'supported' if code == 0 and ORIGIN.fullmatch(url) else 'unsupported_origin'


def compare(root, remote):
    local = None
    try:
        code, local = git(root, 'show-ref', '--hash', '--verify', MAIN)
        if code != 0 or not re.fullmatch(OID, local):
            return None, 'unknown'
        if local == remote:
            return local, 'up_to_date'
        # Older Git versions that lack --no-lazy-fetch fail closed here.
        code, _ = git(root, '--no-lazy-fetch', 'merge-base', '--is-ancestor', remote, local)
        if code == 0:
            return local, 'ahead'
        if code == 1:
            code, _ = git(root, '--no-lazy-fetch', 'merge-base', '--is-ancestor', local, remote)
            if code == 0:
                return local, 'behind'
            if code == 1 and git(root, 'rev-parse', '--is-shallow-repository') == (0, 'false'):
                return local, 'diverged'
    except (OSError, subprocess.TimeoutExpired):
        pass
    return local, 'unknown'


def state_path(root):
    return root / '.letthemcook' / 'update-check.sqlite3'


def connect(path, mode):
    conn = sqlite3.connect(path.as_uri() + '?mode=' + mode, uri=True, timeout=0.1)
    conn.row_factory = sqlite3.Row
    return conn


def read_state(path):
    if not path.exists():
        return {}
    with closing(connect(path, 'ro')) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='update_check'").fetchone():
            return {}
        row = conn.execute('SELECT * FROM update_check WHERE id=1').fetchone()
        return dict(row) if row else {}


def check(root=ROOT):
    """Return a one-time hint for a new upstream tip, if this call observed one."""
    path = state_path(root)
    previous = read_state(path)
    if previous and time.time() < previous['last_attempt'] + INTERVAL:
        return None
    if origin_status(root) != 'supported':
        return None

    path.parent.mkdir(exist_ok=True)
    with closing(connect(path, 'rwc')) as conn:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute(SCHEMA)
            attempt = time.time()
            cursor = conn.execute("""INSERT INTO update_check(id, last_attempt, outcome)
                VALUES (1, ?, 'checking')
                ON CONFLICT(id) DO UPDATE SET last_attempt=excluded.last_attempt, outcome='checking'
                WHERE update_check.last_attempt <= excluded.last_attempt - ?""", (attempt, INTERVAL))
            if not cursor.rowcount:
                return None
            previous_tip = conn.execute('SELECT remote_main FROM update_check WHERE id=1').fetchone()[0]

        # ls-remote reads advertised refs without fetching objects.
        try:
            code, reply = git(root, '-c', 'http.followRedirects=false', '-c', 'credential.interactive=false',
                              'ls-remote',
                              '--exit-code', '--refs', 'origin', MAIN, timeout=2)
        except subprocess.TimeoutExpired:
            outcome = 'timeout'
        except OSError:
            outcome = 'request_failed'
        else:
            match = re.fullmatch(f'({OID})\t{MAIN}', reply)
            if code == 0 and match:
                tip = match[1]
                success = time.time()
                local, relation = compare(root, tip)
                with conn:
                    cursor = conn.execute("""UPDATE update_check
                        SET outcome='ok', last_success=?, local_main=?, remote_main=?, relation=?
                        WHERE id=1 AND last_attempt=?""", (success, local, tip, relation, attempt))
                if cursor.rowcount and tip != previous_tip and relation in ('behind', 'diverged', 'unknown'):
                    detail = {
                        'behind': 'Local main is behind GitHub main.',
                        'diverged': 'Local and GitHub main have diverged.',
                        'unknown': 'GitHub main was observed, but local history cannot establish its relation to local main.',
                    }[relation]
                    return HINT.format(tip=tip[:12], detail=detail)
                return None
            outcome = {0: 'invalid_reply', 2: 'remote_main_missing'}.get(code, 'request_failed')
        with conn:
            conn.execute('UPDATE update_check SET outcome=? WHERE id=1 AND last_attempt=?', (outcome, attempt))
    return None


def hook_hint():
    try:
        return check()
    except STATE_ERRORS:
        return None


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def status(root=ROOT):
    state = read_state(state_path(root))
    attempt = state.get('last_attempt')
    due = attempt + INTERVAL if attempt is not None else None
    outcome = state.get('outcome', 'not_checked')
    if outcome == 'checking' and time.time() >= attempt + 10:
        outcome = 'interrupted'
    return {
        'origin': origin_status(root),
        'state_file': str(state_path(root)),
        'last_attempt_utc': utc(attempt),
        'last_success_utc': utc(state.get('last_success')),
        'next_eligible_utc': utc(due),
        'last_attempt_outcome': outcome,
        'local_main_at_last_success': state.get('local_main'),
        'remote_main_at_last_success': state.get('remote_main'),
        'relation_at_last_success': state.get('relation'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check now if the shared 24-hour interval has elapsed')
    args = parser.parse_args()
    try:
        if args.check:
            check()
        print(json.dumps(status(), indent=2))
    except STATE_ERRORS as error:
        sys.exit(f'ltc-update: {error}')
