
import os
import re
import csv
import json
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import numpy as np
import pandas as pd
import yfinance as yf


# =========================================================
# 1. KONFIGURASI
# =========================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "data")
STATE_PATH = os.path.join(DATA_DIR, "alert_state.json")
UNIVERSE_PATH = os.path.join(DATA_DIR, "universe", "idx-tickers.csv")

TZ = ZoneInfo("Asia/Jakarta")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

UNIVERSE_URL = CFG.get(
    "universe_url",
    "https://www.idx.co.id/primary/ListedCompany/GetCompanyProfiles"
)

MAX_TICKERS = int(CFG.get("max_tickers", 1000))
BATCH_SIZE = max(1, int(CFG.get("batch_size", 40)))
MIN_PRICE = float(CFG.get("min_price_idr", 50))
MIN_VALUE = float(CFG.get("min_avg_value_idr", 1_000_000_000))
MIN_SCORE = float(CFG.get("min_score", 70))
MAX_ALERTS = int(CFG.get("max_alerts", 10))
MAX_DATA_AGE = int(CFG.get("max_live_data_age_minutes", 45))

os.makedirs(DATA_DIR, exist_ok=True)


# =========================================================
# 2. TELEGRAM
# =========================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Secret TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum tersedia."
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
        raise RuntimeError(f"Telegram error: {result}")

    logging.info("Notifikasi Telegram berhasil dikirim.")


# =========================================================
# 3. DAFTAR SAHAM BEI
# =========================================================

def normalize_ticker(value):
    value = str(value).strip().upper()

    if value.endswith(".JK"):
        value = value[:-3]

    if re.fullmatch(r"[A-Z0-9]{3,5}", value):
        return value

    return None


def extract_tickers(data):
    found = []

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                found.extend(extract_tickers(value))

            elif key.lower() in (
                "kodeemiten", "kode", "code", "symbol", "ticker"
            ):
                ticker = normalize_ticker(value)

                if ticker:
                    found.append(ticker)

    elif isinstance(data, list):
        for item in data:
            found.extend(extract_tickers(item))

    return found


def load_local_universe():
    if not os.path.isfile(UNIVERSE_PATH):
        return []

    logging.info("Membaca daftar ticker lokal.")

    tickers = []

    with open(
        UNIVERSE_PATH,
        encoding="utf-8-sig",
        newline=""
    ) as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            return []

        columns = {
            name.strip().lower(): name
            for name in reader.fieldnames
        }

        column = (
            columns.get("code")
            or columns.get("ticker")
            or columns.get("symbol")
        )

        if not column:
            raise RuntimeError(
                "CSV ticker harus memiliki kolom code, ticker, atau symbol."
            )

        for row in reader:
            ticker = normalize_ticker(row.get(column, ""))

            if ticker:
                tickers.append(ticker)

    return sorted(set(tickers))


def load_idx_universe():
    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
        "Referer": "https://www.idx.co.id/",
        "Origin": "https://www.idx.co.id",
        "X-Requested-With": "XMLHttpRequest"
    }

    session.get(
        "https://www.idx.co.id/",
        headers=headers,
        timeout=20
    )

    response = session.get(
        UNIVERSE_URL,
        headers=headers,
        timeout=40
    )

    response.raise_for_status()

    return sorted(set(extract_tickers(response.json())))


def get_universe():
    # Prioritas pertama: daftar lokal yang telah disiapkan.
    local = load_local_universe()

    if local:
        logging.info(
            "Universe lokal: %s ticker",
            len(local)
        )
        return local[:MAX_TICKERS]

    # Prioritas kedua: IDX.
    try:
        logging.info("Mencoba mengambil daftar saham dari IDX.")

        tickers = load_idx_universe()

        if tickers:
            logging.info(
                "Universe IDX berhasil: %s ticker",
                len(tickers)
            )
            return tickers[:MAX_TICKERS]

    except Exception as exc:
        logging.warning("IDX gagal diakses: %s", exc)

    raise RuntimeError(
        "IDX tidak dapat diakses dan CSV ticker lokal belum tersedia. "
        "Buat data/universe/idx-tickers.csv dengan kolom code "
        "berisi daftar ticker BEI yang valid."
    )


# =========================================================
# 4. INDIKATOR TEKNIKAL
# =========================================================

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

    result = 100 - (100 / (1 + rs))

    result = result.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100
    )

    result = result.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0
    )

    return result


# =========================================================
# 5. VALIDASI DATA
# =========================================================

def normalize_stock_frame(df):
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        return None

    required = ["Close", "High", "Low", "Volume"]

    if not all(col in df.columns for col in required):
        return None

    df = df.copy()

    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)

    df = df[
        (df["Close"] > 0)
        & (df["High"] > 0)
        & (df["Low"] > 0)
        & (df["Volume"] >= 0)
    ]

    if df.empty:
        return None

    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()

    return df


