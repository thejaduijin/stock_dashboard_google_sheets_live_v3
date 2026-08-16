"""
app.py
Local one-click dashboard server + CLI mode for GitHub Actions.

Modes:
  - Server mode: python app.py          (runs Flask dashboard on http://127.0.0.1:<PORT>)
  - CLI mode:   python app.py --cli     (runs pipeline once, outputs files, exits)

Pipeline: Scout -> Technician/Fundamentalist/Newsdesk -> Bull/Bear -> Judge -> Messenger
Persists audit trail to SQLite, fires Telegram BUY alerts.
"""

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

import data_sources
import llm

EXCEL_UNIVERSE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excel_universe.json")
EXCEL_WORKBOOK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excel_final_list.xlsx")
# Google Sheets source: this is the user's live-updating source workbook.
GOOGLE_SHEET_ID = os.environ.get(
    "GOOGLE_SHEET_ID",
    "1_VnJzEdeMlqLqISrlLDG8MCEbhkJWimndxKVx-_U1mY"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "audit.db")
ENV_PATH = os.path.join(BASE_DIR, ".env")


# --------------------------------------------------------------------------
# Tiny .env loader (no python-dotenv dependency)
# --------------------------------------------------------------------------

def load_dotenv(path=ENV_PATH):
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


load_dotenv()

