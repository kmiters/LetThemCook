"""Registry membership and Git checkout identity for multi-repository workspaces."""
from pathlib import Path
import subprocess


def ensure_members(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS project_members (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
        name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, common_dir TEXT NOT NULL UNIQUE,
        UNIQUE(project_id, name)
    )''')
    for table in ('tasks', 'cook_runs'):
        if 'member_id' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN member_id INTEGER REFERENCES project_members(id)')


def has_members_table(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_members'").fetchone())


def git_paths(path):
    """Return canonical top-level and common Git directory, or None outside a checkout."""
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--path-format=absolute',
                             '--show-toplevel', '--git-common-dir'], capture_output=True, text=True)
    if result.returncode:
        return None
    top, common = result.stdout.splitlines()
    return Path(top).resolve(), Path(common).resolve()


def matching_project_ids(conn, cwd):
    cwd = Path(cwd).resolve()
    projects = list(conn.execute('SELECT id, path FROM projects ORDER BY id'))
    members = list(conn.execute('SELECT project_id, path, common_dir FROM project_members')) if has_members_table(conn) else []
    # The non-Git workspace root is an explicit selection. Other folders under
    # it (including unrelated Git repositories) do not inherit membership.
    here = git_paths(cwd)
    workspace_ids = {r[0] for r in members}
    if not here:
        return [pid for pid, path in projects if pid in workspace_ids and Path(path) == cwd]
    exact = {pid for pid, path in projects if pid not in workspace_ids and Path(path) == here[0]}
    exact.update(pid for pid, path, common in members if (Path(path), Path(common)) == here)
    if exact:
        return sorted(exact)
    matches = {pid for pid, _, common in members if Path(common) == here[1]}
    for pid, path in projects:
        if pid not in workspace_ids:
            identity = git_paths(path)
            if identity and identity[1] == here[1]:
                matches.add(pid)
    return sorted(matches)


def workspace_checkouts(conn, path, names):
    root = Path(path).expanduser().resolve(strict=True)
    git_dir = subprocess.run(['git', '-C', str(root), 'rev-parse', '--git-dir'], capture_output=True)
    if not root.is_dir() or git_dir.returncode == 0 or (root / '.git').is_symlink() or (root / '.git').exists():
        raise ValueError('Workspace must be an existing directory outside a Git checkout')
    members = []
    for name in names:
        if not name.strip() or name in ('.', '..') or Path(name).name != name:
            raise ValueError(f'Member must name a direct child checkout: {name!r}')
        member = root / name
        if member.is_symlink():
            raise ValueError(f'Member checkout cannot be a symlink: {member}')
        identity = git_paths(member)
        if not identity or identity[0] != member:
            raise ValueError(f'Member is not a Git checkout root: {member}')
        if matching_project_ids(conn, member):
            raise ValueError(f'Member checkout already belongs to a registered project: {member}')
        members.append((name, str(member), str(identity[1])))
    return str(root), members


def check_single_checkout(conn, path):
    identity = git_paths(Path(path).expanduser())
    if not identity:
        raise ValueError(f'Git checkout not found: {path}')
    if conn.execute('SELECT 1 FROM project_members WHERE path = ? OR common_dir = ?',
                    tuple(map(str, identity))).fetchone():
        raise ValueError('Checkout is already a registered workspace member')
    return str(identity[0])


def members_for(conn, project_id):
    return [dict(row) for row in conn.execute('SELECT * FROM project_members WHERE project_id = ? ORDER BY id',
                                              (project_id,))]


def select_member(conn, project_id, name):
    members = members_for(conn, project_id)
    if not members and name is None:
        return None
    if not members:
        raise ValueError('--member is only valid for a workspace')
    for member in members:
        if member['name'] == name:
            return member['id']
    raise ValueError('Select a registered repository with --member: ' + ', '.join(m['name'] for m in members))


def member_for_task(conn, project_id, task_id):
    """Load a task's member even on a legacy registry opened read-only."""
    if not has_members_table(conn):
        return None
    row = conn.execute('''SELECT tasks.member_id, m.name, m.path, m.common_dir FROM tasks
        LEFT JOIN project_members m ON m.id = tasks.member_id AND m.project_id = tasks.project_id
        WHERE tasks.id = ? AND tasks.project_id = ?''', (task_id, project_id)).fetchone()
    if row is None:
        raise ValueError('Task does not belong to this project')
    if row[0] is None:
        if conn.execute('SELECT 1 FROM project_members WHERE project_id = ?', (project_id,)).fetchone():
            raise ValueError('Workspace task has no assigned member')
        return None
    if row[1] is None:
        raise ValueError('Task member does not belong to this workspace')
    return dict(zip(('id', 'name', 'path', 'common_dir'), row))


def checked_member_path(member, root):
    path = Path(member['path'])
    if (path.parent != Path(root) or path.name != member['name'] or path.resolve() != path
            or git_paths(path) != (path, Path(member['common_dir']))):
        raise ValueError('Registered workspace member checkout identity changed')
    return path


def check_member_worktree(member_path, worktree):
    path = Path(worktree)
    if (path.parent != member_path / '.letthemcook' / 'worktrees'
            or path.resolve() != path or path.name in ('', '.', '..')):
        raise ValueError('Worktree is outside the assigned member worktree directory')


def member_for_run(conn, run):
    member = member_for_task(conn, run['project_id'], run['task_id'])
    if run.get('member_id') != (member['id'] if member else None):
        raise ValueError('Run member does not match the task member')
    if member:
        path = checked_member_path(member, run['project_path'])
        check_member_worktree(path, run['worktree'])
    return member


def member_prompt(member, root):
    return (f"\nAssigned repository: {member['name']}\nWorkspace: {root}\n"
            "Read the workspace instructions and the applicable member instructions. "
            "Keep edits in your assigned worktree. Report downstream impact to the Chef.")


def member_label_matches(record, labels):
    member_id = record.get('member_id')
    if member_id is None:
        return 'ltc.member' not in labels
    return labels.get('ltc.member') == str(member_id)
