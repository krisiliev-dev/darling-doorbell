# -*- coding: utf-8 -*-
"""Doorbell tests — run against a LIVE server instance (the house rule: test the result).
Sets tiny rate limits via env BEFORE importing app, binds a random port, exercises the
real HTTP surface with urllib."""
import json
import os
import threading
import urllib.error
import urllib.request

os.environ["BELL_GLOBAL_PER_HOUR"] = "500"
os.environ["BELL_PER_IP_PER_HOUR"] = "6"     # small: the rate-limit test trips this
os.environ["BELL_PORT"] = "0"

import app  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

_srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
PORT = _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT


def _get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _post(path, obj, ip=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    if ip:
        req.add_header("X-Forwarded-For", ip)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_health():
    code, body = _get("/health")
    assert code == 200 and body["ok"] and body["service"] == "doorbell"


def test_ring_and_check_seq_and_notes():
    code, body = _get("/check?to=panel")
    assert code == 200 and body["seq"] == 0 and body["notes"] == []
    code, body = _post("/ring", {"to": "panel", "note": "прочети статус дока", "from": "woo"}, ip="10.0.0.1")
    assert code == 200 and body["seq"] == 1
    code, body = _post("/ring", {"to": "panel", "note": "second", "from": "woo"}, ip="10.0.0.1")
    assert code == 200 and body["seq"] == 2
    code, body = _get("/check?to=panel")
    assert code == 200 and body["seq"] == 2
    assert [n["note"] for n in body["notes"]] == ["прочети статус дока", "second"]
    assert body["notes"][0]["from"] == "woo" and body["notes"][0]["at"]
    # recipients are independent boxes
    code, body = _get("/check?to=woo")
    assert code == 200 and body["seq"] == 0


def test_validation():
    assert _post("/ring", {"to": "nobody", "note": "x"}, ip="10.0.0.2")[0] == 400
    assert _post("/ring", {"to": "woo", "note": ""}, ip="10.0.0.2")[0] == 400
    assert _post("/ring", {"to": "woo", "note": "x" * 201}, ip="10.0.0.2")[0] == 400
    assert _post("/ring", {"to": "woo"}, ip="10.0.0.2")[0] == 400
    assert _get("/check?to=")[0] == 400
    assert _get("/nope")[0] == 404
    # a bad attempt still consumed rate budget (probing is not free)
    code, body = _post("/ring", {"to": "woo", "note": "ok"}, ip="10.0.0.2")
    assert code == 200


def test_per_ip_rate_limit_trips_and_spares_other_ips():
    for i in range(6):
        _post("/ring", {"to": "kris", "note": "n%d" % i}, ip="10.9.9.9")
    code, body = _post("/ring", {"to": "kris", "note": "one too many"}, ip="10.9.9.9")
    assert code == 429
    code, body = _post("/ring", {"to": "kris", "note": "different ip fine"}, ip="10.9.9.8")
    assert code == 200


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001 — a crashed test must FAIL loudly, not kill the runner
                fails += 1
                print("FAIL", name, repr(e))
    raise SystemExit(1 if fails else 0)
