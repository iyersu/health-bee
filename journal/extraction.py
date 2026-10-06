"""Validate optional, evidence-backed local-model suggestions without storing them.

This module has no transport code. Day 11 will supply a local Ollama responder;
until then, tests inject synthetic responders directly. Valid suggestions remain
candidates only and cannot modify a journal entry.
"""

import math


SCHEMA_VERSION = 1
MAX_SUGGESTIONS = 20
MAX_EVIDENCE_LENGTH = 500
TEXT_FIELDS = frozenset({"mood", "meds", "food", "tags"})
ALLOWED_FIELDS = TEXT_FIELDS | {"sleep_hours", "energy", "bleeding", "observations"}
BLEEDING_VALUES = frozenset({"none", "spotting", "light", "moderate", "heavy"})


class ExtractionContractError(ValueError):
    """A proposed response is not safe to show as a suggestion."""


class ExtractionUnavailable(Exception):
    """The injected local responder did not produce a response in time."""


def _text(value, name, *, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ExtractionContractError(f"{name} must be a nonempty string within its limit.")
    return value


def _quoted_in_entry(raw_text, evidence):
    if evidence not in raw_text:
        raise ExtractionContractError("Suggestion evidence must be an exact excerpt from the entry.")


def _value_in_evidence(value, evidence):
    if str(value).casefold() not in evidence.casefold():
        raise ExtractionContractError("Suggested value must appear in its supporting excerpt.")


def _validate_value(field, value, evidence):
    if field in TEXT_FIELDS:
        value = _text(value, field)
        _value_in_evidence(value, evidence)
        return value
    if field == "sleep_hours":
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 24:
            raise ExtractionContractError("sleep_hours must be a finite number from 0 to 24.")
        _value_in_evidence(value, evidence)
        return value
    if field == "energy":
        if type(value) is not int or not 0 <= value <= 10:
            raise ExtractionContractError("energy must be an integer from 0 to 10.")
        _value_in_evidence(value, evidence)
        return value
    if field == "bleeding":
        if value not in BLEEDING_VALUES:
            raise ExtractionContractError("bleeding must be a known flow value.")
        _value_in_evidence(value, evidence)
        return value
    if field == "observations":
        if not isinstance(value, dict) or set(value) - {"symptom", "severity", "notes"}:
            raise ExtractionContractError("Observation values only accept symptom, severity, and notes.")
        symptom = _text(value.get("symptom"), "symptom")
        _value_in_evidence(symptom, evidence)
        severity = value.get("severity")
        if severity is not None and (type(severity) is not int or not 0 <= severity <= 10):
            raise ExtractionContractError("Observation severity must be an integer from 0 to 10 or omitted.")
        notes = value.get("notes")
        if notes is not None:
            _text(notes, "notes")
        result = {"symptom": symptom}
        if severity is not None:
            result["severity"] = severity
        if notes is not None:
            result["notes"] = notes
        return result
    raise ExtractionContractError("Suggestion field is not allowed.")


def validate_suggestions(raw_text, response):
    """Return normalized candidate suggestions after strict structural validation.

    Evidence must quote the submitted entry exactly. The allowlist deliberately
    excludes menstruation status and cycle phase: flow and journal prose alone cannot
    establish either. The caller must still require user review before storage.
    """
    _text(raw_text, "raw_text", maximum=50_000)
    if not isinstance(response, dict) or set(response) != {"schema_version", "suggestions"}:
        raise ExtractionContractError("Response must contain only schema_version and suggestions.")
    if response["schema_version"] != SCHEMA_VERSION:
        raise ExtractionContractError("Unsupported suggestion schema version.")
    suggestions = response["suggestions"]
    if not isinstance(suggestions, list) or len(suggestions) > MAX_SUGGESTIONS:
        raise ExtractionContractError("suggestions must be a list within the configured limit.")
    normalized = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict) or set(suggestion) != {"field", "value", "evidence"}:
            raise ExtractionContractError("Each suggestion must contain only field, value, and evidence.")
        field = suggestion["field"]
        if field not in ALLOWED_FIELDS:
            raise ExtractionContractError("Suggestion field is not allowed.")
        evidence = _text(suggestion["evidence"], "evidence", maximum=MAX_EVIDENCE_LENGTH)
        _quoted_in_entry(raw_text, evidence)
        normalized.append({"field": field, "value": _validate_value(field, suggestion["value"], evidence),
                           "evidence": evidence})
    return normalized


def request_suggestions(raw_text, responder, *, timeout_seconds=30):
    """Validate a response from an injected responder; no network is opened here."""
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive finite number.")
    try:
        response = responder(raw_text, timeout_seconds=timeout_seconds)
    except TimeoutError as error:
        raise ExtractionUnavailable("Local model request timed out.") from error
    return validate_suggestions(raw_text, response)