BRAND = os.environ.get("BRAND", "AgentDesk")
CONFIDENCE_THRESHOLD = int(os.environ.get("CONFIDENCE_THRESHOLD", "7"))
AGENT_DELAY = float(os.environ.get("AGENT_DELAY", "0.6"))
SHORTLIST_PER_BUCKET = int(os.environ.get("SHORTLIST_PER_BUCKET", "4"))
PORT = int(os.environ.get("PORT", "5000"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

app = Flask(__name__, static_folder=None)

AGENT_DEFS = [
    {"id": "scout", "name": "Scout", "role": "screens the stock universe for movers",
     "stat1_label": "Scanned", "stat2_label": "Shortlisted"},
    {"id": "technician", "name": "Technician", "role": "reads price action, RVOL & trend",
     "stat1_label": "Analyzed", "stat2_label": "Avg RVOL"},
    {"id": "fundamentalist", "name": "Fundamentalist", "role": "weighs valuation & analyst targets",
     "stat1_label": "Covered", "stat2_label": "Avg upside"},
    {"id": "newsdesk", "name": "Newsdesk", "role": "pulls live news & scores sentiment",
     "stat1_label": "Headlines", "stat2_label": "Net tone"},
    {"id": "bull", "name": "Bull", "role": "argues the case to buy",
     "stat1_label": "Cases", "stat2_label": "Avg score"},
    {"id": "bear", "name": "Bear", "role": "argues the case against",
     "stat1_label": "Cases", "stat2_label": "Avg score"},
    {"id": "judge", "name": "Judge", "role": "weighs the debate, issues verdict + confidence",
     "stat1_label": "Verdicts", "stat2_label": "Buy"},
    {"id": "messenger", "name": "Messenger", "role": "sends signals to Telegram",
     "stat1_label": "Sent", "stat2_label": "Engine"},
]

PIPELINE_ORDER = [a["id"] for a in AGENT_DEFS]


def _fresh_state():
    return {
        "running": False,
        "mode": "demo",
        "engine": "-",
        "started_at": None,
        "finished_at": None,
        "agents": {
            a["id"]: {
                **a,
                "status": "offline",  # offline -> working -> done
                "stat1": 0,
                "stat2": "-",
            } for a in AGENT_DEFS
        },
        "kpi": {
            "universe": 0,
            "in_debate": 0,
            "buy_signals": 0,
            "top_pick": "-",
            "top_pick_confidence": 0,
        },
        "verdicts": [],  # feed rows, newest first
        "log": [],
        "data_timestamp": None,
        "error": None,
    }


STATE = _fresh_state()
STATE_LOCK = threading.Lock()
RUN_THREAD = None

# Uploaded "Final List" Excel universe - lives in memory + persisted to disk
EXCEL_UNIVERSE = None


def load_excel_universe_from_disk():
    global EXCEL_UNIVERSE
    if os.path.exists(EXCEL_UNIVERSE_PATH):
        try:
            with open(EXCEL_UNIVERSE_PATH, "r") as f:
                EXCEL_UNIVERSE = json.load(f)
        except (json.JSONDecodeError, OSError):
            EXCEL_UNIVERSE = None


def save_excel_universe_to_disk():
    with open(EXCEL_UNIVERSE_PATH, "w") as f:
        json.dump(EXCEL_UNIVERSE, f, indent=2)


def refresh_excel_universe_from_workbook():
    """Re-parse the last uploaded workbook before every Excel live run."""
    global EXCEL_UNIVERSE
    if not os.path.exists(EXCEL_WORKBOOK_PATH):
        return False
    with open(EXCEL_WORKBOOK_PATH, "rb") as f:
        parsed = data_sources.parse_excel_final_list(f.read())
    previous_filename = EXCEL_UNIVERSE.get("filename") if EXCEL_UNIVERSE else "excel_final_list.xlsx"
    EXCEL_UNIVERSE = {
        "sheet": parsed["sheet"],
        "tickers": parsed["tickers"],
        "filename": previous_filename,
        "uploaded_at": datetime.now().isoformat(),
    }
    save_excel_universe_to_disk()
    return True


def update_state(fn):
    with STATE_LOCK:
        fn(STATE)


def log_line(msg):
    def _apply(s):
        s["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {msg}")
        s["log"] = s["log"][-200:]
    update_state(_apply)
    # Also print to stdout for CI visibility
    print(f"[LOG] {msg}")


def set_agent(agent_id, status=None, stat1=None, stat2=None):
    def _apply(s):
        a = s["agents"][agent_id]
        if status is not None:
            a["status"] = status
        if stat1 is not None:
            a["stat1"] = stat1
        if stat2 is not None:
            a["stat2"] = stat2
    update_state(_apply)


# --------------------------------------------------------------------------
# SQLite audit
# --------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT, finished_at TEXT, mode TEXT, engine TEXT,
        universe_count INTEGER, shortlisted_count INTEGER, buy_count INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS verdicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER, symbol TEXT, name TEXT, cap_segment TEXT,
        verdict TEXT, confidence INTEGER, winner TEXT, rationale TEXT,
        key_catalyst TEXT, live_price REAL, day_change_pct REAL,
        engine TEXT, telegram_sent INTEGER, created_at TEXT
    )""")
    conn.commit()
    conn.close()


def db_insert_run(mode, engine, universe_count, shortlisted_count, buy_count, started_at, finished_at):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO runs (started_at, finished_at, mode, engine, universe_count, "
        "shortlisted_count, buy_count) VALUES (?,?,?,?,?,?,?)",
        (started_at, finished_at, mode, engine, universe_count, shortlisted_count, buy_count),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def db_insert_verdict(run_id, row):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO verdicts (run_id, symbol, name, cap_segment, verdict, confidence, "
        "winner, rationale, key_catalyst, live_price, day_change_pct, engine, telegram_sent, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, row["symbol"], row["name"], row["cap_segment"], row["verdict"],
         row["confidence"], row["winner"], row["rationale"], row["key_catalyst"],
         row["live_price"], row["day_change_pct"], row["engine"],
         int(row["telegram_sent"]), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def telegram_send(text):
    """POST a message to the Telegram Bot API. Never logs/prints the token."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log_line("Messenger: Telegram not configured, skipping send.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        # scrub token from any error string before logging
        safe_err = str(e).replace(TELEGRAM_BOT_TOKEN, "***")
        log_line(f"Messenger: Telegram send failed ({safe_err})")
        return False


def format_buy_message(row):
    return (
        f"\U0001F7E2 <b>BUY SIGNAL</b> — {row['symbol']} ({row['cap_segment'].title()} cap)\n\n"
        f"Verdict: <b>BUY</b> | Confidence: {row['confidence']}/10\n"
        f"Winner: {row['winner']}\n"
        f"Why: {row['rationale']}\n"
        f"Key catalyst: {row['key_catalyst']}\n"
        f"Live price: \u20B9{row['live_price']} | Day change: {row['day_change_pct']}%\n\n"
        f"— Analysis only. No trade was placed. Not investment advice."
    )


def format_summary_message(fired_rows, engine, timestamp):
    if not fired_rows:
        body = "No BUY signals fired this run."
    else:
        lines = [f"• {r['symbol']} — {r['confidence']}/10" for r in fired_rows]
        body = "\n".join(lines)
    return (
        f"\U0001F4CA <b>Daily Summary</b> — {BRAND}\n\n"
        f"{body}\n\n"
        f"Engine: {engine} | Data: {timestamp} IST"
    )


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def _require_yfinance():
    try:
        import yfinance  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "yfinance is not installed. Run: pip install -r requirements.txt"
        )


