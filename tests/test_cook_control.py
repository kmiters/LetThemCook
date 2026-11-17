import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CookControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'registry.sqlite'
        project = self.root / 'project'
        self.project = project.resolve()
        project.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(project)], check=True)
        subprocess.run(['git', '-C', str(project), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['git', '-C', str(project), 'config', 'user.email', 'test@example.invalid'], check=True)
        (project / 'file').write_text('base')
        subprocess.run(['git', '-C', str(project), 'add', 'file'], check=True)
        subprocess.run(['git', '-C', str(project), 'commit', '-qm', 'base'], check=True)
        base = subprocess.run(['git', '-C', str(project), 'rev-parse', 'HEAD'], text=True, capture_output=True, check=True).stdout.strip()
        self.worktree = (self.root / 'worktree').resolve()
        subprocess.run(['git', '-C', str(project), 'worktree', 'add', '--detach', str(self.worktree)], capture_output=True, check=True)
        base_env = dict(os.environ, LTC_DB=str(self.db))
        subprocess.run([str(ROOT / 'bin/ltc-db'), 'project', 'add', 'app', str(project), '--space', 'sp1'], env=base_env, capture_output=True, check=True)
        subprocess.run([str(ROOT / 'bin/ltc-db'), 'task', 'add', 'app', 'task'], env=base_env, capture_output=True, check=True)
        with sqlite3.connect(self.db) as conn:
            token = conn.execute('SELECT token FROM registry_identity').fetchone()[0]
            conn.execute("INSERT INTO cook_runs (task_id, project_id, space, worktree, base, terminal, hc_session, name, profile, launch_state) VALUES (1, 1, 'sp1', ?, ?, 't1', 'my-session', 'task-1', 'default', 'started')", (str(self.worktree), base))
        self.labels = {'ltc.registry': token, 'ltc.run': '1', 'ltc.task': '1', 'ltc.project': '1', 'herdr.space': 'sp1'}
        self.state = self.root / 'state'
        self.state.write_text(json.dumps({'address': 't1', 'state': 'live', 'labels': self.labels}))
        hc = self.root / 'hc'
        hc.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ['STATE'])
log = Path(os.environ['LOG'])
with log.open('a') as f: f.write(json.dumps({'args':sys.argv[1:], 'session':os.environ.get('HC_SESSION'), 'socket':os.environ.get('HC_SOCKET')}) + '\\n')
if sys.argv[1] == 'get': print(state.read_text())
elif sys.argv[1] == 'kill':
    data = json.loads(state.read_text()); data['state'] = 'finished'; state.write_text(json.dumps(data))
elif sys.argv[1] == 'rm':
    if os.environ.get('FAIL_RM'): sys.exit(1)
    state.unlink()
