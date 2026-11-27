
import os
import re
import csv
import json
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import numpy as np
import pandas as pd
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =========================================================
# 1. KONFIGURASI DASAR
# =========================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")

DATA_DIR = os.path.join(ROOT, "data")
UNIVERSE_DIR = os.path.join(DATA_DIR, "universe")
UNIVERSE_PATH = os.path.join(UNIVERSE_DIR, "idx-tickers.csv")
STATE_PATH = os.path.join(DATA_DIR, "alert_state.json")

TZ = ZoneInfo("Asia/Jakarta")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

if not os.path.isfile(CONFIG_PATH):
    raise FileNotFoundError("config.json tidak ditemukan.")

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

HF_DATASET = "DimmsMath/Indonesia-Stock-Symbols-and-Metadata"
HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UNIVERSE_DIR, exist_ok=True)


# =========================================================
# 2. HTTP SESSION DAN RETRY
# =========================================================

def create_session():
    session = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )

    adapter = HTTPAdapter(max_retries=retry)

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8"
    })

    return session


HTTP = create_session()


# =========================================================
# 3. TELEGRAM
# =========================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum diatur."
        )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = HTTP.post(
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

    logging.info("Pesan Telegram berhasil dikirim.")


# =========================================================
# 4. NORMALISASI TICKER
# =========================================================

def normalize_ticker(value):
    if value is None:
        return None

    value = str(value).strip().upper()

    if value.endswith(".JK"):
        value = value[:-3]

    if re.fullmatch(r"[A-Z0-9]{3,5}", value):
        return value

    return None


# =========================================================
# 5. SUMBER TICKER PERTAMA: CSV LOKAL
# =========================================================

def load_local_universe():
    if not os.path.isfile(UNIVERSE_PATH):
        return []

    logging.info("Memeriksa CSV ticker lokal.")

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

        ticker_column = (
            columns.get("code")
            or columns.get("ticker")
            or columns.get("symbol")
        )

        if not ticker_column:
            raise ValueError(
                "CSV harus mempunyai kolom code, ticker, atau symbol."
            )

        for row in reader:
            ticker = normalize_ticker(
                row.get(ticker_column)
            )

            if ticker:
                tickers.append(ticker)

    return sorted(set(tickers))


# =========================================================
# 6. SUMBER TICKER KEDUA: IDX
# =========================================================

def extract_tickers(data):
    found = []

    if isinstance(data, dict):
        for key, value in data.items():

            if isinstance(value, (dict, list)):
                found.extend(extract_tickers(value))

            elif key.lower() in (
                "kodeemiten",
                "kode",
                "code",
                "symbol",
                "ticker"
            ):
                ticker = normalize_ticker(value)

                if ticker:
                    found.append(ticker)

    elif isinstance(data, list):
        for item in data:
            found.extend(extract_tickers(item))

    return found


def load_idx_universe():
    session = create_session()

    headers = {
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

    data = response.json()

    tickers = sorted(set(extract_tickers(data)))

    if not tickers:
        raise RuntimeError(
            "IDX berhasil diakses tetapi ticker tidak ditemukan."
        )

    return tickers


# =========================================================
# 7. SUMBER TICKER KETIGA: HUGGING FACE
# =========================================================

def load_huggingface_universe():
    logging.info("Mengakses dataset alternatif.")

    tickers = []
    offset = 0
    page_size = 100
    total_rows = None

    while True:
        response = HTTP.get(
            HF_ROWS_URL,
            params={
                "dataset": HF_DATASET,
                "config": "default",
                "split": "train",
                "offset": offset,
                "length": page_size
            },
            timeout=40
        )

        response.raise_for_status()

        payload = response.json()
        rows = payload.get("rows", [])

        if total_rows is None:
            total_rows = payload.get("num_rows_total")

            logging.info(
                "Jumlah baris dataset: %s",
                total_rows
            )

        if not rows:
            break

        for item in rows:
            row = item.get("row", {})

            market = str(
                row.get("market", "")
            ).strip().upper()

            if market != "IDX":
                continue

            ticker = normalize_ticker(
                row.get("ticker")
            )

            if ticker:
                tickers.append(ticker)

        offset += len(rows)

        if len(rows) < page_size:
            break

        if total_rows is not None and offset >= total_rows:
            break

        time.sleep(0.2)

    tickers = sorted(set(tickers))

    if not tickers:
        raise RuntimeError(
            "Dataset alternatif tidak menghasilkan ticker IDX."
        )

    logging.info(
        "Dataset alternatif menghasilkan %s ticker.",
        len(tickers)
    )

    return tickers


# =========================================================
# 8. PEMILIHAN UNIVERSE
# =========================================================

def get_universe():

    # Prioritas 1: CSV lokal
    try:
        tickers = load_local_universe()

        if tickers:
            logging.info(
                "Menggunakan CSV lokal: %s ticker.",
                len(tickers)
            )

            return tickers[:MAX_TICKERS]

    except Exception as exc:
        logging.warning("CSV lokal gagal: %s", exc)

    # Prioritas 2: IDX
    try:
        logging.info("Mencoba mengambil daftar saham dari IDX.")

        tickers = load_idx_universe()

        if tickers:
            logging.info(
                "IDX berhasil: %s ticker.",
                len(tickers)
            )

            return tickers[:MAX_TICKERS]

    except Exception as exc:
        logging.warning("IDX gagal: %s", exc)

    # Prioritas 3: Hugging Face
    try:
        logging.info("Mencoba sumber alternatif.")

        tickers = load_huggingface_universe()

        if tickers:
            return tickers[:MAX_TICKERS]

    except Exception as exc:
        logging.exception(
            "Dataset alternatif gagal: %s",
            exc
        )

    raise RuntimeError(
        "Seluruh sumber daftar saham gagal. "
        "Pemindaian dibatalkan agar tidak menghasilkan data palsu."
    )


# =========================================================
# 9. INDIKATOR TEKNIKAL
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

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0
    )

    return rsi


# =========================================================
# 10. NORMALISASI DATA YAHOO FINANCE
# =========================================================

def normalize_stock_frame(df):
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        return None

    required = [
        "Close",
        "High",
        "Low",
        "Volume"
    ]

    if not all(col in df.columns for col in required):
        return None

    df = df.copy()

    for col in required:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=required
    )

    df = df[
        (df["Close"] > 0)
        & (df["High"] > 0)
        & (df["Low"] > 0)
        & (df["Volume"] >= 0)
    ]

    if df.empty:
        return None

    df = df[
        ~df.index.duplicated(keep="last")
    ]

    return df.sort_index()


