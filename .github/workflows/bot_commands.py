import os, json, requests
from pathlib import Path
API="https://api.telegram.org/bot{}/{}"; STATE=Path("data/telegram_state.json")
def telegram(method,payload):
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    if not token: raise RuntimeError("TELEGRAM_BOT_TOKEN kosong")
    r=requests.post(API.format(token,method),json=payload,timeout=30); r.raise_for_status()
    data=r.json()
    if not data.get("ok"): raise RuntimeError(data)
    return data.get("result")
def send(chat_id,message):
    # Telegram message limit is 4096 chars.
    for i in range(0,len(message),3900): telegram("sendMessage",{"chat_id":chat_id,"text":message[i:i+3900],"parse_mode":"HTML"})
def load_offset():
    try:return int(json.loads(STATE.read_text()).get("offset",0))
    except Exception:return 0
def save_offset(value):
    STATE.parent.mkdir(parents=True,exist_ok=True); STATE.write_text(json.dumps({"offset":value}))
def read_json(path,default):
    try:return json.loads(path.read_text())
    except Exception:return default
def handle(command,chat_id):
    cmd=(command or "").strip(); parts=cmd.split(); name=parts[0].split("@")[0].lower() if parts else ""
    if name in ("/start","/help"):
        send(chat_id,"<b>BEI AUTO SCANNER</b>\n/status — status bot\n/top10 — scan kandidat teratas sekarang\n/scan — jalankan pemindaian sekarang\n/analyze KODE — analisis teknikal satu saham\n/watchlist — lihat watchlist\n/add KODE — tambah saham\n/remove KODE — hapus saham\n/performance — ringkasan evaluasi")
    elif name=="/status": send(chat_id,"Bot Telegram aktif. Pemindaian memakai GitHub Actions; data Yahoo Finance dapat tertunda dan bukan real-time.")
    elif name in ("/scan","/top10"):
        send(chat_id,"Pemindaian on-demand dimulai. Proses dapat memerlukan beberapa menit.")
        from scanner import run_scan
        run_scan()
    elif name=="/watchlist":
        items=read_json(Path("data/watchlist.json"),[]); send(chat_id,"Watchlist: "+(", ".join(items) if items else "kosong"))
    elif name in ("/add","/remove") and len(parts)==2:
        ticker=parts[1].upper().replace(".JK","")
        if not ticker.replace(".","").isalnum(): send(chat_id,"Kode saham tidak valid."); return
        p=Path("data/watchlist.json"); items=read_json(p,[])
        if name=="/add" and ticker not in items: items.append(ticker)
        if name=="/remove": items=[x for x in items if x!=ticker]
        p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(items,indent=2))
        send(chat_id,f"{ticker} {'ditambahkan ke' if name=='/add' else 'dihapus dari'} watchlist.")
    elif name=="/analyze" and len(parts)==2:
        ticker=parts[1].upper().replace(".JK","")
        from scanner import analyze
        item=analyze(ticker+".JK",include_fundamental=True)
        if not item: send(chat_id,f"{ticker}: data tidak cukup atau tidak lolos filter teknikal.")
        else: send(chat_id,f"<b>{ticker}</b> | Rp{item['price']} | Skor {item['score']}/100\nRSI {item['rsi']} | RVOL {item['rvol']} | VWAP {item['vwap']}\nEntry {item['entry_low']}-{item['entry_high']} | SL {item['stop_loss']} | TP1 {item['target_1']} | TP2 {item['target_2']}")
    elif name=="/performance":
        from signal_evaluation import summary
        s=summary(); send(chat_id,f"Total sinyal: {s['total']}\nSelesai: {s['closed']}\nWin rate: {s['win_rate_pct'] if s['win_rate_pct'] is not None else 'belum tersedia'}%")
    else: send(chat_id,"Format perintah tidak dikenal. Ketik /help.")
def poll():
    owner=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not owner: raise RuntimeError("TELEGRAM_CHAT_ID kosong")
    # Bot ini memakai long polling; hapus webhook lama tanpa membuang antrean pesan.
    telegram("deleteWebhook",{"drop_pending_updates":False})
    offset=load_offset(); updates=telegram("getUpdates",{"offset":offset,"timeout":5,"allowed_updates":["message"]}) or []; nxt=offset
    for u in updates:
        nxt=max(nxt,int(u["update_id"])+1); msg=u.get("message",{}); chat=msg.get("chat",{})
        if str(chat.get("id"))!=owner: continue
        try: handle(msg.get("text",""),owner)
        except Exception as exc: send(owner,f"Perintah gagal: {type(exc).__name__}. Periksa log GitHub Actions.")
    if nxt!=offset: save_offset(nxt)
    print(f"Telegram updates diterima: {len(updates)}")
if __name__=="__main__": poll()
