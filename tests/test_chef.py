import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ChefTest(unittest.TestCase):
    def test_sync_routes_to_space_and_avoids_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            db = root / 'state.sqlite'
            subprocess.run([str(ROOT / 'bin/ltc-db'), '--db', str(db), 'project', 'add', 'app', str(project), '--space', 'sp1'], capture_output=True, check=True)
            app = root / 'herdr-app'
            app.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ['FAKE_STATE'])
if sys.argv[1] == 'ls':
    print(json.dumps({'spaces': [{'id':'sp1', 'machine':None, 'tabs':json.loads(state.read_text()) if state.exists() else []}]}))
else:
    args = sys.argv[2:]
    name = args[args.index('--name') + 1]
    state.write_text(json.dumps([{'id':'tab1', 'name':name}]))
    Path(os.environ['FAKE_ARGS']).write_text(json.dumps(args))
    print(json.dumps({'id':'tab1'}))
''')
            app.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], FAKE_STATE=str(root / 'tabs'), FAKE_ARGS=str(root / 'args'))
            cmd = [str(ROOT / 'bin/ltc-chef'), '--db', str(db)]
            first = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(first.stdout)[0]['status'], 'started')
            args = json.loads((root / 'args').read_text())
            self.assertEqual(args[args.index('--name') + 1], '👨‍🍳 Chef @ app')
            self.assertEqual(json.loads(args[args.index('--argv') + 1]), [
                'codex', '--no-daemon', '--approve-for-me',
                '-c', 'tui.fullscreen_transcript=true', '-c', 'tui.collapse_turn_details=true',
            ])
            self.assertIn('Registry: ' + str(db.resolve()), args[args.index('--task') + 1])
            self.assertEqual(args[args.index('--focus') + 1], 'false')
            self.assertEqual(args[args.index('--space') + 1], 'sp1')
            self.assertEqual(args[args.index('--folder') + 1], str(project.resolve()))
            second = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(second.stdout)[0]['status'], 'already_present')
            (root / 'tabs').write_text(json.dumps([{'id': 'legacy', 'name': 'Chef @ app'}]))
            legacy = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(legacy.stdout)[0], {'project': 'app', 'space': 'sp1', 'status': 'already_present', 'tabs': ['legacy']})
            (root / 'tabs').write_text('[]')
            subprocess.run(cmd + ['--use-codex-display-settings', 'app'], env=env, check=True, capture_output=True)
            args = json.loads((root / 'args').read_text())
            self.assertEqual(json.loads(args[args.index('--argv') + 1]), ['codex', '--no-daemon', '--approve-for-me'])

    def test_old_database_is_migrated(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'old.sqlite'
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, path TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                conn.execute("INSERT INTO projects (name,path) VALUES ('app','/tmp/app')")
            conn.close()
            result = subprocess.run([str(ROOT / 'bin/ltc-db'), '--db', str(db), 'project', 'update', 'app', '--space', 'sp1'], text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)['space'], 'sp1')


if __name__ == '__main__':
    unittest.main()
