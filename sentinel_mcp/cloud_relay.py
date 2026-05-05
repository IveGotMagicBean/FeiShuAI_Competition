"""Cloud relay for Sentinel-MCP — mirrors local audit/approvals to a Cloudflare
Pages Functions backend so a paired phone (TWA .apk) can see this instance's
events and remotely approve/deny pending decisions.

Why polling instead of event hooks: the local dashboard already polls SQLite via
`/api/events`; mirroring with a 1-second poll loop keeps cloud_relay completely
decoupled from `guard.audit` / `sentinel_mcp.approvals` (no schema changes, no
listener wiring), and that 1-second lag is invisible vs the network roundtrip
to a phone anyway.

Three background threads (all daemon, self-healing on transient errors):
  - _event_loop      : poll AuditLog → push new events to /api/events/push
  - _approval_loop   : poll PendingDecisions.list_pending → push to /api/approvals/push
  - _decision_loop   : poll cloud /api/decisions/poll → apply via approvals.decide(by="phone")

Bootstrap flow:
  1. Load instance_id + admin_token from ~/.config/sentinel-mcp/instance.json
  2. If missing, POST /api/pair/register (no body) → server mints both, persist locally
  3. On every boot, also rotate a fresh pair code (5-min TTL) so the dashboard
     can show "scan/type to pair" without forcing the user to restart anything.

Failure handling: every cloud call is wrapped — network errors / non-2xx are
logged and swallowed. Local dashboard keeps working with zero degradation.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("sentinel_mcp.cloud_relay")

DEFAULT_CLOUD_BASE = "https://sentinel-mcp.542058929.workers.dev"
DEFAULT_INSTANCE_PATH = Path.home() / ".config" / "sentinel-mcp" / "instance.json"

EVENT_POLL_INTERVAL = 1.0
APPROVAL_POLL_INTERVAL = 1.0
DECISION_POLL_INTERVAL = 2.0
HTTP_TIMEOUT = 8.0


class CloudRelay:
    """Single-process relay between local dashboard and cloud middleware.

    Construct with the same `audit` and `approvals` objects the dashboard uses,
    then call `.start()` to spawn the three background threads. `.pair_code`
    holds the most recent 6-char code the dashboard should display.
    """

    def __init__(
        self,
        audit,
        approvals,
        *,
        cloud_base: str | None = None,
        instance_path: Path | None = None,
    ):
        self.audit = audit
        self.approvals = approvals
        self.cloud_base = (cloud_base or os.environ.get("SENTINEL_CLOUD_BASE")
                          or DEFAULT_CLOUD_BASE).rstrip("/")
        self.instance_path = instance_path or Path(
            os.environ.get("SENTINEL_INSTANCE_PATH", str(DEFAULT_INSTANCE_PATH))
        )
        self.instance_id: str | None = None
        self.admin_token: str | None = None
        self.pair_code: str | None = None
        self.pair_code_expires_at: int = 0
        self._mirrored_event_ts: float = 0.0
        self._mirrored_approval_ids: set[str] = set()
        self._stop = threading.Event()
        self._started = False

    # -------- public API --------------------------------------------------

    def start(self) -> None:
        """Bootstrap (load/register) + spawn 3 background threads. Safe to call once."""
        if self._started:
            return
        try:
            self._bootstrap()
        except Exception as ex:  # noqa: BLE001
            log.warning("cloud_relay bootstrap failed (%s); skipping cloud sync", ex)
            return
        self._started = True
        for fn in (self._event_loop, self._approval_loop, self._decision_loop):
            t = threading.Thread(target=fn, daemon=True, name=f"cloud_relay_{fn.__name__}")
            t.start()
        log.info("cloud_relay started · instance=%s · pair_code=%s",
                 self.instance_id, self.pair_code)

    def stop(self) -> None:
        self._stop.set()

    def rotate_pair_code(self) -> dict[str, Any] | None:
        """Get a fresh 6-char pair code (5-min TTL). Returns the full register payload."""
        if not (self.instance_id and self.admin_token):
            return None
        body = {"instance_id": self.instance_id, "admin_token": self.admin_token}
        resp = self._http("POST", "/api/pair/register", body=body)
        if resp:
            self.pair_code = resp.get("pair_code")
            self.pair_code_expires_at = int(resp.get("pair_code_expires_at", 0))
        return resp

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._started,
            "cloud_base": self.cloud_base,
            "instance_id": self.instance_id,
            "pair_code": self.pair_code,
            "pair_code_expires_at": self.pair_code_expires_at,
        }

    # -------- bootstrap ---------------------------------------------------

    def _bootstrap(self) -> None:
        loaded = self._load_instance()
        if loaded:
            self.instance_id, self.admin_token = loaded
            # Rotate code on boot so the dashboard always shows a fresh one.
            self.rotate_pair_code()
        else:
            # First-time registration.
            resp = self._http("POST", "/api/pair/register", body={})
            if not resp or "instance_id" not in resp:
                raise RuntimeError(f"register failed: {resp}")
            self.instance_id = resp["instance_id"]
            self.admin_token = resp["admin_token"]
            self.pair_code = resp.get("pair_code")
            self.pair_code_expires_at = int(resp.get("pair_code_expires_at", 0))
            self._save_instance(self.instance_id, self.admin_token)

    def _load_instance(self) -> tuple[str, str] | None:
        try:
            data = json.loads(self.instance_path.read_text())
            iid, tok = data.get("instance_id"), data.get("admin_token")
            if iid and tok:
                return (iid, tok)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        return None

    def _save_instance(self, instance_id: str, admin_token: str) -> None:
        self.instance_path.parent.mkdir(parents=True, exist_ok=True)
        self.instance_path.write_text(json.dumps(
            {"instance_id": instance_id, "admin_token": admin_token, "saved_at": time.time()},
            indent=2,
        ))
        try:
            self.instance_path.chmod(0o600)
        except OSError:
            pass

    # -------- background loops --------------------------------------------

    def _event_loop(self) -> None:
        # Seed the high-water mark so we don't replay all historical events on first boot.
        try:
            recent = self.audit.query(limit=1)
            if recent:
                self._mirrored_event_ts = float(recent[0].get("timestamp") or 0.0)
        except Exception:  # noqa: BLE001
            pass

        while not self._stop.is_set():
            try:
                events = self.audit.query(limit=50, since=self._mirrored_event_ts or None)
                # query returns DESC; flip to ASC so we push in chronological order.
                for ev in reversed(events):
                    ts = float(ev.get("timestamp") or 0.0)
                    if ts <= self._mirrored_event_ts:
                        continue
                    if self._push_event(ev):
                        self._mirrored_event_ts = ts
            except Exception as ex:  # noqa: BLE001
                log.debug("event loop tick error: %s", ex)
            self._stop.wait(EVENT_POLL_INTERVAL)

    def _approval_loop(self) -> None:
        while not self._stop.is_set():
            try:
                pending = self.approvals.list_pending(limit=50)
                for a in pending:
                    pid = a.get("id")
                    if not pid or pid in self._mirrored_approval_ids:
                        continue
                    if self._push_approval(a):
                        self._mirrored_approval_ids.add(pid)
            except Exception as ex:  # noqa: BLE001
                log.debug("approval loop tick error: %s", ex)
            self._stop.wait(APPROVAL_POLL_INTERVAL)

    def _decision_loop(self) -> None:
        while not self._stop.is_set():
            try:
                resp = self._http(
                    "GET",
                    f"/api/decisions/poll?instance_id={self.instance_id}&consume=1",
                )
                if resp:
                    for d in resp.get("decisions", []):
                        try:
                            self.approvals.decide(
                                d["id"],
                                approved=bool(d["approved"]),
                                by=str(d.get("by") or "phone"),
                            )
                        except Exception as ex:  # noqa: BLE001
                            log.debug("apply decision %s failed: %s", d.get("id"), ex)
            except Exception as ex:  # noqa: BLE001
                log.debug("decision loop tick error: %s", ex)
            self._stop.wait(DECISION_POLL_INTERVAL)

    # -------- one-off pushes ---------------------------------------------

    def _push_event(self, ev: dict) -> bool:
        body = {"instance_id": self.instance_id, "event": ev}
        return bool(self._http("POST", "/api/events/push", body=body))

    def _push_approval(self, a: dict) -> bool:
        body = {"instance_id": self.instance_id, "approval": a}
        return bool(self._http("POST", "/api/approvals/push", body=body))

    # -------- HTTP --------------------------------------------------------

    def _http(self, method: str, path: str, body: dict | None = None) -> dict | None:
        url = f"{self.cloud_base}{path}"
        headers = {"content-type": "application/json"}
        if self.admin_token:
            headers["x-admin-token"] = self.admin_token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                if resp.status >= 400:
                    log.debug("%s %s → %s", method, path, resp.status)
                    return None
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as ex:
            log.debug("%s %s → HTTP %s", method, path, ex.code)
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            log.debug("%s %s → network error: %s", method, path, ex)
        except json.JSONDecodeError as ex:
            log.debug("%s %s → bad json: %s", method, path, ex)
        return None


# Module-level singleton, set by dashboard startup. None until configured.
_INSTANCE: CloudRelay | None = None


def get_relay() -> CloudRelay | None:
    return _INSTANCE


def set_relay(relay: CloudRelay) -> None:
    global _INSTANCE
    _INSTANCE = relay
