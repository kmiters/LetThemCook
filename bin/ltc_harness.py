"""Resolve a Cook profile and construct interactive harness arguments."""
from dataclasses import dataclass
import json
import os
import re
import shutil
from typing import Optional


EFFORTS = {
    'codex': ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
    'pi': ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
    'claude': ('low', 'medium', 'high', 'xhigh', 'max'),
}


@dataclass(frozen=True)
class CookRoute:
    profile: str
    harness: str
    model: Optional[str]
    reasoning_effort: Optional[str]

    def command(self, prompt, *, claude_session_id=None, resume=False):
        """Build interactive CLI argv and check it before allocating a run."""
        if not isinstance(self.harness, str) or self.harness not in EFFORTS:
            raise ValueError(f'Unknown Cook harness {self.harness!r}. Choose codex, pi or claude.')
        model, effort = self.model, self.reasoning_effort
        if model is not None:
            if not isinstance(model, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/@+\[\]-]*', model):
                raise ValueError(f'Invalid model ID for {self.harness}: {model!r}')
            if self.harness == 'pi' and not re.fullmatch(r'[^/:]+/[^/:]+(?:/[^/:]+)*', model):
                raise ValueError('Pi profiles require a provider/model ID. Set thinking via reasoning_effort.')
            if self.harness == 'codex' and (model.startswith('claude-') or model in ('haiku', 'sonnet', 'opus', 'fable')):
                raise ValueError(f'Claude model {model!r} cannot be used with the Codex profile')
            if self.harness == 'claude' and model.startswith(('gpt-', 'openai/', 'openai-codex/')):
                raise ValueError(f'OpenAI model {model!r} cannot be used with Claude Code')
        if effort is not None and effort not in EFFORTS[self.harness]:
            raise ValueError(f'Unsupported {self.harness} reasoning_effort {effort!r}. Choose from {EFFORTS[self.harness]} or null.')
        executable = shutil.which(self.harness)
        if executable is None:
            raise ValueError(f'Cook harness executable {self.harness!r} was not found on PATH')

        # Herdr can have a different PATH. Keep symlink names used by CLI shims.
        argv = [os.path.abspath(executable)]
        if self.harness == 'codex':
            argv += ['--no-daemon', '--approve-for-me']
            if model is not None:
                argv += ['-m', model]
            if effort is not None:
                argv += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        else:
            if model is not None:
                argv += ['--model', model]
            if effort is not None:
                argv += ['--thinking' if self.harness == 'pi' else '--effort', effort]
        if claude_session_id:
            if self.harness != 'claude':
                raise ValueError('Only Claude Code supports a conversation UUID')
            argv += ['--resume' if resume else '--session-id', claude_session_id]
        # Even after --, Pi treats a leading @ as a filename.
        if self.harness == 'pi' and prompt.startswith('@'):
            prompt = '\n' + prompt
        return argv + ['--', prompt]


def resolve_route(config, profile_name, harness):
    profiles = config['profiles']
    if profile_name not in profiles:
        raise ValueError(f'Unknown Cook profile {profile_name!r}')
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise ValueError(f'Cook profile {profile_name!r} must be an object')
    if not isinstance(harness, str) or harness not in EFFORTS:
        raise ValueError(f'Unknown Cook harness {harness!r}. Choose codex, pi or claude.')
    # Flat model/effort keys are the original Codex profile format.
    settings = profile if harness == 'codex' else profile.get(harness)
    if not isinstance(settings, dict):
        raise ValueError(f'Cook profile {profile_name!r} has no settings for harness {harness!r}')
    return CookRoute(profile_name, harness, settings.get('model'), settings.get('reasoning_effort'))
