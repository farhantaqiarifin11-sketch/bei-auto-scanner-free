
import os
import re
import json
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import numpy as np
import pandas as pd
import yfinance as yf


# ==========================================================
# KONFIGURASI
# ==========================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "data")
STATE_FILE = os.path.join(DATA_DIR, "alert_state.json")

TZ = ZoneInfo("Asia/Jakarta")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CFG = json.load(f)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

UNIVERSE_URL = CFG.get(
    "universe_url",
    "https://www.idx.co.id/primary/ListedCompany/GetCompanyProfiles"
)

MAX_TICKERS = int(CFG.get("max_tickers", 1000))
BATCH_SIZE = int(CFG.get("batch_size", 40))
MIN_PRICE = float(CFG.get("min_price_idr", 50))
MIN_VALUE = float(CFG.get("min_avg_value_idr", 1_000_000_000))
MIN_SCORE = float(CFG.get("min_score", 70))
MAX_ALERTS = int(CFG.get("max_alerts", 10))
MAX_DATA_AGE = int(CFG.get("max_live_data_age_minutes", 45))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


# ==========================================================
# TELEGRAM
# ==========================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum diatur."
        )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=30
    )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram gagal: {result}")


# ==========================================================
# MENGAMBIL UNIVERSE SAHAM BEI
# ==========================================================

def extract_tickers(data):
    found = []

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list):
                found.extend(extract_tickers(value))

            elif isinstance(value, dict):
                found.extend(extract_tickers(value))

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                ticker = None

                for key in (
                    "KodeEmiten",
                    "kodeEmiten",
                    "Code",
                    "code",
                    "Kode",
                    "kode"
                ):
                    if item.get(key):
                        ticker = str(item[key]).strip().upper()
                        break

                if ticker and re.fullmatch(r"[A-Z0-9]{3,5}", ticker):
                    found.append(ticker)

    return found


def get_universe():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.idx.co.id/"
    }

    logging.info("Mengambil daftar saham dari BEI...")

    response = requests.get(
        UNIVERSE_URL,
        headers=headers,
        timeout=40
    )
    response.raise_for_status()

    data = response.json()
    tickers = sorted(set(extract_tickers(data)))

    if not tickers:
        raise RuntimeError(
            "Daftar saham BEI kosong atau format API berubah."
        )

    tickers = tickers[:MAX_TICKERS]

    logging.info("Jumlah saham ditemukan: %s", len(tickers))

    return tickers


# ==========================================================
# INDIKATOR TEKNIKAL
# ==========================================================

def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    return 100 - (100 / (1 + rs))


def analyze_stock(ticker, df):
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        return None

    required = {"Close", "High", "Low", "Volume"}

    if not required.issubset(df.columns):
        return None

    df = df.copy()
    df = df.dropna(subset=["Close", "Volume"])

    if len(df) < 50:
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    price = float(close.iloc[-1])

    if not np.isfinite(price) or price < MIN_PRICE:
        return None

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()

    rsi = calculate_rsi(close)

    typical_price = (high + low + close) / 3
    cumulative_volume = volume.cumsum()

    vwap = (
        (typical_price * volume).cumsum()
        / cumulative_volume.replace(0, np.nan)
    )

    avg_volume = volume.rolling(20).mean()

    if pd.isna(ma50.iloc[-1]) or pd.isna(rsi.iloc[-1]):
        return None

    current_rsi = float(rsi.iloc[-1])
    current_ma5 = float(ma5.iloc[-1])
    current_ma20 = float(ma20.iloc[-1])
    current_ma50 = float(ma50.iloc[-1])
    current_vwap = float(vwap.iloc[-1])

    avg_vol = float(avg_volume.iloc[-1])

    if not np.isfinite(avg_vol) or avg_vol <= 0:
        return None

    current_volume = float(volume.iloc[-1])
    rvol = current_volume / avg_vol

    avg_value = float(
        (close * volume).rolling(20).mean().iloc[-1]
    )

    if not np.isfinite(avg_value) or avg_value < MIN_VALUE:
        return None

    score = 0
    signals = []

    # MA trend
    if price > current_ma5:
        score += 10
        signals.append("Harga > MA5")

    if current_ma5 > current_ma20:
        score += 15
        signals.append("MA5 > MA20")

    if current_ma20 > current_ma50:
        score += 15
        signals.append("MA20 > MA50")

    # RSI momentum
    if 50 <= current_rsi <= 70:
        score += 15
        signals.append("RSI positif")
    elif 45 <= current_rsi < 50:
        score += 7

    # VWAP
    if price > current_vwap:
        score += 15
        signals.append("Di atas VWAP")

    # Relative volume
    if rvol >= 2:
        score += 20
        signals.append("Volume meningkat")
    elif rvol >= 1.5:
        score += 15
        signals.append("Volume di atas rata-rata")
    elif rvol >= 1.2:
        score += 10

    # Perubahan harga
    if len(close) >= 4:
        change = (
            (price / float(close.iloc[-4]) - 1) * 100
        )

        if change > 0:
            score += 10
            signals.append("Momentum harga positif")
    else:
        change = 0

    return {
        "ticker": ticker,
        "price": price,
        "score": min(score, 100),
        "rsi": current_rsi,
        "rvol": rvol,
        "ma5": current_ma5,
        "ma20": current_ma20,
        "ma50": current_ma50,
        "vwap": current_vwap,
        "avg_value": avg_value,
        "change": change,
        "signals": signals
    }