# =========================================================
# 11. VALIDASI UMUR DATA
# =========================================================

def data_is_fresh(df, mode):
    if df is None or df.empty:
        return False

    timestamp = pd.Timestamp(df.index[-1])

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(TZ)
    else:
        timestamp = timestamp.tz_convert(TZ)

    now = pd.Timestamp.now(tz=TZ)

    age_minutes = (
        now - timestamp
    ).total_seconds() / 60

    # Watchlist pagi menggunakan data sesi sebelumnya.
    if mode in ("TOP10", "PREOPEN"):
        max_age = 96 * 60
    else:
        max_age = MAX_DATA_AGE

    return 0 <= age_minutes <= max_age


# =========================================================
# 12. ANALISIS SAHAM
# =========================================================

def analyze_stock(ticker, raw_df, mode):

    df = normalize_stock_frame(raw_df)

    if df is None or len(df) < 50:
        return None

    if not data_is_fresh(df, mode):
        logging.info(
            "%s dilewati karena data tidak segar.",
            ticker
        )
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

    typical_price = (
        high + low + close
    ) / 3

    # VWAP direset setiap hari perdagangan.
    dates = pd.Series(
        df.index.date,
        index=df.index
    )

    cumulative_pv = (
        typical_price * volume
    ).groupby(dates).cumsum()

    cumulative_volume = (
        volume.groupby(dates).cumsum()
    )

    vwap = (
        cumulative_pv
        / cumulative_volume.replace(0, np.nan)
    )

    avg_volume = volume.rolling(20).mean()

    # Nilai transaksi dihitung sebagai rata-rata nilai harian,
    # bukan rata-rata satu candle 5 menit.
    daily_value = (
        close * volume
    ).groupby(dates).sum()

    avg_daily_value = daily_value.tail(5).mean()

    required_values = [
        ma5.iloc[-1],
        ma20.iloc[-1],
        ma50.iloc[-1],
        rsi.iloc[-1],
        vwap.iloc[-1],
        avg_volume.iloc[-1],
        avg_daily_value
    ]

    if any(pd.isna(x) for x in required_values):
        return None

    if avg_volume.iloc[-1] <= 0:
        return None

    if avg_daily_value < MIN_VALUE:
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
        signals.append("Harga > MA5")

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
        signals.append("Harga > VWAP")

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
    else:
        change = 0

    if change > 0:
        score += 10
        signals.append("Momentum positif")

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
        "avg_value": float(avg_daily_value),
        "change": float(change),
        "signals": signals
    }


