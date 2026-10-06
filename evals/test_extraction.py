"""Offline contract checks using only synthetic responses and fixture text."""

import json
from pathlib import Path
import socket
import unittest
from unittest.mock import Mock, patch

from journal import extraction


FIXTURES = Path(__file__).with_name("fixtures") / "extraction_contract.json"


class ExtractionContractTests(unittest.TestCase):
    def test_synthetic_fixtures_match_the_contract(self):
        fixtures = json.loads(FIXTURES.read_text())
        self.assertGreaterEqual(len(fixtures), 15)
        for fixture in fixtures:
            with self.subTest(fixture=fixture["name"]):
                self.assertEqual(extraction.validate_suggestions(fixture["raw_text"], fixture["response"]),
                                 fixture["expected"])

    def test_schema_document_matches_supported_fields(self):
        schema = json.loads(Path(extraction.__file__).with_name("extraction_schema.json").read_text())
        self.assertEqual(schema["properties"]["schema_version"]["const"], extraction.SCHEMA_VERSION)
        self.assertEqual(set(schema["properties"]["suggestions"]["items"]["properties"]["field"]["enum"]),
                         extraction.ALLOWED_FIELDS)

    def test_invented_or_unsupported_fields_are_rejected(self):
        raw_text = "Synthetic note: spotting today."
        for field, value in (("cycle_phase", "Ovulatory"), ("period_status", "confirmed"),
                             ("diagnosis", "healthy"), ("bleeding", "heavy")):
            response = {"schema_version": 1, "suggestions": [{"field": field, "value": value,
                        "evidence": "spotting"}]}
            with self.subTest(field=field), self.assertRaises(extraction.ExtractionContractError):
                extraction.validate_suggestions(raw_text, response)

    def test_invalid_schema_and_evidence_are_rejected(self):
        raw_text = "Synthetic note: feeling calm."
        cases = [
            {"schema_version": 2, "suggestions": []},
            {"schema_version": 1, "suggestions": [], "extra": True},
            {"schema_version": 1, "suggestions": [{"field": "mood", "value": "calm", "evidence": "invented"}]},
            {"schema_version": 1, "suggestions": [{"field": "mood", "value": "tired", "evidence": "feeling calm"}]},
            {"schema_version": 1, "suggestions": [{"field": "energy", "value": 11, "evidence": "11"}]},
        ]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(extraction.ExtractionContractError):
                extraction.validate_suggestions(raw_text, response)

    def test_mocked_timeout_is_nonfatal_and_no_network_is_opened(self):
        responder = Mock(side_effect=TimeoutError())
        with patch.object(socket, "create_connection", side_effect=AssertionError("network call")):
            with self.assertRaises(extraction.ExtractionUnavailable):
                extraction.request_suggestions("Synthetic note.", responder, timeout_seconds=1)
        responder.assert_called_once_with("Synthetic note.", timeout_seconds=1)

    def test_mocked_response_is_validated_before_returning(self):
        response = {"schema_version": 1, "suggestions": [{"field": "mood", "value": "calm",
                    "evidence": "calm"}]}
        responder = Mock(return_value=response)
        self.assertEqual(extraction.request_suggestions("Synthetic calm note.", responder), response["suggestions"])


if __name__ == "__main__":
    unittest.main()
