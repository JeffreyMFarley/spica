"""boto3 <-> wand bridge for read-only AWS calls.

AWS clients that only issue ``describe_*`` / ``list_*`` calls can record and
replay their responses through wand without the Go proxy handling AWS traffic
directly. This shim intercepts calls at
``botocore.client.BaseClient._make_api_call`` — the point where botocore deals
in already-parsed Python dicts, on both the request (``operation_name`` +
``api_params``) and response side. That sidesteps the two problems with routing
signed AWS traffic through the Go proxy:

* the proxy can't sign SigV4 or terminate TLS, so it can't capture live AWS;
* EC2/ELB/RDS speak XML, but wand's store is JSON — intercepting the parsed
  dict avoids the wire format entirely.

Fixtures are written in wand's on-disk format: JSON Lines (``.jsonl``) with a
one-line JSON header followed by the one-line JSON body, content-addressed by a
BLAKE2b-128 hash of the normalized request. They live in the same
``__fixtures__/`` store as proxy-captured fixtures and remain readable by wand
CLI tooling.

Modes (``WAND_MODE``, default ``ci``):

* ``ci`` — replay only. A miss raises :class:`WandFixtureMiss`.
* ``capture`` — call real AWS, strip response noise, write the fixture pair.
* ``passthrough`` — call real AWS, write nothing.

Capture is necessarily Python-side (it makes the real call); replay reads the
committed files directly, so ``ci`` runs need no live credentials and no proxy.

Note the module is named ``boto3_shim`` (not ``boto3``) so it never shadows the
real ``boto3`` package when its directory is on ``sys.path``.
"""

import datetime
import json
import os
import re
from contextlib import contextmanager

import botocore.client

# Works both as a loose sibling module and when dropped into a package.
try:
    from client import WandFixtureMiss, normalized_hash
except ImportError:  # pragma: no cover - depends on import context
    from .client import WandFixtureMiss, normalized_hash

FIXTURES_ROOT = os.environ.get("WAND_FIXTURES", "__fixtures__")

# Response fields that change every call and must not end up in a fixture.
_NOISE_FIELDS = ("ResponseMetadata",)

# Wall-clock values must be neutralized before hashing, or a request whose
# params carry a "now"-relative window (e.g. CloudWatch GetMetricStatistics'
# StartTime/EndTime) hashes to a new digest on every run and never matches its
# committed fixture. This mirrors the Go proxy's `normalize_timestamps`
# transform (proxy/normalizer.go): the proxy applies it to proxy-routed
# services, but AWS traffic bypasses the proxy, so the shim must do it here.
# Matching Go, any ISO-8601 timestamp collapses to a single sentinel.
_TIMESTAMP_SENTINEL = "1970-01-01T00:00:00Z"
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)


def _normalize_scalar(value):
    """Collapse a single wall-clock value to the sentinel, else return as-is."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return _TIMESTAMP_SENTINEL
    if isinstance(value, str) and _TIMESTAMP_RE.search(value):
        return _TIMESTAMP_SENTINEL
    return value


def _normalize_request(node):
    """Deep copy of a request payload with every timestamp neutralized.

    Only the copy used for hashing/storage is normalized; the live AWS call
    still runs against the caller's original, real-time params.
    """
    if isinstance(node, dict):
        return {k: _normalize_request(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_normalize_request(v) for v in node]
    return _normalize_scalar(node)


def _json_default(obj):
    """Make AWS responses JSON-serializable (datetimes -> ISO, bytes -> str)."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    raise TypeError(f"cannot serialize {type(obj).__name__} into a wand fixture")


def _fixture_header(service):
    # Matches wand's proxy/store.go formatFixtureHeader (compact, one line).
    today = datetime.date.today().isoformat()
    return json.dumps(
        {"wand_version": "1", "service": service, "captured": today},
        separators=(",", ":"),
    ) + "\n"


def _request_payload(service, operation, params):
    """The canonical request that gets hashed and stored."""
    return {"service": service, "operation": operation, "params": params or {}}


def _strip_noise(response):
    return {k: v for k, v in response.items() if k not in _NOISE_FIELDS}


def _paths(service, digest):
    base = os.path.join(FIXTURES_ROOT, service)
    return (
        os.path.join(base, f"{digest}_req.jsonl"),
        os.path.join(base, f"{digest}_resp.jsonl"),
    )


def _read_fixture(service, digest):
    _, resp_path = _paths(service, digest)
    if not os.path.exists(resp_path):
        return None
    with open(resp_path, encoding="utf-8") as fh:
        text = fh.read()
    # Strip the one-line header, mirroring wand's stripHeader.
    lines = text.split("\n", 1)
    body = lines[1] if len(lines) > 1 and lines[0].strip().startswith("{") else text
    return json.loads(body)


def _write_fixture(service, digest, request, response):
    req_path, resp_path = _paths(service, digest)
    os.makedirs(os.path.dirname(req_path), exist_ok=True)

    normalized_req = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    with open(req_path, "w", encoding="utf-8") as fh:
        fh.write(_fixture_header(service) + normalized_req)
    with open(resp_path, "w", encoding="utf-8") as fh:
        fh.write(
            _fixture_header(service)
            + json.dumps(response, separators=(",", ":"), default=_json_default)
        )
    _update_index(service, digest, request)


def _update_index(service, digest, request):
    index_path = os.path.join(FIXTURES_ROOT, "index.json")
    index = {}
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as fh:
            index = json.load(fh) or {}
    index[digest] = {
        "scenario": f"{request['service']}:{request['operation']}",
        "service": service,
        "captured": datetime.date.today().isoformat(),
        "captured_by": "wand-boto3-shim/1.0.0",
        "tests": [],
        "request_summary": json.dumps(request, sort_keys=True, separators=(",", ":")),
    }
    os.makedirs(FIXTURES_ROOT, exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
        fh.write("\n")


def _replay_metadata():
    # boto3 internals (and some callers) expect this to exist.
    return {"HTTPStatusCode": 200, "RequestId": "wand-replay"}


@contextmanager
def intercept(mode=None):
    """Patch ``_make_api_call`` for the duration of the context.

    ``mode`` overrides ``WAND_MODE`` (default ``ci``). The patch is global to
    botocore, so every client and resource built inside the context is covered.
    """
    resolved = (mode or os.environ.get("WAND_MODE") or "ci").lower()
    original = botocore.client.BaseClient._make_api_call

    def _patched(self, operation_name, api_params):
        service = self._service_model.service_name
        request = _request_payload(service, operation_name, api_params)
        # Hash and store the normalized request so a "now"-relative window
        # produces a stable, replayable digest; the live call below still uses
        # the caller's original params.
        normalized = _normalize_request(request)
        digest = normalized_hash(normalized)

        if resolved == "ci":
            cached = _read_fixture(service, digest)
            if cached is None:
                raise WandFixtureMiss(
                    f"no fixture for {service}:{operation_name} ({digest})\n"
                    f"normalized request: "
                    f"{json.dumps(normalized, sort_keys=True, separators=(',', ':'))}"
                )
            return {**cached, "ResponseMetadata": _replay_metadata()}

        response = original(self, operation_name, api_params)
        if resolved == "capture":
            _write_fixture(service, digest, normalized, _strip_noise(response))
        return response

    botocore.client.BaseClient._make_api_call = _patched
    try:
        yield
    finally:
        botocore.client.BaseClient._make_api_call = original
