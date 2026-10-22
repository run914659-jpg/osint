# PLAN.md — Premium Telegram OSINT Bot ("OSINT BOT")

> Target dir: `C:\Users\Sahil Beniwal\Desktop\OSINT BOT` (currently empty)
> Stack (FIXED): Python 3.11+, **aiogram v3**, SQLite, Render (web service, webhook + polling fallback), inline keyboards only, HTML parse mode.

---

## 0. Overview

Build a premium-feel Telegram OSINT bot with:
- **Admin side**: add/list/delete channels, choose join mode (request vs direct), all via inline buttons.
- **Force-join gate**: every `/start` and every tool run verifies the user is a member of all saved channels; `chat_join_request` updates are auto-approved.
- **16 OSINT tools** with an animated loading screen and rich HTML results (all keyless):
  📱Number→Info, 📧Email→Info, 👤Username→Sites scan, 🔍TG @→Info, 🆔TG ID→Info, 🌐IP→Info,
  🔗URL→Info, 🌍Domain→DNS, 🧬WHOIS/RDAP, 🔢Text→Hash/Encode, 🔑Hash→Reverse, 💳BIN→Card info,
  📡MAC→Vendor, 🖼️Image→EXIF, 📇IBAN→Validate, 🔎Google Dork builder.
- **One keyboard-builder module** that *structurally enforces* the layout rules (2-up rows, full-width back/verify as last row, `style` colors).

## 1. Requirements (acceptance)

- R1: Only users in `ADMIN_IDS` (comma-separated env) see/run admin flows; others get a graceful "not admin" answer.
- R2: Add Channel accepts `t.me/...` link, `+invite` link, `@username`, or a forwarded message (`forward_origin`).
- R3: Bot refuses to save a chat where the bot is not an administrator ("Invite users via link" required).
- R4: Public channel ⇒ `join_mode=direct`; private ⇒ user picks `request` (creates_join_request invite) or `direct_private` (exportChatInviteLink).
- R5: Force-join blocks tools until `getChatMember` reports joined for ALL channels; verify button re-checks.
- R6: `chat_join_request` ⇒ auto-approve (`answerChatJoinRequestQuery(query_id, "approve")` if `query_id` else `approveChatJoinRequest`) + welcome message with `[➡ Main Menu]`.
- R7: All 16 tools work keyless; optional env keys (`NUMVERIFY_API_KEY`, `BINLIST_API_KEY` etc.) only *add* data.
- R8: Layout rules D1–D6 enforced by `keyboards.py` and proven by unit tests.
- R9: Runs on Render with webhook (`/webhook/<secret>`) and `/health`, polling fallback via `RUN_MODE=polling`.

## 2. Assumptions & constraints

- SQLite file on a Render persistent disk (`/var/data/bot.db`); local dev uses `./bot.db`.
- State for FSM: `MemoryStorage` (bot is single-process on Render; acceptable for prompt flows). Documented as a known limitation.
- One process, one bot instance (Render web service, `concurrency=1` implied — no horizontal scaling with MemoryStorage).
- OSINT lookups must have timeouts (5–8 s each) and never block the event loop (wrap sync libs: `dns.resolver`, `socket` — use `asyncio.to_thread`).
- No reply keyboards anywhere; no colored text (color only via button `style`).

## 3. Architecture / file-by-file