def run_pipeline(mode, provider=None, source="manual"):
    started_at = datetime.now().isoformat()
    fired_rows = []
    engine_used = "-"

    try:
        # ---------------- Scout ----------------
        set_agent("scout", status="working")

        if mode == "demo":
            log_line("Scout: screening universe (demo data)...")
            all_bundles = data_sources.load_demo_bundles()
            universe_size = len(all_bundles)
            shortlisted = sorted(
                all_bundles, key=lambda b: abs((b.get("price") or {}).get("day_change_pct") or 0),
                reverse=True,
            )[:max(SHORTLIST_PER_BUCKET, min(8, len(all_bundles)))]
        elif mode == "live" and source == "excel":
            _require_yfinance()
            tickers = EXCEL_UNIVERSE["tickers"] if EXCEL_UNIVERSE else []
            sheet_name = EXCEL_UNIVERSE["sheet"] if EXCEL_UNIVERSE else "?"
            log_line(f"Scout: pulling live data for {len(tickers)} tickers from '{sheet_name}'...")
            shortlisted = data_sources.build_bundles_for_tickers(tickers, cap_segment="watchlist")
            all_bundles = shortlisted
            universe_size = len(tickers)
            # Already a curated/final list - no further screening needed.
        else:
            _require_yfinance()
            log_line("Scout: screening universe (live NSE via yfinance)...")
            universe = data_sources.load_universe()
            universe_size = sum(len(v) for v in universe.values())
            all_bundles, shortlisted = data_sources.screen_and_shortlist(
                universe, shortlist_per_bucket=SHORTLIST_PER_BUCKET
            )

        time.sleep(AGENT_DELAY)
        set_agent("scout", status="done", stat1=universe_size, stat2=len(shortlisted))
        update_state(lambda s: s["kpi"].update({
            "universe": universe_size, "in_debate": len(shortlisted)
        }))
        log_line(f"Scout: scanned {universe_size}, shortlisted {len(shortlisted)}.")

        # ---------------- Technician / Fundamentalist / Newsdesk ----------------
        set_agent("technician", status="working")
        set_agent("fundamentalist", status="working")
        set_agent("newsdesk", status="working")
        time.sleep(AGENT_DELAY)

        rvols = [
            (b.get("technicals") or {}).get("rvol") for b in shortlisted
            if (b.get("technicals") or {}).get("rvol") is not None
        ]
        avg_rvol = round(sum(rvols) / len(rvols), 2) if rvols else "-"
        set_agent("technician", status="done", stat1=len(shortlisted), stat2=avg_rvol)

        upsides = [
            (b.get("analyst") or {}).get("upside_pct") for b in shortlisted
            if (b.get("analyst") or {}).get("upside_pct") is not None
        ]
        avg_upside = f"{round(sum(upsides) / len(upsides), 1)}%" if upsides else "-"
        set_agent("fundamentalist", status="done", stat1=len(shortlisted), stat2=avg_upside)

        headlines = sum((b.get("news") or {}).get("total", 0) for b in shortlisted)
        net_tone = sum(
            (b.get("news") or {}).get("positive", 0) - (b.get("news") or {}).get("negative", 0)
            for b in shortlisted
        )
        set_agent("newsdesk", status="done", stat1=headlines, stat2=("+" if net_tone >= 0 else "") + str(net_tone))
        log_line(f"Technician/Fundamentalist/Newsdesk: covered {len(shortlisted)} shortlisted names.")

        # ---------------- Bull / Bear / Judge ----------------
        set_agent("bull", status="working")
        set_agent("bear", status="working")
        set_agent("judge", status="working")

        bull_scores, bear_scores = [], []
        buy_count = 0
        top_pick = None
        top_confidence = -1
        engines_seen = set()

        for ev in shortlisted:
            symbol = ev.get("symbol", "?")
            try:
                result = llm.evaluate(ev, provider=provider)
            except Exception:
                import scoring
                result = scoring.evaluate_deterministic(ev)
                result["_engine"] = "deterministic"

            engine_used = result.get("_engine", "deterministic")
            engines_seen.add(engine_used)

            scores = result.get("scores", {})
            verdict = result.get("verdict", {})

            bull_scores.append(scores.get("bull", {}).get("score", 0))
            bear_scores.append(scores.get("bear", {}).get("score", 0))

            v = verdict.get("verdict", "WATCH")
            conf = verdict.get("confidence", 1)
            price = (ev.get("price") or {}).get("live")
            day_change = (ev.get("price") or {}).get("day_change_pct")

            row = {
                "symbol": symbol,
                "name": ev.get("name", symbol),
                "cap_segment": ev.get("cap_segment", "-"),
                "verdict": v,
                "confidence": conf,
                "winner": verdict.get("winner", "-"),
                "rationale": verdict.get("rationale", ""),
                "key_catalyst": verdict.get("key_catalyst", ""),
                "live_price": price,
                "day_change_pct": day_change,
                "engine": engine_used,
                "telegram_sent": False,
            }

            fired = (v == "BUY" and conf >= CONFIDENCE_THRESHOLD)
            if fired:
                buy_count += 1

            if conf > top_confidence:
                top_confidence = conf
                top_pick = row

            def _apply(s, row=row):
                s["verdicts"].insert(0, row)
                s["verdicts"] = s["verdicts"][:60]
            update_state(_apply)

            if fired:
                fired_rows.append(row)

            time.sleep(min(AGENT_DELAY, 0.35))

        avg_bull = round(sum(bull_scores) / len(bull_scores), 1) if bull_scores else "-"
        avg_bear = round(sum(bear_scores) / len(bear_scores), 1) if bear_scores else "-"
        set_agent("bull", status="done", stat1=len(shortlisted), stat2=avg_bull)
        set_agent("bear", status="done", stat1=len(shortlisted), stat2=avg_bear)
        set_agent("judge", status="done", stat1=len(shortlisted), stat2=buy_count)

        update_state(lambda s: s["kpi"].update({
            "buy_signals": buy_count,
            "top_pick": f"{top_pick['symbol']} ({top_pick['confidence']}/10)" if top_pick else "-",
            "top_pick_confidence": top_pick["confidence"] if top_pick else 0,
        }))
        log_line(f"Judge: {buy_count} BUY signal(s) out of {len(shortlisted)} debated.")

        # ---------------- Messenger ----------------
        set_agent("messenger", status="working")
        sent_count = 0
        for row in fired_rows:
            ok = telegram_send(format_buy_message(row))
            row["telegram_sent"] = ok
            if ok:
                sent_count += 1

        timestamp = data_sources.now_ist_str() if hasattr(data_sources, "now_ist_str") else datetime.now().isoformat()
        telegram_send(format_summary_message(fired_rows, engine_used, timestamp))

        set_agent("messenger", status="done", stat1=sent_count, stat2=engine_used)
        log_line(f"Messenger: sent {sent_count} BUY alert(s) + daily summary.")

        # ---------------- Persist ----------------
        finished_at = datetime.now().isoformat()
        run_id = db_insert_run(mode, engine_used, universe_size, len(shortlisted), buy_count, started_at, finished_at)
        for row in fired_rows:
            db_insert_verdict(run_id, row)
        # also persist non-firing verdicts for a full audit trail
        with STATE_LOCK:
            all_rows = list(STATE["verdicts"])
        for row in all_rows:
            if row not in fired_rows:
                db_insert_verdict(run_id, row)

        def _finish(s):
            s["running"] = False
            s["finished_at"] = finished_at
            s["engine"] = engine_used
            s["data_timestamp"] = timestamp
        update_state(_finish)

        # Save outputs for GitHub Actions
        _save_outputs()

        return True

    except Exception as e:
        traceback.print_exc()
        def _err(s, msg=str(e)):
            s["running"] = False
            s["error"] = msg
        update_state(_err)
        log_line(f"ERROR: {e}")
        return False


