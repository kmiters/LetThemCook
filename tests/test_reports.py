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
import os, sqlite3, sys
from pathlib import Path
root = Path(os.environ['REPORT_TEST'])
if sys.argv[1] == 'send':
    with sqlite3.connect(os.environ['LTC_DB'], timeout=.1) as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert conn.execute('SELECT count(*) FROM cook_reports').fetchone()[0] == 1
    with (root/'sends').open('a') as log: print(sys.argv[2], file=log)
    sys.exit(1 if (root/'fail').exists() else 0)
print(os.environ['TEST_COOK' if sys.argv[2]=='cook' else 'TEST_CHEF'])
""")
        hc.chmod(0o755)

    def report(self, outcome='<result>', url='http://localhost:8080', *flags):
        return subprocess.run([BIN/'ltc-report', '--outcome', outcome, '--validation', '2 checks passed',
                               '--review-url', url, *flags], env=self.env, text=True, capture_output=True)

    def test_save_before_notify_retry_and_render(self):
        (self.root/'fail').touch()
        args = ('<result>', 'http://localhost:8080', '--notify')
        first = self.report(*args)
        self.assertNotEqual(first.returncode, 0)
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
        (self.root/'fail').unlink()
        for _ in range(2):
            retry = self.report(*args)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(retry.stdout)['report_id'], 1)
        self.assertEqual((self.root/'sends').read_text().splitlines(), ['chef', 'chef'])
        self.assertEqual(self.report('Follow-up').returncode, 0)
        with closing(connect(self.db)) as conn:
            board, new_revision = lookup(conn, '/projects/1')[1]
            self.assertNotEqual(new_revision, revision)
            self.assertIn('Follow-up', board)
            self.assertEqual(conn.execute('SELECT count(*) FROM cook_reports').fetchone()[0], 2)

    def test_invalid_fields_or_identity_never_save(self):
        self.assertNotEqual(self.report('x'*4001).returncode, 0)
        self.assertNotEqual(self.report('result', 'file:///tmp/file').returncode, 0)
        self.labels['ltc.project'] = 'other'
        self.env['TEST_COOK'] = json.dumps({'address': 'cook', 'state': 'live', 'labels': self.labels})
        self.assertNotEqual(self.report().returncode, 0)
        with closing(connect(self.db)) as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='cook_reports'").fetchone())
