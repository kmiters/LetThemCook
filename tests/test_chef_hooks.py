import json
import os
from pathlib import Path
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / 'bin' / 'ltc-db'
MARKER = 'ltc-kind: instruction-maintenance'


class ChefHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.install = Path(self.tmp.name) / 'install'
        install_bin = self.install / 'bin'
        install_bin.mkdir(parents=True)
        for name in ('ltc_chef_hooks.py', 'ltc_registry.py', 'ltc_update.py'):
            shutil.copy2(ROOT / 'bin' / name, install_bin)
        self.hook = install_bin / 'ltc_chef_hooks.py'
        self.root = Path(self.tmp.name) / 'project-🍳'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.db = Path(self.tmp.name) / 'registry.sqlite'
        self.project = 'hook-fixture'
        self.cli = [str(REGISTRY), '--db', str(self.db)]
        subprocess.run(self.cli + ['project', 'add', self.project, str(self.root)], check=True,
                       capture_output=True, text=True)

    def tearDown(self):
        self.tmp.cleanup()

    def add_task(self, title, context='fixture evidence'):
        result = subprocess.run(self.cli + ['task', 'add', self.project, title,
                                            '--intent', title, '--context', context],
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)['id']

    def update_task(self, task_id, *args):
        return subprocess.run(self.cli + ['task', 'update', str(task_id), *args],
                              check=True, capture_output=True, text=True)

    def call_hook(self, command, event, session='session-1', cli='codex', source='startup',
                  cwd=None, tool_name='Bash', raw_size=0, env=None):
        payload = {
            'hook_event_name': event,
            'session_id': session,
            'cwd': str(cwd or self.root),
            'source': source,
            'tool_name': tool_name,
            'transcript_path': '/not-read/fixture-transcript.jsonl',
        }
        serialized = json.dumps(payload)
        if raw_size:
            serialized = ' ' * raw_size
        result = subprocess.run([sys.executable, str(self.hook), command, '--db', str(self.db),
                                 '--project', self.project, '--root', str(self.root)],
                                input=serialized, capture_output=True, text=True, check=True, env=env)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def test_startup_and_completion_stdout_match_both_native_hook_shapes(self):
        recent_task_id = self.add_task('previously accepted task')
        self.update_task(recent_task_id, '--status', 'done')
        task_id = self.add_task('accepted substantive task')
        for cli in ('codex', 'claude'):
            started = self.call_hook('session-start', 'SessionStart', session=cli, cli=cli)
            self.assertEqual(started['hookSpecificOutput']['hookEventName'], 'SessionStart')
            self.assertIn(f'#{recent_task_id}', started['hookSpecificOutput']['additionalContext'])
            self.assertNotIn('fixture-transcript.jsonl', json.dumps(started))

        self.update_task(task_id, '--description', 'Updated outcome notes', '--status', 'done')
        self.update_task(task_id, '--description', 'Updated again without a status transition', '--status', 'done')
        with sqlite3.connect(self.db) as conn:
            context = conn.execute('SELECT context FROM task_briefs WHERE task_id=?', (task_id,)).fetchone()[0]
            self.assertEqual(context, 'fixture evidence')
            self.assertEqual(conn.execute('SELECT count(*) FROM task_status_events WHERE task_id=?',
                                          (task_id,)).fetchone()[0], 1)

        for cli in ('codex', 'claude'):
            completed = self.call_hook('post-tool-use', 'PostToolUse', session=cli, cli=cli, source='')
            self.assertEqual(completed['hookSpecificOutput'], {
                'hookEventName': 'PostToolUse',
                'additionalContext': (f'LetThemCook recorded task completion for #{task_id}. '
                                      'Review the task evidence and acceptance, then check whether durable '
                                      'agent guidance is missing or stale. This is a reminder only and does '
                                      'not authorize edits, commits, or pushes.'),
            })
            self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', session=cli, cli=cli, source=''))

    def test_maintenance_context_marker_survives_description_update_and_suppresses_loop(self):
        task_id = self.add_task('maintain agent instructions', 'Evidence\n' + MARKER)
        self.call_hook('session-start', 'SessionStart')
        self.update_task(task_id, '--description', 'Updated result details', '--status', 'done')
        with sqlite3.connect(self.db) as conn:
            context = conn.execute('SELECT context FROM task_briefs WHERE task_id=?', (task_id,)).fetchone()[0]
            self.assertIn(MARKER, context)
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', source=''))

        ordinary = self.add_task('substantive task')
        self.update_task(ordinary, '--status', 'done')
        completion = self.call_hook('post-tool-use', 'PostToolUse', source='')
        self.assertIn(f'#{ordinary}', completion['hookSpecificOutput']['additionalContext'])
        self.assertNotIn(f'#{task_id}', completion['hookSpecificOutput']['additionalContext'])

    def test_unrelated_session_event_and_invalid_input_fail_open(self):
        task_id = self.add_task('accepted task')
        self.call_hook('session-start', 'SessionStart')
        self.update_task(task_id, '--status', 'done')
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', session='unregistered', source=''))
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', cwd=self.root.parent, source=''))
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', tool_name='Write', source=''))
        self.assertIsNone(self.call_hook('post-tool-use', 'SessionStart', source=''))
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', source='', raw_size=65537))

    def test_updates_on_startup_resume_and_second_day_of_active_session(self):
        git = shutil.which('git')
        subprocess.run([git, 'init', '-q', str(self.install)], check=True)
        subprocess.run([git, '-C', str(self.install), 'remote', 'add', 'origin',
                        'https://github.com/kmiters/LetThemCook.git'], check=True)
        fixture_bin = self.install / 'fixture-bin'
        fixture_bin.mkdir()
        fake_git = fixture_bin / 'git'
        fake_git.write_text(f"""#!/bin/sh
for arg do
    if [ "$arg" = ls-remote ]; then
        echo request >> "$UPDATE_REQUESTS"
        tip=$(cat "$UPDATE_TIP")
        [ "$tip" != offline ] || exit 128
        printf '%s\\trefs/heads/main\\n' "$tip"
        exit
    fi
done
exec {shlex.quote(git)} "$@"
""")
        fake_git.chmod(0o755)
        requests = self.install / 'requests'
        tip = self.install / 'tip'
        env = dict(os.environ, PATH=str(fixture_bin) + os.pathsep + os.environ['PATH'],
                   UPDATE_REQUESTS=str(requests), UPDATE_TIP=str(tip))
        state = self.install / '.letthemcook' / 'update-check.sqlite3'

        def expire():
            with sqlite3.connect(state) as conn:
                conn.execute('UPDATE update_check SET last_attempt=last_attempt-86400')

        self.assertIsNone(self.call_hook('session-start', 'SessionStart', source='clear', env=env))
        self.assertFalse(requests.exists())
        tip.write_text('1' * 40)
        start = self.call_hook('session-start', 'SessionStart', env=env)
        context = start['hookSpecificOutput']['additionalContext']
        self.assertIn('Review agent instructions', context)
        self.assertIn('LetThemCook update check', context)

        resume = self.call_hook('session-start', 'SessionStart', session='other', source='resume', env=env)
        self.assertNotIn('LetThemCook update check', resume['hookSpecificOutput']['additionalContext'])
        self.assertEqual(requests.read_text().splitlines(), ['request'])

        expire()
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', tool_name='Write', env=env))
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', session='unregistered', env=env))
        self.assertEqual(requests.read_text().splitlines(), ['request'])
        tip.write_text('2' * 40)
        day_two = self.call_hook('post-tool-use', 'PostToolUse', env=env)
        self.assertIn('LetThemCook update check', day_two['hookSpecificOutput']['additionalContext'])
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', env=env))

        expire()
        tip.write_text('3' * 40)
        resume = self.call_hook('session-start', 'SessionStart', session='other', source='resume', env=env)
        self.assertIn('LetThemCook update check', resume['hookSpecificOutput']['additionalContext'])

        expire()
        tip.write_text('offline')
        task_id = self.add_task('work completed while offline')
        self.update_task(task_id, '--status', 'done')
        after_failure = self.call_hook('post-tool-use', 'PostToolUse', env=env)
        context = after_failure['hookSpecificOutput']['additionalContext']
        self.assertIn(f'recorded task completion for #{task_id}', context)
        self.assertNotIn('update check', context)
        self.assertEqual(requests.read_text().splitlines(), ['request'] * 4)
        with sqlite3.connect(state) as conn:
            outcome, last_attempt, last_success = conn.execute(
                'SELECT outcome, last_attempt, last_success FROM update_check').fetchone()
        self.assertEqual(outcome, 'request_failed')
        self.assertGreater(last_attempt, last_success)

    def test_no_completion_does_not_take_write_lock_or_advance_cursor(self):
        self.call_hook('session-start', 'SessionStart')
        with sqlite3.connect(self.db) as conn:
            before = conn.execute('SELECT last_event_id, updated_at FROM chef_hook_sessions').fetchone()
        self.assertIsNone(self.call_hook('post-tool-use', 'PostToolUse', source=''))
        with sqlite3.connect(self.db) as conn:
            after = conn.execute('SELECT last_event_id, updated_at FROM chef_hook_sessions').fetchone()
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
