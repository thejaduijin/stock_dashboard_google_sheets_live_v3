# AgentDesk — Indian Stock Analysis Dashboard

A local, one-click dashboard where a panel of named agents (Scout, Technician,
Fundamentalist, Newsdesk, Bull, Bear, Judge, Messenger) screens Indian stocks,
debates each pick, and sends BUY signals to your Telegram. Runs entirely on
your own machine — no cloud backend.

## What it does

1. **Scout** screens the universe (demo bundles, or live NSE data via
   `yfinance`) and shortlists the biggest movers per cap segment.
2. **Technician / Fundamentalist / Newsdesk** cover price action, valuation,
   and news sentiment for each shortlisted stock.
3. **Bull vs Bear** debate each stock, and the **Judge** issues a verdict
   (BUY / WATCH / AVOID) with a 1–10 confidence score — either via an LLM
   panel or a deterministic rule engine that always works.
4. **Messenger** sends a Telegram message for every BUY signal at or above
   your confidence threshold, plus a daily summary. Every run is logged to
   a local SQLite database (`audit.db`).

**This tool only analyzes and messages. It never places any trade.**

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. (Optional) Set up the LLM debate engine

The app auto-detects, in this order:

1. **Claude Code CLI** (uses your Claude subscription, no API key, no
   per-call billing):
   ```bash
   npm install -g @anthropic-ai/claude-code
   claude
   # then run /login and sign in with your Claude Pro/Max plan
   ```
   Once `claude` is on your `PATH` and logged in, the app will use it
   automatically.

2. **Anthropic API key** — set `ANTHROPIC_API_KEY` in `.env`.

3. **OpenAI API key** — set `OPENAI_API_KEY` in `.env`.

If none of these are available, the app **automatically falls back** to a
deterministic rule-based engine — the dashboard always works, with or
without an LLM. You can force a specific provider with `LLM_PROVIDER` in
`.env` (`claude_code` | `anthropic` | `openai`).

## 3. Set up Telegram alerts

1. Message **@BotFather** on Telegram, run `/newbot`, and copy the bot
   token it gives you.
2. Message **@userinfobot** to get your numeric chat ID.
3. Copy `.env.example` to `.env` and fill in:
   ```
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```

If Telegram isn't configured, the app still runs fine — it just skips the
send and logs that alerts were skipped.

## 4. Run it

```bash
python app.py
```

Open **http://127.0.0.1:5000** and click **Start agents**.

- Use **Demo** mode first to see the full flow offline, using the bundled
  sample data in `demo_data/`.
- Use **Live** mode during NSE market hours (Mon–Fri, 09:15–15:30 IST) to
  pull real data for the tickers listed in `universe.json` (edit this file
  to change which stocks are screened).
- Use **Live · Excel Final List** to skip `universe.json` entirely and feed
  the agents a ticker list straight from your own workbook (see below).

## Using your own Excel "Final List"

If you keep a screened watchlist in Excel (e.g. a "Final List" sheet with an
**NSE Code** column), you can hand it straight to the agents instead of
maintaining `universe.json`:

1. In the dashboard, set the mode dropdown to **Live · Excel Final List**.
2. Click **⬆ Upload Final List (.xlsx)** and pick your workbook.
3. The app looks for a sheet whose name contains "Final List" (falls back to
   the first sheet if none matches), finds the column header **"NSE Code"**,
   and reads every ticker below it until a blank row. Each code gets `.NS`
   appended automatically (e.g. `RELIANCE` → `RELIANCE.NS`).
4. Once you see "N tickers loaded" next to the button, click **Start
   agents**. Scout skips re-screening — since this list is already your
   curated pick, every ticker goes straight to Technician/Fundamentalist/
   Newsdesk coverage and the Bull/Bear/Judge debate, pulling live prices via
   `yfinance`.

The upload is remembered (saved to `excel_universe.json`) so you don't need
to re-upload it every time you restart the app — just re-upload when you
update the sheet with a new list.

## File layout

| File | Purpose |
|---|---|
| `app.py` | Flask server, agent pipeline/state machine, Telegram sender, SQLite audit |
| `scoring.py` | Deterministic rule-based agents + Judge (always-works fallback) |
| `llm.py` | LLM debate engine, provider auto-detection, grounding verifier |
| `data_sources.py` | Demo loader + yfinance live adapter + evidence bundle builder |
| `dashboard.html` | Self-contained UI (inline CSS/JS, no build step) |
| `universe.json` | Editable ticker list per cap segment (live mode) |
| `demo_data/*.json` | Sample evidence bundles for offline demo mode |
| `.env.example` | Config template — copy to `.env` |

## Config reference (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | — | Your chat ID from @userinfobot |
| `ANTHROPIC_API_KEY` | — | Optional, enables Anthropic API debate engine |
| `OPENAI_API_KEY` | — | Optional, enables OpenAI debate engine |
| `LLM_PROVIDER` | auto | Force `claude_code`, `anthropic`, or `openai` |
| `BRAND` | `AgentDesk` | Dashboard header name |
| `CONFIDENCE_THRESHOLD` | `7` | Minimum Judge confidence to fire a BUY alert |
| `AGENT_DELAY` | `0.6` | Seconds of visual pacing between pipeline stages |
| `SHORTLIST_PER_BUCKET` | `4` | Stocks kept per cap segment after screening |
| `PORT` | `5000` | Local server port |

## Notes on the data feed

The evidence bundle built from `yfinance` does **not** include raw
fundamental ratios like P/E or ROE — only price, technicals (RVOL, SMA,
52-week range), analyst targets/consensus, and recent news headlines. Any
field that can't be computed is set to `null` and named in `data_gaps`, and
every agent is instructed to say "data unavailable" rather than invent a
number.

## Disclaimer

This is an analysis tool for personal research only. It does not place
trades and is not investment advice.


## Live Google Sheets source

The dashboard is now configured to use the supplied Google Sheet as the primary
source for **Live · Excel Final List**:

`1_VnJzEdeMlqLqISrlLDG8MCEbhkJWimndxKVx-_U1mU1`

Every time **Start agents** is pressed in Live · Excel Final List mode, the
server fetches the current Google Sheet XLSX export and re-parses the Final List.
The UI also has **Sync Google Sheet** and performs a background sync every 5
minutes while the page is open.

If the Google Sheet cannot be accessed, the previously uploaded local Excel
file is used as a fallback.

For the Google export to work, the spreadsheet must be accessible to the
dashboard (for example, shared so that the export can be read without an
interactive Google login).