def _save_outputs():
    """Save pipeline outputs to disk for GitHub Actions artifacts."""
    # Save verdicts as JSON
    with STATE_LOCK:
        verdicts = list(STATE["verdicts"])
        kpi = dict(STATE["kpi"])
        engine = STATE.get("engine", "-")
        timestamp = STATE.get("data_timestamp", datetime.now().isoformat())

    output = {
        "timestamp": timestamp,
        "engine": engine,
        "kpi": kpi,
        "verdicts": verdicts,
        "total_verdicts": len(verdicts),
    }

    with open("output.json", "w") as f:
        json.dump(output, f, indent=2)

    log_line(f"Outputs saved: {len(verdicts)} verdicts, engine={engine}")


def start_run(mode, provider=None, source="manual"):
    global RUN_THREAD

    def _reset(s):
        base = _fresh_state()
        base["running"] = True
        base["mode"] = mode
        base["started_at"] = datetime.now().isoformat()
        s.clear()
        s.update(base)

    update_state(_reset)
    label = f"{mode} mode" + (" (Excel Final List)" if source == "excel" else "")
    log_line(f"Run started in {label}.")

    RUN_THREAD = threading.Thread(target=run_pipeline, args=(mode, provider, source), daemon=True)
    RUN_THREAD.start()