```
osintbot/
  __init__.py        # empty, version string
  config.py          # dataclass Config.from_env(): BOT_TOKEN, ADMIN_IDS:set[int], RUN_MODE,
                     #   WEBHOOK_PATH, WEBHOOK_SECRET, PUBLIC_BASE_URL, DB_PATH, API keys
  db.py              # aiosqlite pool-less wrapper: init_db(), upsert_user(), get/set user state,
                     #   channels CRUD, get_all_channels(), get_setting/set_setting
  main.py            # entrypoint: create Bot, Dispatcher, register middleware+routers,
                     #   start webhook (set_webhook w/ secret_token) or start_polling, /health aiohttp route
  keyboards.py       # THE builder: build_row(*btn), build_full(*btn), back(), verify(), rules asserted in tests
  texts.py           # all HTML templates (WELCOME, ADMIN_*, FORCE_JOIN, TOOL_* prompts/results)
  loaders.py         # LoadingAnimation: spinner frames + % on editMessageText, ≥1s throttle, 10s cap
  middlewares.py     # DbMiddleware, UserUpsertMiddleware, AdminCheckMiddleware, ForceJoinGateMiddleware
  handlers/
    __init__.py      # aggregates routers -> all_routers()
    start.py         # /start, main menu, verify callback, channels listing for users
    admin.py         # channel manage, add-channel FSM, list, detail, delete, change join mode
    joinrequests.py  # chat_join_request auto-approve
    tools/
      __init__.py    # tool registry: code -> {emoji, title, prompt, lookup, render, sample}
      generic.py     # ONE generic handler for all 16 tools: t:<code> -> FSM prompt -> loader -> lookup -> rich result
  osint/             # pure async lookup functions, no aiogram imports (except tg tools get bot passed in)
    __init__.py phone.py email.py username.py tguser.py tgid.py ipinfo.py urlinfo.py
    dnslookup.py rdap.py textutils.py hashcrack.py binlookup.py macvendor.py exiftool.py iban.py dork.py
    Each module: async lookup(q, cfg, bot=None) -> dict  +  render(data) -> str (HTML)
tests/
  test_keyboards.py  # layout rules (Phase 2 gate)
  test_db.py         # CRUD round-trip on tmp db
  test_osint_parsers.py  # pure parsers (phone format, MX parse, disposable list, RDAP JSON->dict)
render.yaml  requirements.txt  .env.example  README.md  PLAN.md  pytest.ini
```

**Dependency rule**: `handlers -> keyboards/texts/loaders/db + osint`; `osint` never imports aiogram; `keyboards` never imports db.

### DB schema (DDL, `db.py:init_db`)

```sql
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
  is_blocked INTEGER DEFAULT 0, joined_at TEXT, last_seen TEXT);

CREATE TABLE IF NOT EXISTS channels (
  chat_id INTEGER PRIMARY KEY, username TEXT, title TEXT, type TEXT,
  join_mode TEXT NOT NULL CHECK(join_mode IN ('direct','request','direct_private')),
  invite_url TEXT, photo_url TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS user_channels (          -- verified membership cache
  user_id INTEGER, chat_id INTEGER, verified_at TEXT,
  PRIMARY KEY (user_id, chat_id));

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
```
`photo_url` from `getChat(...).photo.big_file_id` is NOT a public URL — store `file_id` only, or skip sending photo (documented).

### callback_data scheme (prefix:arg, **total ≤ 64 bytes** — add test)

| Prefix | Arg | Meaning |
|---|---|---|
| `adm:menu` | — | admin main |
| `adm:add` | — | start add-channel prompt |
| `adm:list` | — | channel list |
| `adm:ch` | `chat_id` | channel detail |
| `adm:del` | `chat_id` | delete confirm |
| `adm:dely` / `adm:deln` | `chat_id` | confirm yes/no |
| `adm:mode` | `chat_id` | join-mode chooser |
| `adm:mode:set` | `chat_id:direct\|request` | set join mode (truncate chat_id if needed) |
| `back:menu` | — | back to main menu |
| `back:admin` | — | back to admin menu |
| `back:tool` | — | back from tool prompt to menu |
| `vj:check` | — | force-join verify |
| `ch:open` | `chat_id` | user taps a channel (url buttons preferred; this is fallback) |
| `t:<code>` | code (`num`,`mail`,`user`,`ip`,`url`,`tg`,`txt`,`rdap`) | open tool / again |

Format helper: `cb("adm:ch", chat_id)` → `"adm:ch:" + str(chat_id)`; assert `len(b) <= 64`.
For `adm:mode:set` use `adm:ms:<chat_id>:<mode>` — if > 64 bytes, fall back to a two-step (store pending choice in FSM). Test covers this.

### Middleware order (outermost → innermost)

1. `DbMiddleware` — opens/binds aiosqlite connection to `data["db"]`.
2. `UserUpsertMiddleware` — upserts `users` row on any message/callback (skips `chat_join_request`).
3. `ForceJoinGateMiddleware` — for non-admin users on `F.message`/`F.callback_query`: if unverified channels exist → replace with force-join screen and `raise CancelHandler()`. Whitelists: `/start`, `vj:check`, `ch:open`, and callbacks starting with `back:`/`vj:`.
4. `AdminCheckMiddleware` (handlers flagged `admin_only=True` via `data`) — answers callback with `⛔` alert instead of raising.

