import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['git', '-C', str(self.project), 'config', 'user.email', 'test@example.invalid'], check=True)
        (self.project / 'file').write_text('committed')
        subprocess.run(['git', '-C', str(self.project), 'add', 'file'], check=True)
        subprocess.run(['git', '-C', str(self.project), 'commit', '-qm', 'base'], check=True)
        self.worktree = self.root / 'worktree'
        subprocess.run(['git', '-C', str(self.project), 'worktree', 'add', '--detach', str(self.worktree)], capture_output=True, check=True)
        (self.worktree / 'file').write_text('unfinished work')
        self.db = self.root / 'state.sqlite'
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'], LTC_DB=str(self.db), HC_TERMINAL_ID='t-chef', HC_SESSION='session-x', TERMINAL_INFO=str(self.root / 'terminal.json'), HC_LOG=str(self.root / 'log'))
        self.env.pop('HC_SOCKET', None)
        subprocess.run([str(ROOT / 'bin/ltc-db'), 'project', 'add', 'app', str(self.project), '--space', 'sp1'], env=self.env, capture_output=True, check=True)
        subprocess.run([str(ROOT / 'bin/ltc-db'), 'task', 'add', 'app', 'Test task', '--description', 'Preserve requirements', '--intent', 'User wording', '--acceptance', 'Pass criteria'], env=self.env, capture_output=True, check=True)
        with sqlite3.connect(self.db) as conn:
            token = conn.execute('SELECT token FROM registry_identity').fetchone()[0]
            conn.execute("INSERT INTO cook_runs (task_id,project_id,space,worktree,base,terminal,hc_session,name,profile,launch_state) VALUES (1,1,'sp1',?,'abc','t-old','session-x','task-1','default','started')", (str(self.worktree),))
        self.info = {'address':'t-old', 'state':'finished', 'labels':{'ltc.registry':token,'ltc.run':'1','ltc.task':'1','ltc.project':'1','herdr.space':'sp1'}}
        self.info_path = self.root / 'terminal.json'
        self.write_info()
        hc = self.root / 'hc'
        hc.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ['HC_LOG'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'get': print(Path(os.environ['TERMINAL_INFO']).read_text())
else: print(json.dumps({'address':'t-new', 'first_message':{'sent':True}}))
''')
        hc.chmod(0o755)

    def write_info(self):
        self.info_path.write_text(json.dumps(self.info))

    def call(self):
        return subprocess.run([str(ROOT / 'bin/ltc-resume'), '1'], env=self.env, text=True, capture_output=True)

    def test_resumes_same_worktree_and_preserves_changes(self):
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((payload['previous_run_id'], payload['run_id'], payload['worktree']), (1, 2, str(self.worktree)))
        self.assertEqual((self.worktree / 'file').read_text(), 'unfinished work')
        calls = [json.loads(line) for line in (self.root / 'log').read_text().splitlines()]
        self.assertEqual([c[0] for c in calls], ['get', 'run'])
        codex_args = calls[1][calls[1].index('--') + 1:-1]
        self.assertIn('--approve-for-me', codex_args)
        self.assertNotIn('--ask-for-approval', codex_args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', codex_args)
        self.assertEqual(calls[1][calls[1].index('--cwd') + 1], str(self.worktree))
        self.assertRegex(calls[1][calls[1].index('--name') + 1], r'^test-task-[a-f0-9]{8}$')
        self.assertIn('ltc.run=2', calls[1])
        self.assertIn('Preserve requirements', calls[1][-1])
        self.assertIn('## User request\nUser wording', calls[1][-1])
        self.assertIn('## Acceptance criteria\nPass criteria', calls[1][-1])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT terminal,launch_state FROM cook_runs WHERE id=2').fetchone(), ('t-new', 'started'))
        again = self.call()
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('newer run', again.stderr)

    def test_refuses_live_mismatched_and_missing_terminal(self):
        for state, task in [('live', '1'), ('finished', 'wrong')]:
            self.info['state'] = state
            self.info['labels']['ltc.task'] = task
            self.write_info()
            self.assertNotEqual(self.call().returncode, 0)
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE cook_runs SET terminal=NULL WHERE id=1')
        self.assertNotEqual(self.call().returncode, 0)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cook_runs').fetchone()[0], 1)
        self.assertEqual((self.worktree / 'file').read_text(), 'unfinished work')

    def test_refuses_worktree_from_different_project(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE cook_runs SET worktree=? WHERE id=1', (str(self.project),))
        self.assertNotEqual(self.call().returncode, 0)


if __name__ == '__main__':
    unittest.main()
