import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


BIN = Path(__file__).resolve().parents[1] / 'bin'


class BrowserTest(unittest.TestCase):
    def test_commands_only_use_the_owned_headless_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'chrome-devtools-axi'
            fake.write_text("""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

env = {key: value for key, value in os.environ.items()
       if key.startswith('CHROME_DEVTOOLS_AXI_')}
session = Path.cwd() / env['CHROME_DEVTOOLS_AXI_SESSION']
if sys.argv[1] == 'open':
    session.touch()
elif sys.argv[1] == 'stop':
    session.unlink()
print(json.dumps([sys.argv[1:], env]))
""")
            fake.chmod(0o755)
            unrelated = root / 'ltc-unrelated'
            unrelated.touch()
            env = dict(os.environ, PATH=f'{root}{os.pathsep}{os.environ["PATH"]}',
                       CHROME_DEVTOOLS_AXI_SESSION=unrelated.name,
                       CHROME_DEVTOOLS_AXI_HEADED='1',
                       CHROME_DEVTOOLS_AXI_AUTO_CONNECT='1',
                       CHROME_DEVTOOLS_AXI_BROWSER_URL='http://127.0.0.1:9222',
                       CHROME_DEVTOOLS_AXI_MCP_SERVER_URL='https://example.invalid/mcp',
                       CHROME_DEVTOOLS_AXI_WS_HEADERS='{}',
                       CHROME_DEVTOOLS_AXI_USER_DATA_DIR='/personal-profile',
                       CHROME_DEVTOOLS_AXI_CHROME_ARGS='--user-data-dir=/personal-profile',
                       CHROME_DEVTOOLS_AXI_PORT='9224',
                       CHROME_DEVTOOLS_AXI_IDLE_TIMEOUT_MS='0',
                       CHROME_DEVTOOLS_AXI_MCP_PATH='/local/devtools-mcp.js',
                       CHROME_DEVTOOLS_AXI_EXECUTABLE_PATH='/local/chrome')
            owned = root / 'ltc-task-62-test'
            expected_env = dict(CHROME_DEVTOOLS_AXI_SESSION=owned.name,
                                CHROME_DEVTOOLS_AXI_HEADED='0',
                                CHROME_DEVTOOLS_AXI_IDLE_TIMEOUT_MS='120000',
                                CHROME_DEVTOOLS_AXI_MCP_PATH='/local/devtools-mcp.js',
                                CHROME_DEVTOOLS_AXI_EXECUTABLE_PATH='/local/chrome')

            def browser(session, *args, check=True):
                return subprocess.run([BIN / 'ltc-browser', session, *args], cwd=root, env=env,
                                      text=True, capture_output=True, check=check, timeout=5)

            for args in (['open', 'http://127.0.0.1:8000/'], ['screenshot', 'review draft.png']):
                self.assertEqual(json.loads(browser(owned.name, *args).stdout), [args, expected_env])
            self.assertTrue(owned.exists())
            self.assertEqual(json.loads(browser(owned.name, 'stop').stdout), [['stop'], expected_env])
            self.assertFalse(owned.exists())
            self.assertEqual(browser('default', 'stop', check=False).returncode, 2)
            self.assertTrue(unrelated.exists())