`dp.message.middleware(...)` / `dp.callback_query.middleware(...)` registered in `main.py` in that exact order (aiogram runs first-registered = outermost).

### Key aiogram v3 APIs used

- `Router()`, `dp.include_router(...)`; filters `F.message.text`, `F.callback_query.data.startswith("t:")`, `Command("start")`.
- `CallbackQuery.answer(text, show_alert=True)`, `edit_message_text(text=..., reply_markup=..., parse_mode="HTML")`.
- `FSMContext` + `State`/`StateGroup` (`AddChannel.waiting_for_target`, `ToolFlow.waiting_query`) with `MemoryStorage`.
- `Bot.send_chat_action(chat_id, ChatAction.TYPING)`.
- Updates: `F.chat_join_request` with `.chat`, `.from_user`, `.user_chat_id`, `.invite_link`, `.bio`, `.query_id`.
- Forward: `msg.forward_origin` → `UserMessageOriginChat/Hidden` / `ChatMessageOriginChat` (fallback: `msg.forward_from_chat` for older payloads).
- Webhook: `bot.set_webhook(url, secret_token=..., allowed_updates=[...])`, handler validates `X-Telegram-Bot-Api-Secret-Token` header.

---

## 4. Implementation Phases (each independently verifiable)

### Phase 1 — Scaffold, config, DB  *(complexity: low)*
**Files**: `requirements.txt`, `.env.example`, `osintbot/__init__.py`, `config.py`, `db.py`, `pytest.ini`, `tests/test_db.py`
1. `requirements.txt`: `aiogram>=3.7`, `aiosqlite`, `python-dotenv`, `dnspython`, `aiohttp`, `pytest`, `pytest-asyncio`. (`maigret` left optional — see Phase 6 note.)
2. `config.py`: `@dataclass Config` with `from_env()`; `ADMIN_IDS` parsed as `{int(x) for x in s.split(",") if x.strip()}`; fail fast if `BOT_TOKEN` missing.
3. `db.py`: `init_db()` executes the DDL above; CRUD: `upsert_user`, `add_channel`, `get_channel`, `list_channels`, `delete_channel`, `set_join_mode`, `is_verified`, `set_verified`, `clear_verifications(user_id)`, `get_setting/set_setting`.
4. `tests/test_db.py`: tmp-path db → add/list/set_join_mode/delete round trip.

**Verify**: `python -m compileall osintbot` ; `pytest -q tests/test_db.py`
**Risk**: Low. **Deps**: none.

### Phase 2 — Keyboard builder + texts + layout tests  *(low)*
**Files**: `keyboards.py`, `texts.py`, `tests/test_keyboards.py`, `tests/test_cb_size.py`
1. `keyboards.py` exposes ONLY:
   - `btn(text, cb=None, url=None, style=None, disabled=False) -> InlineKeyboardButton`
   - `row2(a, b) -> list[InlineKeyboardButton]`  (asserts exactly 2)
   - `full(*buttons) -> list[InlineKeyboardButton]` (asserts 1..N on one row; used for back/verify)
   - `menu(items, *, back_cb)` → items chunked by 2, final row = `full(btn(Back, back_cb, style="primary"))`
   - `chain(*rows, tail)` → rows + `full(tail)`; **`tail` (Back/Verify) is always appended as its own last row** — callers cannot put it in a 2-up row.
   - style constants: `PRIMARY/SUCCESS/DANGER`; validation that `danger` only on delete/cancel (lint-style assert in dev).
2. `texts.py`: `WELCOME`, `FORCE_JOIN`, `PROMPT_QUERY(title)`, `RESULT_WRAP(title, body)`, `ERR`, `ADMIN_*` — all HTML.
3. Tests (the Phase-2 gate):
   - back button is **last row** and that row has **exactly 1** button;
   - every non-tail row has exactly 2 buttons;
   - `len(cb.encode()) <= 64` for every constructed callback;
   - no `ReplyKeyboardMarkup` import anywhere (grep test).

**Verify**: `pytest -q tests/test_keyboards.py tests/test_cb_size.py`
**Risk**: Low. **Deps**: Phase 1 (only for texts referencing channels).

