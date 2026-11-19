import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import ltc_update as update


class UpdateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.a = self.commit('initial')
        self.git('remote', 'add', 'origin', 'https://github.com/kmiters/LetThemCook.git')
        self.path = update.state_path(self.root)
        self.real_git = update.git
        self.now = int(time.time()) + update.INTERVAL
        self.tip = self.a
        self.calls = 0
        self.failure = None
        self.enter_patch(patch.object(update, 'git', side_effect=self.request))
        self.enter_patch(patch.object(update.time, 'time', side_effect=lambda: self.now))

    def enter_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git('commit', '--allow-empty', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def request(self, root, *args, **kwargs):
        if 'ls-remote' not in args:
            return self.real_git(root, *args, **kwargs)
        self.calls += 1
        if self.failure == 'timeout':
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        if self.failure:
            return self.failure
        return 0, f'{self.tip}\t{update.MAIN}'

    def check(self):
        return update.check(self.root)

    def state(self):
        return update.read_state(self.path)

    def test_initial_equal_cooldown_and_concurrency(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(lambda _: self.check(), range(8))), [None] * 8)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.state()['relation'], 'up_to_date')
        self.assertEqual(self.state()['last_attempt'], self.now)
        self.assertEqual(self.state()['last_success'], self.now)
        self.now += update.INTERVAL - 0.001
        self.assertIsNone(self.check())
        self.assertEqual(self.calls, 1)
        self.now += 0.001
        self.assertIsNone(self.check())
        self.assertEqual(self.calls, 2)

    def test_initial_unknown_tip_is_visible_once_and_updates_leave_git_unchanged(self):
        self.tip = '1' * 40
        self.assertIn('cannot establish', self.check())
        self.assertEqual(self.state()['relation'], 'unknown')
        self.assertIsNone(self.check())
        self.now += update.INTERVAL
        self.assertIsNone(self.check())
        self.now += update.INTERVAL
        self.tip = '2' * 40
        before = self.git('show-ref'), self.git('count-objects', '-v'), self.git('status', '--porcelain')
        self.assertIn('cannot establish', self.check())
        self.assertEqual(self.state()['remote_main'], self.tip)
        self.assertEqual((self.git('show-ref'), self.git('count-objects', '-v'), self.git('status', '--porcelain')), before)
        self.assertFalse((self.root / '.git/FETCH_HEAD').exists())

    def test_initial_ahead_known_behind_and_diverged(self):
        b = self.commit('local ahead')
        self.assertIsNone(self.check())
        self.assertEqual(self.state()['relation'], 'ahead')
        self.git('checkout', '-q', '--detach', b)
        c = self.commit('upstream extends local')
        self.tip = c
        self.now += update.INTERVAL
        self.assertIn('behind', self.check())
        self.assertEqual(self.state()['local_main'], b)
        self.git('checkout', '-q', '--detach', self.a)
        self.tip = self.commit('diverges')
        self.now += update.INTERVAL
        self.assertIn('diverged', self.check())
        self.assertEqual(self.state()['relation'], 'diverged')

    def test_failure_keeps_last_success_and_uses_same_cooldown(self):
        self.check()
        first_success = self.now
        for failure, outcome in [((128, ''), 'request_failed'), ('timeout', 'timeout'),
                                 ((2, ''), 'remote_main_missing'), ((0, 'invalid'), 'invalid_reply')]:
            self.failure = failure
            self.now += update.INTERVAL
            self.assertIsNone(self.check())
            self.assertEqual(self.state()['outcome'], outcome)
            self.assertEqual(self.state()['last_attempt'], self.now)
            self.assertEqual(self.state()['last_success'], first_success)
            self.assertEqual(self.state()['remote_main'], self.a)
            calls = self.calls
            self.assertIsNone(self.check())
            self.assertEqual(self.calls, calls)

    def test_unsupported_origin_and_rewrite_skip_the_network(self):
        self.git('remote', 'remove', 'origin')
        self.assertIsNone(self.check())
        self.assertEqual(update.status(self.root)['origin'], 'unsupported_origin')
        self.git('remote', 'add', 'origin', 'https://github.com/kmiters/LetThemCook.git')
        self.git('config', 'url.https://other.invalid/.insteadOf', 'https://github.com/')
        self.assertIsNone(self.check())
        self.assertEqual(self.calls, 0)
        self.assertFalse(self.path.exists())

    def test_cli_status_and_throttled_check(self):
        install_bin = self.root / 'bin'
        install_bin.mkdir()
        for name in ('ltc-update', 'ltc_update.py'):
            shutil.copy2(update.ROOT / 'bin' / name, install_bin)
        cli = [str(install_bin / 'ltc-update')]
        initial = json.loads(subprocess.check_output(cli, text=True))
        self.assertEqual(initial['last_attempt_outcome'], 'not_checked')
        self.assertFalse(self.path.exists())
        self.check()
        # The fixture's attempt is in the future relative to the child clock.
        current = json.loads(subprocess.check_output([*cli, '--check'], text=True))
        self.assertEqual(current['last_attempt_utc'], update.utc(self.now))
        self.assertEqual(current['last_success_utc'], update.utc(self.now))
        self.assertEqual(current['next_eligible_utc'], update.utc(self.now + update.INTERVAL))
        self.assertEqual(current['remote_main_at_last_success'], self.a)
        self.assertEqual(current['relation_at_last_success'], 'up_to_date')
