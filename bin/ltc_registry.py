"""Shared SQLite schema for Cook launch records."""
import sqlite3
import re
import unicodedata
import uuid


def terminal_name(title):
    """Make a readable Herdr name with room for a per-launch unique suffix."""
    title = title.translate(str.maketrans({'ł': 'l', 'Ł': 'L'}))
    title = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode().lower()
    slug = re.sub(r'[^a-z0-9]+', '-', title).strip('-')[:23].rstrip('-') or 'task'
    return f'{slug}-{uuid.uuid4().hex[:8]}'


def ensure_runs(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS registry_identity (id INTEGER PRIMARY KEY CHECK(id = 1), token TEXT NOT NULL)')
    conn.execute('INSERT OR IGNORE INTO registry_identity (id, token) VALUES (1, ?)', (uuid.uuid4().hex,))
    conn.execute('''CREATE TABLE IF NOT EXISTS cook_runs (
        id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
        project_id INTEGER NOT NULL REFERENCES projects(id), space TEXT NOT NULL,
        worktree TEXT NOT NULL, base TEXT NOT NULL, terminal TEXT,
        hc_session TEXT NOT NULL, name TEXT NOT NULL, profile TEXT NOT NULL,
        launch_state TEXT NOT NULL CHECK(launch_state IN
          ('starting', 'started', 'delivery_failed', 'failed', 'unknown')),
        detail TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    # NULL harness identifies runs written by the old Codex-only launcher.
    columns = {row[1] for row in conn.execute('PRAGMA table_info(cook_runs)')}
    for column in ('harness', 'model', 'reasoning_effort', 'claude_session_id'):
        if column not in columns:
            conn.execute(f'ALTER TABLE cook_runs ADD COLUMN {column} TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS cook_runs_task_id ON cook_runs(task_id, id)')


def start_run(conn, task_id, project_id, space, worktree, base, hc_session, name, route, claude_session_id=None):
    cursor = conn.execute("""INSERT INTO cook_runs
        (task_id, project_id, space, worktree, base, hc_session, name, profile, harness, model, reasoning_effort, claude_session_id, launch_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'starting')""",
        (task_id, project_id, space, str(worktree), base, hc_session, name,
         route.profile, route.harness, route.model, route.reasoning_effort, claude_session_id))
    return cursor.lastrowid


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


BRIEF_FIELDS = ('intent', 'context', 'scope', 'acceptance', 'checks')
INSTRUCTION_MAINTENANCE_MARKER = 'ltc-kind: instruction-maintenance'


def ensure_briefs(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS task_briefs (
        task_id INTEGER PRIMARY KEY REFERENCES tasks(id),
        intent TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '', acceptance TEXT NOT NULL DEFAULT '',
        checks TEXT NOT NULL DEFAULT ''
    )''')


def update_brief(conn, task_id, fields):
    if not fields:
        return
    if not fields.keys() <= set(BRIEF_FIELDS):
        raise ValueError('Unknown task brief field')
    conn.execute('INSERT OR IGNORE INTO task_briefs (task_id) VALUES (?)', (task_id,))
    conn.execute('UPDATE task_briefs SET ' + ', '.join(key + ' = ?' for key in fields)
                 + ' WHERE task_id = ?', (*fields.values(), task_id))


def add_task(conn, project_id, member_id, title, description, fields):
    task_id = conn.execute('INSERT INTO tasks (project_id, member_id, title, description) VALUES (?, ?, ?, ?)',
                           (project_id, member_id, title, description)).lastrowid
    update_brief(conn, task_id, fields)
    return task_id


def ensure_task_status_events(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS task_status_events (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        task_id INTEGER NOT NULL REFERENCES tasks(id), from_status TEXT NOT NULL,
        to_status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS task_status_events_project_id ON task_status_events(project_id, id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS chef_hook_sessions (
        project_id INTEGER NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL,
        last_event_id INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(project_id, session_id)
    )''')


def get_brief(conn, task_id):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_briefs'").fetchone()
    if not exists:
        return None
    row = conn.execute('SELECT intent, context, scope, acceptance, checks FROM task_briefs WHERE task_id = ?', (task_id,)).fetchone()
    return dict(zip(BRIEF_FIELDS, row)) if row else None


def cook_reporting_context(db, root):
    return (f'\nRegistry: {db}\nLetThemCook directory: {root}'
            '\nSave results, questions and blockers with ltc-report. The Chef reads the project inbox.')


def format_task(task):
    sections = [f"Task #{task['id']}: {task['title']}"]
    seen = {}
    def compact(value):
        if value in seen:
            reference = f'Same text as {seen[value]} above.'
            if len(reference) < len(value):
                return reference
        return value
    for key, heading in (('intent', 'User request'), ('context', 'Context'), ('scope', 'Scope'),
                         ('acceptance', 'Acceptance criteria'), ('checks', 'Verification')):
        value = (task.get('brief') or {}).get(key, '')
        if value and value.strip():
            body = compact(value)
            sections.append(f'## {heading}\n{body}')
            seen.setdefault(value, heading)
    if task.get('description', '').strip():
        value = task['description']
        body = compact(value)
        sections.append('## Additional notes\n' + body)
    return '\n\n'.join(sections)


def review_url(value):
    """Accept only absolute HTTP(S) review links in both stored and rendered data."""
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(value)
        return (0 < len(value) <= 2048 and not any(c.isspace() or ord(c) < 32 for c in value)
                and '\\' not in value and parsed.scheme in ('http', 'https')
                and bool(parsed.hostname) and parsed.username is None and parsed.password is None)
    except (TypeError, ValueError):
        return False


def has_reports(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cook_reports'").fetchone() is not None


def task_reports(conn, project_id, task_id):
    if not has_reports(conn):
        return []
    return [dict(row) for row in conn.execute('''SELECT p.id, p.run_id, p.outcome, p.validation, p.review_url, p.created_at
        FROM cook_reports p JOIN cook_runs r ON r.id=p.run_id
        WHERE r.task_id=? AND r.project_id=? ORDER BY p.id DESC LIMIT 20''', (task_id, project_id))]