''')
        hc.chmod(0o755)
        self.log = self.root / 'log'
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'], LTC_DB=str(self.db), HC_SESSION='wrong', HC_SOCKET='/wrong', STATE=str(self.state), LOG=str(self.log))

    def call(self, *args):
        return subprocess.run([str(ROOT / 'bin/ltc-cook-control'), *args], env=self.env, text=True, capture_output=True)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_send_checks_identity_and_session(self):
        result = self.call('send', '1', 'Follow up')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.commands()
        self.assertEqual([c['args'][0] for c in calls], ['get', 'send'])
        self.assertTrue(all(c['session'] == 'my-session' and c['socket'] is None for c in calls))
        self.assertEqual(calls[1]['args'][1:], ['t1', 'Follow up'])

    def test_mismatch_refuses_and_close_requires_done(self):
        bad = dict(self.labels, **{'ltc.task': '2'})
        self.state.write_text(json.dumps({'address': 't1', 'state': 'live', 'labels': bad}))
        self.assertNotEqual(self.call('send', '1', 'hello').returncode, 0)
        self.assertEqual([c['args'][0] for c in self.commands()], ['get'])
        self.assertNotEqual(self.call('close', '1').returncode, 0)
        self.assertEqual(len(self.commands()), 1)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        self.assertNotEqual(self.call('close', '1').returncode, 0)
        self.assertEqual([c['args'][0] for c in self.commands()], ['get', 'get'])

    def test_close_rechecks_after_kill(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        result = self.call('close', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['args'][0] for c in self.commands()], ['get', 'kill', 'get', 'get', 'rm'])
        self.assertFalse(self.worktree.exists())
        status = subprocess.run([str(ROOT / 'bin/ltc-status'), 'app'], env=self.env, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(status.stdout)['omitted']['runs'], 1)
        self.assertEqual(json.loads(status.stdout)['runs'], [])
        full = subprocess.run([str(ROOT / 'bin/ltc-status'), '--all', 'app'], env=self.env, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(full.stdout)['runs'][0]['terminal_status'], {'state':'closed', 'reason':'verified_close'})
        self.assertEqual([c['args'][0] for c in self.commands()], ['get', 'kill', 'get', 'get', 'rm'])


    def test_landed_change_can_be_closed(self):
        (self.worktree / 'file').write_text('landed')
        subprocess.run(['git', '-C', str(self.worktree), 'add', 'file'], check=True)
        subprocess.run(['git', '-C', str(self.worktree), 'commit', '-qm', 'work'], check=True)
        head = subprocess.run(['git', '-C', str(self.worktree), 'rev-parse', 'HEAD'], text=True, capture_output=True, check=True).stdout.strip()
        subprocess.run(['git', '-C', str(self.project), 'merge', '--ff-only', head], capture_output=True, check=True)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        self.assertTrue(json.loads(self.call('check', '1').stdout)['safe_to_remove'])
        result = self.call('close', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.worktree.exists())
        self.assertEqual((self.project / 'file').read_text(), 'landed')

    def test_failed_terminal_removal_is_not_recorded_as_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        self.env['FAIL_RM'] = '1'
        self.assertNotEqual(self.call('close', '1').returncode, 0)
        with sqlite3.connect(self.db) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='closed_runs'").fetchone()
            if exists:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM closed_runs').fetchone()[0], 0)
        self.assertTrue(self.state.exists())
        self.state.unlink()
        status = subprocess.run([str(ROOT / 'bin/ltc-status'), 'app'], env=self.env, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(status.stdout)['runs'][0]['terminal_status']['state'], 'unknown')

    def test_uncommitted_changes_require_explicit_discard(self):
        (self.worktree / 'file').write_text('unfinished change')
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        report = self.call('check', '1')
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertTrue(json.loads(report.stdout)['dirty'])
        rejected = self.call('close', '1')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('work_not_confirmed_on_main', rejected.stderr)
        self.assertEqual((self.worktree / 'file').read_text(), 'unfinished change')
        self.assertNotIn('kill', [c['args'][0] for c in self.commands()])
        result = self.call('close', '1', '--discard-changes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['discarded_changes'])
        self.assertFalse(self.worktree.exists())

    def test_unlanded_commit_and_revert_are_not_safe(self):
        (self.worktree / 'file').write_text('work')
        subprocess.run(['git', '-C', str(self.worktree), 'add', 'file'], check=True)
        subprocess.run(['git', '-C', str(self.worktree), 'commit', '-qm', 'work'], check=True)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        first = json.loads(self.call('check', '1').stdout)
        self.assertFalse(first['commit_on_main'])
        self.assertNotEqual(self.call('close', '1').returncode, 0)
        head = subprocess.run(['git', '-C', str(self.worktree), 'rev-parse', 'HEAD'], text=True, capture_output=True, check=True).stdout.strip()
        subprocess.run(['git', '-C', str(self.project), 'merge', '--ff-only', head], capture_output=True, check=True)
        subprocess.run(['git', '-C', str(self.project), 'revert', '--no-edit', head], capture_output=True, check=True)
        report = json.loads(self.call('check', '1').stdout)
        self.assertTrue(report['commit_on_main'])
        self.assertFalse(report['changed_paths_match_main'])
        self.assertNotEqual(self.call('close', '1').returncode, 0)
        self.assertTrue(self.worktree.exists())

    def test_ignored_files_are_preserved_without_discard(self):
        (self.project / '.git' / 'info' / 'exclude').write_text('ignored-file\n')
        (self.worktree / 'ignored-file').write_text('private')
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
        self.assertNotEqual(self.call('close', '1').returncode, 0)
        self.assertTrue((self.worktree / 'ignored-file').exists())


if __name__ == '__main__':
    unittest.main()
