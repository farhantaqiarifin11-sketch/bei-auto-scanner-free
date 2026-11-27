import numpy as np
import pandas as pd

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)

def enrich(frame):
    df = frame.copy()
    for col in ("Open","High","Low","Close","Volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close","Volume"]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if len(df) < 50:
        return None
    df["MA5"] = df.Close.rolling(5).mean()
    df["MA20"] = df.Close.rolling(20).mean()
    df["MA50"] = df.Close.rolling(50).mean()
    df["RSI"] = rsi(df.Close)
    typical = (df.High + df.Low + df.Close) / 3
    dates = pd.Series(df.index.date, index=df.index)
    pv = typical * df.Volume
    df["VWAP"] = pv.groupby(dates).cumsum() / df.Volume.groupby(dates).cumsum().replace(0, np.nan)
    # RVOL sederhana: volume candle terakhir dibanding rerata 20 candle sebelumnya.
    df["RVOL"] = df.Volume / df.Volume.shift(1).rolling(20).mean().replace(0, np.nan)
    return df

def detect_patterns(df):
    last, prev = df.iloc[-1], df.iloc[-2]
    recent_high = df.High.iloc[-21:-1].max()
    recent_low = df.Low.iloc[-21:-1].min()
    breakout = bool(last.Close > recent_high and last.Volume > df.Volume.iloc[-21:-1].mean())
    pullback = bool(last.Close > last.MA20 and last.Low <= last.MA20 * 1.01 and last.Close > last.Open)
    momentum = bool(last.Close > last.MA5 > last.MA20 and last.RSI >= 55)
    volume_spike = bool(pd.notna(last.RVOL) and last.RVOL >= 2)
    return {"breakout":breakout,"pullback":pullback,"momentum":momentum,"volume_spike":volume_spike}

def score_stock(df, patterns):
    x = df.iloc[-1]
    score = 0
    score += 20 if x.Close > x.MA20 else 0
    score += 10 if x.MA5 > x.MA20 else 0
    score += 10 if x.MA20 > x.MA50 else 0
    score += 15 if 50 <= x.RSI <= 70 else (5 if x.RSI > 70 else 0)
    score += 15 if x.Close > x.VWAP else 0
    score += 15 if pd.notna(x.RVOL) and x.RVOL >= 1.5 else 0
    score += 10 if patterns["breakout"] else 0
    score += 5 if patterns["pullback"] else 0
    return min(100, int(score))
