"""Check only the recorded Claude conversation before requesting native resume."""
import json
import os
from pathlib import Path
from uuid import UUID


def confirm_transcript(session_id, worktree):
    unavailable = 'Cannot confirm the recorded Claude conversation. Inspect the run or choose --fresh explicitly.'
    try:
        if str(UUID(session_id)) != session_id:
            raise ValueError(unavailable)
        # Claude can locate a UUID across projects, including long hashed
        # directory names. Reject ambiguous copies or a different working root.
        projects = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
        matches = list(projects.glob('*/' + session_id + '.jsonl'))
        if len(matches) != 1:
            raise ValueError(unavailable)
        path = matches[0]
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise ValueError(unavailable)
        with path.open('rb') as fp:
            # Read a bounded prefix. Unknown formats require explicit recovery.
            remaining = 4 * 1024 * 1024
            while remaining > 0:
                line = fp.readline(remaining)
                if not line:
                    break
                remaining -= len(line)
                entry = json.loads(line)
                if isinstance(entry, dict) and entry.get('type') in ('user', 'assistant'):
                    if entry.get('sessionId') == session_id and entry.get('cwd') == str(worktree):
                        return
                    break
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as error:
        raise ValueError(unavailable) from error
    raise ValueError(unavailable)
