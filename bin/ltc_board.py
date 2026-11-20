"""Local read-only HTTP view over the Chef registry."""
from collections import Counter
from contextlib import closing
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import groupby
import os
from pathlib import Path
import re
import sqlite3
import subprocess

from ltc_registry import get_brief, has_reports, review_url, task_reports

ASSETS = Path(__file__).resolve().parent.parent / 'board'
LANES = {'todo': 'To do', 'in_progress': 'In progress', 'blocked': 'Blocked',
         'done': 'Done'}
STATUS_LABELS = {**LANES, 'cancelled': 'Cancelled'}
BRIEF = {'intent': 'User request', 'context': 'Context', 'scope': 'Scope',
         'acceptance': 'Acceptance criteria', 'checks': 'Verification'}
FENCE = re.compile(r' {0,3}(`{3,}|~{3,})(?:[ \t]*([\w.+-]+))?[ \t]*')
BACKTICKS = re.compile(r'`+')
LIST_ITEM = re.compile(r'( {0,3}([-+*•]|[0-9]{1,9}[.)])[ \t]+)(\S.*)')
# Recognize command-shaped lines from old briefs, without treating paths or
# sentences mentioning a tool as commands.
LEGACY_COMMAND = re.compile(
    r'(?:\$[ \t]+)?(?:'
    r'(?:\./)?bin/ltc(?:-[\w-]+)?(?:[ \t]+.*)?'
    r'|git[ \t]+(?:status|diff|log|branch)(?:[ \t]+[-./].*)?'
    r'|git[ \t]+(?:commit|fetch|pull|push|show)(?:[ \t]+\S.*)?'
    r'|git[ \t]+(?:add|checkout|restore|switch|worktree)[ \t]+\S.*'
    r'|python3?[ \t]+(?:-m[ \t]+[\w.]+|\S+\.py)(?:[ \t]+.*)?'
    r'|(?:npm|pnpm|yarn|bun)[ \t]+(?:run[ \t]+\S.*|(?:test|install|start)(?:[ \t]+-.*)?)'
    r'|(?:cd|ls|cat|curl)[ \t]+(?:[./~-]|https?://)\S.*'
    r')'
)
LEGACY_INLINE_COMMAND = re.compile(
    r'(?<![\w./~-])(?:git diff --binary HEAD|npm run check)(?![\w/-]|\.\w)'
)
SHELL_LANGUAGES = {'sh', 'bash', 'zsh', 'fish', 'shell', 'shellscript'}
SUBCOMMAND_TOOLS = {'git', 'npm', 'pnpm', 'yarn', 'bun'}
INLINE_SHELL = re.compile(r'(?:echo|printf|cd|ls|mkdir|grep|curl|pwd|--?[a-zA-Z][\w-]*)(?:[ \t].*)?')
SHELL_TOKEN = re.compile(
    r'(?P<comment>(?<![^ \t\n;|&()])#[^\n]*)|'
    r"""(?P<string>'[^']*'|"(?:\\[\s\S]|[^"\\])*")|"""
    r'(?P<operator>[|&;<>()]+)|(?P<newline>\n)|'
    r"""(?P<word>(?:\\[^\n]|[^ \t\r\n'"|&;<>()\\])+)"""
)


def connect(db):
    if not db.is_file():
        raise ValueError(f'Registry unavailable at {db}. Set LTC_DB or pass --db to select an existing registry.')
    conn = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    return conn


def database_key(db):
    return sha256(os.fsencode(db)).hexdigest()


def list_projects(conn):
    return conn.execute('SELECT * FROM projects ORDER BY name COLLATE NOCASE, id').fetchall()


def git_path(directory, option):
    result = subprocess.run(['git', '-C', str(directory), 'rev-parse', option],
                            text=True, capture_output=True)
    if result.returncode == 0:
        return (directory / result.stdout.rstrip('\n')).resolve()
    return None


def checkout_matches(projects, cwd):
    top = git_path(cwd, '--show-toplevel')
    if not top:
        return []
    exact = [p['id'] for p in projects if Path(p['path']).resolve() == top]
    if exact:
        return exact
    common = git_path(top, '--git-common-dir')
    return [p['id'] for p in projects if common and git_path(Path(p['path']), '--git-common-dir') == common]


