# slash-dhan-mcp

An MCP server that gives an AI assistant (Claude or ChatGPT) read-only access to Indian market
data from Dhan through one `run_python` tool. The model writes a Python script against the
`slashdhan` library, the server runs it in an isolated subprocess and returns only what the
script printed, so large data never enters the model's context. The same server hosts market
watchers (model-authored conditions checked on a schedule without AI tokens, emailed when they
become true) and a page where anyone can pause, resume or cancel them.

One Dhan session, owned by the operator, feeds every user: there are no user accounts and no
OAuth. The operator pushes a Dhan access token once through the admin API; the server then renews
it by itself every day through Dhan's `RenewToken`.
The MCP endpoint is served unauthenticated at the host root, so this is built for an internal host.

## How it fits together

| Piece | What it does |
|---|---|
| `src/slashdhan` | Library the model's scripts import: feed-account reads, quotes, candles, instruments (every segment Dhan serves), Dhan's option chain with IV and greeks, expired-option history, indicators, market hours. Read-only by construction. |
| `src/slash_dhan_mcp` | MCP server (Streamable HTTP, no auth, served at the host root), tools `run_python`, `feed_status`, `create_watcher`, `list_watchers`; admin API; watchers page. |
| Feed | A single encrypted Dhan token in SQLite. `PUT /admin/feed/token` validates the token against Dhan before accepting it, records its expiry and data plan, and resumes watchers paused by the previous expiry. The sweeper then chains it: 12 hours after issue it calls Dhan's `GET /v2/RenewToken`, stores the new 24-hour token and repeats, so one push lasts until the chain breaks. |
| Gateway | Local `/dhan/...` endpoint. Scripts hold only a per-run token; the gateway injects the Dhan headers, allows read endpoints only and applies Dhan's rate limits once for everyone. |
| Runner | Spawns `python -I` per script with a scrubbed environment, wall-clock timeout, output cap and a shared workspace that persists between calls. The scrip master is downloaded by the server once per day and served to scripts. |
| Watchers | SQLite-backed sweeper: baseline on first check, one alert per not-met→met transition, market-hours gate (NSE or MCX), pause when the feed token dies and auto-resume after the next token. Alerts go to the email given at creation. |
| Operators | Receive an email when the feed token dies (the chain broke) and a warning when renewal keeps failing with under 3 hours left. |

Dhan facts that shape the design: access tokens last 24 hours; `RenewToken` swaps a live one for a
fresh 24-hour token (the old one dies at once) but refuses an expired one;
rate limits apply per token, so one limiter fronts all users; market data needs the paid
Data-API plan on the feed account (error 806 otherwise); the option-chain endpoint allows one
call per underlying and expiry every 3 seconds.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- A Dhan account with the Data-API subscription and an access token generated on Dhan Web (the
  type `RenewToken` accepts).
- A [Resend](https://resend.com) API key for alert and operator emails (optional in
  development: mails are logged instead).

## Run locally

```sh
cp .env.example .env            # fill the two secrets and SLASH_DHAN_MCP_OPERATOR_EMAILS
uv sync
uv run slash-dhan-mcp           # MCP at http://127.0.0.1:8020/, watchers page at /watchers
```

Push a token (the server validates it against Dhan first, then keeps it alive by itself):

```sh
curl -X PUT http://127.0.0.1:8020/admin/feed/token \
  -H "Authorization: Bearer $SLASH_DHAN_MCP_ADMIN_SECRET" -H 'Content-Type: application/json' \
  -d '{"dhan_client_id": "1000000001", "access_token": "<24h token>"}'
curl http://127.0.0.1:8020/admin/feed/status -H "Authorization: Bearer $SLASH_DHAN_MCP_ADMIN_SECRET"
curl -X POST http://127.0.0.1:8020/admin/feed/renew -H "Authorization: Bearer $SLASH_DHAN_MCP_ADMIN_SECRET"  # force a renewal
```

Renewal runs inside the 60-second sweeper: a token is renewed `SLASH_DHAN_MCP_FEED_RENEW_AFTER_HOURS`
(default 12) after it was issued, which leaves a 12-hour budget to notice trouble. A network
error or a Dhan 5xx is retried every 5 minutes; if that keeps failing into the last
`SLASH_DHAN_MCP_FEED_WARNING_HOURS` (default 3) the operators get one warning. A refusal from
Dhan (token revoked, replaced elsewhere, or already expired) breaks the chain: the feed is
marked expired, watchers pause and the operators get the outage mail, and one new push
restarts everything. Scripts never hold the Dhan token, so a renewal needs no restart; a 401
earned by the previous token during the swap is recognised by its fingerprint and ignored.