### Phase 3 — Entry point, middleware, `/start`, main menu  *(medium)*
**Files**: `main.py`, `middlewares.py`, `handlers/start.py`, `texts.py` additions
1. `main.py`: `async def main()` → `Config.from_env()`, `init_db()`, `Bot(token)`, `Dispatcher(storage=MemoryStorage())`, register middlewares (order above), `include_router(start_router)`; `RUN_MODE=="webhook"` → `aiohttp` app with `POST {WEBHOOK_PATH}` (validate secret header) + `GET /health` returning `{"ok":true}`; else `dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())`.
2. `handlers/start.py`: `/start` → force-join screen if pending, else welcome (`<pre>` banner, `<blockquote>` tagline, spoiler rules) + channel buttons (url/join) + `full(✅ I Joined / Verify)` + main-menu buttons (2-up grid per spec order).
3. Main menu handler `back:menu` / `t:<code>` → tool prompt with `[◀ Back to Menu]`.

**Verify** (manual): `python -m osintbot.main` with `.env` → `/start` renders menu; `curl localhost:PORT/health`.
**Risk**: Medium (webhook secret / polling update types). **Deps**: Phase 1–2.

### Phase 4 — Admin: channel management  *(medium-high)*
**Files**: `handlers/admin.py`, `texts.py`
1. `adm:menu` → `[➕ Add Channel] [📋 Channel List]` (2-up) + `full(◀ Back)`.
2. Add flow FSM `AddChannel.waiting_for_target`: prompt text; parse input:
   - `@user` / `t.me/xxx` → `chat_id = "@" + username` for `getChat`;
   - `+invite` / `t.me/+` → try `getChat` on the invite (works only if bot already member) else ask for a forwarded message;
   - forward → resolve `forward_origin` (handle `ChatMessageOriginChat`, hidden origin → error text).
   - `bot.get_chat` + `bot.get_chat_administrators`; if `bot.id` not in list → abort with ⚠️ message (DANGER-styled note).
   - public (`.username`) → save `direct`; private → 2-up `[📩 Request join] [🔗 Direct join]` + `full(◀ Back)`.
   - request → `create_chat_invite_link(chat_id, name=..., creates_join_request=True)`; direct → `export_chat_invite_link(chat_id)` wrapped in try/except (missing right → re-prompt).
3. List → one button per channel, `style` by join_mode (`success` direct/direct_private, `primary` request); detail view: title/type/`get_chat_member_count`/join mode/bot-admin + `[🗑 Delete] [🔁 Change Join Mode]` (2-up) + `full(◀ Back)`.
4. Delete → `adm:del` shows confirm `[✅ Yes, delete] [❌ No]` (danger/success) → row deleted → `user_channels` cleared for that chat.
5. Change join mode → for private chats regenerate link as per mode; public → answer alert "Public channel — direct only".

**Verify**: manual — add public channel, add private with each mode, delete, list; `python -m compileall osintbot`.
**Risk**: High-ish (invite rights, forward parsing). **Deps**: Phase 1–3.

