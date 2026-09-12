"""Headless routing and failure evidence regressions; no real AI calls."""
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from agent_mesh_core import MeshStore, after
from agent_mesh_adapters import AdapterSpec, AdapterResult
from agent_mesh_autonomy import AutonomyManager
from test_agent_mesh import make_settings


class HeadlessOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.settings = make_settings(self.base)
        self.store = MeshStore(self.settings)
        self.manager = AutonomyManager(self.store, self.settings)
        self.manager.registry.refresh = lambda: self.manager.registry._specs

    def tearDown(self):
        self.manager.stop()
        self.tmp.cleanup()

    def provider(self, name, capabilities=('analysis',), **extra):
        self.store.register_agent({'name': name, 'capabilities': list(capabilities),
            'metadata': {'autonomy': {'available': True, 'adapter_kind': 'command'}}, **extra})
        spec = AdapterSpec(name, 'command', (sys.executable, '-c', 'pass'), capabilities=capabilities)
        self.manager.registry._specs[name] = spec
        return spec

    def test_failure_evidence_survives_discovery_heartbeat_and_restart(self):
        self.provider('Provider')
        self.assertEqual(self.store.get_agent('Provider')['execution_status'], 'configured')
        self.assertFalse(self.store.get_agent('Provider')['autonomy_ready'])
        self.store.record_provider_outcome('Provider', success=False, failure_kind='authentication',
                                          detail='Authorization: Bearer synthetic-test-value')
        self.store.heartbeat_agent('Provider', {'status': 'active'})
        self.store.update_agent_adapter_state('Provider', available=True, adapter_kind='command', adapter_source='builtin')
        store = MeshStore(self.settings)
        agent = store.get_agent('Provider')
        self.assertFalse(agent['autonomy_ready'])
        self.assertEqual(agent['execution_status'], 'provider_blocked')
        self.assertNotIn('synthetic-test-value', agent['metadata_json'])
        self.assertFalse(store.provider_routable(agent))
        store.record_provider_outcome('Provider', success=True, duration_seconds=0.1)
        self.assertTrue(store.get_agent('Provider')['autonomy_ready'])

    def test_offline_gui_falls_back_only_when_reassignment_is_allowed(self):
        self.provider('GUI', status='offline')
        self.provider('Headless')
        for pinned in (False, True):
            run = self.store.create_run({'run_id': 'gui-' + str(pinned), 'request': 'Background work',
                'lead_agent': 'Lead', 'metadata': {'autonomous': True}, 'plan': {'tasks': [{
                    'task_id': 'gui-task-' + str(pinned), 'assigned_agent': 'GUI',
                    'reassign_on_retry': not pinned, 'required_capabilities': ['analysis']}]}})
            task = run['tasks'][0]
            self.assertEqual(task['assigned_agent'], 'GUI' if pinned else 'Headless')
            self.assertEqual(task['status'], 'waiting_agent' if pinned else 'sent')
            if not pinned:
                self.assertIn('task.fallback_selected', {e['event_type'] for e in run['events']})

    def test_worker_cannot_be_its_own_autonomous_auditor(self):
        self.provider('OnlyWorker')
        request = self.store.create_autonomous_request({'objective': 'Independent verification',
            'workspace': str(self.base), 'lead_agent': 'Lead'})
        run = self.store.create_run({'run_id': 'independent', 'request': 'Verify', 'lead_agent': 'Lead',
            'plan': {'tasks': [{'task_id': 'single', 'assigned_agent': 'OnlyWorker'}]}})
        self.store.update_autonomous_request(request['id'], orchestration_run_id=run['id'])
        task = dict(run['tasks'][0], status='verifying')
        self.manager._start_audits(request, dict(run, tasks=[task]))
        self.assertEqual(self.store.get_autonomous_request(request['id'])['state'], 'WAITING')
        self.assertFalse(self.manager._futures)

    def test_role_selection_skips_provider_cooldown(self):
        self.provider('Bad')
        self.provider('Good')
        self.store.record_provider_outcome('Bad', success=False, failure_kind='quota')
        self.assertEqual(self.manager._choose_spec({'auditor_agent': 'Bad'}, 'auditor').agent, 'Good')

    def test_planning_does_not_block_other_scheduler_work(self):
        request = self.store.create_autonomous_request({'objective': 'Plan slowly', 'workspace': str(self.base), 'lead_agent': 'Lead'})
        started = threading.Event()
        release = threading.Event()
        def plan(_):
            started.set()
            release.wait(2)
        self.manager._plan = plan
        try:
            before = time.monotonic()
            self.manager._advance(request)
            self.assertLess(time.monotonic() - before, 0.5)
            self.assertTrue(started.wait(0.5))
        finally:
            release.set()
            self.manager._futures['plan:' + request['id']].result(timeout=2)

    def test_local_provider_capacity_is_shared_by_all_roles(self):
        one = self.provider('LocalOne', ('analysis', 'local_private'))
        two = self.provider('LocalTwo', ('analysis', 'local_private'))
        active = 0
        peak = 0
        lock = threading.Lock()
        timeouts = []
        def invoke(spec, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                timeouts.append(spec.timeout)
            time.sleep(0.05)
            with lock:
                active -= 1
            return AdapterResult(spec.agent, spec.kind, stdout='{"valid": true}')
        self.manager.registry.invoke = invoke
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.manager._invoke_provider, spec, payload={'role': 'auditor'},
                prompt='Check', workspace=self.base) for spec in (one, two)]
            for f in futures:
                self.assertTrue(f.result(timeout=2).ok)
        self.assertEqual(peak, 1)
        self.assertTrue(all(t <= self.settings.autonomy_audit_timeout for t in timeouts))

    def test_invalid_integrator_cannot_finalize_with_an_aggregation(self):
        spec = self.provider('BrokenIntegrator')
        request = self.store.create_autonomous_request({'objective': 'Integrate actual evidence', 'workspace': str(self.base), 'lead_agent': 'Lead'})
        run = self.store.create_run({'run_id': 'integration', 'request': 'Integrate', 'lead_agent': 'Lead',
            'plan': {'tasks': [{'task_id': 'task', 'assigned_agent': 'BrokenIntegrator'}]}})
        self.store.update_autonomous_request(request['id'], orchestration_run_id=run['id'])
        self.manager.registry.invoke = lambda *a, **k: AdapterResult(spec.agent, spec.kind, returncode=1, stderr='provider failed')
        self.manager._integrate(request['id'], run['id'], spec, 'test')
        current = self.store.get_autonomous_request(request['id'])
        self.assertEqual(current['state'], 'WAITING')
        self.assertFalse(current['report'])
        self.assertIn('autonomy.integration_failed', {e['event_type'] for e in self.store.get_run(run['id'])['events']})