def run_cli(mode="live", provider=None, source="excel"):
    """Run the pipeline once in CLI mode and exit. For GitHub Actions."""
    print(f"\n{'='*60}")
    print(f"  {BRAND} — CLI Mode")
    print(f"  Mode: {mode} | Source: {source} | Provider: {provider or 'auto'}")
    print(f"{'='*60}\n")

    # Refresh data source
    if source == "excel":
        try:
            refresh_excel_universe_from_google()
            print(f"✅ Google Sheets refreshed: {len(EXCEL_UNIVERSE['tickers'])} tickers")
        except Exception as e:
            print(f"⚠️ Google Sheets failed: {e}")
            if not refresh_excel_universe_from_workbook():
                print("❌ No Excel fallback available. Exiting.")
                sys.exit(1)
            print(f"✅ Excel fallback loaded: {len(EXCEL_UNIVERSE['tickers'])} tickers")

    # Run pipeline synchronously
    success = run_pipeline(mode, provider=provider, source=source)

    if success:
        print(f"\n{'='*60}")
        print("  ✅ Pipeline completed successfully!")
        with STATE_LOCK:
            kpi = dict(STATE["kpi"])
            engine = STATE.get("engine", "-")
        print(f"  Engine: {engine}")
        print(f"  Universe: {kpi['universe']} | Debated: {kpi['in_debate']} | BUYs: {kpi['buy_signals']}")
        print(f"  Top Pick: {kpi['top_pick']}")
        print(f"{'='*60}\n")
        sys.exit(0)
    else:
        print(f"\n{'='*60}")
        print("  ❌ Pipeline failed!")
        print(f"{'='*60}\n")
        sys.exit(1)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "dashboard.html")


