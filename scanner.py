import os, json, time, math, re, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
TZ = ZoneInfo(CFG.get("market_timezone", "Asia/Jakarta"))
STATE_PATH = os.path.join(ROOT, "data", "last_alerts.json")
FALLBACK_PATH = os.path.join(ROOT, "tickers_fallback.txt")

def now_local():
    return datetime.now(TZ)

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def recursive_tickers(obj):
    found = set()
    if isinstance(obj, dict):
        keys = {str(k).lower(): k for k in obj.keys()}
        code_key = next((keys[k] for k in ("kodeemiten", "code", "ticker", "symbol") if k in keys), None)
        if code_key:
            code = str(obj.get(code_key, "")).strip().upper()
            is_stock = obj.get("EfekEmiten_Saham", obj.get("efekEmiten_Saham", True))
            status = str(obj.get("Status", obj.get("status", "0"))).lower()
            if re.fullmatch(r"[A-Z]{4}", code) and is_stock is not False and status not in ("delisted", "inactive"):
                found.add(code)
        for v in obj.values():
            found |= recursive_tickers(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= recursive_tickers(v)
    return found

def get_universe():
    url = CFG["universe_url"]
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; BEI-Auto-Scanner/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.idx.co.id/id/perusahaan-tercatat/profil-perusahaan/"
    }
    try:
        r = requests.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        data = r.json()
        tickers = sorted(recursive_tickers(data))
        if len(tickers) >= 100:
            print(f"Universe BEI berhasil diambil: {len(tickers)} kode.")
            return tickers[:int(CFG.get("max_tickers", 1000))], "IDX"
        print(f"Respons IDX tidak memuat universe yang cukup ({len(tickers)}).")
    except Exception as e:
        print(f"Gagal mengambil universe IDX: {e}")
    fallback = []
    if os.path.exists(FALLBACK_PATH):
        with open(FALLBACK_PATH, encoding="utf-8") as f:
            fallback = [x.strip().upper() for x in f if x.strip() and not x.startswith("#")]
    if len(fallback) < 100:
        raise RuntimeError("Daftar seluruh saham tidak tersedia. Endpoint IDX gagal dan fallback hanya berisi contoh. Jangan anggap hasil ini pemindaian seluruh BEI.")
    return fallback[:int(CFG.get("max_tickers", 1000))], "fallback"

def normalize_yf_frame(raw, ticker):
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        # yfinance commonly returns (Ticker, Price) with group_by='ticker'
        if ticker in raw.columns.get_level_values(0):
            df = raw[ticker].copy()
        elif ticker in raw.columns.get_level_values(-1):
            df = raw.xs(ticker, axis=1, level=-1).copy()
        else:
            return None
    else:
        df = raw.copy()
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return None
    df = df[list(required)].copy()
    df = df.dropna(subset=["close"])
    return df if not df.empty else None

def fetch_intraday(tickers):
    symbols = [t + ".JK" for t in tickers]
    results = {}
    batch_size = int(CFG.get("batch_size", 40))
    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]
        try:
            raw = yf.download(
                tickers=batch, period="5d", interval="5m",
                group_by="ticker", auto_adjust=False, threads=False,
                progress=False, prepost=False, timeout=30
            )
            for symbol in batch:
                code = symbol[:-3]
                df = normalize_yf_frame(raw, symbol)
                if df is not None and len(df) >= 55:
                    results[code] = df
        except Exception as e:
            print(f"Batch {start//batch_size + 1} gagal: {e}")
        time.sleep(1.2)
    return results

