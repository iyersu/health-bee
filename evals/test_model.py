"""Offline checks for the fixed local-only Ollama boundary."""

import subprocess
import unittest
from unittest.mock import patch

from journal import model


class _Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def read(self, size):
        return self.body


class _Connection:
    def __init__(self, host, port, *, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.requested = None

    def request(self, method, path, body=None, headers=None):
        self.requested = (method, path, body, headers)

    def getresponse(self):
        return _Response(200, b'{"models":[]}')

    def close(self):
        pass


class ModelTests(unittest.TestCase):
    def test_transport_is_pinned_to_loopback_without_redirects(self):
        created = []

        def factory(*args, **kwargs):
            connection = _Connection(*args, **kwargs)
            created.append(connection)
            return connection

        self.assertEqual(model._request("GET", "/api/tags", connection_factory=factory), {"models": []})
        self.assertEqual((created[0].host, created[0].port), ("127.0.0.1", 11434))
        self.assertNotIn("Proxy", " ".join(created[0].requested[3]))
        with self.assertRaises(model.LocalModelError):
            model._request("GET", "/unapproved", connection_factory=factory)

    def test_status_requires_the_pinned_installed_model(self):
        with patch("journal.model._request", return_value={"models": []}):
            with self.assertRaises(model.LocalModelUnavailable):
                model.model_status()
        responses = iter([
            {"models": [{"name": "qwen3:4b", "digest": "sha256:" + "a" * 64}]},
            {"model_info": {"qwen3.context_length": 32768}},
        ])
        with patch("journal.model._request", side_effect=lambda *args, **kwargs: next(responses)):
            self.assertEqual(model.model_status(), {
                "status": "ready", "model": "qwen3:4b", "digest": "sha256:" + "a" * 64,
                "model_context_limit": 32768, "configured_context_limit": 2048,
            })

    def test_status_normalizes_the_current_bare_sha256_digest(self):
        responses = iter([
            {"models": [{"name": "qwen3:4b", "digest": "B" * 64}]},
            {"model_info": {"qwen3.context_length": 262144}},
        ])
        with patch("journal.model._request", side_effect=lambda *args, **kwargs: next(responses)):
            status = model.model_status()
        self.assertEqual(status["digest"], "sha256:" + "b" * 64)

    def test_benchmark_uses_only_its_fixed_synthetic_prompt(self):
        generated = {"model": "qwen3:4b", "done": True, "total_duration": 2_000_000,
                     "load_duration": 1_000_000, "prompt_eval_count": 9, "eval_count": 3}
        with patch("journal.model.model_status", return_value={"status": "ready", "model": "qwen3:4b",
              "digest": "sha256:" + "b" * 64, "model_context_limit": 32768,
              "configured_context_limit": 2048}), patch("journal.model._request", return_value=generated) as request, \
             patch("journal.model.ollama_memory_bytes", return_value=1234):
            result = model.benchmark_qwen3_4b()
        payload = request.call_args.args[2]
        self.assertEqual(payload["prompt"], model.SYNTHETIC_BENCHMARK_PROMPT)
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertEqual(payload["options"]["num_ctx"], 2048)
        self.assertEqual((result["ollama_total_duration_ms"], result["ollama_process_memory_bytes"]), (2.0, 1234))

    def test_suggestion_generation_is_pinned_and_bounded(self):
        generated = {"model": "qwen3:4b", "done": True, "response": "{\"schema_version\":1,\"suggestions\":[]}"}
        with patch("journal.model._request", return_value=generated) as request:
            self.assertEqual(model.generate_suggestion_json("Synthetic prompt", timeout=9), generated["response"])
        payload = request.call_args.args[2]
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertEqual(payload["format"], "json")
        self.assertEqual(payload["options"]["num_predict"], 512)
        self.assertEqual(request.call_args.kwargs["timeout"], 9)

    def test_transport_timeout_has_a_distinct_local_error(self):
        with patch("journal.model.http.client.HTTPConnection", side_effect=TimeoutError("synthetic timeout")):
            with self.assertRaises(model.LocalModelTimeout):
                model._request("GET", "/api/tags")

    def test_server_environment_disables_cloud_and_proxies(self):
        environment = model.local_ollama_environment({"HTTP_PROXY": "http://proxy", "KEEP": "yes"})
        self.assertEqual(environment["OLLAMA_NO_CLOUD"], "1")
        self.assertEqual(environment["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertEqual(environment["NO_PROXY"], "*")
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertEqual(environment["KEEP"], "yes")

    def test_unavailable_server_is_a_clear_nonfatal_status(self):
        with patch("journal.model.http.client.HTTPConnection", side_effect=OSError("offline")):
            with self.assertRaises(model.LocalModelUnavailable):
                model.model_status()
        with patch("journal.model.shutil.which", return_value=None):
            with self.assertRaises(model.LocalModelUnavailable):
                model.serve_local_ollama()

    def test_serve_passes_the_restricted_environment_to_ollama(self):
        with patch("journal.model.shutil.which", return_value="/synthetic/ollama"), \
             patch("journal.model.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(model.serve_local_ollama(), 0)
        self.assertEqual(run.call_args.args[0], ["/synthetic/ollama", "serve"])
        self.assertEqual(run.call_args.kwargs["env"]["OLLAMA_NO_CLOUD"], "1")


if __name__ == "__main__":
    unittest.main()
