from contextlib import closing
import json
import os
import http.client
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
from ltc_board import connect, select_project

NAMES = ('xtb-proto-design-system', 'xtb-prototype-shell', 'xtb-prototype-starter')


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / 'mobile prototypes'
        self.workspace.mkdir()
        self.db = self.root / 'registry.sqlite'
        self.env = dict(os.environ, LTC_DB=str(self.db), PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                        HC_SESSION='fixture', HC_TERMINAL_ID='chef', FAKE_ROOT=str(self.root))
        self.env.pop('HC_SOCKET', None)
        for name in NAMES:
            repo = self.workspace / name
            self.git('init', '-qb', 'main', repo)
            (repo / 'source').write_text(name)
            self.git('-C', repo, 'add', 'source')
            self.git('-C', repo, '-c', 'user.name=Fixture', '-c', 'user.email=test@example.invalid',
                     'commit', '-qm', 'initial')
            (repo / 'source').write_text('staged user work')
            self.git('-C', repo, 'add', 'source')
            (repo / 'source').write_text('unstaged user work')
        self.fake('codex', 'raise AssertionError("real agent invocation")')
        self.fake('hc', """
import json, os, sys
from pathlib import Path
root = Path(os.environ['FAKE_ROOT'])
args = sys.argv[1:]
with (root / 'hc.log').open('a') as log:
    print(json.dumps(args), file=log)
if args[0] == 'run':
    labels = dict(args[i+1].split('=', 1) for i, arg in enumerate(args) if arg == '--label')
    address = 'fixture-' + labels['ltc.run']
    data = {'address':address,'labels':labels,'state':'live','status':{'state':'idle'}}
    (root / address).write_text(json.dumps(data))
    print(json.dumps(data))
elif args[0] in ('get','kill','rm'):
    path = root / args[1]
    data = json.loads(path.read_text())
    if args[0] == 'get':
        print(json.dumps(data))
    elif args[0] == 'kill':
        data['state'] = 'finished'
        path.write_text(json.dumps(data))
    else:
        path.unlink()
elif args[0] == 'read':
    print(json.dumps({'text':'fixture output','complete':True}))
""")
        self.fake('herdr-app', """
import json, os, sys
from pathlib import Path
p = Path(os.environ['FAKE_ROOT']) / 'chef'
if sys.argv[1] == 'ls':
    print(json.dumps({'spaces':[{'id':'sp','folder':'/wrong','tabs':
        [{'id':'chef','name':'👨‍🍳 Chef @ group'}] if p.exists() else []}]}))
else:
    p.write_text(json.dumps(sys.argv[1:]))
    print(json.dumps({'id':'chef'}))
""")

    def fake(self, name, body):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + body)
        path.chmod(0o755)

    def git(self, *args):
        return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout

    def cli(self, *args, ok=True, cwd=None):
        proc = subprocess.run([BIN / args[0], *args[1:]], env=self.env, cwd=cwd or self.root,
                              capture_output=True, text=True)
        if not ok:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            return proc.stderr
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def register(self, *names, ok=True):
        flags = [arg for name in names for arg in ('--member', name)]
        return self.cli('ltc-db', 'project', 'add', 'group', self.workspace, '--space', 'sp', *flags, ok=ok)

    def launch(self, index=0):
        task = self.cli('ltc-db', 'task', 'add', 'group', 'Inspect', '--member', NAMES[index])
        return self.cli('ltc-cook', 'group', str(task['id']), 'same-name', '--profile', 'routine')

    def test_registration_boundaries_and_atomic_failure(self):
        outside = self.root / 'outside'
        self.git('init', '-q', outside)
        (self.workspace / 'alias').symlink_to(outside, target_is_directory=True)
        linked = self.workspace / 'linked'
        self.git('-C', self.workspace / NAMES[0], 'worktree', 'add', '-q', '--detach', linked)
        for name in (NAMES[0], str(outside), '../outside', 'alias', 'missing', 'linked', '.', NAMES[1] + '/sub'):
            with self.subTest(name=name):
                self.register(NAMES[0], name, ok=False)
                self.assertEqual(self.cli('ltc-db', 'project', 'list'), [])
        self.cli('ltc-db', 'project', 'add', 'other', linked)
        self.assertIn('already belongs', self.register(*NAMES, ok=False))
        self.assertEqual(len(self.cli('ltc-db', 'project', 'list')), 1)

    def test_registration_and_one_chef(self):
        flags = [arg for name in NAMES for arg in ('--member', name)]
        result = subprocess.run([BIN / 'ltc-project', 'group', self.workspace, 'sp', 'Dependency chain', *flags],
                                env=self.env, capture_output=True, text=True, check=True)
        project, reports = map(json.loads, result.stdout.splitlines())
        self.assertEqual([m['name'] for m in project['members']], list(NAMES))
        self.assertEqual([r['status'] for r in reports], ['started'])
        args = json.loads((self.root / 'chef').read_text())
        self.assertEqual(args[args.index('--folder') + 1], str(self.workspace))
        self.assertEqual(args[args.index('--space') + 1], 'sp')
        prompt = args[args.index('--task') + 1]
        for name in (*NAMES, '--member', '--harness', 'registry.sqlite'):
            self.assertIn(name, prompt)
        self.assertEqual(self.cli('ltc-chef', 'group')[0]['status'], 'already_present')
        self.assertFalse((self.workspace / '.git').exists())
        self.cli('ltc-db', 'project', 'add', 'duplicate', self.workspace / NAMES[0], ok=False)
        self.cli('ltc-db', 'project', 'update', 'group', '--path', self.workspace / NAMES[0], ok=False)
        self.cli('ltc-db', 'task', 'add', 'group', 'implicit', ok=False)
        self.cli('ltc-db', 'task', 'add', 'group', 'wrong member', '--member', 'unlisted', ok=False)
        self.assertEqual(self.cli('ltc-db', 'task', 'list'), [])

    def test_three_isolated_members_and_project_resolution(self):
        project = self.register(*NAMES)
        runs = []
        for i, name in enumerate(NAMES):
            repo = self.workspace / name
            base = self.git('-C', repo, 'rev-parse', 'HEAD').strip()
            launch = self.launch(i)
            runs.append(launch)
            run = self.cli('ltc-db', 'run', 'show', str(launch['run_id']))
            self.assertEqual((run['member'], run['member_id'], run['base']),
                             (name, project['members'][i]['id'], base))
            worktree = Path(run['worktree'])
            self.assertEqual(worktree, repo / '.letthemcook' / 'worktrees' / 'same-name')
            self.assertEqual((worktree / 'source').read_text(), name)
            self.assertEqual((repo / 'source').read_text(), 'unstaged user work')
            self.assertEqual(self.git('-C', repo, 'show', ':source'), 'staged user work')
            self.assertEqual(self.git('-C', repo, 'status', '--porcelain').strip(), 'MM source')
            for cwd in (self.workspace, repo, worktree):
                with closing(connect(self.db)) as conn:
                    self.assertEqual(select_project(conn, None, cwd)['id'], project['id'])
                report = self.cli('ltc-whatsup', cwd=cwd)
                self.assertEqual(report['project']['id'], project['id'])
                self.assertEqual(report['runs'][-1]['member'], name)
            self.assertIn('Assigned repository: ' + name, (self.root / 'hc.log').read_text())
        other = self.workspace / 'unlisted'
        self.git('init', '-q', other)
        with closing(connect(self.db)) as conn:
            self.assertIsNone(select_project(conn, None, other))
        self.cli('ltc-whatsup', cwd=other, ok=False)
        status = self.cli('ltc-status', 'group')
        self.assertEqual([t['member'] for t in status['tasks']], list(NAMES))
        self.assertEqual([r['member'] for r in status['runs']], list(NAMES))
        self.assertTrue(all(r['terminal_status']['state'] == 'live' for r in status['runs']))
        self.cli('ltc', '--task-id', '1', '--project', self.workspace / NAMES[1], '--space', 'sp',
                 '--name', 'wrong', '--task', 'wrong repo', ok=False)
        self.cli('ltc-cook', 'other', '1', ok=False)
        self.assertFalse((self.workspace / NAMES[1] / '.letthemcook/worktrees/wrong').exists())
        self.assertEqual(len(self.cli('ltc-db', 'run', 'list')), len(runs))

    def test_board_cli_opens_the_workspace_from_member_and_linked_worktree(self):
        project = self.register(*NAMES)
        launch = self.launch()
        expected = f"/projects/{project['id']}"
        server = subprocess.Popen([BIN / 'ltc-tasks', '--serve', 'group'], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            url = server.stdout.readline().strip()
            self.assertTrue(url, 'Board failed to bind')
            parsed = urlsplit(url)
            self.assertEqual(parsed.path, expected)
            for directory in (self.workspace / NAMES[0], Path(launch['worktree'])):
                board = subprocess.run([BIN / 'ltc-tasks', '--no-open'], cwd=directory, env=self.env,
                                       capture_output=True, check=True, text=True, timeout=10)
                self.assertEqual(board.stdout.strip(), url)
            self.fake('codex', """
import json, sys
from pathlib import Path
assert sys.argv[1:4] == ['-a', 'never', 'exec']
assert sys.argv[sys.argv.index('--model') + 1] == 'gpt-6-luna'
assert 'model_reasoning_effort="low"' in sys.argv
assert 'fixture output' in sys.stdin.read()
result = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
result.write_text(json.dumps({'runs':[{'id':1,'summary':'Summary from fixture model.'}]}))
""")
            summary = subprocess.run([BIN / 'ltc-whatsup', '--summarize', 'codex'], cwd=launch['worktree'],
                                     env=self.env, capture_output=True, check=True, text=True, timeout=10)
            self.assertIn('Summary from fixture model.', summary.stdout)
            self.assertIn(NAMES[0], summary.stdout)
            self.assertEqual(summary.stdout.splitlines()[-1], f'[Project kanban]({url})')
            with closing(http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)) as client:
                client.request('GET', expected)
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn('Inspect', response.read().decode())
        finally:
            server.terminate()
            server.communicate(timeout=5)

    def test_member_identity_controls_resume_and_cleanup(self):
        project = self.register(*NAMES)
        launch = self.launch()
        address = launch['herdr']['address']
        state_file = self.root / address
        terminal = json.loads(state_file.read_text())
        original = dict(terminal['labels'])
        self.cli('ltc-cook-control', 'send', '1', 'Follow up')
        for label in (None, str(project['members'][1]['id'])):
            terminal['labels'] = original.copy()
            if label is None:
                del terminal['labels']['ltc.member']
            else:
                terminal['labels']['ltc.member'] = label
            terminal['state'] = 'finished'
            state_file.write_text(json.dumps(terminal))
            before = len((self.root / 'hc.log').read_text().splitlines())
            self.cli('ltc-cook-control', 'send', '1', 'Check', ok=False)
            self.cli('ltc-cook-control', 'check', '1', ok=False)
            self.cli('ltc-resume', '1', ok=False)
            self.cli('ltc-db', 'task', 'update', '1', '--status', 'done')
            self.cli('ltc-cook-control', 'close', '1', ok=False)
            self.cli('ltc-db', 'task', 'update', '1', '--status', 'in_progress')
            self.assertEqual(self.cli('ltc-status', 'group')['runs'][0]['terminal_status']['reason'], 'identity_mismatch')
            snapshot = self.cli('ltc-whatsup', 'group')
            self.assertEqual(snapshot['runs'][0]['screen_untrusted']['reason'], 'identity_mismatch')
            calls = (self.root / 'hc.log').read_text().splitlines()[before:]
            self.assertTrue(all(json.loads(args)[0] == 'get' for args in calls))
        terminal['labels'] = original
        state_file.write_text(json.dumps(terminal))
        self.assertEqual(self.cli('ltc-cook-control', 'check', '1')['member'], NAMES[0])
        worktree = Path(launch['worktree'])
        (worktree / 'unfinished').write_text('keep')
        resumed = self.cli('ltc-resume', '1')
        self.assertEqual(resumed['worktree'], str(worktree))
        self.assertEqual(resumed['member'], NAMES[0])
        self.assertEqual(resumed['profile'], 'routine')
        self.assertEqual(resumed['herdr']['labels']['ltc.member'], original['ltc.member'])
        self.assertEqual((worktree / 'unfinished').read_text(), 'keep')
        self.assertEqual(self.cli('ltc-db', 'run', 'show', '2')['member'], NAMES[0])
        self.cli('ltc-db', 'task', 'update', '1', '--status', 'done')
        self.cli('ltc-cook-control', 'close', '2', ok=False)
        (worktree / 'unfinished').unlink()
        self.assertTrue(self.cli('ltc-cook-control', 'check', '2')['safe_to_remove'])
        self.assertEqual(self.cli('ltc-cook-control', 'close', '2')['removed_worktree'], str(worktree))
        self.assertFalse(worktree.exists())
        self.assertEqual((self.workspace / NAMES[0] / 'source').read_text(), 'unstaged user work')

    def test_refuses_redirected_worktree_or_wrong_task_member(self):
        self.register(*NAMES)
        managed = self.workspace / NAMES[0] / '.letthemcook'
        managed.symlink_to(self.root, target_is_directory=True)
        self.cli('ltc-db', 'task', 'add', 'group', 'One', '--member', NAMES[0])
        self.cli('ltc-cook', 'group', '1', ok=False)
        self.assertEqual(self.cli('ltc-db', 'run', 'list'), [])
        self.assertFalse((self.root / 'worktrees').exists())
        managed.unlink()
        launch = self.cli('ltc-cook', 'group', '1')
        second = self.launch(1)
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE cook_runs SET worktree = ? WHERE id = 1', (second['worktree'],))
        self.cli('ltc-cook-control', 'check', '1', ok=False)
        self.cli('ltc-resume', '1', ok=False)
        self.cli('ltc-db', 'task', 'update', '1', '--status', 'done')
        self.cli('ltc-cook-control', 'close', '1', '--discard-changes', ok=False)
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE cook_runs SET worktree = ?, member_id = 2 WHERE id = 1', (launch['worktree'],))
        self.cli('ltc-cook-control', 'check', '1', ok=False)
        report = self.cli('ltc-whatsup', 'group')
        self.assertEqual(report['runs'][0]['cook_state_reason'], 'task_member_mismatch')
        self.assertTrue(Path(second['worktree']).is_dir())
        self.assertTrue(Path(launch['worktree']).is_dir())
