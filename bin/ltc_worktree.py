"""Conservative evidence that a Cook worktree is safe to remove."""
import os
from pathlib import Path
import subprocess


def git(directory, *args, check=True):
    return subprocess.run(['git', '-C', str(directory), *args], capture_output=True, check=check)


def inspect(project_path, worktree_path, base):
    project = Path(project_path).resolve(strict=True)
    worktree = Path(worktree_path).resolve(strict=True)
    if project == worktree or not worktree.is_dir():
        raise ValueError('Invalid worktree: refusing to remove the project checkout')
    listed = git(project, 'worktree', 'list', '--porcelain', '-z').stdout.split(b'\0')
    registered = [Path(os.fsdecode(x[9:])).resolve() for x in listed if x.startswith(b'worktree ')]
    if worktree not in registered:
        raise ValueError('Worktree is not registered to this project')
    top = os.fsdecode(git(worktree, 'rev-parse', '--show-toplevel').stdout).strip()
    if Path(top).resolve() != worktree:
        raise ValueError('Worktree is not its own Git root')
    remote = git(project, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD', check=False)
    if remote.returncode == 0:
        remote_name = os.fsdecode(remote.stdout).strip()
        if not remote_name.startswith('origin/'):
            raise ValueError('Cannot determine the main branch')
        branch = remote_name[len('origin/'):]
        if git(project, 'show-ref', '--verify', '--quiet', 'refs/heads/' + branch, check=False).returncode:
            raise ValueError('The origin default branch has no matching local branch')
    else:
        candidates = [name for name in ('main', 'master') if git(project, 'show-ref', '--verify', '--quiet', 'refs/heads/' + name, check=False).returncode == 0]
        if len(candidates) != 1:
            raise ValueError('Cannot determine main branch unambiguously (origin/HEAD or exactly one of main/master required)')
        branch = candidates[0]
    ref = 'refs/heads/' + branch
    head = os.fsdecode(git(worktree, 'rev-parse', '--verify', 'HEAD^{commit}').stdout).strip()
    main_head = os.fsdecode(git(project, 'rev-parse', '--verify', ref + '^{commit}').stdout).strip()
    base = os.fsdecode(git(project, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}').stdout).strip()
    dirty = git(worktree, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignored=matching').stdout
    entries = dirty.split(b'\0')
    paths = [os.fsdecode(x) for x in entries if x]
    ancestry = git(project, 'merge-base', '--is-ancestor', head, main_head, check=False)
    if ancestry.returncode not in (0, 1):
        raise ValueError('Could not verify commit ancestry')
    base_ancestry = git(project, 'merge-base', '--is-ancestor', base, head, check=False)
    if base_ancestry.returncode not in (0, 1):
        raise ValueError('Could not verify worktree base')
    changed = git(project, 'diff', '--no-renames', '--name-only', '-z', base, head, '--').stdout.split(b'\0')
    changed = [x for x in changed if x]
    # Compare all changed paths against the target tree; avoids treating reverted
    # commits as landed. A later unrelated change to the same file blocks cleanup.
    same = True
    for raw in changed:
        path = os.fsdecode(raw)
        result = git(project, '--literal-pathspecs', 'diff', '--quiet', head, main_head, '--', path, check=False)
        if result.returncode not in (0, 1):
            raise ValueError('Could not compare changes with the main branch')
        if result.returncode == 1:
            same = False
            break
    return {'project': str(project), 'worktree': str(worktree), 'branch': branch,
            'main_commit': main_head, 'worktree_commit': head, 'base_commit': base,
            'dirty': bool(dirty), 'dirty_entries': paths[:30], 'dirty_entries_truncated': len(paths) > 30,
            'commit_on_main': ancestry.returncode == 0, 'base_in_history': base_ancestry.returncode == 0,
            'changed_paths_match_main': same,
            'safe_to_remove': not dirty and ancestry.returncode == 0 and base_ancestry.returncode == 0 and same}
