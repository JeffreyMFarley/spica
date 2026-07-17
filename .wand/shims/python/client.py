"""A thin shim client for the wand proxy.

The shim's only job is protocol translation: serialize a call into a JSON HTTP
POST to the proxy and deserialize the response. All mode logic (ci / capture /
passthrough / livetest), normalization, hashing and fixture I/O live in the Go
proxy — never here. See the wand DESIGN doc for the architecture.

Contract (mirrors wand's proxy/server.go):

* POST any path to the proxy base URL with a JSON body.
* Route to a service by setting the ``X-Wand-Service`` header (defaults to
  ``http`` on the proxy side when omitted).
* In ``ci`` mode a fixture miss returns HTTP 404 with the normalized request
  echoed in the body, which we surface as :class:`WandFixtureMiss`.
"""

import hashlib
import json
import os
import socket
from urllib.parse import urlparse

import requests

DEFAULT_PROXY = os.environ.get("WAND_PROXY", "http://localhost:8877")


class WandError(RuntimeError):
    """Base error for wand shim failures."""


class WandFixtureMiss(WandError):
    """Raised in ci mode when no fixture matches the request (HTTP 404)."""


class WandProxy:
    """Minimal HTTP client that speaks the wand proxy contract."""

    def __init__(self, base_url=None, service="http", session=None):
        self.base_url = (base_url or DEFAULT_PROXY).rstrip("/")
        self.service = service
        self._session = session or requests.Session()

    def call(self, payload, service=None, timeout=5):
        """POST ``payload`` (a JSON-serializable dict) and return the response.

        Returns the parsed JSON response body. Raises :class:`WandFixtureMiss`
        on a ci-mode miss and :class:`WandError` on any other proxy failure.
        """
        svc = service or self.service
        try:
            resp = self._session.post(
                f"{self.base_url}/",
                json=payload,
                headers={"X-Wand-Service": svc},
                timeout=timeout,
            )
        except requests.RequestException as exc:  # network-level failure
            raise WandError(f"could not reach wand proxy at {self.base_url}: {exc}")

        if resp.status_code == 404:
            raise WandFixtureMiss(resp.text.strip())
        if resp.status_code >= 400:
            raise WandError(f"wand proxy returned {resp.status_code}: {resp.text.strip()}")
        return resp.json()

    def is_available(self, timeout=0.5):
        """Best-effort TCP check that the proxy is listening.

        Handy for a test harness that wants to skip (rather than fail) its wand
        suite when no proxy is running — the common case in a plain test run.
        """
        parsed = urlparse(self.base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False


def normalized_hash(payload):
    """Reproduce the proxy's request hash for a payload, offline.

    Matches wand's BLAKE2b-128 hash over the *normalized* request. This helper
    only reproduces the ``http`` service's normalization (no field removal),
    which is enough to author/verify fixtures by hand. Services with
    ``remove_fields`` / sentinels / patterns must be normalized by the proxy in
    capture mode instead.

    The normalization mirrors Go's ``json.Marshal``: object keys sorted, compact
    separators, non-ASCII left as-is (HTML escaping of <>& is not reproduced
    here, so avoid those characters in hand-authored fixtures).
    """
    normalized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.blake2b(normalized, digest_size=16).hexdigest()
    return "-".join(digest[i : i + 4] for i in range(0, len(digest), 4))