### Phase 5 — Force-join gate + join requests  *(medium)*
**Files**: `handlers/joinrequests.py`, `middlewares.py` (gate logic), `handlers/start.py` (verify)
1. Force-join screen builder: for each channel → url/join button (public `t.me/<username>`, private `invite_url`), `full(✅ I Joined / Verify)`.
2. `vj:check`: for each channel `get_chat_member(chat, uid)`; joined = `status in ("member","administrator","creator")` or `status=="restricted" and is_member`. Cache successes in `user_channels`; on error `bot_not_member`/`not enough rights` → treat as "unverifiable", show warning but allow proceed after 2 fails (graceful, don't hard-lock users).
3. All joined → `clear pending`, send main menu. Not all → edit screen, alert remaining titles.
4. `chat_join_request` handler: approve (query_id path first), `set_verified(user_id, chat)`, DM "✅ Request approved — welcome!" + `full(➡ Main Menu)`.
5. Every tool entry re-checks `user_channels` freshness (middleware covers it).

**Verify**: manual with a second account: don't join → blocked; join public → verify passes; request-mode → auto-approve message arrives. `pytest -q`.
**Risk**: Medium (approval races). **Deps**: Phase 3–4.

### Phase 6 — OSINT modules + loading animation + 16 tool handlers  *(high)*
**Files**: `osint/*.py` (16 modules), `loaders.py`, `handlers/tools/__init__.py` (registry), `handlers/tools/generic.py`, `tests/test_osint_parsers.py`

**Tool registry** (code → module): `num` phone, `mail` email, `user` username, `tg` tguser(@), `tgid` tgid, `ip` ipinfo, `url` urlinfo, `dns` dnslookup, `rdap` rdap, `txt` textutils, `hash` hashcrack, `bin` binlookup, `mac` macvendor, `exif` exiftool, `iban` iban, `dork` dork. Main menu: 16 tool buttons 2-up (8 rows) + full-width Back.

1. `loaders.LoadingAnimation.start(message)` → edit loop: frames `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` + percent (0→99 over 8 s), **min interval 1.0 s**, stops on `.stop()`; catch `TelegramBadRequest` `message is not modified` silently; at 10 s cap → stop and mark `partial=True` → result footer uses a DANGER button "⏱ partial/failed".
2. Each `osint/*.py`: `async def lookup(q, cfg, bot=None) -> dict` with `asyncio.wait_for(..., 8)`; sync work (dns/socket/Pillow/regex) in `asyncio.to_thread`; plus `render(data) -> str` returning rich HTML (blockquote summary, `<b>` labels, `<span class="tg-spoiler">` sensitive, `<code>` hashes/IDs).
   - `phone.py`: `phonenumbers` offline — validity, number type, region/city (geocoder), country, timezone, carrier (`carrier.name_for_number(n, 'en')`). If `NUMVERIFY_API_KEY` set → extra line via numverify (demo key is DEAD — env key only).
   - `email.py`: RFC-ish validation, MX via dnspython, SMTP TCP connect (port 25/587, 3 s, in thread), disposable-domain bundled set, Gravatar `https://www.gravatar.com/avatar/<md5>?d=404&s=64` → 404 = no profile, 200 = exists (verified working).
   - `username.py`: ~18-site registry `{name: url_template}` (github, gitlab, reddit, instagram, tiktok, x, youtube, pinterest, tumblr, medium, devto, steam, twitch, spotify, soundcloud, aboutme, linktree, keybase), `aiohttp` TCPConnector(limit=15), HEAD then GET fallback, 404=absent, 200=exists, 403/429=unknown, `gather(return_exceptions=True)`.
   - `tguser.py`: `bot.get_chat("@"+name)` → id/type/title/description + `get_chat_member_count`; bot passed in as arg.
   - `tgid.py`: numeric id → if user started bot: `bot.get_chat(user_id)`, `getUserProfilePhotos` count, profile fields; else honest "private/limited — never interacted" message. **NEVER claim phone number** (impossible via Bot API; state it in the result footnote).
   - `ipinfo.py`: `http://ip-api.com/json/{ip}?fields=...` (verified no-key).
   - `urlinfo.py`: aiohttp fetch (8 s, ≤512 KB, ≤5 redirects, record chain): status, final URL, `<title>`, server, content-type, resolved IP; **SSRF guard** — resolve host, reject private/loopback/link-local targets.
   - `dnslookup.py`: A/AAAA/MX/NS/TXT/CNAME via `dns.asyncresolver`.
   - `rdap.py`: `https://rdap.org/domain/{d}` → registrar, statuses, created/updated/expires, nameservers (verified no-key).
   - `textutils.py`: md5/sha1/sha256/sha512, base64 enc/dec, url-encode/decode, reverse, word/char count.
   - `hashcrack.py`: best-effort md5 reverse via `https://md5.gromweb.com/?query=<hash>` (no-key HTML parse) + hash type/length detection; honest "not found" message.
   - `binlookup.py`: `https://lookup.binlist.net/<bin>` (no-key, rate-limited; optional `BINLIST_API_KEY`) → scheme/type/brand/bank/country; graceful rate-limit message.
   - `macvendor.py`: `https://api.macvendors.com/<mac>` (no-key) → vendor.
   - `exiftool.py`: image URL → download (≤5 MB, jpg/png/webp) → Pillow `getexif()` in thread → DateTime, Make, Model, Software, GPSPresence.
   - `iban.py`: offline — country, length check, mod-97 checksum, pretty format; honest invalid output.
   - `dork.py`: input → builds operator queries (site:, filetype:, intext:) as tappable `https://www.google.com/search?q=` URL buttons + `copy_text` button.
3. Handler: FSM `ToolFlow.waiting_query` (state per tool code) → prompt + `full(◀ Back)` → on text: start loader → lookup → registry render → `RESULT_WRAP` → row2(`[🔄 Again] [📋 Copy]` via copy_text) + `full(◀ Back to Menu)` (Back ALWAYS last full-width row).

**Verify**: `pytest -q tests/test_osint_parsers.py`; live smoke: run `lookup()` for phone/email/ip/rdap/mac/bin examples directly (network) — expect ≤ ~10 s each.
**Risk**: High (third-party flakiness) — mitigate with timeouts + partial rendering.
**Deps**: Phase 3, 5.

### Phase 7 — Render deployment + docs  *(low)*
**Files**: `render.yaml`, `Dockerfile` (optional), `README.md`
1. `render.yaml`:
```yaml
services:
  - type: web
    name: osint-bot
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: python -m osintbot.main
    healthCheckPath: /health
    disk:
      name: bot-data
      mountPath: /var/data
      sizeGB: 1
    envVars:
      - key: RUN_MODE        value: webhook
      - key: DB_PATH         value: /var/data/bot.db
      - key: BOT_TOKEN       sync: false
      - key: ADMIN_IDS       sync: false
      - key: PUBLIC_BASE_URL value: https://osint-bot.onrender.com
```
2. README: create bot via @BotFather → set Render env vars → deploy → set `set_webhook` (auto on boot, prints URL) → local dev: `RUN_MODE=polling python -m osintbot.main`.
3. Pitfalls section in README (below).

**Verify**: after deploy `curl https://<app>.onrender.com/health` → 200; `/start` works; restart service → webhook re-registered.
**Risk**: Low. **Deps**: all.

---

## 5. Testing strategy

- **Unit** (`pytest -q`):
  - `tests/test_keyboards.py` — layout rules: (a) last row == 1 button and is the Back/Verify tail; (b) every feature row has exactly 2; (c) styles restricted to primary/success/danger/None; (d) no reply keyboards.
  - `tests/test_cb_size.py` — all prefixes + worst-case args ≤ 64 bytes; `adm:ms:` overflow falls back to FSM path.
  - `tests/test_db.py` — CRUD + join_mode CHECK constraint rejection.
  - `tests/test_osint_parsers.py` — phone normalization, MX record parse, disposable-domain detection, RDAP/JSON→dict, hash outputs.
- **Integration (manual)**: add channel (public/private×2), force-join with second account, request approval auto-message, all 16 tools (live spot-check network ones), delete confirm, join-mode toggle.
- **Smoke on Render**: `/health`, one tool run, service restart persistence of `bot.db`.

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `editMessageText` "message is not modified" | wrap every edit in try/except `TelegramBadRequest`, ignore that exact message |
| callback_data > 64 bytes | `cb()` helper + unit test; FSM fallback for long args |
| `getChatMember` needs bot admin | try/except per channel; unverifiable → warn + don't hard-lock; admin flow pre-checks membership rights |
| webhook secret leakage | `secret_token` on `set_webhook` + header check; 403 otherwise |
| forward_origin hidden (anonymous admin) | explicit error text asking for a link/`@username` instead |
| `exportChatInviteLink` right missing | catch `TelegramForbiddenError` → re-prompt with the ⚠️ admin-rights text |
| OSINT sources slow/down | per-call `asyncio.wait_for(8)`, loader cap 10 s → partial result with danger-styled note |
| Render free tier sleeps | on boot re-`set_webhook`; README notes cold-start delay; polling fallback flag |
| MemoryStorage lost on restart | acceptable; document; DB-backed storage listed as future work |
| Blocking sync code (dns/socket) | always `asyncio.to_thread` |

## 7. Success criteria

- [ ] `python -m compileall osintbot` and `pytest -q` green.
- [ ] Admin can add (public + both private modes), list, toggle mode, delete channels; non-admin blocked.
- [ ] User without membership sees force-join screen; verify passes after joining; request-mode auto-approves and DMs welcome.
- [ ] All 16 tools return rich HTML results with animated loading, throttled edits, back button always last/full-width.
- [ ] No `ReplyKeyboard*` anywhere; every non-tail row has exactly 2 buttons (test-enforced).
- [ ] Render: `/health` 200, sqlite persisted across restarts, webhook registered.