# ==========================================================
# PEMINDAIAN SAHAM
# ==========================================================

def scan_market():
    tickers = get_universe()
    results = []

    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start:start + BATCH_SIZE]

        symbols = [ticker + ".JK" for ticker in batch]

        logging.info(
            "Memindai batch %s dari %s",
            start // BATCH_SIZE + 1,
            (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
        )

        try:
            data = yf.download(
                tickers=symbols,
                period="5d",
                interval="5m",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
                timeout=35
            )

        except Exception as exc:
            logging.warning("Batch gagal: %s", exc)
            continue

        if data is None or data.empty:
            continue

        for ticker in batch:
            symbol = ticker + ".JK"

            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if symbol not in data.columns.get_level_values(0):
                        continue

                    stock_data = data[symbol]
                else:
                    if len(batch) != 1:
                        continue

                    stock_data = data

                result = analyze_stock(ticker, stock_data)

                if result and result["score"] >= MIN_SCORE:
                    results.append(result)

            except Exception as exc:
                logging.warning(
                    "Gagal menganalisis %s: %s",
                    ticker,
                    exc
                )

        time.sleep(1)

    results.sort(
        key=lambda x: (x["score"], x["rvol"], x["avg_value"]),
        reverse=True
    )

    return results[:MAX_ALERTS]


# ==========================================================
# FORMAT PESAN
# ==========================================================

def rupiah(value):
    return "Rp{:,.0f}".format(value).replace(",", ".")


def format_results(results, title, indicative=False):
    now = datetime.now(TZ)

    lines = [
        f"<b>{title}</b>",
        f"Waktu: {now.strftime('%d-%m-%Y %H:%M WIB')}",
        ""
    ]

    if indicative:
        lines.append(
            "⚠️ Data pra-pembukaan bersifat indikatif "
            "dan dapat menggunakan data perdagangan terakhir."
        )
        lines.append("")

    if not results:
        lines.append(
            "Belum ditemukan saham yang memenuhi kriteria."
        )
        return "\n".join(lines)

    for i, stock in enumerate(results, 1):
        signals = ", ".join(stock["signals"][:4])

        lines.extend([
            f"<b>{i}. {stock['ticker']}</b>",
            f"Harga: {rupiah(stock['price'])}",
            f"Skor teknikal: {stock['score']}/100",
            f"RSI: {stock['rsi']:.1f}",
            f"RVOL: {stock['rvol']:.2f}x",
            f"Perubahan: {stock['change']:.2f}%",
            f"Rata-rata nilai transaksi: {rupiah(stock['avg_value'])}",
            f"Sinyal: {signals or '-'}",
            ""
        ])

    lines.append(
        "Catatan: Ini adalah hasil penyaringan teknikal, "
        "bukan rekomendasi beli atau jual."
    )

    return "\n".join(lines)


# ==========================================================
# PENGATURAN JENIS NOTIFIKASI
# ==========================================================

def get_scan_mode():
    now = datetime.now(TZ)
    hour = now.hour
    minute = now.minute

    if hour == 6:
        return "TOP10"

    if hour == 8 and minute >= 35:
        return "PREOPEN"

    if hour == 9 and 10 <= minute <= 20:
        return "MOMENTUM"

    return "ROUTINE"


# ==========================================================
# RIWAYAT NOTIFIKASI
# ==========================================================

def load_state():
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.isfile(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def filter_new_alerts(results, state, mode):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    key = f"{today}_{mode}"

    previous = set(state.get(key, []))

    fresh = [
        stock for stock in results
        if stock["ticker"] not in previous
    ]

    state[key] = sorted(
        previous | {stock["ticker"] for stock in results}
    )

    return fresh


# ==========================================================
# MAIN
# ==========================================================

def main():
    now = datetime.now(TZ)

    logging.info("BEI AUTO SCANNER DIMULAI")
    logging.info("Waktu WIB: %s", now.isoformat())

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Secret Telegram belum dikonfigurasi di GitHub."
        )

    mode = get_scan_mode()

    logging.info("Mode pemindaian: %s", mode)

    results = scan_market()

    if mode == "TOP10":
        message = format_results(
            results,
            "📊 TOP 10 SAHAM BEI - WATCHLIST PAGI",
            indicative=True
        )
        send_telegram(message)

    elif mode == "PREOPEN":
        message = format_results(
            results,
            "🔎 HASIL PEMINDAIAN PRA-PEMBUKAAN",
            indicative=True
        )
        send_telegram(message)

    elif mode == "MOMENTUM":
        message = format_results(
            results,
            "🚀 ALERT MOMENTUM SAHAM BEI"
        )
        send_telegram(message)

    else:
        state = load_state()

        fresh = filter_new_alerts(
            results,
            state,
            "ROUTINE"
        )

        save_state(state)

        if fresh:
            message = format_results(
                fresh,
                "📈 ALERT PEMINDAIAN RUTIN BEI"
            )
            send_telegram(message)
        else:
            logging.info(
                "Tidak ada sinyal baru untuk dikirim."
            )

    logging.info("SCANNER SELESAI")


if __name__ == "__main__":
    main()
