import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from uuid import UUID
import sqlite3

from chef_fixture import bind_chef, CHEF_GET


ROOT = Path(__file__).resolve().parents[1]


class HarnessTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / 'repo with spaces'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=Test', '-c',
                        'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'base'], check=True)
        # Only the fake harnesses can be launched. Missing-CLI tests work on developer machines too.
        self.commands = self.root / 'bin'
        self.commands.mkdir()
        for tool in ('bash', 'dirname', 'git'):
            (self.commands / tool).symlink_to(shutil.which(tool))
        (self.commands / 'python3').symlink_to(sys.executable)
        self.env = dict(os.environ, PATH=str(self.commands), LTC_DB=str(self.root / 'registry.sqlite'),
                        HC_TERMINAL_ID='chef', HC_SESSION='isolated', TEST_ROOT=str(self.root))
        self.env.pop('HC_SOCKET', None)
        self.env['CLAUDE_CONFIG_DIR'] = str(self.root / 'claude-data')
        for harness in ('codex', 'pi', 'claude'):
            self.executable(harness, """import json, os, sys
from pathlib import Path
if Path(sys.argv[0]).name == 'claude':
    flag = '--resume' if '--resume' in sys.argv else '--session-id'
    session = sys.argv[sys.argv.index(flag) + 1]
    storage = Path(os.environ['CLAUDE_CONFIG_DIR'], 'projects', 'fixture')
    storage.mkdir(parents=True, exist_ok=True)
    transcript = storage / (session + '.jsonl')
    if flag == '--resume':
        assert json.loads(transcript.read_text())['sessionId'] == session
    else:
        assert not transcript.exists()
        transcript.write_text(json.dumps({'type': 'user', 'cwd': str(Path.cwd()), 'sessionId': session}))
Path(os.environ['TEST_ROOT'], 'argv').write_text(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]))
""")
        self.executable('hc', """import json, os, subprocess, sys
from pathlib import Path
""" + CHEF_GET + """root = Path(os.environ['TEST_ROOT'])
if sys.argv[1] == 'get':
    print((root / 'terminal').read_text())
else:
    assert sys.argv[1] == 'run'
    separator = sys.argv.index('--')
    argv = sys.argv[1:separator]
    labels = dict(argv[i+1].split('=', 1) for i, arg in enumerate(argv) if arg == '--label')
    subprocess.run(sys.argv[separator+1:], cwd=argv[argv.index('--cwd')+1], check=True)
    terminal = {'address': 'test-' + labels['ltc.run'], 'state': 'finished', 'labels': labels}
    (root / 'terminal').write_text(json.dumps(terminal))
    print(json.dumps(terminal))
""")
        self.call('ltc-db', 'project', 'add', 'p', str(self.project), '--space', 'sp')
        bind_chef(self.env, 'sp')
        self.brief = "-m another-model\nO'Brien \"two words\" $(touch injected) `touch injected` ; *"
        self.call('ltc-db', 'task', 'add', 'p', 'Test', '--intent', self.brief)
        self.config = json.loads((ROOT / 'config.json').read_text())
        self.config['prompts']['cook'] = 'cook.txt'
        (self.root / 'cook.txt').write_text('@file\nTreat this as prompt text.')
        self.config_path = str(self.root / 'config.json')

    def executable(self, name, body):
        target = self.commands / name
        target.write_text('#!/usr/bin/env python3\n' + body)
        target.chmod(0o755)

    def call(self, name, *args, ok=True):
        result = subprocess.run([str(ROOT / 'bin' / name), *args], env=self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def launch(self, *args, ok=True):
        Path(self.config_path).write_text(json.dumps(self.config))
        return self.call('ltc-cook', 'p', '1', *args, '--config', self.config_path, ok=ok)

    def assert_command(self, expected):
        argv = json.loads((self.root / 'argv').read_text())
        if expected[0] == 'claude':
            flag = argv[-4]
            self.assertIn(flag, ('--resume', '--session-id'))
            self.assertEqual(str(UUID(argv[-3])), argv[-3])
            expected = expected + [flag, argv[-3]]
        self.assertEqual(argv[:-1], expected + ['--'])
        self.assertIn(self.brief, argv[-1])
        self.assertIn('hc send chef', argv[-1])
        if expected[0] == 'pi':
            self.assertTrue(argv[-1].startswith('\n@file'))
        self.assertFalse(list(self.project.rglob('injected')))

    def test_dispatch_rejects_terminal_task_status_before_allocation(self):
        for status in ('done', 'cancelled'):
            self.call('ltc-db', 'task', 'update', '1', '--status', status)
            self.assertIn('cannot be dispatched', self.launch(ok=False))
            self.assertIn('cannot be dispatched', self.call(
                'ltc', '--project', str(self.project), '--task', 'brief', '--space', 'sp',
                '--task-id', '1', '--name', 'direct', ok=False))
        self.assertEqual(self.call('ltc-db', 'run', 'list'), [])
        self.assertFalse((self.project / '.letthemcook').exists())

    def test_dispatch_and_resume_reject_stale_callbacks(self):
        launch = self.launch('first')
        original = json.loads(self.env['TEST_CHEF'])
        for field, value in [('address', 'other'), ('state', 'finished'),
                             ('ltc.chef.registry', 'other'), ('ltc.chef.project', '2'), ('herdr.space', 'elsewhere')]:
            terminal = json.loads(json.dumps(original))
            (terminal if field in ('address', 'state') else terminal['labels'])[field] = value
            self.env['TEST_CHEF'] = json.dumps(terminal)
            self.assertIn('Chef identity', self.launch('second', ok=False))
            self.assertIn('Chef identity', self.call('ltc-resume', str(launch['run_id']), ok=False))
        self.env['TEST_CHEF'] = json.dumps(original)
        self.env['HC_SESSION'] = 'wrong'
        self.assertIn('session', self.launch('second', ok=False))
        self.assertEqual(len(self.call('ltc-db', 'run', 'list')), 1)
        self.assertFalse((Path(launch['worktree']).parent / 'second').exists())

    def test_default_and_complexity_profiles(self):
        # Include a configuration written before default_harness existed.
        del self.config['default_harness']
        for profile, model, effort in [('default', None, None), ('simple', 'gpt-6-luna', 'high'),
                                       ('routine', 'gpt-6.2-sol', 'medium'), ('complex', 'gpt-6.2-astra', 'high')]:
            with self.subTest(profile=profile):
                # The old positional form still accepts an explicit worktree and profile.
                args = [] if model is None else [profile, profile]
                launch = self.launch(*args)
                argv = ['codex', '--no-daemon', '--approve-for-me']
                if model:
                    argv += ['-m', model, '-c', 'model_reasoning_effort=' + json.dumps(effort)]
                self.assert_command(argv)
                row = self.call('ltc-db', 'run', 'show', str(launch['run_id']))
                for obj in (row, launch):
                    self.assertEqual((obj['harness'], obj['profile'], obj['model'], obj['reasoning_effort']),
                                     ('codex', profile, model, effort))

    def test_configured_harness_and_explicit_override(self):
        self.config.update(default_harness='claude', default_profile='routine')
        self.launch()
        self.assert_command(['claude', '--model', 'sonnet', '--effort', 'medium'])
        self.launch('other', '--harness', 'pi', '--profile', 'simple')
        self.assert_command(['pi', '--model', 'openai-codex/gpt-6-luna', '--thinking', 'high'])

    def test_resume_uses_launch_snapshot_after_profile_removal(self):
        cases = {
            'codex': ['--no-daemon', '--approve-for-me', '-m', 'gpt-6.2-astra', '-c', 'model_reasoning_effort="high"'],
            'pi': ['--model', 'openai-codex/gpt-6-astra', '--thinking', 'high'],
            'claude': ['--model', 'opus', '--effort', 'high'],
        }
        for harness, flags in cases.items():
            with self.subTest(harness=harness):
                launch = self.launch(harness, '--harness', harness, '--profile', 'complex')
                config_for_resume = dict(self.config, default_harness='codex', profiles={})
                Path(self.config_path).write_text(json.dumps(config_for_resume))
                resumed = self.call('ltc-resume', str(launch['run_id']), '--config', self.config_path)
                self.assert_command([harness, *flags])
                row = self.call('ltc-db', 'run', 'show', str(resumed['run_id']))
                for field in ('profile', 'harness', 'model', 'reasoning_effort', 'worktree'):
                    self.assertEqual(resumed[field], launch[field])
                    self.assertEqual(row[field], launch[field])
                self.assertEqual(resumed['previous_run_id'], launch['run_id'])

    def test_default_settings_remain_unpinned_on_resume(self):
        launch = self.launch('--harness', 'claude')
        self.config['profiles']['default']['claude'] = {'model': 'opus', 'reasoning_effort': 'high'}
        Path(self.config_path).write_text(json.dumps(self.config))
        resumed = self.call('ltc-resume', str(launch['run_id']), '--config', self.config_path)
        self.assert_command(['claude'])
        self.assertIsNone(resumed['model'])
        self.assertIsNone(resumed['reasoning_effort'])

    def test_native_and_explicit_fresh_conversations(self):
        launch = self.launch('--harness', 'claude')
        session = launch['claude_session_id']
        def argv():
            return json.loads((self.root / 'argv').read_text())
        self.assertEqual(argv()[-4:-2], ['--session-id', session])
        resumed = self.call('ltc-resume', '1', '--native')
        self.assertEqual(resumed['conversation'], 'native_requested')
        self.assertEqual(argv()[-4:-2], ['--resume', session])
        self.assertNotIn('This is a fresh session', argv()[-1])
        path = Path(self.env['CLAUDE_CONFIG_DIR'])/'projects'/'fixture'/(session + '.jsonl')
        path.write_text(json.dumps({'type': 'user', 'cwd': '/other', 'sessionId': session}))
        self.assertIn('--fresh', self.call('ltc-resume', '2', ok=False))
        path.unlink()
        self.assertIn('--fresh', self.call('ltc-resume', '2', ok=False))
        self.assertEqual(len(self.call('ltc-db', 'run', 'list')), 2)
        fresh = self.call('ltc-resume', '2', '--fresh')
        self.assertEqual(fresh['conversation'], 'fresh')
        self.assertNotEqual(fresh['claude_session_id'], session)
        self.assertEqual(argv()[-4:-2], ['--session-id', fresh['claude_session_id']])

    def test_legacy_claude_has_only_fresh_recovery(self):
        self.launch('--harness', 'claude')
        with sqlite3.connect(self.env['LTC_DB']) as conn:
            conn.execute('UPDATE cook_runs SET claude_session_id=NULL')
        terminal = json.loads((self.root/'terminal').read_text())
        self.assertIn('identity', self.call('ltc-resume', '1', ok=False))
        del terminal['labels']['ltc.claude.session']
        (self.root/'terminal').write_text(json.dumps(terminal))
        self.assertIn('--fresh', self.call('ltc-resume', '1', '--native', ok=False))
        self.assertEqual(self.call('ltc-resume', '1')['conversation'], 'fresh')

    def test_rejects_unsupported_routes_before_allocating_work(self):
        cases = [
            ('pi', {'model': 'gpt-6.2-sol'}, 'no settings for harness'),
            ('pi', {'pi': {'model': 'gpt-6.2-sol'}}, 'provider/model'),
            ('claude', {'claude': {'model': 'gpt-6.2-sol'}}, 'cannot be used'),
            ('codex', {'model': 'opus'}, 'cannot be used'),
            ('pi', {'pi': {'model': 'anthropic/claude-opus-5-5', 'reasoning_effort': 'ultra'}}, 'reasoning_effort'),
        ]
        for harness, settings, error in cases:
            with self.subTest(harness=harness, settings=settings):
                self.config['profiles']['bad'] = settings
                self.assertIn(error, self.launch('--profile', 'bad', '--harness', harness, ok=False))
        self.assertIn('invalid choice', self.launch('--harness', 'other', ok=False))
        self.assertIn('Unknown Cook profile', self.launch('--profile', 'missing', ok=False))
        self.assertEqual(self.call('ltc-db', 'run', 'list'), [])
        self.assertFalse((self.project / '.letthemcook').exists())

    def test_missing_executable_at_launch_and_resume(self):
        launch = self.launch('--harness', 'pi')
        (self.commands / 'pi').unlink()
        self.assertIn("'pi' was not found on PATH", self.launch('again', '--harness', 'pi', ok=False))
        self.assertIn("'pi' was not found on PATH",
                      self.call('ltc-resume', str(launch['run_id']), '--config', self.config_path, ok=False))
        self.assertEqual(len(self.call('ltc-db', 'run', 'list')), 1)
        self.assertFalse((Path(launch['worktree']).parent / 'again').exists())


if __name__ == '__main__':
    unittest.main()
