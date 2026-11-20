from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from chef_fixture import bind_chef

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
from ltc_board import connect, lookup


class ReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / 'registry'
        self.env = dict(os.environ, LTC_DB=str(self.db), HC_SESSION='fixture', HC_TERMINAL_ID='chef',
                        PATH=str(self.root) + os.pathsep + os.environ['PATH'], REPORT_TEST=str(self.root))
        self.env.pop('HC_SOCKET', None)
        subprocess.run(['git', 'init', '-q', self.root], check=True)
        for args in [('project', 'add', 'app', str(self.root), '--space', 's'), ('task', 'add', 'app', 'Task')]:
            subprocess.run([BIN / 'ltc-db', *args], env=self.env, check=True, capture_output=True)
        bind_chef(self.env, 's')
        token = json.loads(self.env['TEST_CHEF'])['labels']['ltc.chef.registry']
        self.env['HC_TERMINAL_ID'] = 'cook'
        self.labels = {'ltc.registry': token, 'ltc.run': '1', 'ltc.task': '1', 'ltc.project': '1', 'herdr.space': 's'}
        self.env['TEST_COOK'] = json.dumps({'address': 'cook', 'state': 'live', 'labels': self.labels})
        with sqlite3.connect(self.db) as conn:
            conn.execute("""INSERT INTO cook_runs (project_id,task_id,space,worktree,base,terminal,hc_session,name,profile,launch_state)
                VALUES (1,1,'s','/fixture','base','cook','fixture','test','default','started')""")
        hc = self.root / 'hc'
        hc.write_text("""#!/usr/bin/env python3
import os, sys
from pathlib import Path
root = Path(os.environ['REPORT_TEST'])
with (root/'calls').open('a') as log: print(sys.argv[1], file=log)
assert sys.argv[1] == 'get'
print(os.environ['TEST_COOK' if sys.argv[2]=='cook' else 'TEST_CHEF'])
""")
        hc.chmod(0o755)

    def report(self, outcome='<result>', url='http://localhost:8080', *flags):
        return subprocess.run([BIN/'ltc-report', '--outcome', outcome, '--validation', '2 checks passed',
                               '--review-url', url, *flags], env=self.env, text=True, capture_output=True)

    def test_quiet_save_legacy_retry_and_render(self):
        args = ('<result>', 'http://localhost:8080', '--notify')
        first = self.report(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(json.loads(first.stdout)['notified'])
        self.assertTrue(json.loads(first.stdout)['saved'])
        with closing(connect(self.db)) as conn:
            board, revision = lookup(conn, '/projects/1')[1]
            self.assertIn('Cook report', board)
            detail = lookup(conn, '/projects/1/tasks/1')[1][0]
            self.assertIn('&lt;result&gt;', detail)
            self.assertIn('2 checks passed', detail)
            self.assertIn('href="http://localhost:8080"', detail)
            self.assertEqual(lookup(conn, '/projects/2/tasks/1')[0], 404)
            self.assertEqual(conn.execute('SELECT status FROM tasks').fetchone()[0], 'todo')
        for _ in range(2):
            retry = self.report(*args)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(retry.stdout)['report_id'], 1)
        self.assertEqual(self.report('Follow-up').returncode, 0)
        focused = subprocess.run([BIN/'ltc-db', 'task', 'show', '1', '--report', '1'], env=self.env,
                                 text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(focused.stdout)['outcome'], '<result>')
        self.assertNotIn('Follow-up', focused.stdout)
        wrong_task = subprocess.run([BIN/'ltc-db', 'task', 'show', '2', '--report', '1'], env=self.env,
                                    text=True, capture_output=True)
        self.assertIn('Report not found for this task', wrong_task.stderr)
        with closing(connect(self.db)) as conn:
            board, new_revision = lookup(conn, '/projects/1')[1]
            self.assertNotEqual(new_revision, revision)
            self.assertIn('Follow-up', board)
            self.assertEqual(conn.execute('SELECT count(*) FROM cook_reports').fetchone()[0], 2)

    def test_chef_receives_question_while_cook_is_live_once(self):
        question = 'Decision needed: Which account should own the preview?'
        saved = self.report(question)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        chef_env = dict(self.env, HC_TERMINAL_ID='chef')
        def inbox():
            return json.loads(subprocess.check_output([BIN/'ltc-watch', 'inbox', 'app'], env=chef_env))
        self.assertEqual(inbox(), {'events': [], 'reports': [{'id': 1, 'run_id': 1, 'task_id': 1}]})
        result = json.loads(subprocess.check_output([BIN/'ltc-db', 'task', 'show', '1'], env=chef_env))
        self.assertEqual(result['reports'][0]['outcome'], question)
        self.assertEqual(self.report(question).returncode, 0)
        self.assertEqual(inbox()['reports'], [])
        self.assertEqual(set((self.root/'calls').read_text().splitlines()), {'get'})

    def test_inbox_acknowledges_only_reports_for_the_bound_project(self):
        self.assertEqual(self.report('First project').returncode, 0)
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO projects (name,path,space) VALUES ('other','/other','s2')")
            conn.execute("INSERT INTO tasks (project_id,title) VALUES (2,'Other task')")
            conn.execute("""INSERT INTO cook_runs (project_id,task_id,space,worktree,base,terminal,hc_session,name,profile,launch_state)
                VALUES (2,2,'s2','/other','base','other-cook','fixture','test','default','started')""")
            conn.execute("""INSERT INTO cook_reports (run_id,outcome,validation,review_url)
                VALUES (2,'Private question','Pending','')""")
        chef_env = dict(self.env, HC_TERMINAL_ID='chef')
        cli = [BIN/'ltc-watch', 'inbox']
        reports = json.loads(subprocess.check_output(cli + ['app'], env=chef_env))['reports']
        self.assertEqual(reports, [{'id': 1, 'run_id': 1, 'task_id': 1}])
        other_chef = dict(self.env, HC_TERMINAL_ID='chef-2')
        bind_chef(other_chef, 's2')
        self.assertNotEqual(subprocess.run(cli + ['other'], env=chef_env, capture_output=True).returncode, 0)
        reports = json.loads(subprocess.check_output(cli + ['other'], env=other_chef))['reports']
        self.assertEqual(reports, [{'id': 2, 'run_id': 2, 'task_id': 2}])
        self.assertEqual(json.loads(subprocess.check_output(cli + ['other'], env=other_chef))['reports'], [])

    def test_invalid_fields_or_identity_never_save(self):
        self.assertNotEqual(self.report('x'*4001).returncode, 0)
        self.assertNotEqual(self.report('result', 'file:///tmp/file').returncode, 0)
        self.labels['ltc.project'] = 'other'
        self.env['TEST_COOK'] = json.dumps({'address': 'cook', 'state': 'live', 'labels': self.labels})
        self.assertNotEqual(self.report().returncode, 0)
        with closing(connect(self.db)) as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='cook_reports'").fetchone())