def data_is_fresh(df):
    if df is None or df.empty:
        return False

    timestamp = pd.Timestamp(df.index[-1])

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")

    timestamp = timestamp.tz_convert(TZ)

    now = pd.Timestamp.now(tz=TZ)

    age = (now - timestamp).total_seconds() / 60

    return 0 <= age <= MAX_DATA_AGE


# =========================================================
# 6. ANALISIS SAHAM
# =========================================================

def analyze_stock(ticker, raw_df):
    df = normalize_stock_frame(raw_df)

    if df is None or len(df) < 50:
        return None

    if not data_is_fresh(df):
        logging.info("%s dilewati: data kedaluwarsa.", ticker)
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    price = float(close.iloc[-1])

    if price < MIN_PRICE:
        return None

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()

    rsi = calculate_rsi(close)

    typical_price = (high + low + close) / 3

    # VWAP dihitung per hari perdagangan.
    dates = pd.Series(
        df.index.date,
        index=df.index
    )

    cumulative_pv = (
        typical_price * volume
    ).groupby(dates).cumsum()

    cumulative_volume = volume.groupby(dates).cumsum()

    vwap = cumulative_pv / cumulative_volume.replace(0, np.nan)

    avg_volume = volume.rolling(20).mean()

    avg_value = (
        close * volume
    ).rolling(20).mean()

    if any(
        pd.isna(x)
        for x in [
            ma5.iloc[-1],
            ma20.iloc[-1],
            ma50.iloc[-1],
            rsi.iloc[-1],
            vwap.iloc[-1],
            avg_volume.iloc[-1],
            avg_value.iloc[-1]
        ]
    ):
        return None

    if avg_volume.iloc[-1] <= 0:
        return None

    if avg_value.iloc[-1] < MIN_VALUE:
        return None

    current_rsi = float(rsi.iloc[-1])
    current_vwap = float(vwap.iloc[-1])

    rvol = float(
        volume.iloc[-1] / avg_volume.iloc[-1]
    )

    score = 0
    signals = []

    if price > ma5.iloc[-1]:
        score += 10
        signals.append("Harga di atas MA5")

    if ma5.iloc[-1] > ma20.iloc[-1]:
        score += 15
        signals.append("MA5 > MA20")

    if ma20.iloc[-1] > ma50.iloc[-1]:
        score += 15
        signals.append("MA20 > MA50")

    if 50 <= current_rsi <= 70:
        score += 15
        signals.append("RSI positif")
    elif 45 <= current_rsi < 50:
        score += 7

    if price > current_vwap:
        score += 15
        signals.append("Harga di atas VWAP")

    if rvol >= 2:
        score += 20
        signals.append("Volume sangat meningkat")
    elif rvol >= 1.5:
        score += 15
        signals.append("Volume meningkat")
    elif rvol >= 1.2:
        score += 10

    if len(close) >= 4:
        change = (
            price / float(close.iloc[-4]) - 1
        ) * 100

        if change > 0:
            score += 10
            signals.append("Momentum positif")
    else:
        change = 0

    return {
        "ticker": ticker,
        "price": price,
        "score": min(score, 100),
        "rsi": current_rsi,
        "rvol": rvol,
        "ma5": float(ma5.iloc[-1]),
        "ma20": float(ma20.iloc[-1]),
        "ma50": float(ma50.iloc[-1]),
        "vwap": current_vwap,
        "avg_value": float(avg_value.iloc[-1]),
        "change": float(change),
        "signals": signals
    }


# =========================================================
# 7. PENGAMBILAN DATA YAHOO FINANCE
# =========================================================

def extract_batch_frame(data, symbol, batch_size):
    if data is None or data.empty:
        return None

    if not isinstance(data.columns, pd.MultiIndex):
        if batch_size == 1:
            return data
        return None

    level0 = data.columns.get_level_values(0)
    level1 = data.columns.get_level_values(1)

    if symbol in level0:
        return data[symbol]

    if symbol in level1:
        return data.xs(symbol, axis=1, level=1)

    return None


def download_batch(symbols):
    for attempt in range(2):
        try:
            return yf.download(
                tickers=symbols,
                period="5d",
                interval="5m",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
                timeout=40
            )
        except Exception as exc:
            logging.warning(
                "Unduh batch gagal, percobaan %s: %s",
                attempt + 1,
                exc
            )
            time.sleep(2)

    return None


