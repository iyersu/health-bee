"""Offline regression checks for Health-bee's local-only privacy boundary."""

from pathlib import Path
import unittest

from journal import model


ROOT = Path(__file__).resolve().parents[1]


class PrivacyTests(unittest.TestCase):
    def test_browser_code_uses_relative_api_paths_and_no_browser_token_storage(self):
        source = (ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        for forbidden in ("http://", "https://", "localStorage", "sessionStorage", "document.cookie", "Authorization"):
            self.assertNotIn(forbidden, source)
        self.assertIn("fetch(path", source)

    def test_vite_keeps_the_proxy_and_preview_on_loopback(self):
        source = (ROOT / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('host: "127.0.0.1"', source)
        self.assertIn('target: "http://127.0.0.1:8000"', source)
        self.assertIn("HEALTH_BEE_SESSION_FILE", source)
        self.assertNotIn("localStorage", source)

    def test_model_environment_clears_every_proxy_form_and_disables_cloud(self):
        environment = model.local_ollama_environment({
            "ALL_PROXY": "x", "HTTPS_PROXY": "x", "HTTP_PROXY": "x",
            "all_proxy": "x", "https_proxy": "x", "http_proxy": "x",
        })
        for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["OLLAMA_NO_CLOUD"], "1")
        self.assertEqual(environment["OLLAMA_HOST"], "127.0.0.1:11434")

    def test_sensitive_runtime_artifacts_are_ignored(self):
        ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertTrue({"data/", "*.db", "*.db-wal", "*.db-shm", "backups/", "exports/",
                         "local-evaluation/", ".venv/", "node_modules/", "dist/"}.issubset(ignored))


if __name__ == "__main__":
    unittest.main()