Add the MCP to claude.ai as a custom connector (authentication: none), or to ChatGPT (Plugins →
Add custom MCP server, no authentication), with the server's public URL (`https://host/`). The
watchers page is `https://host/watchers`.

Give the model [`SKILL.md`](SKILL.md): it is the document that teaches it how to write scripts
against `slashdhan` (one composed script per question, return shapes, Dhan quirks, watcher
contract). The tool description carries only a short reference. The server also serves it as
`https://host/slash-markets-dhan.zip` (a folder `slash-markets-dhan/SKILL.md`, the shape
claude.ai's skill upload and `.claude/skills/` expect) and inline as `https://host/SKILL.md`; the
watchers page links to both.

With `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in `.env`, `uv run python scripts/live_smoke.py`
exercises the library against the real API without the server.

## Deploy

`deploy/` holds a hardened systemd unit, an nginx site, a one-time `setup-uat.sh` (service
user, clone, venv, `.env` with fresh secrets, certificate) and `update.sh` (redeploy
`origin/main`). `deploy/remote.sh` drives them over SSH and reads the box address from
`DEPLOY_HOST` or the git-ignored file `deploy/.host`:

```sh
bash deploy/remote.sh setup                     # once
bash deploy/remote.sh deploy                    # after every push to main
echo "<token>" | bash deploy/remote.sh token <dhan_client_id>   # once; it renews itself from here
bash deploy/remote.sh renew                     # force a renewal
bash deploy/remote.sh feed | urls | status | logs
```

The admin secret never leaves the box: `token`, `renew` and `feed` read it from the box's `.env`.

## HTTP API (the tools without MCP)

The same four tools are also plain HTTP under `/api/v1`, for chat clients that cannot install
an MCP connector. They call the same services as the tools and share their concurrency gate;
like the MCP they are unauthenticated (internal host) and answer browser clients (CORS `*`).

| Tool | Route | Notes |
|------|-------|-------|
| run_python | `POST /api/v1/run` `{code, timeout_seconds?}` | `{ok, exit_code, stdout, stderr, duration_ms, timed_out, truncated, feed, message}`; a failing script is HTTP 200 with `ok:false` and the traceback in `stderr`; a feed that is down is 503 with `message` |
| feed_status | `GET /api/v1/feed` | same object as the tool |
| create_watcher | `POST /api/v1/watchers` `{symbol, label, script, notify_email, check_every_minutes?, expires_in_days?, market?}` | 201 on success, 200 `created:false` with the reason otherwise |
| list_watchers | `GET /api/v1/watchers` | same rows as the tool |
| catalogue | `GET /api/v1/tools` | the four tools exactly as the MCP advertises them (description, input/output schemas) plus each one's route, for building function definitions |

```sh
curl -X POST https://host/api/v1/run -H 'Content-Type: application/json' \
  -d '{"code": "import slashdhan as z\nprint(z.ltp_of(\"NIFTY\"))"}'
```

## Analytics

Set `POSTHOG_API_KEY` (a PostHog project key, `phc_…`; `POSTHOG_HOST` defaults to the US
cloud) and the server reports product events to PostHog; unset, nothing leaves the box. The MCP
has no user accounts, so events are personless and segmented only by `host` (claude / chatgpt /
other, read from the request). Events: `run_python` (outcome, timing, script source, the
`slashdhan` functions called, Dhan endpoint counts), `feed_status`, `list_watchers`,
`create_watcher` (created or why not, script), `watcher_fired`, `watcher_error`,
`watcher_sweep`, `watcher_action` (page pause/resume/cancel), `watchers_page_viewed`,
`skill_downloaded`, `feed_token_set` / `feed_token_rejected` / `feed_token_cleared`,
`feed_renewed` / `feed_renew_failed`,
`feed_expired` and `operator_mailed`. The Dhan token, run tokens and alert email addresses are
never sent. `src/slash_dhan_mcp/analytics.py` is the whole integration.

## Development

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

The suite runs the library against an in-process fake Dhan and boots the real server against
it: admin API, scripts through the gateway, feed expiry, operator mails, watcher lifecycle and
the page and the analytics events. `tests/test_skill.py` pins every function named in
`SKILL.md` to the library.

## Licence

MIT. See [LICENSE](LICENSE).
