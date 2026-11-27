import json
from pathlib import Path
from datetime import datetime, timezone

JOURNAL = Path("data/trade_journal.json")

def load_journal():
    if JOURNAL.exists():
        try: return json.loads(JOURNAL.read_text())
        except Exception: pass
    return []

def record_signal(item):
    rows = load_journal()
    rows.append(item)
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL.write_text(json.dumps(rows[-2000:], indent=2))

def evaluate_open_signals(fetch_price):
    rows = load_journal()
    changed = False
    for row in rows:
        if row.get("status") != "OPEN":
            continue
        price = fetch_price(row["ticker"])
        if not price:
            continue
        row["last_price"] = price
        if price <= row["stop_loss"]:
            row["status"] = "STOP_LOSS"
        elif price >= row["target_2"]:
            row["status"] = "TARGET_2"
        elif price >= row["target_1"]:
            row["status"] = "TARGET_1"
        if row["status"] != "OPEN":
            row["closed_at"] = datetime.now(timezone.utc).isoformat()
        changed = True
    if changed:
        JOURNAL.write_text(json.dumps(rows, indent=2))
    return rows

def summary():
    rows = load_journal()
    closed = [r for r in rows if r.get("status") in ("STOP_LOSS","TARGET_1","TARGET_2")]
    wins = sum(r["status"] != "STOP_LOSS" for r in closed)
    return {"total":len(rows),"closed":len(closed),"wins":wins,
            "win_rate_pct":round(wins/len(closed)*100,1) if closed else None}