# =========================================================
# 13. DOWNLOAD DATA YAHOO FINANCE
# =========================================================

def download_batch(symbols):

    for attempt in range(1, 3):
        try:
            data = yf.download(
                tickers=symbols,
                period="5d",
                interval="5m",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
                timeout=40
            )

            if data is not None and not data.empty:
                return data

        except Exception as exc:
            logging.warning(
                "Download gagal, percobaan %s: %s",
                attempt,
                exc
            )

        time.sleep(attempt * 2)

    return None


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
        return data.xs(
            symbol,
            axis=1,
            level=1
        )

    return None


# =========================================================
# 14. PEMINDAIAN PASAR
# =========================================================

def scan_market(mode):

    tickers = get_universe()

    logging.info(
        "Jumlah saham yang akan dipindai: %s",
        len(tickers)
    )

    results = []

    total_batches = (
        len(tickers) + BATCH_SIZE - 1
    ) // BATCH_SIZE

    for start in range(
        0,
        len(tickers),
        BATCH_SIZE
    ):

        batch = tickers[
            start:start + BATCH_SIZE
        ]

        symbols = [
            ticker + ".JK"
            for ticker in batch
        ]

        batch_number = (
            start // BATCH_SIZE
        ) + 1

        logging.info(
            "Memproses batch %s/%s",
            batch_number,
            total_batches
        )

        data = download_batch(symbols)

        if data is None:
            logging.warning(
                "Batch %s tidak menghasilkan data.",
                batch_number
            )
            continue

        for ticker, symbol in zip(
            batch,
            symbols
        ):

            try:
                frame = extract_batch_frame(
                    data,
                    symbol,
                    len(batch)
                )

                if frame is None:
                    continue

                result = analyze_stock(
                    ticker,
                    frame,
                    mode
                )

                if (
                    result
                    and result["score"] >= MIN_SCORE
                ):
                    results.append(result)

            except Exception:
                logging.exception(
                    "Analisis gagal: %s",
                    ticker
                )

        logging.info(
            "Selesai batch %s/%s. Kandidat sementara: %s",
            batch_number,
            total_batches,
            len(results)
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
# 15. MODE PEMINDAIAN
# =========================================================

def get_scan_mode():

    now = datetime.now(TZ)

    if now.hour == 6:
        return "TOP10"

    if now.hour == 8 and now.minute >= 35:
        return "PREOPEN"

    if now.hour == 9 and 10 <= now.minute <= 30:
        return "MOMENTUM"

    return "ROUTINE"


# =========================================================
# 16. FORMAT PESAN
# =========================================================

def format_rupiah(value):
    return (
        "Rp{:,.0f}".format(value)
        .replace(",", ".")
    )


def format_results(
    results,
    title,
    indicative=False
):

    now = datetime.now(TZ)

    lines = [
        f"<b>{title}</b>",
        f"Waktu: {now.strftime('%d-%m-%Y %H:%M WIB')}",
        ""
    ]

    if indicative:
        lines.extend([
            "⚠️ Watchlist indikatif, bukan sinyal eksekusi.",
            ""
        ])

    if not results:
        lines.append(
            "Tidak ada saham yang memenuhi kriteria."
        )

        return "\n".join(lines)

    for i, stock in enumerate(results, 1):

        signals = ", ".join(
            stock["signals"][:4]
        )

        lines.extend([
            f"<b>{i}. {stock['ticker']}</b>",
            f"Harga: {format_rupiah(stock['price'])}",
            f"Skor: {stock['score']}/100",
            f"RSI: {stock['rsi']:.1f}",
            f"RVOL: {stock['rvol']:.2f}x",
            f"Perubahan: {stock['change']:.2f}%",
            (
                "Rata-rata nilai transaksi harian: "
                + format_rupiah(stock["avg_value"])
            ),
            f"Sinyal: {signals or '-'}",
            ""
        ])

    lines.append(
        "Gunakan analisis lanjutan sebelum mengambil keputusan."
    )

    return "\n".join(lines)


# =========================================================
# 17. RIWAYAT ALERT
# =========================================================

def load_state():

    if not os.path.isfile(STATE_PATH):
        return {}

    try:
        with open(
            STATE_PATH,
            encoding="utf-8"
        ) as f:
            state = json.load(f)

        return state if isinstance(state, dict) else {}

    except Exception:
        logging.warning(
            "Riwayat alert rusak atau tidak dapat dibaca."
        )

        return {}


def save_state(state):

    temporary_path = STATE_PATH + ".tmp"

    with open(
        temporary_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )

    os.replace(
        temporary_path,
        STATE_PATH
    )


def filter_new_alerts(
    results,
    state,
    mode
):

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    key = f"{today}_{mode}"

    previous = set(
        state.get(key, [])
    )

    fresh = [
        item
        for item in results
        if item["ticker"] not in previous
    ]

    state[key] = sorted(
        previous | {
            item["ticker"]
            for item in results
        }
    )

    # Batasi penyimpanan riwayat agar tidak terus membesar.
    valid_keys = sorted(state.keys())[-30:]

    state = {
        k: state[k]
        for k in valid_keys
    }

    return fresh, state


# =========================================================
# 18. PROGRAM UTAMA
# =========================================================

def main():

    now = datetime.now(TZ)

    logging.info("BEI AUTO SCANNER DIMULAI")
    logging.info(
        "Waktu WIB: %s",
        now.isoformat()
    )

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Telegram Secrets belum tersedia."
        )

    mode = get_scan_mode()

    logging.info(
        "Mode pemindaian: %s",
        mode
    )

    results = scan_market(mode)

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

        fresh, state = filter_new_alerts(
            results,
            state,
            "ROUTINE"
        )

        if not fresh:
            logging.info(
                "Tidak ada sinyal rutin baru."
            )
            return

        message = format_results(
            fresh,
            "📈 ALERT PEMINDAIAN RUTIN"
        )

    # Kirim dahulu. Riwayat disimpan setelah pengiriman berhasil.
    send_telegram(message)

    if mode != "ROUTINE":

        today = datetime.now(TZ).strftime("%Y-%m-%d")
        key = f"{today}_{mode}"

        state[key] = sorted(
            set(state.get(key, []))
            | {
                item["ticker"]
                for item in results
            }
        )

    save_state(state)

    logging.info(
        "BEI AUTO SCANNER SELESAI"
    )


if __name__ == "__main__":
    main()
