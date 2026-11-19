"""Adapters for the installed AXI CLIs. Only pixels, labels and geometry enter the artifact."""
import html
import http.client
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess

from ltc_registry import review_url


# Resolve through the same npm installation as the CLI, including nested npm/pnpm layouts.
DECODE = """
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const cli = createRequire(process.argv[1]);
const codec = createRequire(cli.resolve('axi-sdk-js')).resolve('@toon-format/toon');
import(pathToFileURL(codec)).then(({decode}) => {
  const raw = require('node:fs').readFileSync(0, 'utf8');
  process.stdout.write(JSON.stringify(decode(raw)));
});
"""


def decode(raw):
    cli = Path(shutil.which('lavish-axi') or 'lavish-axi').resolve()
    result = subprocess.run(['node', '--conditions=import', '-e', DECODE, str(cli)], input=raw, capture_output=True)
    if result.returncode:
        raise ValueError('Cannot decode AXI output with the TOON codec from the lavish-axi npm installation')
    return json.loads(result.stdout)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def run(argv, env, **options):
    result = subprocess.run(argv, env=env, stderr=subprocess.PIPE, **options)
    if result.returncode:
        detail = (result.stderr or result.stdout or b'').decode(errors='replace').strip()
        raise ValueError(f'{argv[0]} {argv[1]} failed ({result.returncode}): {detail[:1200]!r}')
    return result.stdout


def lavish_env(folder, port):
    env = {k: v for k, v in os.environ.items() if not k.startswith('LAVISH_AXI_')}
    env.update(LAVISH_AXI_HOST='127.0.0.1', LAVISH_AXI_PORT=str(port),
               LAVISH_AXI_STATE_DIR=str(folder / 'provider'), LAVISH_AXI_TELEMETRY='0',
               LAVISH_AXI_IDLE_TIMEOUT_MS='0')
    return env


def check_server(env):
    """An idle review's former port may now belong to an unrelated installation."""
    client = http.client.HTTPConnection('127.0.0.1', int(env['LAVISH_AXI_PORT']), timeout=2)
    try:
        client.request('GET', '/health')
        response = client.getresponse()
        if response.status == 200:
            health = json.loads(response.read(65536))
            if (health.get('app') == 'lavish-axi' and health.get('hosts') == ['127.0.0.1']
                    and health.get('state_dir') == env['LAVISH_AXI_STATE_DIR']):
                return
    except ConnectionRefusedError:
        return
    finally:
        client.close()
    raise ValueError('The review port is occupied by another service. Leave that service running.')


def lavish(env, *args):
    check_server(env)
    return decode(run(['lavish-axi', *args], env, stdout=subprocess.PIPE, timeout=60))


def chrome_env(folder):
    env = {k: v for k, v in os.environ.items() if not k.startswith('CHROME_DEVTOOLS_AXI_')}
    # These two options locate trusted local executables. Every connection/profile option is ours.
    for key in ('CHROME_DEVTOOLS_AXI_MCP_PATH', 'CHROME_DEVTOOLS_AXI_EXECUTABLE_PATH'):
        if os.environ.get(key):
            env[key] = os.environ[key]
    env.update(HOME=str(folder / 'browser'), CHROME_DEVTOOLS_AXI_SESSION='ltc-' + folder.name,
               CHROME_DEVTOOLS_AXI_IDLE_TIMEOUT_MS='120000', CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS='1',
               CHROME_DEVTOOLS_AXI_USER_DATA_DIR=str(folder / 'browser' / 'profile'))
    return env


TARGETS = """() => {
    const root = document.scrollingElement || document.documentElement;
    const docWidth = Math.max(innerWidth, root.scrollWidth);
    const docHeight = Math.max(innerHeight, root.scrollHeight);
    const full = Math.max(docWidth, docHeight) <= 16384 && docWidth * docHeight * devicePixelRatio ** 2 <= 16000000;
    const width = full ? docWidth : innerWidth, height = full ? docHeight : innerHeight;
    const offsetX = full ? scrollX : 0, offsetY = full ? scrollY : 0;
    const items = [];
    const nodes = document.querySelectorAll('a,button,label,h1,h2,h3,h4,p,img,input,textarea,select,[role="button"]');
    for (const node of nodes) {
        if (items.length >= 500) break;
        const r = node.getBoundingClientRect();
        const x = Math.max(0, r.left + offsetX), y = Math.max(0, r.top + offsetY);
        const w = Math.min(width, r.right + offsetX) - x, h = Math.min(height, r.bottom + offsetY) - y;
        if (w < 2 || h < 2 || getComputedStyle(node).visibility !== 'visible') continue;
        const cx = x + w/2 - offsetX, cy = y + h/2 - offsetY;
        if (cx >= 0 && cx < innerWidth && cy >= 0 && cy < innerHeight &&
            !node.contains(document.elementFromPoint(cx, cy))) continue;
        const label = node.getAttribute('aria-label') || node.alt || node.innerText || node.tagName;
        items.push([node.tagName, String(label).trim().slice(0, 160), x, y, w, h]);
    }
    return {url: location.href, title: document.title, full, width, height, items};
}"""


def write_snapshot(artifact, view):
    if not review_url(view['url']):
        raise ValueError('Rendered page did not resolve to an HTTP(S) URL without credentials')
    width, height = (view[key] for key in ('width', 'height'))
    if not all(type(n) is int and 0 < n <= 16384 for n in (width, height)):
        raise ValueError('Invalid capture dimensions')
    labels = []
    for i, (tag, label, x, y, w, h) in enumerate(view['items'][:500], 1):
        if not all(type(n) in (float, int) and math.isfinite(n) and 0 <= n <= 16384 for n in (x, y, w, h)):
            raise ValueError('Invalid annotation bounds')
        bounds = f'left:{100*x/width:.4f}%;top:{100*y/height:.4f}%;width:{100*w/width:.4f}%;height:{100*h/height:.4f}%'
        name = html.escape(f'{tag}: {label}'[:200])
        labels.append(f'<span id="target-{i}-at-{int(x)}-{int(y)}" style="{bounds}">{name}</span>')
    title = html.escape(str(view['title'])[:300])
    artifact.write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#fff}}main{{position:relative;max-width:{width}px}}
img{{display:block;width:100%;height:auto}}span{{position:absolute;overflow:hidden;color:transparent}}</style>
<main><img src="capture.png" alt="Captured page">{''.join(labels)}</main></html>''', encoding='utf-8')


def capture(folder, artifact, url):
    env = chrome_env(folder)
    (folder / 'browser').mkdir()
    env['CHROME_DEVTOOLS_AXI_PORT'] = str(free_port())
    try:
        def chrome(*args):
            return run(['chrome-devtools-axi', *args], env, stdout=subprocess.PIPE, timeout=90)
        chrome('open', url)
        script = f'console.log(JSON.stringify(await page.eval({TARGETS})))'
        view = json.loads(run(['chrome-devtools-axi', 'run'], env, input=script.encode(),
                              stdout=subprocess.PIPE, timeout=90))
        write_snapshot(artifact, view)
        png = artifact.with_name('capture.png')
        options = ['--full-page'] if view['full'] else []
        chrome('screenshot', str(png), '--format', 'png', *options)
        with png.open('rb') as image:
            if image.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ValueError('Capture did not produce a PNG')
        return view['url'], 'page' if view['full'] else 'viewport'
    finally:
        stop_capture(folder)


def stop_capture(folder):
    browser = folder / 'browser'
    if browser.exists():
        run(['chrome-devtools-axi', 'stop'], chrome_env(folder), stdout=subprocess.PIPE, timeout=30)
        shutil.rmtree(browser)
