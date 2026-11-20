import argparse
from concurrent.futures import ThreadPoolExecutor
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
from threading import Event
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
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('HC_TERMINAL_ID', None)
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
        self.health = self.tool_patch(cli, 'check_server', return_value={'listeners': []})
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
        self.health.return_value = {'listeners': []}
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

    def fake_herdr(self):
        self.hc_calls = []
        self.terminals = {}
        listener = cli.listener
        self.tool_patch(cli.inbox, 'caller', return_value=cli.inbox.Agent('fixture-session', 'fixture-chef', 'fixture-space'))
        self.conn.execute("UPDATE projects SET space='fixture-space' WHERE id=1")
        self.conn.commit()

        def hc(session, *argv, **options):
            self.assertEqual(session, 'fixture-session')
            self.hc_calls.append(argv)
            if argv[0] == 'get':
                info = self.terminals.get(argv[1])
                if not info:
                    raise subprocess.CalledProcessError(3, argv)
                return subprocess.CompletedProcess(argv, 0, json.dumps(info))
            self.assertEqual(argv[0], 'run')
            self.assertIn('--no-notify', argv)
            self.assertIn('--no-parent', argv)
            labels = dict(argv[i + 1].split('=', 1) for i, arg in enumerate(argv) if arg == '--label')
            name = argv[argv.index('--name') + 1]
            address = 't' + str(len(self.terminals) + 1)
            info = dict(state='live', address=address, name=name, labels=labels)
            self.terminals[address] = self.terminals[name] = info
            self.assertEqual(labels['ltc.lavish.project'], '1')
            self.assertEqual(labels['ltc.lavish.member'], '1')
            self.assertEqual(labels['herdr.space'], 'fixture-space')
            self.assertEqual(argv[argv.index('--db') + 1], str(self.db))
            self.assertEqual(argv[-2:], ('--member', 'web'))
            self.assertEqual(argv[argv.index('--generation') + 1], labels['ltc.lavish.generation'])
            self.health.return_value = {'listeners': [
                {'key': self.session_key, 'label': 'ltc-' + labels['ltc.lavish.generation']}]}
            return subprocess.CompletedProcess(argv, 0, json.dumps({'address': address}))

        return self.tool_patch(listener, 'call_hc', side_effect=hc)

    def receive(self, armed):
        self.args.generation = armed['generation']
        with patch.dict(os.environ, HC_SESSION='fixture-session', HC_TERMINAL_ID=armed['terminal']):
            return self.action('_receive')

    def test_managed_batches_disconnect_final_delivery_and_idempotent_resume(self):
        self.fake_herdr()
        with patch.dict(os.environ, HC_TERMINAL_ID='fixture-chef'):
            armed = self.open()['receiver']
        self.assertEqual(armed['state'], 'listening')
        self.assertEqual(self.action('resume')['receiver'], armed)
        with self.assertRaisesRegex(ValueError, 'running receiver'):
            self.action('import')
        self.assertEqual(sum(argv[0] == 'run' for argv in self.hc_calls), 1)
        self.queue.extend([({'status': 'feedback'}, [{'prompt': 'first'}]),
                           ({'status': 'browser_disconnected'}, []),
                           ({'status': 'feedback', 'session_ended': True}, [{'prompt': 'second'}])])
        with patch.object(cli.time, 'sleep'):
            self.assertEqual(self.receive(armed)['status'], 'ended')
        self.terminals[armed['terminal']]['state'] = 'finished'
        self.assertEqual(self.action('end')['status'], 'ended')
        self.assertEqual(self.action('status')['receiver']['runtime'], 'finished')
        self.assertEqual([(t['project_id'], t['member_id']) for t in self.tasks()], [(1, 1), (1, 1)])
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.calls.count('stop'), 1)
        self.assertEqual(set(argv[0] for argv in self.hc_calls), {'run', 'get'})

    def test_restart_replays_receipt_before_rearming_poll(self):
        self.fake_herdr()
        self.open()
        first = self.action('resume')['receiver']
        self.queue.append(({'status': 'feedback'}, [{'prompt': 'retained'}]))
        self.conn.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON lavish_imports
                             BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.receive(first)
        receipt, = self.folder.glob('receipt-*.toon')
        self.assertIn(b'retained', receipt.read_bytes())
        self.assertEqual(self.tasks(), [])
        self.terminals[first['terminal']]['state'] = 'finished'
        self.health.return_value = {'listeners': []}
        self.assertIn('fixture failure', self.action('status')['receiver']['detail'])
        self.conn.execute('DROP TRIGGER fail_import')
        second = self.action('resume')['receiver']
        self.assertNotEqual(first['generation'], second['generation'])
        with self.assertRaisesRegex(ValueError, 'generation'):
            self.receive(first)
        self.queue.append(({'status': 'ended'}, []))
        self.receive(second)
        self.assertEqual(self.calls.count('poll'), 2)
        self.assertEqual(len(self.tasks()), 1)
        self.assertFalse(receipt.exists())

    def test_foreign_poll_terminal_and_generation_never_taken_over(self):
        self.fake_herdr()
        self.open()
        for key in (self.session_key, 'b' * 16):
            self.health.return_value = {'listeners': [{'key': key, 'label': 'other'}]}
            for cmd in ('resume', 'end'):
                with self.assertRaisesRegex(ValueError, 'listener'):
                    self.action(cmd)
        self.assertFalse(self.hc_calls)
        self.health.return_value = {'listeners': []}
        armed = self.action('resume')['receiver']
        with self.assertRaisesRegex(ValueError, 'registered Herdr terminal'):
            self.receive(dict(armed, terminal='foreign'))
        self.terminals[armed['terminal']]['labels']['ltc.lavish.member'] = '2'
        for cmd in ('end', 'resume'):
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.action(cmd)
        self.assertEqual(self.action('status')['receiver']['state'], 'unverified')
        self.assertNotIn('end', self.calls)
        self.assertNotIn('stop', self.calls)
        self.assertNotIn('poll', self.calls)

    def test_launch_failure_and_absent_readiness_are_visible(self):
        hc = self.fake_herdr()
        self.open()
        original = hc.side_effect
        hc.side_effect = subprocess.CalledProcessError(1, ['hc', 'run'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.action('resume')
        hc.side_effect = original
        self.assertEqual(self.action('status')['receiver']['state'], 'launch_error')
        self.health.side_effect = lambda env: {'listeners': []}
        with patch.object(cli.time, 'monotonic', side_effect=[0, 16]):
            with self.assertRaisesRegex(ValueError, 'Timed out'):
                self.action('resume')
        self.assertEqual(self.action('status')['receiver']['state'], 'starting')
        self.health.side_effect = None
        self.assertEqual(self.action('resume')['receiver']['state'], 'listening')

    def test_foreground_override_and_scoped_read_only_legacy_scan(self):
        self.args.foreground = True
        with patch.dict(os.environ, HC_TERMINAL_ID='human-shell'):
            self.assertEqual(self.open()['receiver']['state'], 'foreground_required')
        self.args.review = None
        self.conn.execute('DROP TABLE lavish_listeners')
        self.conn.execute('PRAGMA query_only=ON')
        results = self.action('status')
        self.assertEqual([r['review'] for r in results], [self.folder.name])
        self.assertEqual(results[0]['receiver']['state'], 'foreground_required')
        self.args.member = 'api'
        self.assertEqual(self.action('status'), [])
        self.conn.execute('PRAGMA query_only=OFF')

    def test_transient_poll_disconnect_retries_without_consuming_saved_output(self):
        self.fake_herdr()
        self.open()
        armed = self.action('resume')['receiver']
        count = 0

        def interrupted(argv, *, env, stdout, pass_fds):
            nonlocal count
            count += 1
            if count == 1:
                stdout.write(json.dumps({'code': 'SERVER_ERROR',
                    'error': 'Lavish Editor poll response was interrupted'}).encode())
                return subprocess.CompletedProcess(argv, 1)
            return self.poll(argv, env=env, stdout=stdout, pass_fds=pass_fds)

        self.queue.append(({'status': 'feedback', 'session_ended': True}, [{'prompt': 'saved once'}]))
        with patch.object(cli.subprocess, 'run', side_effect=interrupted), patch.object(cli.time, 'sleep') as sleep:
            self.receive(armed)
        sleep.assert_called_once_with(1)
        self.assertEqual(count, 2)
        self.assertEqual(len(self.tasks()), 1)

    def test_only_live_cook_for_selected_project_and_member_can_start(self):
        self.fixture_reports()
        self.conn.execute("UPDATE projects SET space='s' WHERE id=1")
        self.conn.execute("UPDATE cook_runs SET terminal='fixture-cook' WHERE id=20")
        self.conn.commit()
        token = self.conn.execute('SELECT token FROM registry_identity').fetchone()[0]
        info = {'address': 'fixture-cook', 'state': 'live', 'labels': {
            'herdr.space': 's', 'ltc.registry': token, 'ltc.member': '1',
            'ltc.project': '1', 'ltc.run': '20', 'ltc.task': '10'}}
        with patch.dict(os.environ, HC_TERMINAL_ID='fixture-cook', HC_SESSION='test', HC_SOCKET=''), patch.object(
                cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(info))):
            self.assertEqual(cli.inbox.caller(self.conn, 1, 1).session, 'test')
            for project, member in [(1, 2), (2, None)]:
                with self.assertRaisesRegex(ValueError, 'does not own'):
                    cli.inbox.caller(self.conn, project, member)
            info['labels']['ltc.registry'] = 'foreign'
            with patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(info))):
                with self.assertRaisesRegex(ValueError, 'does not own'):
                    cli.inbox.caller(self.conn, 1, 1)

    def managed_html(self):
        self.fake_herdr()
        self.args.command = 'html'
        self.args.file = self.root / 'sample.html'
        self.args.file.write_text('<h1>Sample</h1>')
        (self.root / 'private.txt').write_text('unrelated')
        with patch.dict(os.environ, HC_TERMINAL_ID='fixture-chef'):
            opened = self.open()
        self.assertEqual(opened['capture'], 'html')
        self.assertEqual(cli.artifact(self.folder).read_text(), self.args.file.read_text())
        self.assertEqual([p.name for p in cli.artifact(self.folder).parent.iterdir()], ['snapshot.html'])
        self.capture.assert_not_called()
        self.args.wait = 0
        return opened['receiver']

    def queue_op(self, command, batch=None):
        self.args.command, self.args.batch = command, batch
        return cli.inbox.operate(self.conn, self.args, *cli.owner(self.conn, self.args))

    def test_owner_inbox_repeats_lost_notice_and_final_batch_until_explicit_ack(self):
        armed = self.managed_html()
        self.queue.extend([({'status': 'feedback'}, [{'prompt': 'note one'}]),
                           ({'status': 'browser_disconnected'}, []),
                           ({'status': 'feedback', 'session_ended': True}, [{'prompt': 'final note'}])])
        self.receive(armed)
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.action('status')['delivery'], dict(channel='owner_inbox', stored=2,
                         id_offered=0, read_requested=0, acknowledged=0, awaiting_ack=2))
        # Drop the first ID-only response. A separate CLI invocation gets both IDs again.
        offered = self.queue_op('inbox')['batches']
        self.assertEqual(self.queue_op('inbox')['batches'], offered)
        first, final = [r['batch'] for r in offered]
        self.assertNotIn('note', json.dumps(offered))
        with self.assertRaisesRegex(ValueError, 'Read this batch'):
            self.queue_op('ack', final)
        with self.conn:
            self.conn.execute("UPDATE tasks SET description='Backlog edit' WHERE id=?", (final,))
        for batch, expected in [(first, 'note one'), (final, 'final note')]:
            self.assertEqual(self.queue_op('read', batch)['feedback']['annotations'], [{'prompt': expected}])
            self.assertEqual(self.queue_op('ack', batch), self.queue_op('ack', batch))
        self.assertEqual(self.queue_op('inbox'), {'batches': []})
        self.assertEqual(self.action('status')['delivery']['acknowledged'], 2)
        self.assertTrue(self.args.file.exists())
        self.assertFalse(self.folder.exists())

    def test_active_wait_wakes_after_commit_and_chef_supervision_reoffers_only_its_ids(self):
        from ltc_watch import read_inbox, schema
        armed = self.managed_html()
        self.queue.append(({'status': 'feedback', 'session_ended': True}, [{'prompt': 'private feedback'}]))
        waiting = Event()
        original = cli.inbox.pending

        def observe(*args, **kwargs):
            items = original(*args, **kwargs)
            if not items:
                waiting.set()
            return items

        def wait_in_own_connection():
            args = argparse.Namespace(command='inbox', wait=5)
            with sqlite3.connect(self.db) as connection:
                connection.row_factory = sqlite3.Row
                return cli.inbox.operate(connection, args, 1, 1)

        with patch.object(cli.inbox, 'pending', side_effect=observe), ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(wait_in_own_connection)
            self.assertTrue(waiting.wait(timeout=2))
            self.receive(armed)
            notice = future.result(timeout=5)['batches']
        self.assertEqual(len(notice), 1)
        self.assertEqual(set(notice[0]), {'batch', 'review', 'member'})
        schema(self.conn)
        self.conn.execute("INSERT INTO watch_targets VALUES (1, 'fixture-chef', 'fixture-session', 'fixture-space')")
        self.conn.commit()
        token = self.conn.execute('SELECT token FROM registry_identity').fetchone()[0]
        binding = ('fixture-chef', 'fixture-session', 'fixture-space', token)
        with patch.dict(os.environ, HC_TERMINAL_ID='fixture-chef'), patch('ltc_watch.verify_callback', return_value=binding):
            self.assertEqual(read_inbox(self.conn, 1, False)['lavish_batches'], notice)
            self.assertEqual(read_inbox(self.conn, 1, False)['lavish_batches'], notice)
            self.assertEqual(self.action('status')['delivery']['acknowledged'], 0)
            self.queue_op('read', notice[0]['batch'])
            self.queue_op('ack', notice[0]['batch'])
            self.assertEqual(read_inbox(self.conn, 1, False)['lavish_batches'], [])

    def test_verified_run_ancestry_limits_batch_read_and_ack(self):
        self.fixture_reports()
        armed = self.managed_html()
        with self.conn:
            self.conn.execute("UPDATE lavish_owners SET run_id=20, terminal='cook-a' WHERE review=?", (self.args.review,))
        self.queue.append(({'status': 'feedback', 'session_ended': True}, [{'prompt': 'cook only'}]))
        self.receive(armed)
        batch = self.conn.execute('SELECT id FROM lavish_batches').fetchone()[0]
        self.assertEqual(self.queue_op('inbox')['batches'], [])  # Verified Chef, Cook-owned batch.
        chef = cli.inbox.Agent('fixture-session', 'fixture-chef', 'fixture-space')
        self.assertEqual(cli.inbox.pending(self.conn, 1, None, chef, all_members=True), [])
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.queue_op('read', batch)
        agent = cli.inbox.Agent('fixture-session', 'cook-b', 'fixture-space', 21)
        with patch.object(cli.inbox, 'caller', return_value=agent):
            for cmd in ('ack', 'read'):
                with self.assertRaisesRegex(ValueError, 'not found'):
                    self.queue_op(cmd, batch)
        with self.conn:
            self.conn.execute("""INSERT INTO cook_runs(id,task_id,project_id,member_id,space,worktree,base,
                hc_session,name,profile,launch_state,resumed_from) SELECT 41,task_id,project_id,member_id,
                space,worktree,base,hc_session,name,profile,launch_state,id FROM cook_runs WHERE id=20""")
        resumed = cli.inbox.Agent('fixture-session', 'cook-resumed', 'fixture-space', 41)
        with patch.object(cli.inbox, 'caller', return_value=resumed):
            self.args.member = 'api'
            with self.assertRaisesRegex(ValueError, 'not found'):
                self.queue_op('read', batch)
            self.args.member = 'web'
            self.assertEqual(self.queue_op('read', batch)['batch'], batch)
            self.queue_op('ack', batch)
        saved = self.conn.execute('SELECT ack_run,ack_terminal FROM lavish_batches').fetchone()
        self.assertEqual(tuple(saved), (41, 'cook-resumed'))

    def test_queue_write_failure_rolls_back_task_and_replays_original_receipt(self):
        armed = self.managed_html()
        self.conn.execute("""CREATE TRIGGER fail_batch BEFORE INSERT ON lavish_batches
            BEGIN SELECT RAISE(ABORT,'simulated queue failure'); END""")
        self.queue.append(({'status': 'feedback', 'session_ended': True}, [{'prompt': 'survives restart'}]))
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'queue failure'):
            self.receive(armed)
        receipt, = self.folder.glob('receipt-*.toon')
        self.assertTrue(receipt.stat().st_size)
        for table in ('tasks', 'lavish_imports', 'lavish_batches'):
            self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.terminals[armed['terminal']]['state'] = 'finished'
        self.conn.execute('DROP TRIGGER fail_batch')
        restarted = self.action('resume')['receiver']
        self.health.return_value = {'listeners': []}  # Replay the terminal receipt without a new poll.
        self.receive(restarted)
        self.assertEqual(self.calls.count('poll'), 1)
        self.assertEqual(len(self.queue_op('inbox')['batches']), 1)

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
