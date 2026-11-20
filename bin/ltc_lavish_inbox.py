"""Address submitted Lavish batches to the verified Chef or originating Cook run."""
from dataclasses import dataclass
import json
import os
import time
from typing import Optional

from ltc_endpoint import verify_callback
from ltc_status import inspect_terminal


@dataclass(frozen=True)
class Agent:
    session: str
    terminal: str
    space: str
    run: Optional[int] = None


def caller(conn, project, member):
    address = os.environ.get('HC_TERMINAL_ID')
    session = os.environ.get('HC_SESSION') or 'default'
    if not address or os.environ.get('HC_SOCKET'):
        raise ValueError('Use the registered Chef or Cook Herdr terminal')
    run = conn.execute('''SELECT r.* FROM cook_runs r JOIN tasks t ON t.id=r.task_id
        AND t.project_id=r.project_id AND t.member_id IS r.member_id
        JOIN projects p ON p.id=r.project_id AND p.space=r.space
        WHERE r.terminal=? AND r.hc_session=? ORDER BY r.id DESC LIMIT 1''', (address, session)).fetchone()
    if run:
        token = conn.execute('SELECT token FROM registry_identity WHERE id=1').fetchone()[0]
        if (run['project_id'] != project or run['member_id'] != member or
                inspect_terminal(dict(run), token)[0]['state'] != 'live'):
            raise ValueError('Cook does not own this project and member')
        return Agent(session, address, run['space'], run['id'])
    _, _, space, _ = verify_callback(conn, project, address)
    return Agent(session, address, space)


def setup(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS lavish_owners (
        review TEXT PRIMARY KEY REFERENCES lavish_reviews(id), session TEXT NOT NULL,
        terminal TEXT NOT NULL, space TEXT NOT NULL, run_id INTEGER REFERENCES cook_runs(id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS lavish_batches (
        id INTEGER PRIMARY KEY REFERENCES tasks(id), review TEXT NOT NULL REFERENCES lavish_owners(review),
        payload TEXT NOT NULL, stored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        offered_at TEXT, read_at TEXT, ack_at TEXT, ack_terminal TEXT, ack_run INTEGER REFERENCES cook_runs(id))''')
    conn.execute('CREATE INDEX IF NOT EXISTS lavish_batches_review ON lavish_batches(review,id)')


def bind(conn, review, agent):
    conn.execute('INSERT INTO lavish_owners (review,session,terminal,space,run_id) VALUES (?,?,?,?,?)',
                 (review, agent.session, agent.terminal, agent.space, agent.run))


def enqueue(conn, review, task, payload):
    # The task ID already commits atomically with the spool receipt. Keep the original
    # evidence for replay even if a Chef subsequently edits the backlog task.
    conn.execute('''INSERT INTO lavish_batches (id,review,payload)
        SELECT ?,review,? FROM lavish_owners WHERE review=?''', (task, payload, review))


def has_inbox(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lavish_batches'").fetchone())


def route(agent):
    # Only an explicit ltc-resume successor inherits a Cook's queue. Task membership
    # or reuse of a terminal address/worktree alone does not establish continuity.
    return dict(run=agent.run, session=agent.session, space=agent.space)


LINEAGE = '''WITH RECURSIVE lineage(id) AS (
    SELECT :run UNION SELECT r.resumed_from FROM cook_runs r JOIN lineage ON r.id=lineage.id
    WHERE r.resumed_from IS NOT NULL) '''
MATCH = '''o.session=:session AND o.space=:space AND
    ((:run IS NULL AND o.run_id IS NULL) OR o.run_id IN (SELECT id FROM lineage))'''


def check_owner(conn, review, agent):
    found = conn.execute(LINEAGE + 'SELECT 1 FROM lavish_owners o WHERE review=:review AND ' + MATCH,
                         dict(route(agent), review=review)).fetchone()
    if not found:
        raise ValueError('This receiver belongs to a different agent')


def pending(conn, project, member, agent, *, all_members=False, offer=False):
    rows = conn.execute(LINEAGE + '''SELECT b.id AS batch, b.review, m.name AS member FROM lavish_batches b
        JOIN lavish_owners o ON o.review=b.review JOIN lavish_reviews r ON r.id=b.review
        LEFT JOIN project_members m ON m.id=r.member_id AND m.project_id=r.project_id
        WHERE r.project_id=:project AND (:all_members OR r.member_id IS :member)
        AND b.ack_at IS NULL AND ''' + MATCH + ' ORDER BY b.id LIMIT 100',
        dict(route(agent), project=project, member=member, all_members=all_members)).fetchall()
    if offer:
        conn.executemany('UPDATE lavish_batches SET offered_at=CURRENT_TIMESTAMP WHERE id=? AND offered_at IS NULL',
                         [(row['batch'],) for row in rows])
    return [dict(row) for row in rows]


def operate(conn, args, project, member):
    if args.command == 'inbox':
        if args.wait < 0:
            raise ValueError('--wait must be nonnegative')
        if args.wait:
            agent = caller(conn, project, member)
            deadline = time.monotonic() + args.wait
            # No transaction or Herdr call while sleeping. Reverify before returning IDs.
            while time.monotonic() < deadline and not pending(conn, project, member, agent):
                time.sleep(min(.5, max(0, deadline - time.monotonic())))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        agent = caller(conn, project, member)
        if args.command == 'inbox':
            return dict(batches=pending(conn, project, member, agent, offer=True))
        row = conn.execute(LINEAGE + '''SELECT b.*, r.phase FROM lavish_batches b JOIN lavish_owners o ON o.review=b.review
            JOIN lavish_reviews r ON r.id=b.review WHERE b.id=:batch AND r.project_id=:project
            AND r.member_id IS :member AND ''' + MATCH,
            dict(route(agent), batch=args.batch, project=project, member=member)).fetchone()
        if not row:
            raise ValueError('Batch not found for this agent and backlog')
        if args.command == 'read':
            conn.execute('UPDATE lavish_batches SET read_at=CURRENT_TIMESTAMP WHERE id=? AND read_at IS NULL',
                         (row['id'],))
            return dict(batch=row['id'], review=row['review'], review_phase=row['phase'], feedback=json.loads(row['payload']),
                        acknowledged=bool(row['ack_at']))
        if not row['read_at']:
            raise ValueError('Read this batch before acknowledging it')
        conn.execute('''UPDATE lavish_batches SET ack_at=CURRENT_TIMESTAMP,ack_terminal=?,ack_run=?
            WHERE id=? AND ack_at IS NULL''', (agent.terminal, agent.run, row['id']))
        return dict(batch=row['id'], acknowledged=True)


def summary(conn, review):
    if not has_inbox(conn) or not conn.execute('SELECT 1 FROM lavish_owners WHERE review=?', (review,)).fetchone():
        return dict(channel='backlog_only')
    counts = conn.execute('''SELECT count(*) AS stored, count(offered_at) AS id_offered,
        count(read_at) AS read_requested, count(ack_at) AS acknowledged,
        count(*)-count(ack_at) AS awaiting_ack FROM lavish_batches WHERE review=?''', (review,)).fetchone()
    return dict(channel='owner_inbox', **dict(counts))
