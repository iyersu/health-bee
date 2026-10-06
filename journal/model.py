"""Run Health-bee's optional Ollama model only on the local loopback interface."""

import argparse
from datetime import datetime, timezone
import http.client
import json
import os
import shutil
import subprocess
import time


OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
MODEL_NAME = "qwen3:4b"
BENCHMARK_CONTEXT_LIMIT = 2048
MAX_RESPONSE_BYTES = 1024 * 1024
SYNTHETIC_BENCHMARK_PROMPT = (
    "This is a synthetic local benchmark. Reply with exactly: local benchmark ok"
)


class LocalModelError(Exception):
    """The loopback model returned an invalid or unsafe response."""


class LocalModelUnavailable(LocalModelError):
    """Ollama is not listening locally or the required model is not installed."""


def _json_object(data):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        result = json.loads(data, object_pairs_hook=unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise LocalModelError("Ollama returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise LocalModelError("Ollama returned an unexpected response.")
    return result


def _request(method, path, payload=None, *, timeout=30, connection_factory=None):
    """Make one direct loopback request. http.client never follows redirects."""
    if path not in {"/api/tags", "/api/show", "/api/generate"}:
        raise LocalModelError("Unexpected Ollama API path.")
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "Connection": "close"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection_factory = http.client.HTTPConnection if connection_factory is None else connection_factory
    connection = None
    try:
        connection = connection_factory(OLLAMA_HOST, OLLAMA_PORT, timeout=timeout)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise LocalModelError("Ollama redirect rejected.")
        response_body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise LocalModelError("Ollama response exceeds the local safety limit.")
        if response.status != 200:
            raise LocalModelUnavailable("Local Ollama service or selected model is unavailable.")
        return _json_object(response_body)
    except (OSError, http.client.HTTPException) as error:
        raise LocalModelUnavailable("Local Ollama service is unavailable.") from error
    finally:
        if connection is not None:
            connection.close()


def _installed_model():
    tags = _request("GET", "/api/tags")
    models = tags.get("models")
    if not isinstance(models, list):
        raise LocalModelError("Ollama did not return its local model list.")
    for model in models:
        if isinstance(model, dict) and model.get("name") == MODEL_NAME:
            digest = model.get("digest")
            if not isinstance(digest, str):
                raise LocalModelError("Installed model has no valid digest.")
            digest_value = digest.removeprefix("sha256:")
            if len(digest_value) != 64 or any(character not in "0123456789abcdef" for character in digest_value.lower()):
                raise LocalModelError("Installed model has no valid digest.")
            return {"name": MODEL_NAME, "digest": f"sha256:{digest_value.lower()}"}
    raise LocalModelUnavailable(f"Required local model {MODEL_NAME} is not installed.")


def _context_limit(details):
    info = details.get("model_info")
    if not isinstance(info, dict):
        return None
    limits = [value for key, value in info.items()
              if isinstance(key, str) and key.endswith(".context_length") and type(value) is int]
    return max(limits) if limits else None


def model_status():
    """Return local model metadata without sending journal or benchmark text."""
    model = _installed_model()
    details = _request("POST", "/api/show", {"name": MODEL_NAME})
    return {
        "status": "ready",
        "model": model["name"],
        "digest": model["digest"],
        "model_context_limit": _context_limit(details),
        "configured_context_limit": BENCHMARK_CONTEXT_LIMIT,
    }


def ollama_memory_bytes():
    """Best-effort resident-memory metric for the local Ollama server process."""
    try:
        result = subprocess.run(["ps", "-axo", "pid=,rss=,command="], check=False,
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    memory = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and "ollama serve" in fields[2]:
            try:
                memory.append(int(fields[1]) * 1024)
            except ValueError:
                pass
    return max(memory) if memory else None


def _duration_ms(value):
    if type(value) not in (int, float) or value < 0:
        return None
    return round(value / 1_000_000, 2)


def benchmark_qwen3_4b():
    """Benchmark only a fixed synthetic prompt and return non-sensitive metrics."""
    status = model_status()
    started = time.monotonic_ns()
    response = _request("POST", "/api/generate", {
        "model": MODEL_NAME,
        "prompt": SYNTHETIC_BENCHMARK_PROMPT,
        "stream": False,
        "think": False,
        "keep_alive": "30s",
        "options": {"num_ctx": BENCHMARK_CONTEXT_LIMIT, "num_predict": 16, "temperature": 0},
    }, timeout=120)
    elapsed = time.monotonic_ns() - started
    if response.get("model") != MODEL_NAME or response.get("done") is not True:
        raise LocalModelError("Ollama did not complete the local benchmark.")
    return {
        **status,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_latency_ms": round(elapsed / 1_000_000, 2),
        "ollama_total_duration_ms": _duration_ms(response.get("total_duration")),
        "ollama_load_duration_ms": _duration_ms(response.get("load_duration")),
        "ollama_process_memory_bytes": ollama_memory_bytes(),
        "prompt_tokens": response.get("prompt_eval_count"),
        "generated_tokens": response.get("eval_count"),
    }


def local_ollama_environment(base=None):
    """Build an Ollama environment that binds loopback and disables cloud features."""
    environment = dict(os.environ if base is None else base)
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
        environment.pop(name, None)
    environment.update({"OLLAMA_NO_CLOUD": "1", "OLLAMA_HOST": f"{OLLAMA_HOST}:{OLLAMA_PORT}",
                        "NO_PROXY": "*", "no_proxy": "*"})
    return environment


def serve_local_ollama():
    executable = shutil.which("ollama")
    if executable is None:
        raise LocalModelUnavailable("Ollama is not installed.")
    return subprocess.run([executable, "serve"], env=local_ollama_environment(), check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run Ollama on loopback with cloud features disabled.")
    commands.add_parser("status", help="Check the fixed local model without sending prompts.")
    commands.add_parser("benchmark", help="Run a fixed synthetic benchmark; no journal text is sent.")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return serve_local_ollama()
        result = model_status() if args.command == "status" else benchmark_qwen3_4b()
        print(json.dumps(result, sort_keys=True))
        return 0
    except LocalModelError as error:
        parser.exit(1, f"Local model unavailable: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
