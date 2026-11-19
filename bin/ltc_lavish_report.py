"""Select and render one explicitly requested Cook report as inert HTML."""
from datetime import datetime
from html import escape
from pathlib import Path

from ltc_registry import has_reports, review_url

STYLESHEET = Path(__file__).resolve().parents[1] / 'reports' / 'cook-report.css'


def saved_report(conn, project, member, task, report_id):
    if has_reports(conn):
        row = conn.execute('''SELECT t.id AS task_id, t.title, r.id AS run_id, p.id AS report_id,
                p.outcome, p.validation, p.created_at, p.review_url
            FROM tasks t JOIN cook_runs r ON r.task_id=t.id AND r.project_id=t.project_id
                AND r.member_id IS t.member_id
            JOIN cook_reports p ON p.run_id=r.id
            WHERE t.project_id=? AND t.member_id IS ? AND t.id=? AND (? IS NULL OR p.id=?)
            ORDER BY p.id DESC LIMIT 1''', (project, member, task, report_id, report_id)).fetchone()
        if row:
            return row
    raise ValueError('No saved Cook report matches this project, member, task and report selection')


def paragraphs(field, value):
    # Keep the Cook's own text and line boundaries. Each nonempty line is a stable target.
    return '\n'.join(f'<p id="{field}-{number}">{escape(line)}</p>' if line.strip() else '<br>'
                     for number, line in enumerate(value.split('\n'), 1))


def render_report(row, project, member):
    title = escape(row['title'])
    context = escape(project) + (f' / {escape(member)}' if member else '')
    date = row['created_at']
    try:
        published = datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
        stamp = f'<time datetime="{published.isoformat()}Z">{published:%d %b %Y · %H:%M} UTC</time>'
    except ValueError:
        stamp = escape(date)
    button = ''
    url = row['review_url']
    if review_url(url):
        from urllib.parse import urlsplit
        try:
            valid_port = urlsplit(url).port != 0
        except ValueError:
            valid_port = False
        if valid_port:
            button = f'''<footer data-slot="card-footer"><a data-slot="button" data-variant="default"
href="{escape(url)}" target="_blank" rel="noopener noreferrer">Open review <span aria-hidden="true">↗</span></a></footer>'''
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{title} · Cook report #{row['report_id']}</title>
<style>{STYLESHEET.read_text(encoding='utf-8')}</style></head>
<body><main>
<header class="report-header">
<div class="identity"><span data-slot="badge" data-variant="secondary">Task #{row['task_id']}</span>
<span class="project">{context}</span></div>
<h1>{title}</h1>
<div class="byline"><span>Cook report #{row['report_id']}</span><span>Run #{row['run_id']}</span>{stamp}</div>
</header>
<section data-slot="card" aria-labelledby="outcome-title">
<header data-slot="card-header"><h2 data-slot="card-title" id="outcome-title">Outcome</h2></header>
<div data-slot="card-content" class="outcome">{paragraphs('outcome', row['outcome'])}</div>
{button}</section>
<section data-slot="card" aria-labelledby="validation-title">
<header data-slot="card-header"><h2 data-slot="card-title" id="validation-title">Validation</h2></header>
<div data-slot="card-content" class="validation">{paragraphs('validation', row['validation'])}</div>
</section></main></body></html>'''
