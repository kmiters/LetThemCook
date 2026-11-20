from argparse import Namespace
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from threading import Event
import unittest
from unittest.mock import patch

import test_watch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import ltc_wait
from ltc_watch import observe


class WaitTest(unittest.TestCase):
    setUp = test_watch.WatchTest.setUp
    command = test_watch.WatchTest.command
    scan = test_watch.WatchTest.scan
    update_cook = test_watch.WatchTest.update_cook

    def start(self):
        result = self.command('start', 'app')
        self.assertEqual(result.returncode, 0, result.stderr)

    def wait(self, *args, ok=True):
        result = subprocess.run([ROOT / 'bin/ltc-wait', 'app', *args], env=self.env,
                                text=True, capture_output=True, timeout=5)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    @contextmanager
    def watcher(self):
        suffix = hashlib.sha256((str(self.db) + ':1').encode()).hexdigest()[:16]
        with (self.root / ('.ltc-watch-' + suffix + '.lock')).open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def report(self):
        result = subprocess.run([ROOT / 'bin/ltc-report', '--outcome', 'Decision needed: ' + 'a' * 3900,
                                 '--validation', 'fixture'], env=dict(self.env, HC_TERMINAL_ID='t-cook'),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_polls_blocked_report_resume_and_end(self):
        self.start()
        self.update_cook('blocked')
        self.scan()
        with self.watcher():
            blocked = self.wait('--check')
        self.assertEqual(blocked['updates'], [{'ref': 'event:1', 'run_id': 1, 'task_id': 1,
                                               'state': 'agent_blocked'}])
        report_time = 150
        elapsed = 0
        empty_polls = 0

        def sleep(seconds):
            nonlocal elapsed, empty_polls
            elapsed += seconds
            empty_polls += 1
            if elapsed == report_time:
                self.report()

        with self.watcher(), patch.dict(os.environ, self.env), sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            result = ltc_wait.wait(conn, self.db, 1, Namespace(ack=[('event', 1)], timeout=0, check=False),
                                   now=lambda: elapsed, sleep=sleep)
        self.assertEqual((elapsed, empty_polls), (report_time, 75))
        self.assertEqual(result['updates'], [{'ref': 'report:1', 'run_id': 1, 'task_id': 1, 'state': 'report'}])
        # Advance the same blocked fixture via the previous active-supervision cycle.
        hc = self.root / 'hc'
        hc.write_text(hc.read_text().replace('else: sys.exit(9)', "elif args[0]=='wait': sys.exit(124)\nelse: sys.exit(9)"))
        with sqlite3.connect(self.db) as conn:
            conn.execute('DELETE FROM cook_reports')
        old_calls = 0
        for old_elapsed in range(30, report_time + 1, 30):
            wait = subprocess.run(['hc', 'wait', '--children', 'any', '--timeout', '30'],
                                  env=self.env, capture_output=True)
            self.assertEqual(wait.returncode, 124)
            if old_elapsed == report_time:
                self.report()
            inbox = self.command('inbox', 'app')
            self.assertEqual(inbox.returncode, 0, inbox.stderr)
            self.assertEqual(len(json.loads(inbox.stdout)['reports']), int(old_elapsed == report_time))
            old_calls += 2
        self.assertEqual(old_calls, 10)

        self.update_cook('working')
        self.scan()
        self.assertEqual(self.wait()['updates'], [{'ref': 'live:1', 'run_id': 1, 'task_id': 1, 'state': 'live'}])
        self.update_cook('done')
        self.scan()
        self.assertEqual(self.wait('--ack', 'live:1')['updates'][0]['state'], 'agent_done')
        elapsed = 0
        with self.watcher(), patch.dict(os.environ, self.env), sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row

            def advance(seconds):
                nonlocal elapsed
                elapsed += seconds

            idle = ltc_wait.wait(conn, self.db, 1, Namespace(ack=[('event', 2)], timeout=0, check=False),
                                 now=lambda: elapsed, sleep=advance)
            self.assertEqual((idle, elapsed), ({'status': 'idle'}, ltc_wait.IDLE_GRACE))
        self.assertEqual(self.wait(), {'status': 'watcher_unavailable'})

    def test_scoped_batch_and_recovery_after_chef_replacement(self):
        self.start()
        self.report()
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO projects (name,path,space) VALUES ('elsewhere','/elsewhere','sp2')")
            conn.execute("INSERT INTO tasks (project_id,title) VALUES (2,'elsewhere')")
            conn.execute("""INSERT INTO cook_runs (task_id,project_id,space,worktree,base,hc_session,name,profile,launch_state)
                 VALUES (1,1,'sp1','/work','abc','core','retry','default','failed'),
                        (2,2,'sp2','/other','abc','core','other','default','failed')""")
            conn.execute("INSERT INTO cook_reports (run_id,outcome,validation,review_url) VALUES (3,'secret','test','')")
            conn.executemany("INSERT INTO cook_reports (run_id,outcome,validation,review_url) VALUES (2,?,'test','')",
                             [(f'report {i}',) for i in range(12)])
        first = self.wait()
        self.assertEqual(len(first['updates']), 10)
        self.assertEqual(first['updates'][0]['run_id'], 1)
        self.assertNotIn('secret', json.dumps(first))
        original_env = self.env
        self.env = dict(self.env, HC_SESSION='elsewhere')
        self.assertIn('session', self.wait('--ack', 'report:1', ok=False))
        self.env = dict(original_env, HC_SOCKET='/other.sock')
        self.assertIn('override', self.wait(ok=False))
        self.env = original_env
        self.assertIn('project and Space', self.wait('--ack', 'report:1', '--ack', 'report:2', ok=False))
        self.assertEqual(self.wait(), first)  # Both ack changes rolled back.
        hc = self.root / 'hc'
        hc.write_text(hc.read_text().replace("args[1]=='t-chef'", "args[1] in ('t-chef', 't-new')"))
        (self.root / 'chef').write_text(json.dumps({'address': 't-new', 'state': 'live', 'labels': {'herdr.space': 'sp1'}}))
        self.env = dict(original_env, HC_TERMINAL_ID='t-new')
        self.start()
        self.assertEqual(self.wait(), first)
        args = [arg for entry in first['updates'] for arg in ('--ack', entry['ref'])]
        remaining = self.wait('--check', *args)
        self.assertEqual(len(remaining['updates']), 3)
        self.assertTrue(all(row['run_id'] == 2 for row in remaining['updates']))
        self.assertEqual(self.wait('--check', *args), remaining)

    def test_follow_up_can_wake_done_cook_before_idle_and_unknown_is_explicit(self):
        self.start()
        self.update_cook('done')
        self.scan()
        with self.watcher(), patch.dict(os.environ, self.env), sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            elapsed = 0

            def sleep(seconds):
                nonlocal elapsed
                elapsed += seconds
                if elapsed == 6:
                    self.update_cook('working')
                    observe(conn, 1, self.token)

            args = Namespace(ack=[('event', 1)], timeout=5 * ltc_wait.IDLE_GRACE, check=False)
            result = ltc_wait.wait(conn, self.db, 1, args, now=lambda: elapsed, sleep=sleep)
            self.assertEqual((elapsed, result['updates'][0]['ref']), (6, 'live:1'))
        (self.root / 'cook').unlink()
        self.scan()
        self.assertEqual(self.wait()['updates'][0]['state'], 'unknown:herdr_unavailable_or_terminal_missing')
        with self.watcher():
            result = self.wait('--ack', 'live:1', '--ack', 'event:2', '--check')
        self.assertEqual(result, {'status': 'unverified'})

    @unittest.skipUnless(ltc_wait.lavish, 'managed Lavish inbox is optional')
    def test_managed_feedback_wakes_only_bound_chef_and_survives_rebind(self):
        from ltc_lavish_listener import setup_listener

        self.start()
        lavish = ltc_wait.lavish
        with self.watcher(), patch.dict(os.environ, self.env), sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TABLE lavish_reviews(id TEXT PRIMARY KEY, project_id, member_id, phase)')
            conn.execute("INSERT INTO lavish_reviews VALUES ('review',1,NULL,'open'), ('cook-review',1,NULL,'open')")
            lavish.setup(conn)
            setup_listener(conn)
            lavish.bind(conn, 'review', lavish.Agent('core', 't-chef', 'sp1'))
            lavish.bind(conn, 'cook-review', lavish.Agent('core', 't-cook', 'sp1', 1))
            conn.execute("INSERT INTO lavish_listeners VALUES ('review','gen','core','listener','receiving',NULL)")
            conn.execute("UPDATE tasks SET status='done' WHERE id=1")
            conn.execute("INSERT INTO tasks(project_id,title) VALUES (1,'Chef batch'), (1,'Cook batch')")
            lavish.enqueue(conn, 'cook-review', 3, '["private Cook feedback"]')
            conn.commit()
            elapsed = 0

            def sleep(seconds):
                nonlocal elapsed
                elapsed += seconds
                if elapsed == 6:
                    with conn:
                        lavish.enqueue(conn, 'review', 2, '["private Chef feedback"]')
                        conn.execute("UPDATE lavish_reviews SET phase='drained' WHERE id='review'")

            result = ltc_wait.wait(conn, self.db, 1, Namespace(ack=[], timeout=10, check=False),
                                   now=lambda: elapsed, sleep=sleep)
            self.assertEqual(elapsed, 6)
            expected = [{'batch': 2, 'review': 'review', 'member': None}]
            self.assertEqual(result, {'status': 'updates', 'updates': [], 'lavish_batches': expected})
            self.assertIsNone(conn.execute('SELECT offered_at FROM lavish_batches WHERE id=2').fetchone()[0])
        hc = self.root / 'hc'
        hc.write_text(hc.read_text().replace("args[1]=='t-chef'", "args[1] in ('t-chef', 't-new')"))
        (self.root / 'chef').write_text(json.dumps({'address': 't-new', 'state': 'live', 'labels': {'herdr.space': 'sp1'}}))
        self.env = dict(self.env, HC_TERMINAL_ID='t-new')
        self.start()
        self.assertEqual(self.wait()['lavish_batches'], expected)
        legacy = self.command('inbox', 'app', '--recent')
        self.assertEqual(json.loads(legacy.stdout)['lavish_batches'], expected)
        with self.watcher(), patch.dict(os.environ, self.env), sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            record = lavish.operate(conn, Namespace(command='read', batch=2), 1, None)
            self.assertEqual(record['feedback'], ['private Chef feedback'])
            lavish.operate(conn, Namespace(command='ack', batch=2), 1, None)
            self.assertEqual(self.wait(), {'status': 'idle'})

    def test_binding_change_during_wait_and_persistent_registry_failure(self):
        self.start()
        self.scan()
        with self.watcher(), patch.dict(os.environ, self.env), sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row

            def rebind(_):
                with sqlite3.connect(self.db) as other:
                    other.execute("UPDATE watch_targets SET chef='new'")

            with self.assertRaisesRegex(ValueError, 'binding changed'):
                ltc_wait.wait(conn, self.db, 1, Namespace(ack=[], timeout=0, check=False), sleep=rebind)
        self.start()
        with sqlite3.connect(self.db) as conn:
            conn.execute('DROP TABLE watch_events')
        self.assertIn('no such table', self.wait(ok=False))
        self.start()
        with self.watcher():
            self.assertEqual(self.wait('--check'), {'status': 'waiting'})
            self.assertEqual(self.wait('--timeout', '1'), {'status': 'deadline'})

    def test_registry_busy_retries_and_preserves_unconfirmed_updates_on_failure(self):
        self.start()
        self.report()
        self.update_cook('blocked')
        self.scan()
        args = Namespace(ack=[('event', 1)], timeout=0, check=False)
        busy = sqlite3.OperationalError('database table is locked: cook_reports')
        with patch.dict(os.environ, self.env), sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            with patch.object(ltc_wait, 'snapshot', side_effect=busy) as snapshot:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    ltc_wait.wait(conn, self.db, 1, args, sleep=lambda _: None)
                self.assertEqual(snapshot.call_count, 3)
            self.assertEqual(conn.execute('SELECT delivered FROM watch_events').fetchone()[0], 0)
            pending = ltc_wait.snapshot(conn, 1, ltc_wait.chef_binding(conn, 1))
            with patch.object(ltc_wait, 'snapshot', side_effect=[busy, pending]) as snapshot:
                result = ltc_wait.wait(conn, self.db, 1, Namespace(ack=[], timeout=0, check=False),
                                       sleep=lambda _: None)
                self.assertEqual(snapshot.call_count, 2)
            self.assertEqual([row['ref'] for row in result['updates']], ['event:1', 'report:1'])

    def test_interrupt_foreground_wait_does_not_claim(self):
        self.start()
        self.scan()
        with self.watcher(), subprocess.Popen([ROOT / 'bin/ltc-wait', 'app'], env=self.env,
                                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                # Wait until the process has entered its own verified Chef check.
                log = self.root / 'log'
                initial = log.read_text().count('["get", "t-chef"]')
                until = Event()
                for _ in range(80):
                    if log.read_text().count('["get", "t-chef"]') > initial:
                        break
                    until.wait(.02)
                else:
                    self.fail('wait never verified the Chef')
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=3)
                self.assertEqual((process.returncode, stdout, stderr), (130, '', ''))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.report()
        self.assertEqual(self.wait()['updates'][0]['ref'], 'report:1')

    def test_running_legacy_watcher_wakes_wait_and_releases_unchanged_lock(self):
        self.start()
        self.assertEqual(self.wait('--check'), {'status': 'watcher_unavailable'})
        with subprocess.Popen([ROOT / 'bin/ltc-watch', 'run', 'app', '--interval', '1'], env=self.env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as watcher:
            try:
                until = Event()
                for _ in range(15):
                    if self.wait('--check') == {'status': 'waiting'}:
                        break
                    until.wait(.02)
                else:
                    self.fail('existing watcher did not acquire its lock')
                cook = self.root / 'cook'
                terminal = json.loads(cook.read_text())
                terminal['state'] = 'finished'
                replacement = cook.with_suffix('.new')
                replacement.write_text(json.dumps(terminal))
                replacement.replace(cook)
                result = self.wait('--timeout', '3')
                self.assertEqual(result['updates'][0]['state'], 'terminal_finished')
            finally:
                watcher.terminate()
                watcher.wait(timeout=3)
        self.assertEqual(self.wait('--ack', 'event:1', '--check'), {'status': 'watcher_unavailable'})


if __name__ == '__main__':
    unittest.main()
