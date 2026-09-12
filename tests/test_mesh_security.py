"""Workflow authority and secret-persistence regression tests."""
import json
import os
import tempfile
from pathlib import Path

from test_agent_mesh import MeshTestCase, HTTPAndMCPTests
from agent_mesh_core import sanitize, redact_text, MeshError
from configure_shared_agents import _backup


class SecurityRegressionTests(MeshTestCase):
    http_request = HTTPAndMCPTests.http_request

    def test_sensitive_values_redacted_before_persistence_and_idempotent(self):
        jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljLXNpZ25hdHVyZQ'
        cases = {
            'plain': jwt,
            'url': 'https://someone:synthetic-password@example.test/path?signature=synthetic-query',
            'session': 'https://app.kilo.ai/s/' + jwt,
            'Authorization': 'Bearer synthetic-bearer-value',
            'Cookie': 'session=synthetic-cookie-value',
            'lease_token': 'synthetic-lease-value',
            'private': '-----BEGIN PRIVATE KEY-----\nsynthetic-private-material\n-----END PRIVATE KEY-----',
        }
        self.store.create_message({'from_agent': 'Lead', 'to_agent': 'Worker',
            'subject': 'sanitizer regression', 'body': 'safe', 'payload': cases})
        with self.store.connect() as db:
            value = db.execute('SELECT payload_json FROM messages ORDER BY id DESC LIMIT 1').fetchone()[0]
        for secret in (jwt, 'synthetic-password', 'synthetic-query', 'synthetic-bearer-value',
                       'synthetic-cookie-value', 'synthetic-lease-value', 'synthetic-private-material'):
            self.assertNotIn(secret, value)
        self.assertEqual(json.loads(value)['url'], 'https://example.test/path')
        for value in (sanitize(cases), 'https://app.kilo.ai/s/[REDACTED]', 'Cookie: [REDACTED]'):
            self.assertEqual(sanitize(sanitize(value)), sanitize(value))
        self.assertEqual(self.settings.db.stat().st_mode & 0o777, 0o600)

    def test_http_scoped_lease_cannot_be_bypassed_or_released(self):
        server, base = self.start_http()
        server.store.register_agent({'name': 'Worker', 'capabilities': ['analysis']})
        server.store.create_run({'run_id': 'secure', 'request': 'Do work', 'lead_agent': 'Lead',
            'plan': {'tasks': [{'task_id': 'secure-one', 'assigned_agent': 'Worker'},
                               {'task_id': 'secure-two', 'assigned_agent': 'Worker'}]}})
        status, inbox, headers = self.http_request(base, 'POST', '/tasks/poll', {'agent': 'Worker', 'limit': 2},
                                                   extra_headers={'X-Agent-Mesh-Agent': 'Worker'}, return_headers=True)
        self.assertEqual(status, 200)
        self.assertEqual(len(inbox), 1)
        lease = headers['X-Agent-Mesh-Task-Lease']
        task = inbox[0]['task']['task_key']
        body = {'agent': 'Worker', 'message_id': inbox[0]['message']['id']}
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/ack', body)[0], 403)
        spoof = dict(body, _http_request=False, _caller_agent='Worker', _lease_token=lease)
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/ack', spoof)[0], 403)
        good = {'X-Agent-Mesh-Agent': 'Worker', 'X-Agent-Mesh-Task-Lease': lease}
        wrong = {'X-Agent-Mesh-Agent': 'Intruder', 'X-Agent-Mesh-Task-Lease': lease}
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/ack', spoof, extra_headers=wrong)[0], 403)
        for action in ('release', 'claim'):
            self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/' + action, {'agent': 'Worker'})[0], 409)
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/ack', body, extra_headers=good)[0], 200)
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/result',
            {'agent': 'Worker', 'result': {'summary': 'done'}}, extra_headers=good)[0], 200)
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/verify',
            {'verified_by': 'Worker', 'valid': True}, extra_headers=good)[0], 403)
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/verify',
            {'verified_by': 'Lead', 'valid': True}, extra_headers=good)[0], 403)
        self.assertEqual(self.http_request(base, 'POST', '/tasks/' + task + '/verify',
            {'verified_by': 'Lead', 'valid': True}, extra_headers={'X-Agent-Mesh-Agent': 'Lead'})[0], 200)

    def test_http_custom_adapter_registration_requires_administrator_credential(self):
        _, base = self.start_http()
        payload = {'name': 'Custom', 'provider': 'test', 'capabilities': ['analysis'],
                   'autonomy_adapter': {'kind': 'command', 'argv': ['/bin/echo']}}
        self.assertEqual(self.http_request(base, 'POST', '/agents/register', payload)[0], 403)
        self.assertEqual(self.http_request(base, 'POST', '/agents/register', payload,
                                           extra_headers={'X-Agent-Mesh-Admin': 'unit-test-admin'})[0], 200)

    def test_autonomous_workspace_must_be_under_configured_root(self):
        manager = self.settings.root / 'allowed'
        manager.mkdir()
        with self.assertRaises(MeshError):
            self.store.create_autonomous_request({'objective': 'bounded', 'lead_agent': 'Lead',
                                                  'workspace': str(self.base.parent)})

    def test_zero_depth_disables_delegation(self):
        self.register('Parent', ['orchestration'])
        self.register('Child', ['analysis'])
        run = self.store.create_run({'run_id': 'zero-depth', 'request': 'No recursion', 'lead_agent': 'Lead',
            'max_delegation_depth': 0, 'plan': {'tasks': [{'task_id': 'root', 'assigned_agent': 'Parent'}]}})
        inbox = self.store.poll_tasks('Parent')[0]
        self.store.acknowledge_task('root', {'agent': 'Parent', 'message_id': inbox['message']['id'], '_lease_token': inbox['lease_token']})
        with self.assertRaises(MeshError):
            self.store.delegate_subtasks('root', {'agent': 'Parent', '_lease_token': inbox['lease_token'],
                'idempotency_key': 'zero-depth-batch', 'tasks': [{'task_id': 'child', 'assigned_agent': 'Child'}]})

    def test_client_config_backups_are_private_and_distinct(self):
        one = self.base / 'one' / 'mcp.json'
        two = self.base / 'two' / 'mcp.json'
        for i, path in enumerate((one, two)):
            path.parent.mkdir()
            path.write_text('config-' + str(i))
            path.chmod(0o644)
        backups = self.base / 'backups'
        _backup(one, backups)
        _backup(two, backups)
        self.assertEqual({p.read_text() for p in backups.iterdir()}, {'config-0', 'config-1'})
        self.assertEqual(backups.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in backups.iterdir()))
