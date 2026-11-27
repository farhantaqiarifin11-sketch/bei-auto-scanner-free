import os, json, logging, time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import requests, pandas as pd, yfinance as yf
from technical_engine import enrich, detect_patterns, score_stock
from risk_management import trade_levels
from fundamental_engine import get_fundamentals
from signal_evaluation import record_signal, evaluate_open_signals, summary
from bot_commands import send

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
CFG=json.loads(Path("config.json").read_text(encoding="utf-8"))
TZ=ZoneInfo(CFG.get("market_timezone","Asia/Jakarta"))
DATA=Path("data"); DATA.mkdir(exist_ok=True)

# Fallback is intentionally labelled partial; IDX endpoint/local CSV take precedence.
FALLBACK=["BBCA","BBRI","BMRI","BBNI","TLKM","ASII","ANTM","INCO","MDKA","AMRT","ICBP","INDF","UNVR","ADRO","ITMG","PTBA","PGAS","BRPT","TPIA","CPIN"]

def telegram(message):
    chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if chat: send(chat,message)

def normalize_ticker(value):
    value=str(value).strip().upper().replace(".JK","")
    return value if value and value.replace(".","").isalnum() else ""

def universe():
    limit=int(CFG.get("max_tickers",1000)); local=DATA/"universe/idx-tickers.csv"
    if local.exists():
        try:
            df=pd.read_csv(local)
            col=next((c for c in ("ticker","code","symbol") if c in df.columns),None)
            vals=list(dict.fromkeys(normalize_ticker(x) for x in df[col].dropna())) if col else []
            vals=[x for x in vals if x]
            if vals:
                logging.info("Universe memakai CSV lokal (%d kode)",len(vals))
                return [x+".JK" for x in vals[:limit]], "CSV lokal"
        except Exception as exc: logging.warning("CSV universe lokal gagal dibaca: %s",type(exc).__name__)
    try:
        r=requests.get(CFG["universe_url"],headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"},timeout=25)
        r.raise_for_status(); payload=r.json()
        rows=[]
        if isinstance(payload,list): rows=payload
        elif isinstance(payload,dict):
            for key in ("data"," data","Data","results"):
                if isinstance(payload.get(key),list): rows=payload[key]; break
        found=[]
        for row in rows:
            if not isinstance(row,dict): continue
            code=next((row.get(k) for k in ("kodeEmiten"," kodeEmiten","KodeEmiten","code","Code","ticker","symbol") if row.get(k)),None)
            code=normalize_ticker(code) if code else ""
            if code and code not in found: found.append(code)
        if found:
            logging.info("Universe IDX berhasil: %d kode",len(found))
            return [x+".JK" for x in found[:limit]], "IDX"
    except Exception as exc: logging.warning("Universe IDX tidak tersedia: %s",type(exc).__name__)
    logging.warning("Memakai fallback parsial %d saham; bukan seluruh BEI",len(FALLBACK))
    return [x+".JK" for x in FALLBACK[:limit]], "fallback parsial"

def fetch_frame(ticker):
    try:
        df=yf.download(ticker,period="5d",interval="5m",progress=False,auto_adjust=False,threads=False,timeout=20)
        if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
        return df.dropna() if df is not None else pd.DataFrame()
    except Exception as exc:
        logging.warning("%s gagal diunduh: %s",ticker,type(exc).__name__); return pd.DataFrame()

def analyze(ticker, include_fundamental=True):
    raw=fetch_frame(ticker); df=enrich(raw)
    if df is None or df.empty: return None
    last=df.iloc[-1]; price=float(last.Close)
    if price<float(CFG.get("min_price_idr",50)): return None
    value=(df.Close*df.Volume).tail(50).mean()
    if pd.isna(value) or value<float(CFG.get("min_avg_value_idr",1_000_000_000))/78: return None
    patterns=detect_patterns(df); score=score_stock(df,patterns)
    if score<int(CFG.get("min_score",70)): return None
    levels=trade_levels(price,CFG)
    result={"ticker":ticker.replace(".JK",""),"price":round(price,2),"score":score,
      "rsi":round(float(last.RSI),1),"rvol":round(float(last.RVOL),2) if pd.notna(last.RVOL) else None,
      "vwap":round(float(last.VWAP),2) if pd.notna(last.VWAP) else None,"patterns":patterns,**levels}
    if include_fundamental and CFG.get("fundamental_enabled"):
        result["fundamental"]=get_fundamentals(ticker)
    return result

def mode_now(now):
    t=now.hour*60+now.minute
    if 360<=t<365: return "TOP10"
    if 520<=t<525: return "PREOPEN"
    if 555<=t<560: return "MOMENTUM"
    return "ROUTINE"

def run_scan():
    now=datetime.now(TZ); mode=mode_now(now); tickers,source=universe()
    if not tickers: raise RuntimeError("Universe ticker kosong")
    logging.info("Universe %d (%s)",len(tickers),source)
    results=[]
    for i,ticker in enumerate(tickers,1):
        item=analyze(ticker)
        if item: results.append(item)
        if i%50==0: logging.info("Progres %d/%d",i,len(tickers))
        time.sleep(0.05)
    results.sort(key=lambda x:x["score"],reverse=True); selected=results[:int(CFG.get("max_alerts",10))]
    lines=[f"<b>BEI AUTO SCANNER — {mode}</b>",f"Waktu: {now:%Y-%m-%d %H:%M} WIB",f"Universe: {len(tickers)} ({source}) | Kandidat: {len(results)}"]
    for x in selected:
        tags=", ".join(k.upper() for k,v in x["patterns"].items() if v) or "konfirmasi teknikal"
        lines.append(f"\n<b>{x['ticker']}</b> | Skor {x['score']}/100 | Rp{x['price']:.2f}\nRSI {x['rsi']} | RVOL {x['rvol']} | VWAP {x['vwap']}\nPola: {tags}\nEntry {x['entry_low']}-{x['entry_high']} | SL {x['stop_loss']} | TP1 {x['target_1']} | TP2 {x['target_2']}")
        record_signal({"ticker":x["ticker"]+".JK","entry":x["price"],"stop_loss":x["stop_loss"],"target_1":x["target_1"],"target_2":x["target_2"],"status":"OPEN","created_at":now.isoformat()})
    if not selected: lines.append("Tidak ada kandidat yang lolos filter pada pemindaian ini.")
    lines.append("\nData Yahoo Finance dapat tertunda/tidak lengkap; bukan feed real-time/orderbook. Fallback universe bersifat parsial.")
    telegram("\n".join(lines))
    if CFG.get("evaluation_enabled"):
        def lookup(t):
            d=fetch_frame(t); return float(d.Close.iloc[-1]) if not d.empty else None
        evaluate_open_signals(lookup); logging.info("Evaluasi: %s",summary())
    return selected

if __name__=="__main__":
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise SystemExit("Secrets TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID wajib diatur.")
    run_scan()
