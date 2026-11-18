"""Synthetic watcher registration and hc get response used by launcher tests."""
import json
import os
import sqlite3


def bind_chef(env, space):
    with sqlite3.connect(env['LTC_DB']) as conn:
        project, token = conn.execute('SELECT projects.id,token FROM projects,registry_identity WHERE space=?', (space,)).fetchone()
        conn.execute('CREATE TABLE IF NOT EXISTS watch_targets (project_id INTEGER PRIMARY KEY, chef, session, space)')
        chef = env['HC_TERMINAL_ID']
        conn.execute('INSERT OR REPLACE INTO watch_targets VALUES (?,?,?,?)',
                     (project, chef, env.get('HC_SESSION') or 'default', space))
    env['TEST_CHEF'] = json.dumps({'address': chef, 'state': 'live', 'labels': {
        'ltc.chef.registry': token, 'ltc.chef.project': str(project), 'herdr.space': space}})


CHEF_GET = """if sys.argv[1:] == ['get', os.environ['HC_TERMINAL_ID']]:
    print(os.environ['TEST_CHEF'])
    sys.exit(0)
"""
