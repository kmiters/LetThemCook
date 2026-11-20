"""Track one Herdr receiver generation for an explicitly selected Lavish review."""
import json
from pathlib import Path
import subprocess
import sys
import time

from ltc_watch import call_hc


def setup_listener(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS lavish_listeners (
        review TEXT PRIMARY KEY REFERENCES lavish_reviews(id), generation TEXT NOT NULL,
        session TEXT NOT NULL, terminal TEXT, state TEXT NOT NULL, detail TEXT)''')


def current(conn, row):
    # Read-only status must also work before a legacy review has been resumed.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='lavish_listeners'").fetchone():
        return None
    return conn.execute('SELECT * FROM lavish_listeners WHERE review=?', (row['id'],)).fetchone()


def labels(conn, row, job):
    token, space = conn.execute('''SELECT token, space FROM registry_identity JOIN projects
        ON projects.id=? WHERE registry_identity.id=1''', (row['project_id'],)).fetchone()
    return {'ltc.lavish.review': row['id'], 'ltc.lavish.generation': job['generation'],
            'ltc.lavish.registry': token, 'ltc.lavish.project': str(row['project_id']),
            'ltc.lavish.member': str(row['member_id']), 'herdr.space': space}


def terminal_name(job):
    return 'ltc-lavish-' + job['generation'][:20]


def terminal(conn, row, job):
    target = job['terminal'] or terminal_name(job)
    try:
        info = json.loads(call_hc(job['session'], 'get', target).stdout)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 3:
            return None
        raise
    if (not isinstance(info, dict) or not isinstance(info.get('labels'), dict)
            or not info.get('address') or (job['terminal'] and info['address'] != job['terminal'])
            or info.get('name') != terminal_name(job) or info.get('state') not in ('live', 'finished')
            or any((info.get('labels') or {}).get(k) != v for k, v in labels(conn, row, job).items())):
        raise ValueError('Herdr receiver identity mismatch. Inspect the recorded terminal.')
    return info


def launch(conn, args, row, session, generation):
    with conn:
        conn.execute('''INSERT OR REPLACE INTO lavish_listeners
            (review,generation,session,state) VALUES (?,?,?,'starting')''', (row['id'], generation, session))
    job = current(conn, row)
    command = ['run', '--bg', '--no-notify', '--no-parent', '--outlive-parent', '--name', terminal_name(job)]
    for k, v in labels(conn, row, job).items():
        command.extend(['--label', k + '=' + v])
    command.extend(['--', sys.executable, str(Path(__file__).with_name('ltc-lavish').resolve()),
                    '--db', str(Path(args.db).expanduser().resolve()), '_receive', args.project,
                    row['id'], '--generation', generation])
    if args.member:
        command.extend(['--member', args.member])
    try:
        started = json.loads(call_hc(session, *command, timeout=30).stdout)
        info = terminal(conn, row, job)
        if not info or info['address'] != started['address']:
            raise ValueError('Herdr did not confirm the launched receiver')
        with conn:
            conn.execute('UPDATE lavish_listeners SET terminal=? WHERE review=? AND generation=?',
                         (info['address'], row['id'], generation))
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        record(conn, job, 'launch_error', str(exc))
        raise


def record(conn, job, state, detail=None):
    with conn:
        conn.execute('UPDATE lavish_listeners SET state=?, detail=? WHERE review=? AND generation=?',
                     (state, detail, job['review'], job['generation']))


def wait_for(probe, description, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise ValueError(f'Timed out waiting for {description}. Inspect status and retry.')
        time.sleep(.15)
