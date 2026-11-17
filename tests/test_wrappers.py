import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WrappersTest(unittest.TestCase):
    def test_project_registration_and_cook_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / 'repo with spaces'
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            subprocess.run(['git', '-C', str(project), 'config', 'user.name', 'Test'], check=True)
            subprocess.run(['git', '-C', str(project), 'config', 'user.email', 'test@example.invalid'], check=True)
            (project / 'file').write_text('original')
            subprocess.run(['git', '-C', str(project), 'add', 'file'], check=True)
            subprocess.run(['git', '-C', str(project), 'commit', '-qm', 'base'], check=True)
            app = root / 'herdr-app'
            app.write_text('''#!/usr/bin/env python3
import json, sys
if sys.argv[1] == 'ls': print(json.dumps({'spaces':[{'id':'sp1','machine':None,'tabs':[]}]}))
else: print(json.dumps({'id':'tab1'}))
''')
            app.chmod(0o755)
            hc = root / 'hc'
            hc.write_text('''#!/usr/bin/env python3
import json, os, sys
open(os.environ['HC_ARGS'], 'w').write(json.dumps(sys.argv[1:]))
print(json.dumps({'address':'t-cook'}))
''')
            hc.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], LTC_DB=str(root / 'registry.sqlite'), HC_TERMINAL_ID='t-chef', HC_ARGS=str(root / 'hc-args'))
            subprocess.run([str(ROOT / 'bin/ltc-project'), 'app', str(project), 'sp1'], env=env, text=True, capture_output=True, check=True)
            added = subprocess.run([str(ROOT / 'bin/ltc-db'), 'task', 'add', 'app', 'Task title', '--description', 'Acceptance criteria'], env=env, text=True, capture_output=True, check=True)
            task_id = str(json.loads(added.stdout)['id'])
            result = subprocess.run([str(ROOT / 'bin/ltc-cook'), 'app', task_id], env=env, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)['herdr']['address'], 't-cook')
            args = json.loads((root / 'hc-args').read_text())
            self.assertLess(args.index('--parent'), args.index('--'))
            self.assertEqual(args[args.index('--parent') + 1], 't-chef')
            self.assertIn('Acceptance criteria', args[-1])
            self.assertIn('hc send t-chef', args[-1])

    def test_cook_requires_chef_terminal(self):
        env = dict(os.environ)
        env.pop('HC_TERMINAL_ID', None)
        result = subprocess.run([str(ROOT / 'bin/ltc-cook'), 'app', '1'], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HC_TERMINAL_ID', result.stderr)


if __name__ == '__main__':
    unittest.main()
