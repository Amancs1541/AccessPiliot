#!/usr/bin/env python3
"""AccessPilot Active Directory connectivity agent — Phase 1 (connectivity only).

Runs inside your corporate network, next to (or with line-of-sight to) your domain controller, and calls OUT to
your AccessPilot instance over plain HTTPS on an interval. Nothing ever connects INTO this machine — no inbound
firewall rule is needed on your network for this to work.

This phase does NOT talk to Active Directory at all yet — it only proves the agent can reach AccessPilot. LDAP
sync, group membership writes, and provisioning arrive in a later phase; running this script today is purely a
connectivity test.

Stdlib only, no pip install needed — Python 3.8+.

Configure via environment variables:
  ACCESSPILOT_URL          Your AccessPilot instance's base URL, e.g. https://accesspilot.example.com
  ACCESSPILOT_PROVIDER_ID  The Active Directory provider's id (shown on the Providers page)
  ACCESSPILOT_AGENT_KEY    The agent key generated for that provider (shown once, at generation time)

Usage:
  python accesspilot_agent.py            # runs forever, heartbeats every 60s
  python accesspilot_agent.py --once     # sends a single heartbeat and exits (0 on success, 1 on failure)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

HEARTBEAT_INTERVAL_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 10


def _log(message: str) -> None:
    print(f"[accesspilot-agent] {message}", flush=True)


def send_heartbeat(base_url: str, provider_id: str, api_key: str) -> None:
    url = f"{base_url.rstrip('/')}/api/v1/providers/{provider_id}/agent/heartbeat"
    request = urllib.request.Request(url, method="POST", headers={"X-Agent-Key": api_key, "Content-Length": "0"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        if response.status >= 300:
            raise urllib.error.HTTPError(url, response.status, "Unexpected response", response.headers, None)


def main() -> int:
    base_url = os.environ.get("ACCESSPILOT_URL")
    provider_id = os.environ.get("ACCESSPILOT_PROVIDER_ID")
    api_key = os.environ.get("ACCESSPILOT_AGENT_KEY")
    missing = [name for name, value in (("ACCESSPILOT_URL", base_url), ("ACCESSPILOT_PROVIDER_ID", provider_id), ("ACCESSPILOT_AGENT_KEY", api_key)) if not value]
    if missing:
        _log(f"Missing required environment variable(s): {', '.join(missing)}. See the comment at the top of this file.")
        return 1

    once = "--once" in sys.argv

    if once:
        try:
            send_heartbeat(base_url, provider_id, api_key)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            try:
                detail = json.loads(body).get("error", {}).get("message", body)
            except (ValueError, AttributeError):
                detail = body
            _log(f"Heartbeat failed: HTTP {exc.code} — {detail or 'no further detail'}")
            return 1
        except (urllib.error.URLError, OSError) as exc:
            _log(f"Could not reach AccessPilot at {base_url}: {exc}")
            return 1
        _log("Heartbeat succeeded. AccessPilot can see this agent as connected.")
        return 0

    _log(f"Starting. Heartbeating {base_url} every {HEARTBEAT_INTERVAL_SECONDS}s. Press Ctrl+C to stop.")
    while True:
        try:
            send_heartbeat(base_url, provider_id, api_key)
            _log("Heartbeat OK.")
        except urllib.error.HTTPError as exc:
            _log(f"Heartbeat failed: HTTP {exc.code} (will retry in {HEARTBEAT_INTERVAL_SECONDS}s)")
        except (urllib.error.URLError, OSError) as exc:
            _log(f"Could not reach AccessPilot: {exc} (will retry in {HEARTBEAT_INTERVAL_SECONDS}s)")
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
