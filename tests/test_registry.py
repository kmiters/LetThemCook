import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REGISTRY = Path(__file__).resolve().parents[1] / 'bin' / 'ltc-db'


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.db = self.root / 'state' / 'registry.sqlite3'

    def call(self, *args, ok=True):
        result = subprocess.run([str(REGISTRY), '--db', str(self.db), *args], text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def test_project_and_task_lifecycle(self):
        project = self.call('project', 'add', 'app', str(self.project), '--description', 'Opis')
        self.assertEqual(project['path'], str(self.project.resolve()))
        self.assertEqual(self.call('project', 'show', 'app')['description'], 'Opis')
        task = self.call('task', 'add', 'app', 'Napraw błąd', '--description', 'Kryteria')
        self.assertEqual(task['status'], 'todo')
        self.assertEqual(self.call('task', 'list', '--project', 'app')[0]['id'], task['id'])
        self.call('task', 'update', str(task['id']), '--status', 'blocked', '--description', 'Brak danych')
        self.assertEqual(self.call('task', 'show', str(task['id']))['description'], 'Brak danych')
        self.assertEqual(len(self.call('task', 'list', '--status', 'blocked')), 1)
        self.assertEqual(self.call('task', 'list', '--status', 'todo'), [])
        self.assertEqual(self.call('project', 'update', 'app', '--description', 'Nowy opis')['description'], 'Nowy opis')

    def test_invalid_inputs_do_not_create_records(self):
        self.assertIn('Project not found', self.call('task', 'add', 'missing', 'Task', ok=False))
        self.call('project', 'add', 'app', str(self.project))
        self.call('project', 'add', 'app', str(self.project), ok=False)
        self.call('task', 'add', 'app', '   ', ok=False)
        self.call('task', 'update', '999', '--status', 'done', ok=False)
        self.assertEqual(self.call('task', 'list'), [])
        self.assertEqual(len(self.call('project', 'list')), 1)

    def test_environment_override(self):
        env = dict(os.environ, LTC_DB=str(self.db))
        result = subprocess.run([str(REGISTRY), 'project', 'list'], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertTrue(self.db.exists())


if __name__ == '__main__':
    unittest.main()