def select_project(conn, name, cwd):
    projects = list_projects(conn)
    if name is not None:
        for project in projects:
            if project['name'] == name:
                return project
        raise ValueError(f'Unknown project {name!r}. Available: ' + ', '.join(p['name'] for p in projects))
    # Workspace-aware installations share the resolver with the other Chef tools.
    try:
        from ltc_workspace import matching_project_ids
    except ModuleNotFoundError as error:
        if error.name != 'ltc_workspace':
            raise
        matches = checkout_matches(projects, cwd)
    else:
        matches = matching_project_ids(conn, cwd)
    if len(matches) == 1:
        return next((p for p in projects if p['id'] == matches[0]), None)
    return None


def project_navigation(projects, active_href, task_id):
    links = [('/', 'All projects')]
    links.extend((f"/projects/{p['id']}", p['name']) for p in projects)
    current = 'location' if task_id is not None else 'page'
    items = []
    for href, label in links:
        selected = f' aria-current="{current}"' if href == active_href else ''
        items.append(f'<a href="{href}"{selected}>{escape(label)}</a>')
    if task_id is not None:
        items.append(f'<span class="view-task">#{task_id}</span>')
    return ''.join(items)


def page(title, body, *, projects=(), active_href=None, task_id=None):
    trail = project_navigation(projects, active_href, task_id)
    # Embed a revision of the full view, including task details and project navigation.
    revision = sha256((trail + body).encode()).hexdigest()
    return f'''<!doctype html>
<html lang="en" data-revision="{revision}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · LetThemCook</title><link rel="icon" href="data:,">
<link rel="stylesheet" href="/board.css"><script defer src="/board.js"></script></head>
<body><header><nav class="view-nav" aria-label="Project navigation">{trail}</nav></header>
<main>{body}</main><footer><span>LetThemCook <b>/</b> Chef registry</span>
<span id="sync" role="status">Read-only view</span></footer></body></html>''', revision


def index(conn):
    rows = conn.execute("""SELECT p.id, p.name, p.description, COUNT(t.id) AS total
                           FROM projects p LEFT JOIN tasks t ON p.id = t.project_id
                           GROUP BY p.id ORDER BY p.name COLLATE NOCASE, p.id""").fetchall()
    links = ''.join(f'''<a class="project-link" href="/projects/{p['id']}">
<span><strong>{escape(p['name'])}</strong><small>{escape(p['description'])}</small></span>
<span class="project-meta">{p['total']} {'task' if p['total'] == 1 else 'tasks'} <span class="arrow" aria-hidden="true">›</span></span></a>''' for p in rows)
    return page('Projects', '<section class="project-picker"><h1>Projects</h1>' +
                (f'<div class="project-list">{links}</div>' if links else
                 '<p class="notice">No projects yet. Register a checkout with ltc-project to see its board here.</p>') +
                '</section>', projects=rows, active_href='/')


def card(task, base):
    preview = (task['preview'] or task['description']).strip()
    excerpt = escape(preview[:160] + ('…' if len(preview) > 160 else ''))
    outcome = task['outcome'] or ''
    summary = f'<p><b>Cook report</b> {escape(outcome[:120])}{"…" if len(outcome) > 120 else ""}</p>' if outcome else ''
    return f'''<a class="ticket" href="{base}/tasks/{task['id']}">
<span class="ticket-top"><span>#{task['id']}</span><span class="arrow" aria-hidden="true">›</span></span>
<strong>{escape(task['title'])}</strong>{f'<p>{excerpt}</p>' if excerpt else ''}
{summary}<span class="stamp">Updated {escape(task['updated_at'][:10])}</span></a>'''


