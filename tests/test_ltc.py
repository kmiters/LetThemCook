import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LTC = Path(__file__).resolve().parents[1] / 'bin' / 'ltc'


class LaunchTest(unittest.TestCase):
    def test_isolated_worktree_and_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            subprocess.run(['git', '-C', str(project), 'config', 'user.name', 'Test'], check=True)
            subprocess.run(['git', '-C', str(project), 'config', 'user.email', 'test@example.invalid'], check=True)
            (project / 'tracked').write_text('committed')
            subprocess.run(['git', '-C', str(project), 'add', 'tracked'], check=True)
            subprocess.run(['git', '-C', str(project), 'commit', '-qm', 'base'], check=True)
            (project / 'tracked').write_text('uncommitted')
            hc = root / 'hc'
            hc.write_text('#!/bin/sh\nprintf \'{"address":"t-test"}\\n\'\n')
            hc.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
            cmd = [str(LTC), '--project', str(project), '--space', 'sp1', '--name', 'task', '--profile', 'simple', '--task', 'Count files']
            result = subprocess.run(cmd, env=env, text=True, capture_output=True, check=True)
            report = json.loads(result.stdout)
            worktree = Path(report['worktree'])
            self.assertEqual((worktree / 'tracked').read_text(), 'committed')
            self.assertEqual((project / 'tracked').read_text(), 'uncommitted')
            self.assertTrue(report['uncommitted_changes_excluded'])
            self.assertEqual(report['herdr']['address'], 't-test')
            self.assertEqual(report['profile'], 'simple')
            status = subprocess.run(['git', '-C', str(project), 'status', '--porcelain'], text=True, capture_output=True, check=True).stdout
            self.assertNotIn('.letthemcook', status)
            self.assertNotEqual(subprocess.run(cmd, env=env, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
