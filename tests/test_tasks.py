from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
from ltc_board import connect, lookup, select_project


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.words = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.words.append(data)


class BoardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.db = self.root / 'registry ?#%.sqlite'
        self.repo = self.root / 'app'
        self.env = dict(os.environ, LTC_DB=str(self.db))
        subprocess.run(['git', 'init', '-q', self.repo], check=True)
        self.registry('project', 'add', 'app', self.repo, '--space', 'space-one')

    def registry(self, *args):
        result = subprocess.run([BIN / 'ltc-db', *args], env=self.env, capture_output=True, check=True)
        return json.loads(result.stdout)

    def cli(self, *args, cwd=None, check=True):
        return subprocess.run([BIN / 'ltc-tasks', '--no-open', *args], cwd=cwd or self.repo,
                              env=self.env, text=True, capture_output=True, check=check, timeout=12)

    def test_selection_from_subdirectory_linked_worktree_and_ambiguity(self):
        subprocess.run(['git', '-C', self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '--allow-empty', '-qm', 'base'], check=True)
        linked = self.root / 'linked'
        subprocess.run(['git', '-C', self.repo, 'worktree', 'add', '-q', '--detach', linked], check=True)
        sub = linked / 'sub'
        sub.mkdir()
        with closing(connect(self.db)) as conn:
            self.assertEqual(select_project(conn, None, sub)['name'], 'app')
            self.assertIsNone(select_project(conn, None, self.root))
        self.registry('project', 'add', 'other', linked)
        extra = self.root / 'third'
        subprocess.run(['git', '-C', self.repo, 'worktree', 'add', '-q', '--detach', extra], check=True)
        with closing(connect(self.db)) as conn:
            self.assertEqual(select_project(conn, None, sub)['name'], 'other')
            self.assertIsNone(select_project(conn, None, extra))
            self.assertEqual(select_project(conn, 'app', extra)['name'], 'app')
        self.assertIn('Unknown project', self.cli('absent', check=False).stderr)

    def test_workspace_lookup_uses_shared_readonly_resolver_when_available(self):
        workspace = types.SimpleNamespace(matching_project_ids=lambda conn, cwd: [1])
        with patch.dict(sys.modules, ltc_workspace=workspace), closing(connect(self.db)) as conn:
            self.assertEqual(select_project(conn, None, self.root)['name'], 'app')

    def test_foreground_open_and_stable_url_after_restart(self):
        marker = self.root / 'opened'
        browser = self.root / 'test-browser'
        browser.write_text('#!/bin/sh\nprintf "%s" "$1" > "$OPENED_URL"\n')
        browser.chmod(0o755)
        env = dict(self.env, BROWSER=str(browser) + ' %s', OPENED_URL=str(marker))

        def foreground():
            process = subprocess.Popen([BIN / 'ltc-tasks', '--serve', 'app'], cwd=self.repo, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(process.stderr.close)
            self.addCleanup(process.stdout.close)
            self.addCleanup(process.wait, 5)
            self.addCleanup(process.terminate)
            return process, process.stdout.readline().strip()

        process, url = foreground()
        self.assertRegex(url, r'^http://127\.0\.0\.1:\d+/projects/1$')
        opened = subprocess.run([BIN / 'ltc-tasks'], env=env, cwd=self.repo,
                                text=True, capture_output=True, check=True, timeout=12)
        self.assertEqual(opened.stdout.strip(), url)
        self.assertEqual(marker.read_text(), url)
        self.assertIn(f'PID {process.pid}.', opened.stderr)
        process.terminate()
        process.wait(timeout=5)
        _, restored = foreground()
        self.assertEqual(restored, url)

    def test_missing_registry_and_legacy_registry_stay_unmodified(self):
        missing = self.root / 'missing'
        self.assertNotEqual(self.cli('--db', missing, check=False).returncode, 0)
        self.assertFalse(missing.exists())
        self.registry('task', 'add', 'app', 'Legacy', '--description', 'Original notes')
        self.registry('task', 'add', 'app', 'Cancelled legacy task', '--description', 'Cancelled notes')
        self.registry('task', 'update', '2', '--status', 'cancelled')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DROP TABLE task_briefs')
            conn.commit()
        before = hashlib.sha256(self.db.read_bytes()).digest()
        with closing(connect(self.db)) as conn:
            status, (board, _) = lookup(conn, '/projects/1')
            self.assertEqual(status, 200)
            self.assertIn('Original notes', board)
            self.assertNotIn('class="lane cancelled"', board)
            self.assertNotIn('href="/projects/1/tasks/2"', board)
            self.assertIn('class="task-total" title="Registered tasks">2<', board)
            self.assertIn('Original notes', lookup(conn, '/projects/1/tasks/1')[1][0])

            status, (detail, _) = lookup(conn, '/projects/1/tasks/2')
            self.assertEqual(status, 200)
            self.assertIn('Cancelled notes', detail)
            self.assertIn('class="status cancelled"><i class="dot"></i>Cancelled', detail)
            with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                conn.execute("UPDATE tasks SET status = 'done'")
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).digest())

    def test_isolation_escaping_and_stored_brief(self):
        unsafe = '<custom-element data-x="&">literal text</custom-element>'
        self.registry('project', 'update', 'app', '--description', unsafe)
        self.registry('task', 'add', 'app', unsafe, '--intent', unsafe, '--context', 'Background',
                      '--scope', 'Bounds', '--acceptance', 'Criteria', '--checks', 'Checks', '--description', 'Notes')
        repo2 = self.root / 'second'
        subprocess.run(['git', 'init', '-q', repo2], check=True)
        self.registry('project', 'add', 'second', repo2)
        self.registry('task', 'add', 'second', 'Distinct private task')
        with closing(connect(self.db)) as conn:
            for path in ('/', '/projects/1', '/projects/1/tasks/1'):
                status, (body, _) = lookup(conn, path)
                self.assertEqual(status, 200)
                parser = TextParser()
                parser.feed(body)
                self.assertNotIn('custom-element', parser.tags)
                self.assertIn(unsafe, ''.join(parser.words))
                self.assertNotIn('Distinct private task', body)
            self.assertTrue(all(s in body for s in ('Background', 'Bounds', 'Criteria', 'Checks', 'Notes')))
            self.assertEqual(lookup(conn, '/projects/1/tasks/2')[0], 404)

    def test_registry_status_changes_move_the_existing_ticket(self):
        self.registry('task', 'add', 'app', 'A single task')
        for status in ('todo', 'in_progress', 'blocked', 'done'):
            with self.subTest(status=status):
                self.registry('task', 'update', '1', '--status', status)
                with closing(connect(self.db)) as conn:
                    html = lookup(conn, '/projects/1')[1][0]
                lane = html.split(f'<section class="lane {status}">', 1)[1].split('</section>', 1)[0]
                self.assertIn('<span class="count">1</span>', lane)
                self.assertIn('href="/projects/1/tasks/1"', lane)
                self.assertEqual(html.count('class="ticket"'), 1)

    def test_project_navigation_on_overview_board_and_detail(self):
        task = self.registry('task', 'add', 'app', 'A single task')
        task_path = f"/projects/1/tasks/{task['id']}"
        with closing(connect(self.db)) as conn:
            _, revision_before = lookup(conn, task_path)[1]

        repo2 = self.root / 'second'
        subprocess.run(['git', 'init', '-q', repo2], check=True)
        project = self.registry('project', 'add', 'Alpha & Beta', repo2)
        other_path = f"/projects/{project['id']}"
        paths = (('/', '/', 'page'), ('/projects/1', '/projects/1', 'page'),
                 (task_path, '/projects/1', 'location'))
        with closing(connect(self.db)) as conn:
            for path, active, current in paths:
                with self.subTest(path=path):
                    html, revision = lookup(conn, path)[1]
                    nav = html.split('<nav ', 1)[1].split('</nav>', 1)[0]
                    self.assertEqual(re.findall(r'<a href="([^"]+)"', nav),
                                     ['/', other_path, '/projects/1'])
                    self.assertIn('Alpha &amp; Beta</a>', nav)
                    self.assertEqual(nav.count('aria-current='), 1)
                    self.assertIn(f'href="{active}" aria-current="{current}"', nav)
                    if path == task_path:
                        self.assertIn(f'<span class="view-task">#{task["id"]}</span>', nav)
                        self.assertNotEqual(revision_before, revision)

    def test_concurrent_launch_http_refresh_reuse_and_collision(self):
        # Real CLI, including the detached listener. Stop just this test's process.
        with closing(socket.socket()) as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        def launch():
            return self.cli('--port', str(port))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(launch) for _ in range(2)]
            first = futures[0].result()
            pid = int(re.search(r'Local board PID (\d+)', first.stderr)[1])
            self.addCleanup(os.kill, pid, signal.SIGTERM)
            second = futures[1].result()
        self.assertIn(f'PID {pid}.', second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(urlsplit(first.stdout.strip()).path, '/projects/1')

        def request(path, method='GET', headers=None):
            with closing(http.client.HTTPConnection('127.0.0.1', port, timeout=3)) as client:
                client.request(method, path, headers=headers or {})
                response = client.getresponse()
                return response.status, dict(response.getheaders()), response.read().decode()

        status, headers, body = request('/projects/1')
        self.assertEqual(status, 200)
        self.assertEqual(body.count('No tasks here'), 4)
        self.assertIn('frame-ancestors', headers['Content-Security-Policy'])
        self.assertEqual(request('/projects/1', 'HEAD', {'If-None-Match': headers['ETag']})[0], 304)
        self.registry('task', 'add', 'app', 'Fresh change')
        self.assertIn('Fresh change', request('/projects/1')[2])
        self.assertNotEqual(headers['ETag'], request('/projects/1', 'HEAD')[1]['ETag'])
        self.assertEqual(request('/projects/1', headers={'Host': 'elsewhere.invalid'})[0], 403)
        self.assertEqual(request('/projects/1', 'POST')[0], 501)
        self.assertEqual(request('/projects/1/tasks/999')[0], 404)
        self.assertEqual(self.cli('--port', str(port), cwd=self.root).stdout.strip(), f'http://127.0.0.1:{port}/')
        self.assertIn('/projects/1', self.cli('app', '--port', str(port), cwd=self.root).stdout)
        alternate = self.root / 'other.sqlite'
        with closing(sqlite3.connect(alternate)) as dest, closing(connect(self.db)) as src:
            src.backup(dest)
        conflict = self.cli('--db', alternate, '--port', str(port), check=False)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn('occupied', conflict.stderr)
        self.db.rename(self.root / 'moved.sqlite')
        self.assertEqual(request('/projects/1')[0], 503)
        self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
