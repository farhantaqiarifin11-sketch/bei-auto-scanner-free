import yfinance as yf

def get_fundamentals(ticker):
    """Fundamental data is best-effort; Yahoo coverage for IDX tickers is incomplete."""
    try:
        info = yf.Ticker(ticker).info or {}
        fields = {
            "PER": info.get("trailingPE"),
            "PBV": info.get("priceToBook"),
            "ROE": info.get("returnOnEquity"),
            "ROA": info.get("returnOnAssets"),
            "DER": info.get("debtToEquity")
        }
        available = {k:v for k,v in fields.items() if isinstance(v,(int,float))}
        return {"available": bool(available), "fields": available,
                "note": "Data indikatif dari Yahoo Finance; verifikasi dengan laporan resmi emiten." if available else "Data fundamental tidak tersedia."}
    except Exception as exc:
        return {"available":False,"fields":{},"note":f"Data fundamental gagal diambil: {type(exc).__name__}"}
