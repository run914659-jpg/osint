# Code Review — Telegram OSINT BOT (2026-10-22)

Verdict: **FIX-FIRST** — 280 tests green but 1 CRITICAL + 3 HIGH must land before ship.

## Fix list

### CRITICAL
- **C1** `requirements.txt` missing `Pillow` → fresh install/Render cannot boot (registry imports `osint/exiftool.py` eagerly → ModuleNotFoundError). Fix: add `Pillow>=10.0`.

### HIGH
- **H1** `handlers/start.py` grace path never calls `set_verified` for unverifiable channels → user hard-locked forever. Fix: persist bypass (`set_verified` per unverifiable channel) + reset fail counter.
- **H2** Verification cache never invalidated — join→verify→leave = permanent bypass. Fix: `cb_verify` must re-check ALL must_join channels (not just pending), add `db.clear_verification(user_id, chat_id)` and revoke stale rows.
- **H3** Webhook secret falls back to guessable `"change-me"` → forged-update admin impersonation. Fix: fail-fast in config when `run_mode=="webhook"` and secret is default/short (<32 chars).

### MEDIUM (fix now)
- **M1** Renders >4096 chars fail silently (frozen loader). Fix: truncate at ~4000 with notice + non-silent final fallback.
- **M2** `cb_verify` catches only 2 exception types → use `TelegramAPIError` base.
- **M3** `/start` + force-join gate have no `F.chat.type == "private"` filter → group spam. Fix: add filter to cmd_start + gate send paths + middleware gate (skip non-private chats).
- **M4** SSRF DNS-rebinding TOCTOU in urlinfo/exiftool: document limitation in module docstrings (full resolver pinning = follow-up).
- **M5** `email.py` SMTP probe bypasses SSRF guard (internal port-25 oracle). Fix: classify resolved MX IP, refuse private ranges.
- **M6** Zero tests for ForceJoinGateMiddleware, AdminGuardMiddleware, webhook secret check. Fix: add focused tests (gate blocks + whitelist, admin rejection, webhook 403/200).
- **M7** `requirements.txt` aiogram floor `>=3.7` → pin `aiogram>=3.15,<4`.
- **M8** Back from tool prompt leaves FSM armed. Fix: `state.clear()` in `back:menu` handler.
- **M9** Long channel titles can exceed 200-char callback-alert cap. Fix: truncate alert texts at source (admin.py answer paths).

### LOW (fix cheap ones)
- **L1** ToolError `<code>` markup double-escaped → strip known tags before escaping in build_error_text.
- **L2** Copy says "8 tools" — update texts to 16.
- **L3** Admin toast alerts show raw HTML → plain-text variants for ADMIN_DELETED / ADMIN_MODE_SET alerts.
- **L4** Danger-lint warns on retry button → exempt `t:`-retry / noop from the destructive-prefix assertion.
- **L5** `_VERIFY_FAILS_KEY` counter reset (fold into H1).
- **L6** Webhook returns 500 on handler errors → return 200 after update accepted+validated (log instead; avoids Telegram retry duplicates).
- **L7** (follow-up, skip now) dead code: unused texts, `_chunk2` triplication, unused gate prefixes, `is_blocked` column.

## Verified OK
Gate whitelists (except H2), AdminGuard router attachment, middleware cancellation idiom, HTML escaping (except L1/M9 edges), BIN PAN refusal, session/task cleanup, callback sizes, layout rules, no secrets in source, DB integrity, permissive licenses.
