# 🔍 OSINT BOT — Premium Telegram OSINT Bot

Aiogram v3 + SQLite bot with **16 free OSINT tools**, coloured inline buttons,
animated loading, rich HTML cards, admin channel management and a
force-to-join / join-request system. Deploys to **Render** (webhook) or runs
locally with long polling.

## ✨ Features

### User side
- `/start` → premium welcome (banner `<pre>`, blockquote, spoiler) + all saved
  channels as buttons + **✅ Verify** + main menu with 16 tools
- **Force join**: tools stay locked until the user joined every channel
  (`Verify` re-checks membership live)
- 16 tools — all keyless:

  | | | |
  |---|---|---|
  | 📱 Number → Info | 📧 Email → Info | 👤 Username → 18-site scan |
  | 🔍 Telegram @ → Info | 🆔 Telegram ID → Info | 🌐 IP → Info |
  | 🔗 URL → Info | 🌍 Domain → DNS | 🧬 Domain → WHOIS |
  | 🔢 Text → Hash/Encode | 🔑 MD5 → Reverse | 💳 BIN → Card info |
  | 📡 MAC → Vendor | 🖼️ Image → EXIF | 📇 IBAN → Validate |
  | 🔎 Google Dork builder | | |

- **Cool loading**: spinner `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` + % progress while fetching,
  throttled to 1 edit/sec, 10 s cap → partial-result card

### Admin side (`ADMIN_IDS`)
- `🔧 Channel Manage` → `➕ Add Channel` / `📋 Channel List` / `◀ Back`
- Add via `t.me` link, `@username`, invite link or a **forwarded message** —
  the bot checks it is actually an admin first
- Public → direct join; Private → choose **📩 Request** (join-request flow) or
  **🔗 Direct** (exported invite link)
- Channel list → detail (members, mode, bot-admin status) → delete (with
  confirm) or change join mode (link regenerated automatically)
- Join requests are **auto-approved** → user gets `✅ Request approved` +
  `➡ Main Menu` button

### UI rules (enforced by `osintbot/keyboards.py` + unit tests)
1. Feature buttons only in **2-per-row** (left/right) or **single full-width** rows
2. **Back / Join / Verify always full-width on the last row** — never left/right
3. Colours via Bot API `style`: `primary` blue · `success` green · `danger` red
4. **No reply keyboards anywhere** (test-enforced)
5. All copy is rich HTML (`<b>`, `<blockquote>`, spoilers, emoji)

## 🚀 Run locally

```bash
pip install -r requirements.txt
cp .env.example .env      # fill BOT_TOKEN + ADMIN_IDS
python -m osintbot.main   # RUN_MODE=polling default
pytest -q                 # test suite
```

1. Create the bot with [@BotFather](https://t.me/botFather) → copy the token
2. Get your numeric user id from [@userinfobot](https://t.me/userinfobot)
3. `.env`:
   ```
   BOT_TOKEN=123456:ABC-...
   ADMIN_IDS=123456789
   RUN_MODE=polling
   DB_PATH=./bot.db
   ```
4. Make the bot an **admin** in your channels (rights: *Invite users via link*,
   *Add members*) — then `🔧 Channel Manage → ➕ Add Channel`

## ☁️ Deploy on Render

1. Push this repo to GitHub
2. Render → **New + → Web Service** → pick the repo (Render reads `render.yaml`)
3. Set env vars (Dashboard → Environment):
   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | from BotFather |
   | `ADMIN_IDS` | your numeric id (comma-separated for several) |
   | `PUBLIC_BASE_URL` | `https://<your-app>.onrender.com` |
   | `WEBHOOK_SECRET` | long random string |
4. Deploy — the service auto-registers the webhook on boot
5. Check: `curl https://<your-app>.onrender.com/health` → `{"ok": true}`

The 1 GB disk is mounted at `/var/data` and `bot.db` lives there, so
channels/users survive redeploys and restarts.

> Render free tier sleeps after inactivity → first message takes ~30 s
> (cold start). Webhook re-registers on every boot automatically.

## 🧪 Tests

```bash
pytest -q                 # offline suite (layout rules, DB, parsers)
LIVE=1 pytest -q          # includes live network checks
```

Layout guarantees proven by tests: back button is always the last, single
row; every feature row has exactly 2 buttons; every `callback_data` ≤ 64
bytes; zero `ReplyKeyboard*` imports.

## 📁 Structure

```
osintbot/
  config.py       env → Config (fails fast without BOT_TOKEN)
  db.py           SQLite CRUD (users, channels, verifications, settings)
  keyboards.py    layout-enforcing inline button builder (styles, rules)
  texts.py        HTML message templates
  toolspec.py     single source of truth for the 16 tools
  loaders.py      LoadingAnimation (spinner + %, 1 edit/s, 10s cap)
  middlewares.py  Db → UserUpsert → ForceJoinGate
  main.py         webhook (/webhook + /health) or polling entry
  handlers/       start, admin, joinrequests, tools (registry + generic)
  osint/          pure lookup modules — lookup() + render() each
tests/            pytest suite (layout, db, osint parsers)
```

## ⚠️ Notes / pitfalls

- **TG ID → phone number is impossible** for any bot (Telegram never exposes
  it) — the `tgid` tool says so honestly instead of faking data
- Tools need no API keys; optional env keys (`NUMVERIFY_API_KEY`,
  `BINLIST_API_KEY`) only *add* data
- `callback_data` is capped at 64 bytes — enforced by `keyboards.cb()`
- SQLite + `MemoryStorage` FSM ⇒ single instance (don't scale horizontally)
- OSINT sources are third-party: every call has an 8 s timeout and renders a
  partial card instead of hanging
