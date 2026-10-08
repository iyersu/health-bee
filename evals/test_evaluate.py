"""Offline checks for synthetic local-model evaluation accounting."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from journal import evaluate


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixtures = Path(self.temp.name) / "fixtures.json"
        self.fixtures.write_text(json.dumps([
            {"name": "expected candidate", "raw_text": "Synthetic calm note.",
             "expected": [{"field": "mood", "value": "calm", "evidence": "calm"}]},
            {"name": "abstain", "raw_text": "Synthetic empty note.", "expected": []},
        ]))
        self.status = {"status": "ready", "model": "qwen3:4b", "digest": "sha256:" + "a" * 64,
                       "model_context_limit": 2048, "configured_context_limit": 2048}

    def test_reports_matches_and_unexpected_suggestions_without_fixture_text(self):
        responses = iter([
            json.dumps({"schema_version": 1, "suggestions": [
                {"field": "mood", "value": "calm", "evidence": "calm"}]}),
            json.dumps({"schema_version": 1, "suggestions": [
                {"field": "tags", "value": "empty", "evidence": "empty"}]}),
        ])
        with patch("journal.evaluate.model.model_status", return_value=self.status):
            report = evaluate.evaluate_fixtures(self.fixtures, responder=lambda prompt, **_: next(responses))
        self.assertEqual(report["metrics"]["field_value_accuracy"], 1.0)
        self.assertEqual(report["metrics"]["unexpected_suggestions"], 1)
        self.assertEqual(report["failed_fixtures"], ["abstain"])
        self.assertNotIn("Synthetic calm note.", json.dumps(report))
        self.assertEqual(report["release_decision"], "manual_review_required")

    def test_invalid_model_output_is_counted_and_never_raises(self):
        with patch("journal.evaluate.model.model_status", return_value=self.status):
            report = evaluate.evaluate_fixtures(self.fixtures, responder=lambda prompt, **_: "not json")
        self.assertEqual(report["metrics"]["invalid_output_count"], 2)
        self.assertEqual(report["metrics"]["invalid_output_rate"], 1.0)
        self.assertEqual([case["status"] for case in report["cases"]], ["invalid_output", "invalid_output"])

    def test_report_is_owner_only_and_does_not_overwrite(self):
        target = Path(self.temp.name) / "report.json"
        evaluate.write_report(target, {"fixture_count": 1})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            evaluate.write_report(target, {"fixture_count": 2})
        self.assertEqual(json.loads(target.read_text()), {"fixture_count": 1})


if __name__ == "__main__":
    unittest.main()
