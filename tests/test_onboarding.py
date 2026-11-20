from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

# Both sides of the local Herdr protocol. No windows, model processes or watchers are started.
DESKTOP = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ['DESKTOP_STATE'])
spaces = json.loads(state.read_text())
args = sys.argv[1:]
if args == ['ls']:
    if os.environ.get('WINDOW_CLOSED'): sys.exit('window closed')
    print(json.dumps({'spaces': spaces}))
    sys.exit()
assert args[0] == 'do'
fields = dict(zip(args[2::2], args[3::2]))
assert fields['--focus'] == 'false'
if args[1] == 'space.new':
    sid = 's' + str(len(spaces) + 1)
    spaces.append({'id': sid, 'name': fields['--name'], 'machine': None,
                   'folder': fields['--folder'], 'tabs': []})
    result = {'space': sid}
else:
    assert args[1] == 'tab.new'
    space = next(s for s in spaces if s['id'] == fields['--space'])
    tab = 'tab-' + space['id']
    space['tabs'].append({'id': tab, 'name': fields['--name'], 'launch': fields,
                          'terminals': [{'terminal': 'term-' + tab}]})
    result = {'space': space['id'], 'tab': tab}
state.write_text(json.dumps(spaces))
print(json.dumps(result))
"""

CORE = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ['CORE_STATE'])
terminals = json.loads(state.read_text())
args = sys.argv[1:]
if args[0] == 'get':
    if args[1] not in terminals: sys.exit(3)
    print(json.dumps(terminals[args[1]]))
elif args[0] == 'label':
    terminals[args[1]]['labels'].update(field.split('=', 1) for field in args[2:])
    state.write_text(json.dumps(terminals))
elif args[0] == 'run':
    assert '--no-parent' in args and 'ltc_watch.py' in ' '.join(args)
    print('{}')
elif args[:3] == ['send', '--if', 'idle']:
    terminal = terminals[args[3]]
    if terminal.get('busy'): sys.exit(1)
    terminal.setdefault('messages', []).append(args[4])
    state.write_text(json.dumps(terminals))
else:
    sys.exit('unexpected hc command')
"""


class OnboardingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.install = self.tmp / 'LTC clone'
        self.project = self.tmp / 'App Design'
        for path in (self.install, self.project):
            subprocess.run(['git', 'clone', '--quiet', '--no-local', '--depth=1', str(ROOT), str(path)],
                           check=True)
        # Overlay precisely the working copy under test onto a committed clone with a fresh registry.
        for directory in ('bin', 'prompts'):
            shutil.copytree(ROOT / directory, self.install / directory, dirs_exist_ok=True)
        for file in ('AGENTS.md', 'CLAUDE.md', 'README.md', 'docs/setup.md'):
            shutil.copy2(ROOT / file, self.install / file)
        self.db = self.install / '.letthemcook/registry.sqlite3'
        self.desktop = self.tmp / 'desktop.json'
        self.core = self.tmp / 'core.json'
        self.other = {'id': 'other', 'folder': str(self.tmp), 'name': 'Other',
                      'tabs': [{'name': 'Chef @ other', 'id': 'other-tab'}]}
        self.desktop.write_text(json.dumps([self.other]))
        self.core.write_text('{}')
        commands = self.tmp / 'commands'
        commands.mkdir()
        for name, code in [('herdr-app', DESKTOP), ('hc', CORE), ('codex', '#!/bin/sh\nexit 0'),
                           ('claude', '#!/bin/sh\nexit 0')]:
            path = commands / name
            path.write_text(code)
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{commands}:/usr/bin:/bin', DESKTOP_STATE=str(self.desktop),
                        CORE_STATE=str(self.core))
        for key in ('HC_TERMINAL_ID', 'HC_SESSION', 'HC_SOCKET', 'LTC_DB'):
            self.env.pop(key, None)

    def call(self, binary, *args, address=None, error=None, **extra):
        env = dict(self.env, **extra)
        if address:
            env['HC_TERMINAL_ID'] = address
        result = subprocess.run([self.install / 'bin' / binary, *args], text=True, capture_output=True,
                                env=env, cwd=self.install, timeout=30)
        if error:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def onboard(self, *args, **kwargs):
        return self.call('ltc-onboard', *args, **kwargs)

    def spaces(self):
        return json.loads(self.desktop.read_text())

    def bind(self, space, name):
        tab = space['tabs'][0]
        address = tab['terminals'][0]['terminal']
        terminals = json.loads(self.core.read_text())
        terminals[address] = {'address': address, 'state': 'live', 'labels': {'herdr.space': space['id']}}
        self.core.write_text(json.dumps(terminals))
        self.call('ltc-watch', 'start', name, address=address)
        self.call('ltc-status', name, address=address)
        return address

    def flow(self, harness):
        args = ('root', '--harness', harness, '--language', 'Polish')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.onboard(*args), range(2)))
        self.assertEqual([r['status'] for r in results], ['awaiting_registration'] * 2)
        self.assertEqual(len(self.spaces()), 2)
        root = self.spaces()[1]
        self.assertEqual(len(root['tabs']), 1)
        tab = root['tabs'][0]
        self.assertEqual(tab['name'], '👨‍🍳 Chef @ Let Them Cook')
        self.assertEqual(tab['launch']['--folder'], str(self.install))

        def prompt(tab):
            launch = tab['launch']
            argv = json.loads(launch['--argv'])
            self.assertEqual(Path(argv[0]).name, harness)
            self.assertNotIn('--task', launch)
            self.assertEqual(argv[-2], '--')
            return argv[-1]

        self.assertIn('What would you like to cook up?', prompt(tab))
        self.assertIn('Which folder is your project in?', prompt(tab))
        self.assertIn('every onboarding question in English, including follow-up questions', prompt(tab))
        self.assertNotIn('Co chcesz teraz upichcić?', prompt(tab))
        self.assertIn('ltc-watch start PROJECT', prompt(tab))
        goal = "Sprawdź przycisk ‘Zapisz’\n$(touch unsafe) & wersję roboczą"
        target = ('project', '--goal', goal, '--', str(self.project))
        self.onboard(*target, error='No Chef bound')
        chef = self.bind(root, 'LetThemCook')
        handoff = self.onboard('receive', 'LetThemCook', address=chef)
        self.assertEqual(handoff['greeting_language'], 'Polish')
        self.assertEqual(handoff['question_language'], 'English')
        self.assertEqual(self.onboard(*args)['status'], 'bound')
        self.onboard(*target, address='other-terminal', error='does not match')
        self.assertEqual(self.onboard(*target, address=chef)['status'], 'awaiting_registration')
        self.assertEqual(self.onboard(*target, address=chef)['status'], 'awaiting_registration')
        other, root, project = self.spaces()
        self.assertEqual(other, self.other)
        project_tab, = project['tabs']
        self.assertEqual(project_tab['launch']['--folder'], str(self.project))
        self.assertIn('receive PROJECT', prompt(project_tab))
        self.assertNotIn('What would you like to cook up?', prompt(project_tab))
        project_chef = self.bind(project, 'App Design')
        self.onboard('receive', 'App Design', address=chef, error='does not match')
        self.assertEqual(self.onboard('receive', 'App Design', address=project_chef), {'goal': goal})
        self.assertEqual(self.onboard(*target, address=chef)['status'], 'bound')
        self.assertEqual(len(self.call('ltc-db', 'project', 'list')), 2)
        self.assertEqual(self.call('ltc-db', 'task', 'list'), [])
        self.assertEqual(self.call('ltc-db', 'run', 'list'), [])
        self.assertEqual(self.call('ltc-chef', 'LetThemCook')[0]['status'], 'already_present')
        self.assertNotIn('messages', self.core.read_text())

    def test_codex_conversation(self):
        self.flow('codex')

    def test_claude_conversation(self):
        self.flow('claude')

    def test_missing_dependencies_closed_window_and_invalid_paths(self):
        self.onboard('root', '--harness', 'claude', WINDOW_CLOSED='1', error='Open Herdr Desktop')
        (self.tmp / 'commands/claude').unlink()
        self.onboard('root', '--harness', 'claude', error='Missing command: claude')
        self.onboard('project', '--goal', 'goal', '', error='Choose a project folder')
        self.onboard('project', '--goal', 'goal', str(self.tmp / 'missing'), error='existing Git project')
        self.onboard('project', '--goal', 'goal', str(self.project / 'bin'), error='top folder')
        empty = self.tmp / 'empty'
        subprocess.run(['git', 'init', '-q', str(empty)], check=True)
        self.onboard('project', '--goal', 'goal', str(empty), error='first Git commit')
        self.assertFalse((empty / '.git/refs/heads/main').exists())
        self.assertEqual(self.spaces(), [self.other])

    def test_manual_legacy_tab_receives_handoff_only_after_binding(self):
        local = {'id': 'manual', 'folder': str(self.install), 'name': 'Kitchen', 'tabs': []}
        self.desktop.write_text(json.dumps([self.other, local]))
        self.call('ltc-db', 'project', 'add', 'LetThemCook', str(self.install), '--space', local['id'])
        self.call('ltc-chef', 'LetThemCook')
        tab = self.spaces()[1]['tabs'][0]
        self.assertNotIn('What would you like to cook up?', tab['launch']['--task'])
        args = ('root', '--harness', 'claude')
        result = self.onboard(*args)
        self.assertEqual(result['tab'], '👨‍🍳 Chef @ LetThemCook')
        self.assertEqual(result['status'], 'awaiting_registration')
        self.assertIn('trust', result['next'])
        self.assertEqual(self.core.read_text(), '{}')
        chef = self.bind(self.spaces()[1], 'LetThemCook')
        terminals = json.loads(self.core.read_text())
        terminals[chef]['busy'] = True
        self.core.write_text(json.dumps(terminals))
        self.assertIn('retry setup', self.onboard(*args)['next'].lower())
        terminals[chef]['busy'] = False
        self.core.write_text(json.dumps(terminals))
        self.assertEqual(self.onboard(*args)['status'], 'bound')
        self.onboard(*args)
        message, = json.loads(self.core.read_text())[chef]['messages']
        self.assertIn('onboarding-root.md', message)
        self.assertEqual(self.call('ltc-db', 'project', 'show', 'LetThemCook')['chef_harness'], 'codex')

    def test_choose_among_matching_local_spaces(self):
        local = {'id': 'one', 'folder': str(self.install), 'name': 'Kitchen one', 'tabs': []}
        remote = dict(local, id='remote', machine='vps')
        self.desktop.write_text(json.dumps([remote, local, dict(local, id='two', name='Kitchen two')]))
        args = ('root', '--harness', 'claude')
        self.onboard(*args, error='Kitchen two')
        self.assertEqual(self.call('ltc-db', 'project', 'list'), [])
        self.onboard(*args, '--space', 'remote', error='no longer available')
        self.assertEqual(self.onboard(*args, '--space', 'two')['space'], 'Kitchen two')
        self.assertEqual([len(s['tabs']) for s in self.spaces()], [0, 0, 1])