def score_one(code, df):
    df = df.copy().sort_index()
    df = df[~df.index.duplicated(keep="last")]
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close", "volume"])
    if len(df) < 55:
        return None

    close, high, low, vol = df.close, df.high, df.low, df.volume
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    if not np.isfinite(last) or last <= 0:
        return None

    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - (100 / (1 + rs))).iloc[-1])
    if not np.isfinite(rsi):
        rsi = 50.0

    # VWAP dihitung untuk sesi perdagangan lokal terbaru yang ada di data.
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        local_dates = idx.tz_convert(TZ).date
    else:
        local_dates = idx.date
    latest_date = local_dates[-1]
    session = df[np.array(local_dates) == latest_date]
    typical = (session.high + session.low + session.close) / 3
    vwap_den = float(session.volume.sum())
    vwap = float((typical * session.volume).sum() / vwap_den) if vwap_den > 0 else last

    avg_vol = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else 0
    rvol = float(vol.iloc[-1] / avg_vol) if avg_vol > 0 else 0
    avg_value = float((close * vol).iloc[-20:].mean())

    recent_high = float(high.iloc[-21:-1].max())
    recent_low = float(low.iloc[-21:-1].min())
    change_pct = (last / prev - 1) * 100 if prev else 0

    # Skor gabungan, bukan probabilitas kemenangan.
    score = 0
    reasons = []
    if ma5 > ma20: score += 10; reasons.append("MA5 di atas MA20")
    if ma20 > ma50: score += 10; reasons.append("MA20 di atas MA50")
    if last > ma20: score += 5
    if last > vwap: score += 10; reasons.append("harga di atas VWAP")
    if rvol >= 1.5: score += 15; reasons.append(f"RVOL {rvol:.1f}x")
    elif rvol >= 1.2: score += 8; reasons.append(f"RVOL {rvol:.1f}x")
    if 50 <= rsi <= 70: score += 10; reasons.append(f"RSI {rsi:.0f}")
    elif 45 <= rsi < 50: score += 5
    if last > recent_high: score += 20; reasons.append("breakout high 20 bar")
    elif last > recent_low * 1.015 and last > ma5: score += 10; reasons.append("momentum/pullback pulih")
    if change_pct > 0: score += 5
    if avg_value >= float(CFG["min_avg_value_idr"]): score += 15; reasons.append("likuiditas lolos")
    elif avg_value >= float(CFG["min_avg_value_idr"]) * 0.5: score += 7

    if last < float(CFG["min_price_idr"]):
        return None
    if avg_value < float(CFG["min_avg_value_idr"]) * 0.5:
        return None
    if rsi >= 78 or rsi < 35:
        return None

    # Level indikatif berbasis volatilitas/range; bukan order aktual.
    bar_range = float((high - low).rolling(14).mean().iloc[-1])
    risk_unit = max(bar_range, last * 0.007)
    entry_low = last
    entry_high = last + max(last * 0.002, risk_unit * 0.15)
    sl = max(0, last - risk_unit)
    tp1 = last + risk_unit * 1.5
    tp2 = last + risk_unit * 2.2

    return {
        "ticker": code, "price": last, "score": int(min(score, 100)),
        "rsi": rsi, "rvol": rvol, "ma5": ma5, "ma20": ma20, "ma50": ma50,
        "vwap": vwap, "avg_value": avg_value, "change_pct": change_pct,
        "entry_low": entry_low, "entry_high": entry_high, "sl": sl,
        "tp1": tp1, "tp2": tp2, "reasons": reasons
    }

def fmt_price(x):
    return f"Rp{int(round(x)):,}".replace(",", ".")

def telegram_send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram belum dikonfigurasi; hasil hanya tampil di log.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id, "text": message, "disable_web_page_preview": True
    }, timeout=20)
    r.raise_for_status()

def main():
    stamp = now_local()
    print("Waktu pemindaian:", stamp.isoformat())
    tickers, source = get_universe()
    print(f"Universe: {len(tickers)} ticker; sumber: {source}")
    data = fetch_intraday(tickers)
    print(f"Data intraday berhasil: {len(data)} ticker")
    candidates = []
    for code, df in data.items():
        try:
            row = score_one(code, df)
            if row and row["score"] >= int(CFG["min_score"]):
                candidates.append(row)
        except Exception as e:
            print(f"Analisis {code} gagal: {e}")
    candidates.sort(key=lambda x: (x["score"], x["rvol"], x["avg_value"]), reverse=True)
    candidates = candidates[:int(CFG["max_alerts"])]

    if not candidates:
        print("NO TRADE: tidak ada kandidat yang lolos filter.")
        return

    state = load_state()
    today = stamp.strftime("%Y-%m-%d")
    sent = []
    for c in candidates:
        key = f"{today}:{c['ticker']}"
        # Kirim ulang hanya jika skor naik >=10 dari alert terakhir hari ini.
        previous = int(state.get(key, 0))
        if c["score"] >= previous + 10 or previous == 0:
            sent.append(c)
            state[key] = c["score"]

    if sent:
        lines = [
            "📊 BEI AUTO SCANNER — FREE",
            f"Waktu: {stamp.strftime('%d-%m-%Y %H:%M WIB')}",
            f"Universe: {len(tickers)} ticker | Data: {len(data)} ticker",
            "Strategi: breakout + pullback + momentum",
            "Catatan: data Yahoo Finance tidak dijamin real-time."
        ]
        for c in sent:
            lines += [
                "",
                f"🔎 {c['ticker']} | Skor {c['score']}/100",
                f"Harga referensi: {fmt_price(c['price'])} ({c['change_pct']:+.2f}%)",
                f"Entry pantau: {fmt_price(c['entry_low'])}–{fmt_price(c['entry_high'])}",
                f"TP1: {fmt_price(c['tp1'])} | TP2: {fmt_price(c['tp2'])}",
                f"SL indikatif: {fmt_price(c['sl'])}",
                f"MA5/20/50: {fmt_price(c['ma5'])} / {fmt_price(c['ma20'])} / {fmt_price(c['ma50'])}",
                f"RSI: {c['rsi']:.1f} | RVOL: {c['rvol']:.2f}x | VWAP: {fmt_price(c['vwap'])}",
                "Alasan: " + ", ".join(c["reasons"][:5])
            ]
        telegram_send("\n".join(lines))
        save_state(state)
    else:
        print("Kandidat ada, tetapi belum ada peningkatan skor yang cukup untuk notifikasi ulang.")

if __name__ == "__main__":
    main()