def scan_market():
    tickers = get_universe()

    logging.info(
        "Memulai pemindaian %s saham.",
        len(tickers)
    )

    results = []

    total_batches = (
        len(tickers) + BATCH_SIZE - 1
    ) // BATCH_SIZE

    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start:start + BATCH_SIZE]

        symbols = [
            ticker + ".JK"
            for ticker in batch
        ]

        batch_number = start // BATCH_SIZE + 1

        logging.info(
            "Batch %s/%s",
            batch_number,
            total_batches
        )

        data = download_batch(symbols)

        if data is None or data.empty:
            logging.warning("Batch kosong.")
            continue

        for ticker, symbol in zip(batch, symbols):
            try:
                frame = extract_batch_frame(
                    data,
                    symbol,
                    len(batch)
                )

                if frame is None:
                    continue

                result = analyze_stock(ticker, frame)

                if result and result["score"] >= MIN_SCORE:
                    results.append(result)

            except Exception:
                logging.exception(
                    "Analisis gagal untuk %s",
                    ticker
                )

        time.sleep(1)

    results.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["avg_value"]
        ),
        reverse=True
    )

    logging.info(
        "Pemindaian selesai. Kandidat: %s",
        len(results)
    )

    return results[:MAX_ALERTS]


# =========================================================
# 8. FORMAT PESAN TELEGRAM
# =========================================================

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
        lines.extend([
            "⚠️ Watchlist indikatif. Bukan sinyal eksekusi.",
            ""
        ])

    if not results:
        lines.append(
            "Tidak ditemukan saham yang memenuhi kriteria."
        )
        return "\n".join(lines)

    for i, stock in enumerate(results, 1):
        signals = ", ".join(stock["signals"][:4])

        lines.extend([
            f"<b>{i}. {stock['ticker']}</b>",
            f"Harga: {rupiah(stock['price'])}",
            f"Skor: {stock['score']}/100",
            f"RSI: {stock['rsi']:.1f}",
            f"RVOL: {stock['rvol']:.2f}x",
            f"Perubahan: {stock['change']:.2f}%",
            f"Rata-rata nilai transaksi: {rupiah(stock['avg_value'])}",
            f"Sinyal: {signals or '-'}",
            ""
        ])

    lines.append(
        "Data teknikal bukan rekomendasi beli atau jual."
    )

    return "\n".join(lines)


# =========================================================
# 9. JADWAL MODE NOTIFIKASI
# =========================================================

def get_scan_mode():
    now = datetime.now(TZ)

    if now.hour == 6:
        return "TOP10"

    if now.hour == 8 and now.minute >= 35:
        return "PREOPEN"

    if now.hour == 9 and 10 <= now.minute <= 20:
        return "MOMENTUM"

    return "ROUTINE"


# =========================================================
# 10. PENCEGAHAN ALERT DUPLIKAT
# =========================================================

def load_state():
    if not os.path.isfile(STATE_PATH):
        return {}

    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logging.warning("Riwayat alert tidak dapat dibaca.")
        return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def filter_new_alerts(results, state, mode):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    key = f"{today}_{mode}"

    previous = set(state.get(key, []))

    fresh = [
        item for item in results
        if item["ticker"] not in previous
    ]

    state[key] = sorted(
        previous | {
            item["ticker"] for item in results
        }
    )

    return fresh


# =========================================================
# 11. PROGRAM UTAMA
# =========================================================

def main():
    now = datetime.now(TZ)

    logging.info("BEI AUTO SCANNER DIMULAI")
    logging.info("Waktu WIB: %s", now.isoformat())

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Telegram Secrets belum dikonfigurasi."
        )

    mode = get_scan_mode()

    logging.info("Mode: %s", mode)

    results = scan_market()

    state = load_state()

    if mode == "TOP10":
        message = format_results(
            results,
            "📊 TOP 10 SAHAM BEI",
            indicative=True
        )

    elif mode == "PREOPEN":
        message = format_results(
            results,
            "🔎 WATCHLIST PRA-PEMBUKAAN",
            indicative=True
        )

    elif mode == "MOMENTUM":
        message = format_results(
            results,
            "🚀 ALERT MOMENTUM BEI"
        )

    else:
        fresh = filter_new_alerts(
            results,
            state,
            "ROUTINE"
        )

        if not fresh:
            logging.info("Tidak ada sinyal baru.")
            return

        message = format_results(
            fresh,
            "📈 ALERT PEMINDAIAN RUTIN"
        )

    send_telegram(message)

    state_key = (
        datetime.now(TZ).strftime("%Y-%m-%d")
        + "_"
        + mode
    )

    if mode != "ROUTINE":
        state[state_key] = sorted(
            set(state.get(state_key, []))
            | {item["ticker"] for item in results}
        )

    save_state(state)

    logging.info("BEI AUTO SCANNER SELESAI")


if __name__ == "__main__":
    main()
