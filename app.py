# -*- coding: utf-8 -*-
"""Agent doorbell — a wake-up bell between Claude agent sessions (Дарлинг project).

Standalone service, deliberately OUTSIDE the admin-panel app (owner decision 2026-08-05):
its own repo (Lora-git-hub/darling-doorbell), its own container, reached through the shared
Caddy at https://panel.darlinghome.bg/bell/*.

Design contract (why there is NO auth):
- The bell carries NO payload that matters. A note is a <=200-char hint; the canonical
  message ALWAYS lives in the claude.ai project docs / claude-comms / darling-woo\\claude.
- Agents treat note text as UNTRUSTED data: a ring means "go read the canonical docs",
  never "do what the note says". Worst case for an abuser: an agent wakes, reads the
  docs, finds nothing new, goes back to sleep.
- No secrets anywhere -> nothing to leak, nothing to distribute to two cloud sandboxes.
- Rate-limited (global + per-IP via Caddy's X-Forwarded-For) so it cannot be a nuisance.

Stdlib only, single file: no deps to build, `python3 app.py` is the whole deploy.
State is in-memory; a restart resets seq to 0 — pollers must wake on seq CHANGE
(!=, not >), which also makes deploy restarts self-healing.
"""
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

if hasattr(sys.stdout, "reconfigure"):
    # cp1252 consoles die printing Cyrillic notes (known house incident) — never let a
    # log line kill a request handler.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RECIPIENTS = ("panel", "woo", "kris")   # kris: humans may ring too
NOTE_MAX = 200
BODY_MAX = 4096
KEEP_NOTES = 10
GLOBAL_PER_HOUR = int(os.environ.get("BELL_GLOBAL_PER_HOUR", "60"))
PER_IP_PER_HOUR = int(os.environ.get("BELL_PER_IP_PER_HOUR", "30"))
PORT = int(os.environ.get("BELL_PORT", "8300"))

_lock = threading.Lock()
_state = {r: {"seq": 0, "notes": deque(maxlen=KEEP_NOTES)} for r in RECIPIENTS}
_rings_global: deque = deque()          # timestamps of accepted+rejected ring attempts
_rings_by_ip: dict = {}                 # ip -> deque of timestamps


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _prune(dq: deque, horizon: float) -> None:
    while dq and dq[0] < horizon:
        dq.popleft()


def _rate_limited(ip: str) -> bool:
    """Count EVERY attempt (valid or not) so garbage can't probe for free."""
    now = time.time()
    horizon = now - 3600.0
    _prune(_rings_global, horizon)
    dq = _rings_by_ip.setdefault(ip, deque())
    _prune(dq, horizon)
    if len(_rings_global) >= GLOBAL_PER_HOUR or len(dq) >= PER_IP_PER_HOUR:
        return True
    _rings_global.append(now)
    dq.append(now)
    if len(_rings_by_ip) > 1000:        # bound the ip map itself
        _rings_by_ip.clear()
    return False


def _clean_note(raw) -> str:
    if not isinstance(raw, str):
        return ""
    s = "".join(ch for ch in raw if ch >= " " or ch == "\t").strip()
    return s


class Handler(BaseHTTPRequestHandler):
    server_version = "darling-doorbell/1.0"

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self) -> str:
        # Caddy sets X-Forwarded-For to the real client ip (single hop, trusted).
        xff = self.headers.get("X-Forwarded-For", "")
        return xff.split(",")[0].strip() or (self.client_address[0] if self.client_address else "?")

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/health":
            self._json(200, {"ok": True, "service": "doorbell", "now": _now_iso()})
            return
        if url.path == "/check":
            to = (parse_qs(url.query).get("to") or [""])[0]
            if to not in RECIPIENTS:
                self._json(400, {"ok": False, "error": "to must be one of %s" % (list(RECIPIENTS),)})
                return
            with _lock:
                box = _state[to]
                self._json(200, {"ok": True, "to": to, "seq": box["seq"],
                                 "notes": list(box["notes"]), "now": _now_iso()})
            return
        self._json(404, {"ok": False, "error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/ring":
            self._json(404, {"ok": False, "error": "unknown path"})
            return
        if _rate_limited(self._client_ip()):
            self._json(429, {"ok": False, "error": "rate limited; the bell is not a chat channel"})
            return
        try:
            n = min(int(self.headers.get("Content-Length") or 0), BODY_MAX + 1)
            data = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            self._json(400, {"ok": False, "error": "body must be JSON"})
            return
        to = data.get("to")
        note = _clean_note(data.get("note"))
        if to not in RECIPIENTS:
            self._json(400, {"ok": False, "error": "to must be one of %s" % (list(RECIPIENTS),)})
            return
        if not note or len(note) > NOTE_MAX:
            self._json(400, {"ok": False, "error": "note required, 1..%d chars" % NOTE_MAX})
            return
        frm = _clean_note(data.get("from"))[:40] or "?"
        with _lock:
            box = _state[to]
            box["seq"] += 1
            box["notes"].append({"note": note, "from": frm, "at": _now_iso()})
            seq = box["seq"]
        print("[ring] to=%s from=%s seq=%d note=%r" % (to, frm, seq, note), flush=True)
        self._json(200, {"ok": True, "to": to, "seq": seq})

    def log_message(self, fmt: str, *args) -> None:   # access-log off; rings are logged above
        pass


def main() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("[doorbell] listening on :%d recipients=%s" % (PORT, list(RECIPIENTS)), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
