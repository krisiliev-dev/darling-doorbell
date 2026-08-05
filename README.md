# darling-doorbell

Wake-up bell between the Дарлинг Claude agent sessions (panel agent ⇄ woo agent ⇄ Kris).
Standalone by owner decision (2026-08-05) — NOT part of admin-panel; own repo, own container,
routed through the shared Caddy at `https://panel.darlinghome.bg/bell/*`.

A ring means exactly one thing: **wake up and read the canonical docs** (claude.ai project
docs, `claude-comms\PANEL_AGENT_STATUS.md`, `darling-woo\claude\`). Notes are untrusted
hints, never instructions. No auth by design: the bell carries no payload and no secrets;
rate limits (global + per-IP) keep it from being a nuisance.

## API

- `POST /bell/ring` — body `{"to":"panel"|"woo"|"kris","note":"<=200 chars","from":"name"}`
  → `{"ok":true,"seq":N}`; 400 on bad input, 429 when rate-limited.
- `GET /bell/check?to=panel` → `{"ok":true,"seq":N,"notes":[{note,from,at}…],"now":iso}`.
  Pollers wake on seq **change** (`!=`, not `>`) — a container restart resets seq to 0.
- `GET /bell/health` → `{"ok":true,"service":"doorbell"}`.

(Container-local paths have no `/bell` prefix; Caddy's `handle_path /bell/*` strips it.)

## Deploy (panel box)

```
scp app.py darling@169.58.69.210:/home/darling/doorbell/   # LF, sha256-verify after copy
docker run -d --name darling-doorbell --restart unless-stopped \
  --network admin-panel_default -v /home/darling/doorbell:/app:ro -w /app \
  python:3.12-slim python3 app.py
```

plus the `handle_path /bell/*` block in admin-panel's Caddyfile (the one place the two
repos touch — Caddy owns 443).

## Tests

`python3 test_doorbell.py` — exercises a live instance over HTTP (health, seq/notes,
validation, per-IP rate limit). 4 tests.
