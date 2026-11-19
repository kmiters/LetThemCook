"""Load the configured Cook base and only the selected role's instructions."""
from pathlib import Path

ROLES = ('generic', 'designer', 'developer')
PROMPTS = Path(__file__).resolve().parent.parent / 'prompts'


def cook_instructions(config, config_dir, role):
    if role not in ROLES:
        raise ValueError(f'Unknown Cook role {role!r}')
    prompts = config['prompts']
    base = (config_dir / prompts['cook']).read_text()
    if role == 'generic':
        return base
    roles = prompts.get('roles', {})
    if not isinstance(roles, dict):
        raise ValueError('prompts.roles must be an object')
    path = roles.get(role, str(PROMPTS / f'cook-{role}.md'))
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f'prompts.roles.{role} must be a file path')
    return base.rstrip() + '\n\n' + (config_dir / path).read_text()
