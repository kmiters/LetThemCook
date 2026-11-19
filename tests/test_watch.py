from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / 'state.sqlite'
        project = self.root / 'repo'
        project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'], LTC_DB=str(self.db), HC_TERMINAL_ID='t-chef', HC_SESSION='core', FAKE_DIR=str(self.root))
        self.env.pop('HC_SOCKET', None)
        subprocess.run([str(ROOT / 'bin/ltc-db'), 'project', 'add', 'app', str(project), '--space', 'sp1'], env=self.env, capture_output=True, check=True)
        subprocess.run([str(ROOT / 'bin/ltc-db'), 'task', 'add', 'app', 'Task'], env=self.env, capture_output=True, check=True)
        with sqlite3.connect(self.db) as conn:
            self.token = conn.execute('SELECT token FROM registry_identity').fetchone()[0]
            conn.execute("INSERT INTO cook_runs (task_id,project_id,space,worktree,base,terminal,hc_session,name,profile,launch_state) VALUES (1,1,'sp1','/work','abc','t-cook','core','task-1','default','started')")
        chef = {'address':'t-chef', 'state':'live', 'labels':{'herdr.space':'sp1'}}
        cook = {'address':'t-cook', 'state':'live', 'status':{'state':'working'}, 'labels':{'herdr.space':'sp1','ltc.registry':self.token,'ltc.run':'1','ltc.task':'1','ltc.project':'1'}}
        (self.root / 'chef').write_text(json.dumps(chef))
        (self.root / 'cook').write_text(json.dumps(cook))
        hc = self.root / 'hc'
        hc.write_text('''#!/usr/bin/env python3
import json, os, sqlite3, sys
from pathlib import Path
root=Path(os.environ['FAKE_DIR']); args=sys.argv[1:]
if os.environ.get('HC_SESSION') != 'core' or os.environ.get('HC_SOCKET'): sys.exit(2)
with (root/'log').open('a') as f: f.write(json.dumps(args)+'\\n')
if args[0]=='get':
    if args[1]=='t-chef':
        if (root/'rebind').exists():
            with sqlite3.connect(os.environ['LTC_DB'], timeout=.1) as conn:
                conn.execute("UPDATE watch_targets SET chef='replacement'")
        print((root/'chef').read_text())
    elif args[1]=='t-cook': print((root/'cook').read_text())
    else: sys.exit(3)
elif args[0]=='label':
    p=root/'chef'; data=json.loads(p.read_text())
    for x in args[2:]:
        key, value=x.split('=', 1); data['labels'][key]=value
    p.write_text(json.dumps(data))
elif args[0]=='run': print(json.dumps({'address':'t-watch'}))
else: sys.exit(9)
''')
        hc.chmod(0o755)

    def command(self, *args):
        return subprocess.run([str(ROOT / 'bin/ltc-watch'), *args], env=self.env, text=True, capture_output=True)

    def scan(self):
        result = self.command('run', 'app', '--once')
        self.assertEqual(result.returncode, 0, result.stderr)

    def sends(self):
        return [x for x in (json.loads(line) for line in (self.root / 'log').read_text().splitlines()) if x[0] == 'send']

    def update_cook(self, state):
        p = self.root / 'cook'
        obj = json.loads(p.read_text())
        obj['status']['state'] = state
        p.write_text(json.dumps(obj))

    def inbox(self, *flags):
        response = self.command('inbox', 'app', *flags)
        self.assertEqual(response.returncode, 0, response.stderr)
        return json.loads(response.stdout)['events']

    def test_pull_observed_transitions_once_and_recover_history(self):
        self.assertEqual(self.command('start', 'app').returncode, 0)
        self.scan()
        self.assertEqual(self.inbox(), [])
        self.update_cook('blocked')
        self.scan()
        self.scan()
        event = {'id': 1, 'run_id': 1, 'task_id': 1, 'state': 'agent_blocked'}
        self.assertEqual(self.inbox('--recent'), [event])
        with ThreadPoolExecutor(max_workers=2) as pool:
            reads = list(pool.map(lambda _: self.inbox(), range(2)))
        self.assertCountEqual(reads, [[event], []])
        self.update_cook('working')
        self.scan()
        self.update_cook('blocked')
        self.scan()
        self.assertEqual(self.inbox(), [dict(event, id=2)])
        self.assertEqual(self.inbox('--recent'), [dict(event, id=2), event])
        self.assertEqual(self.sends(), [])

    def test_stale_start_is_unknown_and_recorded_once(self):
        self.assertEqual(self.command('start', 'app').returncode, 0)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE cook_runs SET launch_state='starting', terminal=NULL")
        self.scan()
        self.assertEqual(self.sends(), [])
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE cook_runs SET created_at=datetime('now','-6 minutes')")
        self.scan()
        self.scan()
        self.assertEqual([e['state'] for e in self.inbox()], ['stale_start_without_terminal'])
        self.assertEqual(self.sends(), [])
        status = subprocess.check_output([ROOT / 'bin/ltc-status', 'app'], env=self.env)
        run = json.loads(status)['runs'][0]
        self.assertEqual(run['launch_state'], 'starting')
        self.assertEqual(run['terminal_status'], {'state': 'unknown', 'reason': 'stale_start_without_terminal'})

    def test_relocation_retains_old_space_events_only_in_history(self):
        self.assertEqual(self.command('start', 'app').returncode, 0)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE projects SET space='new' WHERE id=1")
        self.update_cook('blocked')
        self.scan()
        self.assertEqual(self.sends(), [])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT delivered FROM watch_events').fetchall(), [(0,)])
        self.assertNotEqual(self.command('inbox', 'app').returncode, 0)
        (self.root / 'chef').write_text(json.dumps({
            'address': 't-chef', 'state': 'live', 'labels': {'herdr.space': 'new'}}))
        self.assertEqual(self.command('start', 'app').returncode, 0)
        self.scan()
        self.scan()
        self.assertEqual(self.inbox(), [])
        self.assertEqual([e['state'] for e in self.inbox('--recent')], ['agent_blocked'])

    def test_refuses_wrong_chef_identity(self):
        self.assertEqual(self.command('start', 'app').returncode, 0)
        p = self.root / 'chef'
        data = json.loads(p.read_text())
        data['labels']['ltc.chef.project'] = 'wrong'
        p.write_text(json.dumps(data))
        self.update_cook('done')
        self.scan()
        self.assertNotEqual(self.command('inbox', 'app').returncode, 0)
        self.assertEqual(self.sends(), [])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT delivered FROM watch_events').fetchone()[0], 0)

    def test_delivery_failure_does_not_hide_later_cook_result(self):
        self.assertEqual(self.command('start', 'app').returncode, 0)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE cook_runs SET launch_state='delivery_failed' WHERE id=1")
        self.scan()
        self.update_cook('done')
        self.scan()
        with sqlite3.connect(self.db) as conn:
            events = conn.execute('SELECT state FROM watch_events ORDER BY id').fetchall()
        self.assertEqual(events, [('launch_delivery_failed',), ('agent_done',)])

    def test_chef_rebound_during_get_cannot_claim(self):
        self.assertEqual(self.command('start', 'app').returncode, 0)
        self.update_cook('error')
        self.scan()
        (self.root / 'rebind').touch()
        refused = self.command('inbox', 'app')
        self.assertIn('binding changed', refused.stderr)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT chef FROM watch_targets').fetchone()[0], 'replacement')
            self.assertEqual(conn.execute('SELECT delivered FROM watch_events').fetchone()[0], 0)

    def test_inbox_filters_other_projects_and_superseded_runs(self):
        self.assertEqual(self.command('start', 'app').returncode, 0)
        self.update_cook('done')
        self.scan()
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO projects (name,path,space) VALUES ('other','/other','other')")
            conn.execute("INSERT INTO tasks (project_id,title) VALUES (2,'other')")
            conn.execute("""INSERT INTO cook_runs (task_id,project_id,space,worktree,base,hc_session,name,profile,launch_state)
                VALUES (2,2,'other','/other','abc','core','other','default','failed'),
                       (1,1,'sp1','/work','abc','core','resumed','default','failed')""")
            conn.execute("INSERT INTO watch_events (project_id,run_id,state) VALUES (2,2,'other_project')")
            conn.execute("INSERT INTO watch_events (project_id,run_id,state) VALUES (2,3,'wrong_project')")
        self.scan()
        self.assertEqual(self.inbox(), [{'id': 4, 'run_id': 3, 'task_id': 1, 'state': 'launch_failed'}])
        self.assertEqual([e['id'] for e in self.inbox('--recent')], [4, 1])
        self.assertNotEqual(self.command('inbox', 'other').returncode, 0)
        self.assertEqual(self.sends(), [])


if __name__ == '__main__':
    unittest.main()
