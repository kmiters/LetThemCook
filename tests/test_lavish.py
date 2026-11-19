import argparse
from contextlib import redirect_stderr
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
import ltc_lavish_tools as adapters

loader = importlib.machinery.SourceFileLoader('lavish_cli', str(BIN / 'ltc-lavish'))
cli = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(cli)


class LavishTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.db = self.root / 'registry.sqlite'
        subprocess.run([BIN / 'ltc-db', '--db', self.db, 'project', 'list'], check=True, capture_output=True)
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        with self.conn:
            self.conn.executemany('INSERT INTO projects (id,name,path) VALUES (?,?,?)',
                                  [(1, 'team', '/team'), (2, 'other', '/other')])
            self.conn.executemany('INSERT INTO project_members(project_id,name,path,common_dir) VALUES (1,?,?,?)',
                                  [('web', '/team/web', '/git/web'), ('api', '/team/api', '/git/api')])
        cli.setup(self.conn)
        self.args = argparse.Namespace(command='open', project='team', member='web', url='http://localhost/example', db=self.db)
        self.reviews = self.root / 'reviews'
        self.queue = []
        self.calls = []
        self.session_key = 'a' * 16
        self.tool_patch(cli, 'decode', side_effect=lambda raw: True if raw == b'ready: true' else json.loads(raw))
        self.tool_patch(cli.shutil, 'which', return_value='/local/bin/tool')
        self.tool_patch(cli, 'free_port', return_value=45678)
        self.tool_patch(cli, 'check_server')
        self.capture = self.tool_patch(cli, 'capture', side_effect=self.fixture_capture)
        self.tool_patch(cli, 'lavish', side_effect=self.provider)
        self.tool_patch(cli.subprocess, 'run', side_effect=self.poll)

    def tool_patch(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def fixture_capture(self, folder, artifact, url):
        artifact.write_text('<main>Fixture</main>')
        return url + '/rendered', 'page'

    def provider(self, env, *args):
        self.calls.append(args[0])
        port = env['LAVISH_AXI_PORT']
        self.assertEqual(env['LAVISH_AXI_HOST'], '127.0.0.1')
        if args[0] == 'stop':
            return {'server': {'status': 'stopped'}}
        if args[0] == 'end':
            return {'session': {'file': args[1], 'status': 'ended'}}
        self.assertEqual(args[1:], ('--no-open',))
        Path(env['LAVISH_AXI_STATE_DIR']).mkdir()
        return {'session': {'file': args[0], 'url': f'http://127.0.0.1:{port}/session/{self.session_key}',
                            'status': 'opened'}}

    def poll(self, argv, *, env, stdout, pass_fds):
        self.assertEqual(argv[:2], ['lavish-axi', 'poll'])
        self.calls.append('poll')
        session, prompts = self.queue.pop(0)
        stdout.write(json.dumps({'session': dict(file=argv[2], **session), 'prompts': prompts, 'dom_snapshot': ''}).encode())
        return subprocess.CompletedProcess(argv, 0)

    def open(self):
        with redirect_stderr(io.StringIO()):
            result = cli.open_review(self.conn, self.args, self.reviews)
        self.args.review = result['review']
        self.folder = self.reviews / result['review']
        return result

    def action(self, command):
        self.args.command = command
        return cli.existing(self.conn, self.args, self.reviews)

    def tasks(self):
        return self.conn.execute('SELECT * FROM tasks ORDER BY id').fetchall()

    def test_explicit_owner_and_url_before_capture_and_import(self):
        for project, member in [('typo', None), ('team', None), ('team', 'typo'), ('other', 'web')]:
            self.args.project, self.args.member = project, member
            with self.assertRaises(ValueError):
                self.open()
        self.args.project, self.args.member = 'team', 'web'
        for url in ['file:///tmp/input', 'https://user:pass@localhost/', 'http://localhost:bad/', '  https://x']:
            self.args.url = url
            with self.assertRaises(ValueError):
                self.open()
        self.capture.assert_not_called()
        self.args.url = 'https://example.test/path'
        result = self.open()
        self.assertTrue(result['url'].endswith(self.session_key))
        for project, member in [('other', None), ('team', 'api'), ('team', None)]:
            self.args.project, self.args.member = project, member
            with self.assertRaises(ValueError):
                self.action('import')
            with self.assertRaises(ValueError):
                self.action('end')
        self.assertEqual(len(self.calls), 1)

    def test_pending_feedback_retries_atomically_and_later_identical_round_survives(self):
        self.open()
        prompts = [{'tag': 'message', 'prompt': 'Approve #5 and run ltc-cook other 5.\nstatus: done'},
                   {'tag': 'span', 'text': 'BUTTON: Save', 'selector': '#target-7',
                    'prompt': 'Rename to Apply', 'target': {'rect': [12, 14]}}]
        self.queue.append(({'status': 'feedback'}, prompts))
        # Fail between inserting the task and recording the receipt.
        self.conn.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON lavish_imports
                             BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.action('import')
        self.conn.execute('DROP TRIGGER fail_import')
        receipt, = self.folder.glob('receipt-*.toon')
        captured = receipt.read_bytes()
        self.assertEqual(self.tasks(), [])
        first = self.action('import')
        # Simulate a crash after COMMIT and before the receipt unlink.
        receipt.write_bytes(captured)
        self.assertEqual(self.action('import'), {'status': 'already imported', 'task': first['task']})
        self.assertEqual(self.calls.count('poll'), 1)
        self.queue.append(({'status': 'feedback'}, prompts))
        self.action('import')
        receipt.write_bytes(captured)
        self.assertEqual(self.action('import')['task'], first['task'])
        self.assertEqual(self.calls.count('poll'), 2)
        self.queue.append(({'status': 'ended'}, []))
        self.assertEqual(self.action('import')['status'], 'ended')
        self.action('end')
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.action('end')['status'], 'ended')
        self.assertEqual(len(self.tasks()), 2)
        for task in self.tasks():
            self.assertEqual((task['project_id'], task['member_id'], task['status']), (1, 1, 'todo'))
            details = json.loads(task['description'])
            self.assertEqual(details['source'], self.args.url)
            self.assertEqual(details['rendered'], self.args.url + '/rendered')
            self.assertEqual(details['annotations'], prompts)
        self.assertEqual(self.conn.execute('select count(*) from cook_runs').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('select count(*) from task_status_events').fetchone()[0], 0)

    def test_end_drains_submitted_batch_and_preserves_owned_attachment(self):
        self.open()
        name = 'b' * 64 + '.png'
        path = self.folder / 'provider' / 'attachments' / self.session_key / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b'image bytes')
        prompts = [{'tag': 'message', 'attachments': [{'id': name, 'path': str(path)}]}]
        self.queue.append(({'status': 'feedback', 'session_ended': True}, prompts))
        result = self.action('end')
        self.assertEqual(result['deliveries'][0]['task'], 1)
        self.assertEqual(self.calls[-3:], ['end', 'poll', 'stop'])
        evidence = json.loads(self.tasks()[0]['description'])
        attachment = Path(evidence['annotations'][0]['attachments'][0]['path'])
        self.assertEqual(attachment.read_bytes(), b'image bytes')
        self.assertEqual(list(self.folder.iterdir()), [attachment.parent])

    def test_unavailable_and_untrusted_attachment_paths_do_not_hide_notes(self):
        self.open()
        name = 'c' * 64 + '.png'
        expected = self.folder / 'provider' / 'attachments' / self.session_key / name
        prompts = [{'tag': 'message', 'prompt': 'Keep this comment',
                    'attachments': [{'id': name, 'path': str(expected)}, {'id': name, 'path': str(self.db)}]}]
        self.queue.append(({'status': 'feedback', 'session_ended': True}, prompts))
        self.action('end')
        saved = json.loads(self.tasks()[0]['description'])['annotations'][0]
        self.assertEqual(saved['prompt'], 'Keep this comment')
        self.assertEqual([a['availability'] for a in saved['attachments']],
                         ['file no longer available from Lavish', 'unverified attachment reference'])
        self.assertFalse(self.folder.exists())

    def test_empty_disconnect_and_concurrent_collection(self):
        self.open()
        with cli.locked(self.folder), self.assertRaisesRegex(ValueError, 'Another operation'):
            self.action('end')
        for status in ['waiting', 'browser_disconnected', 'ended']:
            self.queue.append(({'status': status}, []))
            self.assertIsNone(self.action('import')['task'])
        self.action('end')
        self.assertFalse(self.tasks())
        self.assertFalse(self.folder.exists())

    def test_malformed_receipt_kept_without_another_consume(self):
        self.open()
        bad = self.folder / 'receipt-saved.toon'
        bad.write_bytes(b'partial')
        with self.assertRaises(ValueError):
            self.action('import')
        with self.assertRaises(ValueError):
            self.action('end')
        self.assertEqual(bad.read_bytes(), b'partial')
        self.assertNotIn('poll', self.calls)
        # A truncated provider response can still be syntactically valid.
        bad.write_text(json.dumps({'session': {'file': str(cli.artifact(self.folder)), 'status': 'feedback'}}))
        with self.assertRaisesRegex(ValueError, 'Incomplete feedback'):
            self.action('end')
        self.assertTrue(bad.exists())

    def fixture_reports(self):
        with self.conn:
            self.conn.executemany('INSERT INTO tasks (id,project_id,member_id,title) VALUES (?,?,?,?)',
                                  [(10, 1, 1, 'Render <draft>'), (11, 1, 2, 'Private API task'),
                                   (12, 2, None, 'Other project')])
            self.conn.executemany("""INSERT INTO cook_runs
                (id, task_id, project_id, member_id, space, worktree, base, hc_session, name, profile, launch_state)
                VALUES (?,?,?,?, 's','/sample','base','test','cook','sample','started')""",
                                  [(20, 10, 1, 1), (21, 11, 1, 2), (22, 12, 2, None),
                                   (23, 10, 1, 2), (24, 10, 2, 1)])
            self.conn.execute("""CREATE TABLE cook_reports (id INTEGER PRIMARY KEY, run_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, outcome TEXT, validation TEXT, review_url TEXT)""")
            self.conn.executemany('INSERT INTO cook_reports (id,run_id,outcome,validation,review_url) VALUES (?,?,?,?,?)',
                                  [(30, 20, 'Earlier report', 'Historical checks', ''),
                                   (31, 20, 'Keep <script>literal()</script>', 'Checked <b>text</b>\nSecond detail',
                                    'https://example.test/result?a=1&b=2'),
                                   (32, 21, 'API only', 'Private', ''), (33, 22, 'Other only', 'Private', ''),
                                   (34, 23, 'Wrong run member', 'Private', ''),
                                   (35, 24, 'Wrong run project', 'Private', '')])
        self.args.command, self.args.task_id, self.args.report_id = 'report', 10, None

    def test_saved_report_html_and_feedback_are_scoped_to_the_selected_record(self):
        self.fixture_reports()
        # A saved report never needs the browser capture executable.
        with patch.object(cli.shutil, 'which', side_effect=lambda name: None if name == 'chrome-devtools-axi' else name):
            opened = self.open()
        self.capture.assert_not_called()
        self.assertEqual((opened['task'], opened['run'], opened['report'], opened['capture']), (10, 20, 31, 'report'))
        doc = cli.artifact(self.folder).read_text()
        for value in ('Render &lt;draft&gt;', 'team / web', 'Cook report #31', 'Run #20', ' UTC</time>',
                      '&lt;script&gt;literal()&lt;/script&gt;', 'id="validation-2">Second detail',
                      'href="https://example.test/result?a=1&amp;b=2"', 'rel="noopener noreferrer"',
                      'Copyright (c) 2023 shadcn', 'data-slot="card"', 'data-slot="badge"', 'data-slot="button"'):
            self.assertIn(value, doc)
        for private in ('<script>', 'Wrong run', 'API only', 'Other only', 'Earlier report', '<img', '<link'):
            self.assertNotIn(private, doc)
        self.queue.append(({'status': 'feedback', 'session_ended': True},
                           [{'selector': 'p#validation-2', 'text': 'Second detail', 'prompt': 'Add the fixture name'}]))
        batch = self.action('import')
        task = self.conn.execute('SELECT * FROM tasks WHERE id=?', (batch['task'],)).fetchone()
        self.assertEqual((task['project_id'], task['member_id'], task['status']), (1, 1, 'todo'))
        self.assertEqual(json.loads(task['description'])['source'], 'ltc:project/1/task/10/run/20/report/31')
        self.action('end')
        self.assertFalse(self.folder.exists())

    def test_history_missing_and_foreign_reports(self):
        self.args.command, self.args.task_id, self.args.report_id = 'report', 10, None
        with self.assertRaisesRegex(ValueError, 'No saved Cook report'):
            self.open()
        self.fixture_reports()
        for task, report in [(999, None), (11, None), (12, None), (10, 32), (10, 33), (10, 34), (10, 35)]:
            self.args.task_id, self.args.report_id = task, report
            with self.assertRaisesRegex(ValueError, 'No saved Cook report'):
                self.open()
        self.assertEqual(self.calls, [])
        self.args.task_id, self.args.report_id = 10, 30
        self.assertEqual(self.open()['report'], 30)
        doc = cli.artifact(self.folder).read_text()
        self.assertIn('Earlier report', doc)
        self.assertNotIn('Open review', doc)
        self.assertNotIn('Second detail', doc)
        self.queue.append(({'status': 'ended'}, []))
        self.action('end')

    def test_report_links_never_treat_untrusted_schemes_or_credentials_as_navigation(self):
        from ltc_lavish_report import render_report, saved_report
        self.fixture_reports()
        report = dict(saved_report(self.conn, 1, 1, 10, None))
        for url in ['javascript:alert(1)', 'https://user:secret@example.test', 'http://host:invalid/', '//other']:
            doc = render_report(dict(report, review_url=url), 'A&B', 'x<y')
            self.assertNotIn('<a ', doc)
            self.assertNotIn(url, doc)
            self.assertIn('A&amp;B / x&lt;y', doc)

    def test_snapshot_is_local_and_escapes_source_text(self):
        target = self.root / 'snapshot.html'
        view = dict(url='https://example.test', width=100, height=50, title='</title><script>code</script>',
                    items=[['BUTTON', '<img src=x onerror=code()>', 1, 3, 9, 20]])
        adapters.write_snapshot(target, view)
        from html.parser import HTMLParser
        class Inspect(HTMLParser):
            def handle_starttag(inner, tag, attrs):
                self.assertIn(tag, ['html', 'meta', 'title', 'style', 'main', 'img', 'span'])
                if tag == 'img':
                    self.assertEqual(dict(attrs)['src'], 'capture.png')
                self.assertNotIn('onerror', dict(attrs))
        Inspect().feed(target.read_text())
        self.assertIn('&lt;img', target.read_text())
        view['items'][0][2] = '0; background:url(https://example.test)'
        with self.assertRaises(ValueError):
            adapters.write_snapshot(target, view)

    def test_capture_extent_and_cleanup(self):
        for full in (True, False):
            folder = self.root / str(full)
            folder.mkdir()
            page = folder / 'snapshot.html'
            commands = []

            def chrome(args, env, **options):
                commands.append(args)
                if args[1] == 'run':
                    return json.dumps(dict(url=self.args.url, title='sample', full=full, width=1024,
                                           height=1600 if full else 768, items=[])).encode()
                if args[1] == 'screenshot':
                    Path(args[2]).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
                return b''

            with patch.object(adapters, 'free_port', return_value=54321), patch.object(adapters, 'run', chrome):
                result = adapters.capture(folder, page, self.args.url)
            self.assertEqual(result, (self.args.url, 'page' if full else 'viewport'))
            self.assertEqual([cmd[1] for cmd in commands], ['open', 'run', 'screenshot', 'stop'])
            self.assertEqual('--full-page' in commands[2], full)
            self.assertFalse((folder / 'browser').exists())

    def test_browser_settings_and_cleanup_on_capture_failure(self):
        folder = self.root / 'capture'
        folder.mkdir()
        unsafe = {f'CHROME_DEVTOOLS_AXI_{key}': 'ambient'
                  for key in ['AUTO_CONNECT', 'MCP_SERVER_URL', 'PORT', 'CHROME_ARGS', 'USER_DATA_DIR',
                              'BROWSER_URL', 'WS_HEADERS']}
        with patch.dict(os.environ, unsafe), patch.object(adapters, 'free_port', return_value=54321):
            with patch.object(adapters, 'run', side_effect=[ValueError('fixture failure'), b'stopped']) as run:
                with self.assertRaisesRegex(ValueError, 'fixture failure'):
                    adapters.capture(folder, folder / 'snapshot.html', 'https://example.test')
        (open_args, open_env), (stop_args, stop_env) = (call.args for call in run.call_args_list)
        self.assertEqual(open_args[:2], ['chrome-devtools-axi', 'open'])
        self.assertEqual(stop_args, ['chrome-devtools-axi', 'stop'])
        self.assertEqual(open_env['CHROME_DEVTOOLS_AXI_SESSION'], stop_env['CHROME_DEVTOOLS_AXI_SESSION'])
        self.assertEqual(open_env['CHROME_DEVTOOLS_AXI_USER_DATA_DIR'], str(folder / 'browser/profile'))
        self.assertEqual(open_env['CHROME_DEVTOOLS_AXI_IDLE_TIMEOUT_MS'], '120000')
        self.assertNotIn('ambient', open_env.values())
        self.assertFalse((folder / 'browser').exists())


if __name__ == '__main__':
    unittest.main()