@app.route("/upload_universe", methods=["POST"])
def upload_universe():
    """Accept an uploaded .xlsx workbook, parse its 'Final List' sheet."""
    global EXCEL_UNIVERSE

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "No file selected."}), 400
    if not f.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify({"ok": False, "error": "Please upload a .xlsx file."}), 400

    try:
        file_bytes = f.read()
        parsed = data_sources.parse_excel_final_list(file_bytes)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not read the workbook: {e}"}), 400

    with open(EXCEL_WORKBOOK_PATH, "wb") as wf:
        wf.write(file_bytes)

    EXCEL_UNIVERSE = {
        "sheet": parsed["sheet"],
        "tickers": parsed["tickers"],
        "filename": f.filename,
        "uploaded_at": datetime.now().isoformat(),
    }
    save_excel_universe_to_disk()
    log_line(f"Uploaded '{f.filename}' -> sheet '{parsed['sheet']}', {parsed['count']} tickers loaded.")

    return jsonify({
        "ok": True,
        "sheet": parsed["sheet"],
        "count": parsed["count"],
        "symbols": [t["symbol"] for t in parsed["tickers"]],
    })


def refresh_excel_universe_from_google():
    """Fetch the latest public Google Sheet workbook and rebuild the ticker universe."""
    global EXCEL_UNIVERSE
    if not GOOGLE_SHEET_ID:
        return False
    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/export?format=xlsx"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "StockDashboard/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        file_bytes = response.read()

    parsed = data_sources.parse_excel_final_list(file_bytes)
    EXCEL_UNIVERSE = {
        "sheet": parsed["sheet"],
        "tickers": parsed["tickers"],
        "filename": "Google Sheets — live source",
        "uploaded_at": datetime.now().isoformat(),
        "source": "google_sheets",
        "spreadsheet_id": GOOGLE_SHEET_ID,
    }
    save_excel_universe_to_disk()
    return True