def board(conn, p, base):
    has_briefs = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_briefs'").fetchone()
    preview = 'b.intent' if has_briefs else "''"
    join = 'LEFT JOIN task_briefs b ON b.task_id = t.id' if has_briefs else ''
    outcome = """(SELECT cr.outcome FROM cook_reports cr JOIN cook_runs r ON r.id=cr.run_id
        WHERE r.task_id=t.id AND r.project_id=t.project_id ORDER BY cr.id DESC LIMIT 1)""" if has_reports(conn) else "NULL"
    tasks = conn.execute(f"""SELECT t.*, {outcome} AS outcome, {preview} AS preview FROM tasks t {join}
                             WHERE t.project_id = ? ORDER BY t.updated_at DESC, t.id DESC""", (p['id'],)).fetchall()
    counts = Counter(t['status'] for t in tasks)
    lanes = []
    for status, label in LANES.items():
        cards = ''.join(card(t, base) for t in tasks if t['status'] == status)
        lanes.append(f'''<section class="lane {status}"><h2><i class="dot"></i>{label}
<span class="count">{counts[status]}</span></h2>{cards or '<p class="empty">No tasks here</p>'}</section>''')
    name = escape(p['name'])
    space = p['space'] if 'space' in p.keys() else None
    context = f' <b>/</b> Space {escape(space)}' if space else ''
    description = escape(p['description'])
    desc = f'<p class="description" title="{description}">{description}</p>' if description else ''
    empty = '' if tasks else '<p class="notice">No tasks recorded yet. Tasks saved by this project’s Chef will appear here.</p>'
    return page(p['name'], f'''<div class="project-header">
<div class="project-heading"><h1>{name}</h1>
<span class="task-total" title="Registered tasks">{len(tasks)}<span class="visually-hidden"> registered tasks</span></span></div>
{desc}<div class="meta">Chef @ {name}{context}</div></div>
{empty}<div class="lanes">{''.join(lanes)}</div>''', projects=list_projects(conn), active_href=base)


def shell_markup(source):
    parts = []
    end = 0
    command = True
    subcommand = False
    for match in SHELL_TOKEN.finditer(source):
        token = match.group()
        kind = match.lastgroup
        parts.append(escape(source[end:match.start()]))
        if kind == 'word':
            if token == '$' and command:
                kind = 'operator'
            elif token.startswith('-') and len(token) > 1:
                kind = 'option'
                command = False
            elif command or subcommand:
                kind = 'command'
                subcommand = command and token in SUBCOMMAND_TOOLS
                command = False
        elif kind == 'newline' or (kind == 'operator' and token[0] in ';|&()'):
            command = True
            subcommand = False

        literal = escape(token)
        if kind not in {'word', 'newline'}:
            literal = f'<span class="syntax-{kind}">{literal}</span>'
        parts.append(literal)
        end = match.end()
    parts.append(escape(source[end:]))
    return ''.join(parts)


def recognized_shell(source):
    line = source.strip()
    return LEGACY_COMMAND.fullmatch(line) or INLINE_SHELL.fullmatch(line)


def inline_legacy_commands(text):
    parts = []
    end = 0
    for match in LEGACY_INLINE_COMMAND.finditer(text):
        parts.append(escape(text[end:match.start()]))
        parts.append(f'<code>{shell_markup(match.group())}</code>')
        end = match.end()
    parts.append(escape(text[end:]))
    return ''.join(parts)


def inline_code(line):
    runs = list(BACKTICKS.finditer(line))
    next_run = {}
    closing = {}
    for run in reversed(runs):
        ticks = run.group()
        if ticks in next_run:
            closing[run.start()] = next_run[ticks]
        next_run[ticks] = run

    parts = []
    end = 0
    for run in runs:
        close = closing.get(run.start())
        if run.start() < end or close is None:
            continue
        parts.append(inline_legacy_commands(line[end:run.start()]))
        code = line[run.end():close.start()]
        parts.append(f'<code>{shell_markup(code) if recognized_shell(code) else escape(code)}</code>')
        end = close.end()
    parts.append(inline_legacy_commands(line[end:]))
    return ''.join(parts)


def brief_blocks(value):
    lines = value.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if fence := FENCE.fullmatch(line):
            marker = fence[1]
            closer = re.compile(rf' {{0,3}}{marker[0]}{{{len(marker)},}}[ \t]*')
            start = index
            while index < len(lines) and not closer.fullmatch(lines[index]):
                index += 1
            if index == len(lines):
                # Preserve unfinished fences, including their opening marker.
                yield 'p', escape('\n'.join(lines[start - 1:]))
                break
            code = lines[start:index]
            language = (fence[2] or '').lower()
            if language in SHELL_LANGUAGES:
                markup = shell_markup('\n'.join(code))
            elif not language:
                markup = '\n'.join(shell_markup(line) if recognized_shell(line) else escape(line)
                                   for line in code)
            else:
                markup = escape('\n'.join(code))
            yield 'pre', markup
            index += 1
        elif not line.strip():
            yield '', ''
        elif item := LIST_ITEM.fullmatch(line):
            tag = 'ol' if item[2][0].isdigit() else 'ul'
            yield tag, (f'<li><span>{escape(item[1])}</span>'
                        f'<span>{inline_code(item[3])}</span></li>')
        elif LEGACY_COMMAND.fullmatch(line.strip()):
            yield 'pre', shell_markup(line)
        else:
            yield 'p', inline_code(line)


