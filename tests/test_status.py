import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StatusTest(unittest.TestCase):
    def test_status_reconciles_identity_and_handles_herdr_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'registry.sqlite'
            env = dict(os.environ, LTC_DB=str(db), PATH=str(root) + os.pathsep + os.environ['PATH'], HC_SOCKET='/wrong')
            for name in ('app', 'other'):
                path = root / name
                path.mkdir()
                subprocess.run(['git', 'init', '-q', str(path)], check=True)
                subprocess.run([str(ROOT / 'bin/ltc-db'), 'project', 'add', name, str(path), '--space', 'sp-' + name], env=env, capture_output=True, check=True)
                subprocess.run([str(ROOT / 'bin/ltc-db'), 'task', 'add', name, name + ' task'], env=env, capture_output=True, check=True)
            with sqlite3.connect(db) as conn:
                token = conn.execute('SELECT token FROM registry_identity').fetchone()[0]
                for i in range(1, 4):
                    conn.execute("INSERT INTO cook_runs (task_id,project_id,space,worktree,base,terminal,hc_session,name,profile,launch_state) VALUES (1,1,'sp-app',?, 'abc', ?, 'session-x', ?, 'default', 'started')", (f'/work{i}', f't{i}', f'task-{i}'))
                conn.execute("INSERT INTO cook_runs (task_id,project_id,space,worktree,base,terminal,hc_session,name,profile,launch_state) VALUES (2,2,'sp-other','/other','abc','t4','session-y','other','default','started')")
            hc = root / 'hc'
            hc.write_text('''#!/usr/bin/env python3
import json, os, sys
if os.environ.get('HC_SOCKET') or os.environ.get('HC_SESSION') != 'session-x': sys.exit(1)
t = sys.argv[-1]
if t == 't3': sys.exit(1)
labels = {'ltc.registry':os.environ['TOKEN'], 'ltc.run':t[1:], 'ltc.task':'1', 'ltc.project':'1', 'herdr.space':'sp-app'}
if t == 't2': labels['ltc.task'] = 'wrong'
print(json.dumps({'address':t,'state':'live','status':{'state':'working'},'labels':labels}))
''')
            hc.chmod(0o755)
            env['TOKEN'] = token
            cmd = [str(ROOT / 'bin/ltc-status'), 'app']
            output = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
            report = json.loads(output.stdout)
            self.assertEqual(report['project']['name'], 'app')
            self.assertEqual([t['title'] for t in report['tasks']], ['app task'])
            runs = report['runs']
            self.assertEqual(len(runs), 3)
            self.assertEqual(runs[0]['terminal_status'], {'state':'live', 'agent':'working'})
            self.assertEqual(runs[1]['terminal_status'], {'state':'unknown', 'reason':'identity_mismatch'})
            self.assertEqual(runs[2]['terminal_status'], {'state':'unknown', 'reason':'herdr_unavailable_or_terminal_missing'})
            self.assertEqual(runs[2]['worktree'], '/work3')

    def test_missing_registry_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'missing.sqlite'
            env = dict(os.environ, LTC_DB=str(db))
            result = subprocess.run([str(ROOT / 'bin/ltc-status'), 'app'], env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(db.exists())


if __name__ == '__main__':
    unittest.main()
