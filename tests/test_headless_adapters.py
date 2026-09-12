"""Headless adapter regressions using synthetic processes and local fixtures."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from agent_mesh_adapters import (
    MAX_PROVIDER_OUTPUT,
    AdapterRegistry,
    AdapterResult,
    AdapterSpec,
    _cli_reports_authenticated,
    _ollama_model_available,
    classify_adapter_failure,
    parse_worker_result,
)
from agent_mesh_core import MeshStore
from test_agent_mesh import make_settings


class AdapterFailureTests(unittest.TestCase):
    def result(self, **kwargs):
        return AdapterResult(agent="Synthetic", kind="command", **kwargs)

    def test_zero_exit_provider_error_envelopes_are_failures(self):
        cases = [
            ({"error": {"code": 401, "message": "Authentication required"}}, "authentication"),
            ({"type": "error", "error": {"name": "IneligibleTierError"}}, "model_unavailable"),
            ({"error": {"code": "model_not_found"}}, "model_unavailable"),
            ({"type": "result", "is_error": True, "result": "API Error: 429"}, "quota"),
            ({"type": "result", "subtype": "error_during_execution", "errors": ["Permission denied"]}, "permission"),
            ({"type": "turn.failed", "error": {"message": "ECONNREFUSED"}}, "network"),
            ({"type": "error", "message": "request timed out"}, "timeout"),
            ({"type": "error", "message": "operation cancelled"}, "cancelled"),
            ({"error": "adapter unavailable"}, "unavailable"),
            ({"status": "error", "message": "unexpected provider response"}, "provider_error"),
            ({"jsonrpc": "2.0", "id": 2, "result": {"isError": True, "content": [{"type": "text", "text": "quota exceeded"}]}}, "quota"),
        ]
        for envelope, expected in cases:
            with self.subTest(envelope=envelope):
                result = self.result(stdout=json.dumps(envelope))
                self.assertFalse(result.ok)
                self.assertEqual(result.failure_code, expected)
                self.assertEqual(classify_adapter_failure(result), expected)
                self.assertIsNone(parse_worker_result(result))

    def test_stream_error_and_stderr_error_are_detected(self):
        stream = '\n'.join([
            json.dumps({"type": "thread.started", "thread_id": "synthetic"}),
            json.dumps({"type": "turn.failed", "error": {"message": "model unavailable"}}),
        ])
        self.assertEqual(self.result(stdout=stream).failure_code, "model_unavailable")
        self.assertEqual(self.result(stdout='{"summary":"done"}', stderr='Error: invalid API key').failure_code, "authentication")

    def test_quoted_errors_in_successful_task_content_are_not_failures(self):
        outputs = [
            {"action": "complete", "summary": 'Explained {"error":"Authentication required"}', "errors": []},
            {"summary": "Documented errors", "example": {"error": "rate limit exceeded"}},
            {"type": "text", "part": {"type": "text", "text": 'Example: {"type":"error","message":"quota"}'}},
            {"type": "result", "is_error": False, "result": "Error: authentication required was the reported symptom."},
            {"role": "user", "error": "quoted task data"},
            {"role": [], "action": [], "message": "unusual but valid output"},
        ]
        for output in outputs:
            with self.subTest(output=output):
                self.assertTrue(self.result(stdout=json.dumps(output)).ok)
        self.assertTrue(self.result(stdout='The task mentioned "Error: quota exceeded".').ok)
        self.assertTrue(self.result(stdout='> Error: quota exceeded').ok)

    def test_lifecycle_only_output_is_not_a_completed_worker_result(self):
        outputs = [
            "provider execution started\n",
            json.dumps({"type": "task.progress", "summary": "provider execution started"}),
            json.dumps({"message": "Reading additional input from stdin..."}),
        ]
        for output in outputs:
            with self.subTest(output=output):
                result = self.result(stdout=output)
                self.assertTrue(result.ok)
                self.assertIsNone(parse_worker_result(result))

    def test_exit_status_and_empty_output_have_stable_categories(self):
        cases = [
            ({}, "empty_output"),
            ({"returncode": 127}, "unavailable"),
            ({"returncode": 126, "stderr": "Permission denied"}, "permission"),
            ({"returncode": 1, "stderr": "HTTP Error 401: Unauthorized"}, "authentication"),
            ({"returncode": 1, "stderr": "Insufficient credits"}, "quota"),
            ({"returncode": 1, "stderr": "unknown failure"}, "provider_error"),
            ({"returncode": -signal.SIGINT}, "cancelled"),
            ({"timed_out": True, "stdout": "partial result"}, "timeout"),
            ({"cancelled": True, "timed_out": True, "stdout": "partial result"}, "cancelled"),
        ]
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.result(**kwargs).failure_code, expected)


class HeadlessAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mesh-headless-adapter-")
        self.base = Path(self.tmp.name)
        self.settings = make_settings(self.base)
        self.store = MeshStore(self.settings)
        self.registry = AdapterRegistry(self.store, self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, code, *, timeout=3.0, heartbeat=None, cancel_check=None):
        spec = AdapterSpec("Synthetic", "command", (sys.executable, "-c", code),
                           timeout=timeout, heartbeat_interval=0.1,
                           auth_env="SYNTHETIC_AUTH")
        return self.registry.invoke(spec, prompt="synthetic task", payload={}, workspace=self.base,
                                    heartbeat=heartbeat, cancel_check=cancel_check)

    def test_heartbeat_output_is_retained_exactly_once(self):
        heartbeats = []
        output = self.invoke(
            "import sys,time; print('first',flush=True); print('warning-one',file=sys.stderr,flush=True); "
            "time.sleep(.35); print('second',flush=True); print('warning-two',file=sys.stderr,flush=True)",
            heartbeat=lambda: heartbeats.append(time.monotonic()),
        )
        self.assertTrue(output.ok)
        self.assertGreaterEqual(len(heartbeats), 2)
        self.assertEqual(output.stdout, "first\nsecond\n")
        self.assertEqual(output.stderr, "warning-one\nwarning-two\n")

    def test_large_output_is_drained_with_bounded_capture(self):
        output = self.invoke(
            f"import os; os.write(1,b'x'*{MAX_PROVIDER_OUTPUT * 3}); os.write(2,b'y'*{MAX_PROVIDER_OUTPUT * 3})"
        )
        self.assertEqual(output.returncode, 0)
        self.assertEqual(output.stdout, "x" * MAX_PROVIDER_OUTPUT)
        self.assertEqual(output.stderr, "y" * MAX_PROVIDER_OUTPUT)

    def test_timeout_retains_partial_output_once_and_reaps_process(self):
        output = self.invoke("import time; print('partial',flush=True); time.sleep(30)", timeout=0.3)
        self.assertEqual(output.failure_code, "timeout")
        self.assertEqual(output.stdout, "partial\n")
        self.assertLess(output.duration_seconds, 2)

    def test_cancellation_is_distinct_from_timeout(self):
        started = time.monotonic()
        output = self.invoke("import time; print('partial',flush=True); time.sleep(30)",
                             cancel_check=lambda: time.monotonic() - started > 0.2)
        self.assertEqual(output.failure_code, "cancelled")
        self.assertTrue(output.cancelled)
        self.assertFalse(output.timed_out)
        self.assertEqual(output.stdout, "partial\n")

    def test_exited_parent_with_inherited_pipes_cannot_stall_capture(self):
        output = self.invoke(
            "import os,time; child=os.fork(); "
            "time.sleep(30) if child == 0 else print('parent-finished',flush=True)",
            timeout=5,
        )
        self.assertEqual(output.stdout, "parent-finished\n")
        self.assertLess(output.duration_seconds, 2)

    def test_child_environment_is_headless_but_preserves_auth_and_service_access(self):
        names = ["DISPLAY", "WAYLAND_DISPLAY", "SWAYSOCK", "SYNTHETIC_AUTH", "DBUS_SESSION_BUS_ADDRESS", "NO_OPEN_BROWSER"]
        with patch.dict(os.environ, {"DISPLAY": ":77", "WAYLAND_DISPLAY": "wayland-test", "SWAYSOCK": "test-socket",
                                     "SYNTHETIC_AUTH": "test-marker", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/synthetic"}):
            output = self.invoke(
                "import os,json,sys; print(json.dumps({'env':{k:os.environ.get(k) for k in " + repr(names) + "},"
                "'stdin_empty':sys.stdin.read()=='','new_session':os.getsid(0)==os.getpid()}))"
            )
        document = json.loads(output.stdout)
        self.assertEqual(document["env"], dict(zip(names, [None, None, None, "test-marker", "unix:path=/synthetic", "1"])))
        self.assertTrue(document["stdin_empty"])
        self.assertTrue(document["new_session"])

    def test_status_probe_is_headless_and_noninteractive(self):
        with patch.dict(os.environ, {"DISPLAY": ":77", "WAYLAND_DISPLAY": "wayland-test", "SWAYSOCK": "test"}), \
             patch("agent_mesh_adapters.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "Logged in", "")) as run:
            self.assertTrue(_cli_reports_authenticated(("synthetic", "status")))
        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SWAYSOCK"):
            self.assertNotIn(name, kwargs["env"])

    def test_command_and_mcp_preflight_require_executable_permission(self):
        command = self.base / "nonexecutable"
        command.write_text("#!/bin/sh\nexit 0\n")
        command.chmod(0o600)
        for kind in ("command", "mcp"):
            spec = AdapterSpec("Synthetic", kind, (str(command),), tool="synthetic")
            self.assertFalse(spec.available)
            self.assertFalse(self.registry.preflight(spec)[0])
            command.chmod(0o700)
            self.assertTrue(spec.available)
            self.assertTrue(self.registry.preflight(spec)[0])
            command.chmod(0o600)

    def test_ollama_model_probe_requires_exact_explicit_tag(self):
        cases = [
            ("model:7b", ["model:8b"], False),
            ("model:7b", ["model:7b"], True),
            ("model", ["model:8b"], False),
            ("model", ["model:latest"], True),
            ("registry:5000/model:7b", ["registry:5000/model:8b"], False),
        ]
        for model, installed, expected in cases:
            with self.subTest(model=model, installed=installed):
                response = io.BytesIO(json.dumps({"models": [{"name": name} for name in installed]}).encode())
                with patch("agent_mesh_adapters.urllib.request.urlopen", return_value=response):
                    self.assertEqual(_ollama_model_available("http://localhost:11434/api/chat", model), expected)

    def test_refresh_updates_custom_adapter_without_advancing_client_heartbeat(self):
        self.store.register_agent({"name": "Custom", "capabilities": ["testing"],
                                   "metadata": {"autonomy_adapter": {"kind": "command", "argv": [sys.executable, "-c", "pass"]}}})
        self.store.register_agent({"name": "GUI", "capabilities": ["testing"]})
        with self.store.transaction() as database:
            database.execute("UPDATE agents SET last_seen_at='2000-01-01T00:00:00+00:00'")
        before = {agent["name"]: agent["last_seen_at"] for agent in self.store.list_agents()}
        self.registry.refresh()
        custom = self.store.get_agent("Custom")
        gui = self.store.get_agent("GUI")
        self.assertTrue(custom["metadata"]["autonomy"]["available"])
        self.assertTrue(custom["capabilities"]["autonomous_worker"])
        self.assertEqual(custom["metadata"]["autonomy"]["adapter_source"], "registered")
        self.assertFalse(gui["metadata"]["autonomy"]["available"])
        self.assertEqual(custom["last_seen_at"], before["Custom"])
        self.assertEqual(gui["last_seen_at"], before["GUI"])

    def test_mcp_capture_is_bounded_and_preserves_protocol_stdin(self):
        code = "import os,sys,json; data=sys.stdin.buffer.read(); print(json.dumps({'received':len(data)>0,'display':os.environ.get('DISPLAY')}))"
        spec = AdapterSpec("SyntheticMCP", "mcp", (sys.executable, "-c", code), tool="synthetic", timeout=3)
        with patch.dict(os.environ, {"DISPLAY": ":77"}):
            result = self.registry.invoke(spec, prompt="test", payload={}, workspace=self.base)
        self.assertTrue(result.ok)
        self.assertEqual(json.loads(result.stdout), {"received": True, "display": None})


if __name__ == "__main__":
    unittest.main()
