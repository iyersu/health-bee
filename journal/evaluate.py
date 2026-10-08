"""Evaluate the fixed local model with synthetic extraction fixtures only.

This command never opens the journal database and never includes journal text
in its report. It sends only version-controlled synthetic fixture text to the
pinned loopback Ollama endpoint.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from journal import extraction, model, parse


FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "extraction_contract.json"
EVALUATION_TIMEOUT_SECONDS = 30


def _fixture_data(path):
    try:
        fixtures = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Could not read the synthetic evaluation fixtures.") from error
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Synthetic evaluation fixtures must be a nonempty list.")
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not isinstance(fixture.get("name"), str) or \
                not isinstance(fixture.get("raw_text"), str) or not isinstance(fixture.get("expected"), list):
            raise ValueError("Synthetic evaluation fixture has an invalid shape.")
    return fixtures


def _candidate_key(candidate):
    """Compare the field/value the user would see, without retaining evidence text."""
    return json.dumps({"field": candidate["field"], "value": candidate["value"]},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _summary_case(fixture, actual, elapsed_ms):
    expected = Counter(_candidate_key(candidate) for candidate in fixture["expected"])
    received = Counter(_candidate_key(candidate) for candidate in actual)
    matched = sum((expected & received).values())
    missing = sum((expected - received).values())
    unexpected = sum((received - expected).values())
    return {
        "fixture": fixture["name"],
        "status": "passed" if not missing and not unexpected else "mismatch",
        "expected_candidates": sum(expected.values()),
        "received_candidates": sum(received.values()),
        "matched_candidates": matched,
        "missing_candidates": missing,
        "unexpected_candidates": unexpected,
        "latency_ms": elapsed_ms,
    }


def evaluate_fixtures(fixture_path=FIXTURES, *, responder=None, clock_ns=None):
    """Return a privacy-safe report from a local synthetic-model evaluation.

    ``responder`` is injectable for offline tests. Production evaluation uses
    only ``model.generate_suggestion_json``, whose transport is pinned to
    127.0.0.1 and the fixed Qwen model.
    """
    fixtures = _fixture_data(fixture_path)
    status = model.model_status()
    responder = model.generate_suggestion_json if responder is None else responder
    clock_ns = time.monotonic_ns if clock_ns is None else clock_ns
    cases = []
    for fixture in fixtures:
        started = clock_ns()
        try:
            response = responder(parse._prompt(fixture["raw_text"]), timeout=EVALUATION_TIMEOUT_SECONDS)
            actual = extraction.decode_and_validate_suggestions(fixture["raw_text"], response)
        except extraction.ExtractionContractError:
            cases.append({"fixture": fixture["name"], "status": "invalid_output",
                          "expected_candidates": len(fixture["expected"]), "received_candidates": 0,
                          "matched_candidates": 0, "missing_candidates": len(fixture["expected"]),
                          "unexpected_candidates": 0,
                          "latency_ms": round((clock_ns() - started) / 1_000_000, 2)})
            continue
        cases.append(_summary_case(fixture, actual, round((clock_ns() - started) / 1_000_000, 2)))
    total_expected = sum(case["expected_candidates"] for case in cases)
    matched = sum(case["matched_candidates"] for case in cases)
    unexpected = sum(case["unexpected_candidates"] for case in cases)
    invalid = sum(case["status"] == "invalid_output" for case in cases)
    failures = [case["fixture"] for case in cases if case["status"] != "passed"]
    return {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixture_count": len(cases),
        "model": {key: status.get(key) for key in ("model", "digest", "model_context_limit", "configured_context_limit")},
        "metrics": {
            "field_value_accuracy": round(matched / total_expected, 4) if total_expected else None,
            "unexpected_suggestions": unexpected,
            "invalid_output_count": invalid,
            "invalid_output_rate": round(invalid / len(cases), 4),
            "mean_latency_ms": round(sum(case["latency_ms"] for case in cases) / len(cases), 2),
        },
        "release_decision": "manual_review_required" if failures else "manual_review_required_no_fixture_failures",
        "failed_fixtures": failures,
        "cases": cases,
    }


def write_report(path, report):
    """Write one owner-only report without replacing an existing result."""
    path = Path(path)
    if not path.parent.is_dir():
        raise ValueError("Evaluation report directory does not exist.")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("Evaluation report already exists; choose a new path.") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES,
                        help="Synthetic fixture JSON; defaults to the version-controlled contract.")
    parser.add_argument("--output", type=Path,
                        help="New owner-only JSON report path. Prints JSON when omitted.")
    args = parser.parse_args(argv)
    try:
        report = evaluate_fixtures(args.fixtures)
        if args.output is None:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            write_report(args.output, report)
            print(f"Synthetic local-model evaluation written to {args.output}")
        return 0
    except (ValueError, model.LocalModelError) as error:
        parser.exit(1, f"Local evaluation did not run: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
