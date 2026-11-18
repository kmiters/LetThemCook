"""Resolve the current project's bound Chef and verify its live Herdr identity."""
import json
import os
import subprocess


def verified_chef(conn, project_id, *, address=None, session=None):
    bound = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watch_targets'").fetchone()
    target = bound and conn.execute('''SELECT w.chef, w.session, p.space, r.token
        FROM watch_targets w JOIN projects p ON p.id=w.project_id AND p.space=w.space
        JOIN registry_identity r ON r.id=1 WHERE p.id=?''', (project_id,)).fetchone()
    if not target:
        raise ValueError('No Chef bound in the current Space. Run ltc-watch start from the project Chef.')
    chef, bound_session, space, token = target
    if (address is not None and chef != address) or (session is not None and session != bound_session):
        raise ValueError('Chef address or Herdr session does not match the project binding')
    env = dict(os.environ, HC_SESSION=bound_session)
    env.pop('HC_SOCKET', None)
    data = json.loads(subprocess.run(['hc', 'get', chef], env=env, text=True,
                                     capture_output=True, check=True, timeout=10).stdout)
    expected = {'ltc.chef.project': str(project_id), 'ltc.chef.registry': token, 'herdr.space': space}
    labels = data.get('labels') if isinstance(data, dict) else None
    if (not isinstance(labels, dict) or data.get('address') != chef or data.get('state') != 'live'
            or any(labels.get(k) != v for k, v in expected.items())):
        raise ValueError('Live Chef identity does not match the registered endpoint')
    return bound_session, chef


def verify_callback(conn, project_id, address):
    if os.environ.get('HC_SOCKET'):
        raise ValueError('Cannot verify a Chef callback with an HC_SOCKET override')
    verified_chef(conn, project_id, address=address, session=os.environ.get('HC_SESSION') or 'default')