def format_brief(value):
    blocks = []
    for tag, group in groupby(brief_blocks(value), key=lambda block: block[0]):
        if tag == 'pre':
            blocks.extend(f'<pre><code>{code}</code></pre>' for _, code in group)
        elif tag == 'p':
            blocks.extend(f'<p>{line}</p>' for _, line in group)
        elif tag:
            items = ''.join(item for _, item in group)
            blocks.append(f'<{tag} role="list">{items}</{tag}>')
        else:
            blocks.extend('' for _ in group)
    return '\n'.join(blocks)


def detail(conn, task, project, base):
    brief = get_brief(conn, task['id']) or {}
    fields = [(label, brief.get(key)) for key, label in BRIEF.items()]
    fields.append(('Additional notes', task['description']))
    sections = ''.join(f'<section><h2>{label}</h2>{format_brief(value)}</section>'
                       for label, value in fields if value and value.strip())
    for report in task_reports(conn, project['id'], task['id']):
        url = report['review_url']
        link = f'<p><a href="{escape(url)}">Open review</a></p>' if url and review_url(url) else ''
        sections += (f'<section><h2>Cook report · Run {report["run_id"]}</h2>'
                     f'<small>{escape(report["created_at"])} UTC</small>'
                     f'{format_brief(report["outcome"])}<h3>Validation</h3>'
                     f'{format_brief(report["validation"])}{link}</section>')
    status = task['status']
    name = escape(project['name'])
    return page(task['title'], f'''<a class="back" href="{base}">← {name} board</a><article class="brief">
<div class="brief-meta"><span class="eyebrow">#{task['id']}</span>
<span class="status {escape(status)}"><i class="dot"></i>{escape(STATUS_LABELS.get(status, status))}</span></div>
<h1>{escape(task['title'])}</h1><div class="meta">Chef @ {name} <b>/</b> Updated {escape(task['updated_at'])} UTC</div>
{sections or '<p class="notice">No brief or notes recorded for this task.</p>'}</article>''',
                projects=list_projects(conn), active_href=base, task_id=task['id'])


def lookup(conn, path):
    if path == '/':
        return 200, index(conn)
    match = re.fullmatch(r'/projects/([1-9][0-9]{0,18})(?:/tasks/([1-9][0-9]{0,18}))?/?', path)
    if match:
        project_id, task_id = match.groups()
        p = conn.execute('SELECT * FROM projects WHERE id = ?', (project_id,)).fetchone()
        base = f'/projects/{project_id}'
        if p and not task_id:
            return 200, board(conn, p, base)
        if p:
            task = conn.execute('SELECT * FROM tasks WHERE project_id = ? AND id = ?', (project_id, task_id)).fetchone()
            if task:
                return 200, detail(conn, task, p, base)
    return 404, page('Page unavailable', '<h1>Page unavailable</h1><p>Check the project and task address or <a href="/">choose a project</a>.</p>',
                     projects=list_projects(conn))


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.headers.get_all('Host') != [f'127.0.0.1:{self.server.server_port}']:
            self.send_error(403)
            return
        status, mime, headers = 200, 'text/plain', {}
        path = self.path.partition('?')[0]
        if path == '/health':
            body = str(os.getpid()).encode()
            headers['X-LTC-Board'] = database_key(self.server.db)
        elif path in ('/board.css', '/board.js'):
            body = (ASSETS / path[1:]).read_bytes()
            mime = 'text/css' if path.endswith('css') else 'application/javascript'
        else:
            try:
                with closing(connect(self.server.db)) as conn:
                    status, (doc, revision) = lookup(conn, path)
            except (sqlite3.Error, OSError, ValueError):
                status = 503
                doc, revision = page('Registry unavailable', '<h1>Registry unavailable</h1><p>Check the registry path and file permissions, then refresh.</p>')
            body, mime = doc.encode(), 'text/html'
            headers['ETag'] = f'"{revision}"'
            if status == 200 and self.headers.get('If-None-Match') == headers['ETag']:
                status, body = 304, b''
        self.send_response(status)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        if status != 304:
            self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src data:; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    def log_message(self, *args):
        pass


class BoardServer(ThreadingHTTPServer):
    def __init__(self, db, port):
        self.db = db
        super().__init__(('127.0.0.1', port), Handler)