@app.route("/refresh_google_data", methods=["POST"])
def refresh_google_data():
    """Accept Google Visualization data fetched by the browser."""
    global EXCEL_UNIVERSE
    try:
        payload = request.get_json(silent=True) or {}
        parsed = data_sources.parse_google_sheet_rows(payload, preferred_sheet=payload.get("sheet") or "Final List")
        EXCEL_UNIVERSE = {
            "sheet": parsed["sheet"],
            "tickers": parsed["tickers"],
            "filename": "Google Sheets — browser live source",
            "uploaded_at": datetime.now().isoformat(),
            "source": "google_sheets_browser",
            "spreadsheet_id": GOOGLE_SHEET_ID,
        }
        save_excel_universe_to_disk()
        log_line(f"Google Sheets (browser) refreshed: {parsed['count']} tickers from '{parsed['sheet']}'.")
        return jsonify({"ok": True, "sheet": parsed["sheet"], "count": parsed["count"], "symbols": [t["symbol"] for t in parsed["tickers"]]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/refresh_google_sheet", methods=["POST"])
def refresh_google_sheet():
    try:
        refresh_excel_universe_from_google()
        log_line(
            f"Google Sheets Final List refreshed: {len(EXCEL_UNIVERSE['tickers'])} "
            f"tickers from '{EXCEL_UNIVERSE['sheet']}'."
        )
        return jsonify({
            "ok": True,
            "sheet": EXCEL_UNIVERSE["sheet"],
            "count": len(EXCEL_UNIVERSE["tickers"]),
            "symbols": [t["symbol"] for t in EXCEL_UNIVERSE["tickers"]],
            "source": "google_sheets",
            "uploaded_at": EXCEL_UNIVERSE["uploaded_at"],
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": (
                "Could not read the Google Sheet. Make sure the sheet is shared "
                "so its contents can be accessed by the dashboard. " + str(e)
            )
        }), 400


@app.route("/refresh_universe", methods=["POST"])
def refresh_universe():
    try:
        if not refresh_excel_universe_from_workbook():
            return jsonify({"ok": False, "error": "No uploaded Excel workbook is available to refresh."}), 400
        log_line(
            f"Excel Final List refreshed: {len(EXCEL_UNIVERSE['tickers'])} tickers from "
            f"'{EXCEL_UNIVERSE['sheet']}'."
        )
        return jsonify({
            "ok": True,
            "sheet": EXCEL_UNIVERSE["sheet"],
            "count": len(EXCEL_UNIVERSE["tickers"]),
            "symbols": [t["symbol"] for t in EXCEL_UNIVERSE["tickers"]],
            "uploaded_at": EXCEL_UNIVERSE["uploaded_at"],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not refresh Excel Final List: {e}"}), 400


@app.route("/universe_status")
def universe_status():
    if EXCEL_UNIVERSE is None:
        return jsonify({"loaded": False})
    return jsonify({
        "loaded": True,
        "sheet": EXCEL_UNIVERSE["sheet"],
        "filename": EXCEL_UNIVERSE["filename"],
        "count": len(EXCEL_UNIVERSE["tickers"]),
        "uploaded_at": EXCEL_UNIVERSE["uploaded_at"],
        "symbols": [t["symbol"] for t in EXCEL_UNIVERSE["tickers"]],
    })


@app.route("/start", methods=["POST"])
def start():
    with STATE_LOCK:
        already_running = STATE["running"]
    if already_running:
        return jsonify({"ok": False, "error": "A run is already in progress."}), 409

    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode", "demo")
    if mode not in ("demo", "live"):
        mode = "demo"
    source = payload.get("source", "manual")
    if source not in ("manual", "excel"):
        source = "manual"
    provider = payload.get("provider") or None

    if mode == "live" and source == "excel":
        try:
            try:
                refresh_excel_universe_from_google()
                log_line(
                    f"Scout source: LIVE Google Sheets — {len(EXCEL_UNIVERSE['tickers'])} "
                    f"tickers from '{EXCEL_UNIVERSE['sheet']}'."
                )
            except Exception as google_error:
                log_line(f"Google Sheets refresh failed; using local Excel fallback: {google_error}")
                if not refresh_excel_universe_from_workbook():
                    return jsonify({
                        "ok": False,
                        "error": "Could not read the Google Sheet and no local Excel fallback is available."
                    }), 400
        except Exception as e:
            return jsonify({"ok": False, "error": f"Could not refresh Final List: {e}"}), 400

    start_run(mode, provider=provider, source=source)
    return jsonify({"ok": True})


@app.route("/status")
def status():
    with STATE_LOCK:
        return jsonify(STATE)


@app.route("/config")
def config():
    forced = os.environ.get("LLM_PROVIDER")
    detected = llm.detect_provider(forced)
    return jsonify({
        "brand": BRAND,
        "agent_count": len(AGENT_DEFS),
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "shortlist_per_bucket": SHORTLIST_PER_BUCKET,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "llm_provider_detected": detected or "none (deterministic fallback)",
        "excel_universe_loaded": EXCEL_UNIVERSE is not None,
    })


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"{BRAND} Stock Dashboard")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode (GitHub Actions)")
    parser.add_argument("--mode", default="live", choices=["demo", "live"], help="Pipeline mode")
    parser.add_argument("--source", default="excel", choices=["manual", "excel"], help="Data source")
    parser.add_argument("--provider", default=None, help="LLM provider (openai/anthropic/claude_code)")
    args = parser.parse_args()

    init_db()
    load_excel_universe_from_disk()

    if args.cli:
        # CLI mode: run once and exit
        run_cli(mode=args.mode, provider=args.provider, source=args.source)
    else:
        # Server mode: run Flask dashboard
        print(f"\n{BRAND} starting on http://127.0.0.1:{PORT}\n")
        app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)