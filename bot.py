# ============================================================
#  SCALP BOT V14 LIVE — doppia strategia A/B in parallelo
#
#  🅰️ QUALITA'         onde=2 pivot=3 fib=50% RSI 70/30 EMA200=SI
#  🅱️ DIVERSIFICAZIONE onde=2 pivot=5 fib=50% RSI 70/30 EMA200=NO
#  Entrambe: TP1=1R (poi SL a entrata), TP2=3R, sessione 08-21 ITA
#
#  Ogni segnale e' etichettato 🅰️ o 🅱️ cosi' decidi quanto rischiare.
#  Il foglio Google traccia esiti e bankroll PER CONFIG e PER ASSET.
#
#  Extra (solo informativi, non cambiano la strategia):
#   - Rating tecnico TradingView nel messaggio (se raggiungibile)
#   - Avviso news ad alto impatto vicine (calendario ForexFactory)
#
#  CONFIGURA: TOKEN qui sotto. Sheets: credenziali_trading.json
#             + foglio Google chiamato "SCALP Trading" condiviso
#             con l'email del service account.
# ============================================================

import json
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ------------------- CONFIGURAZIONE -------------------
TOKEN            = os.environ.get("TOKEN", "QUI_IL_TUO_TOKEN")
CHAT_ID          = os.environ.get("CHAT_ID", "933030689")
FILE_CREDENZIALI = "credenziali_trading.json"
NOME_FOGLIO      = os.environ.get("NOME_FOGLIO", "SCALP Trading")
FILE_STATO       = "stato_scalp.json"
CICLO_SECONDI    = int(os.environ.get("CICLO_SECONDI", "3600"))

COPPIE = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "XAU/USD": "GC=F",
    "GBP/JPY": "GBPJPY=X",
}

# --------- LE DUE STRATEGIE (dai risultati di ottimizza.py) ---------
CONFIGS = {
    "A": {"nome": "Qualita'",         "emoji": "🅰️", "onde": 2, "pivot": 3,
          "fib": 0.50, "rsi_lmax": 70, "rsi_smin": 30, "ema": True},
    "B": {"nome": "Diversificazione", "emoji": "🅱️", "onde": 2, "pivot": 5,
          "fib": 0.50, "rsi_lmax": 70, "rsi_smin": 30, "ema": False},
}

MIN_BODY       = 0.3
EMA_PERIODO    = 200
ORA_INIZIO_UTC = 6      # 08:00 ITA
ORA_FINE_UTC   = 19     # 21:00 ITA
SL_FATTORE     = 0.5
TP1_R          = 1.0
TP2_R          = 3.0
RISCHIO_PCT    = 1.0
BANKROLL_INIZ  = 1000.0

try:
    import gspread
    GSPREAD_OK = True
except ImportError:
    GSPREAD_OK = False

try:
    from tradingview_ta import TA_Handler, Interval
    TV_OK = True
except ImportError:
    TV_OK = False

ESITO_R = {"SL": -1.0, "TP1_BE": 0.5, "TP2": 2.0}
HEADER_SEGNALI = ["ID", "Config", "Data apertura", "Asset", "Direzione",
                  "Entrata", "SL", "TP1", "TP2", "Stato", "R", "Data chiusura"]

TV_SIMBOLI = {           # per il rating TradingView (solo informativo)
    "EUR/USD": ("EURUSD", "forex", "FX_IDC"),
    "GBP/USD": ("GBPUSD", "forex", "FX_IDC"),
    "XAU/USD": ("XAUUSD", "cfd",   "OANDA"),
    "GBP/JPY": ("GBPJPY", "forex", "FX_IDC"),
}
VALUTE = {               # per l'avviso news
    "EUR/USD": {"EUR", "USD"}, "GBP/USD": {"GBP", "USD"},
    "XAU/USD": {"USD"},        "GBP/JPY": {"GBP", "JPY"},
}


# ------------------- TELEGRAM -------------------
def manda_messaggio(testo):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": testo}, timeout=15)
    except Exception as e:
        print(f"Telegram errore: {e}")


# ------------------- STATO -------------------
def carica_stato():
    if os.path.exists(FILE_STATO):
        try:
            with open(FILE_STATO) as f:
                return json.load(f)
        except Exception:
            pass
    return {"segnali": [], "gia_segnalati": []}


def salva_stato(stato):
    try:
        stato["gia_segnalati"] = stato["gia_segnalati"][-400:]
        with open(FILE_STATO, "w") as f:
            json.dump(stato, f, indent=1)
    except Exception as e:
        print(f"Errore salvataggio stato: {e}")


# ------------------- GOOGLE SHEETS -------------------
def apri_foglio():
    if not GSPREAD_OK:
        print("gspread non installato: procedo senza Google Sheets.")
        return None
    if not os.path.exists(FILE_CREDENZIALI):
        cred_env = os.environ.get("GOOGLE_CREDENZIALI")
        if cred_env:
            try:
                with open(FILE_CREDENZIALI, "w") as f:
                    f.write(cred_env)
            except Exception as e:
                print(f"Impossibile scrivere credenziali da env: {e}")
                return None
        else:
            print(f"'{FILE_CREDENZIALI}' non trovato: procedo senza Sheets.")
            return None
    try:
        gc = gspread.service_account(filename=FILE_CREDENZIALI)
        sh = gc.open(NOME_FOGLIO)
        titoli = [w.title for w in sh.worksheets()]
        if "Segnali" not in titoli:
            ws = sh.add_worksheet("Segnali", rows=1000, cols=len(HEADER_SEGNALI))
            ws.append_row(HEADER_SEGNALI)
        if "Bankroll" not in titoli:
            sh.add_worksheet("Bankroll", rows=60, cols=8)
        return sh
    except Exception as e:
        print(f"Google Sheets non disponibile: {e}")
        return None


def sheet_aggiungi_segnale(sh, seg):
    if sh is None:
        return None
    try:
        ws = sh.worksheet("Segnali")
        ws.append_row([seg["id"], seg["config"], seg["apertura"], seg["asset"],
                       seg["dir"], seg["entrata"], seg["sl"], seg["tp1"],
                       seg["tp2"], "APERTO", "", ""],
                      value_input_option="USER_ENTERED")
        return len(ws.get_all_values())
    except Exception as e:
        print(f"Sheets errore (aggiungi): {e}")
        return None


def sheet_chiudi_segnale(sh, seg, esito, r, quando):
    if sh is None:
        return
    try:
        ws = sh.worksheet("Segnali")
        riga = seg.get("riga_sheet")
        if riga:
            ws.update(f"J{riga}:L{riga}", [[esito, r, quando]])
        else:
            ws.append_row([seg["id"], seg["config"], seg["apertura"], seg["asset"],
                           seg["dir"], seg["entrata"], seg["sl"], seg["tp1"],
                           seg["tp2"], esito, r, quando],
                          value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"Sheets errore (chiudi): {e}")


def sheet_ricalcola_bankroll(sh):
    """Ricostruisce la tab Bankroll da zero: per config e per asset."""
    if sh is None:
        return
    try:
        valori = sh.worksheet("Segnali").get_all_values()[1:]
        chiusi = []
        for v in valori:
            if len(v) >= 11 and v[9] in ESITO_R:
                try:
                    chiusi.append({"config": v[1], "asset": v[3], "data": v[2],
                                   "r": float(str(v[10]).replace(",", "."))})
                except ValueError:
                    continue

        def blocco(lista, etichetta):
            n = len(lista)
            win = sum(1 for c in lista if c["r"] > 0)
            loss = sum(1 for c in lista if c["r"] < 0)
            r_tot = sum(c["r"] for c in lista)
            bk = BANKROLL_INIZ
            for c in sorted(lista, key=lambda x: x["data"]):
                bk += bk * (RISCHIO_PCT / 100) * c["r"]
            wr = f"{win / n * 100:.1f}%" if n else "-"
            return [etichetta, n, win, loss, wr, round(r_tot, 2), round(bk, 2)]

        righe = [["Strategia / Asset", "Trade", "Win", "Loss", "Win %",
                  "R totale", f"Bankroll ({BANKROLL_INIZ:.0f}, {RISCHIO_PCT:.0f}%)"]]
        for cid, cfg in CONFIGS.items():
            gc_ = [c for c in chiusi if c["config"] == cid]
            righe.append(blocco(gc_, f"{cfg['emoji']} CONFIG {cid} - {cfg['nome']}"))
            for asset in COPPIE:
                ga = [c for c in gc_ if c["asset"] == asset]
                righe.append(blocco(ga, f"   {asset}"))
            righe.append([])
        righe.append(blocco(chiusi, "TOTALE"))
        righe.append([])
        righe.append(["Aggiornato il",
                      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")])
        ws = sh.worksheet("Bankroll")
        ws.clear()
        ws.update("A1", righe, value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"Sheets errore (bankroll): {e}")


# ------------------- CONFERME ESTERNE (solo info) -------------------
def rating_tradingview(nome):
    if not TV_OK:
        return None
    try:
        sym, screener, exch = TV_SIMBOLI[nome]
        h = TA_Handler(symbol=sym, screener=screener, exchange=exch,
                       interval=Interval.INTERVAL_1_HOUR)
        s = h.get_analysis().summary
        return f"{s['RECOMMENDATION']} (buy {s['BUY']} / sell {s['SELL']})"
    except Exception:
        return None


_cache_news = {"quando": None, "eventi": []}

def news_vicine(nome):
    """Eventi ad alto impatto entro 60 min per le valute della coppia."""
    try:
        adesso = datetime.now(timezone.utc)
        if (_cache_news["quando"] is None
                or (adesso - _cache_news["quando"]).total_seconds() > 1800):
            r = requests.get(
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                timeout=10)
            _cache_news["eventi"] = r.json()
            _cache_news["quando"] = adesso
        avvisi = []
        for ev in _cache_news["eventi"]:
            if str(ev.get("impact", "")).lower() != "high":
                continue
            if ev.get("country") not in VALUTE.get(nome, set()):
                continue
            try:
                t = datetime.fromisoformat(ev["date"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            minuti = (t - adesso).total_seconds() / 60
            if -30 <= minuti <= 60:
                avvisi.append(f"{ev.get('country')} {ev.get('title')} "
                              f"tra {int(minuti)} min")
        return avvisi
    except Exception:
        return []


# ------------------- DATI -------------------
def rsi_wilder(close, periodo=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1 / periodo, min_periods=periodo, adjust=False).mean()
    al = loss.ewm(alpha=1 / periodo, min_periods=periodo, adjust=False).mean()
    return 100 - (100 / (1 + ag / al))


def scarica_dati(ticker):
    df = yf.download(ticker, interval="1h", period="60d",
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.index = (df.index.tz_localize("UTC") if df.index.tz is None
                else df.index.tz_convert("UTC"))
    adesso = datetime.now(timezone.utc)
    if (adesso - df.index[-1].to_pydatetime()).total_seconds() < 3600:
        df = df.iloc[:-1]                     # candela ancora in formazione
    if len(df) < EMA_PERIODO + 20:
        return None
    df["EMA200"] = df["Close"].ewm(span=EMA_PERIODO, adjust=False).mean()
    df["RSI"] = rsi_wilder(df["Close"])
    return df


def ultimi_pivot(df, finestra):
    h, l = df["High"].values, df["Low"].values
    ph, pl = [], []
    for p in range(finestra, len(df) - finestra):
        lo, hi = p - finestra, p + finestra + 1
        if h[p] == h[lo:hi].max():
            ph.append(h[p])
        if l[p] == l[lo:hi].min():
            pl.append(l[p])
    if len(ph) < 2 or len(pl) < 2:
        return None
    return ph[-2], ph[-1], pl[-2], pl[-1]


# ------------------- SEGNALE (candela CHIUSA) -------------------
def cerca_segnale(nome, df, cid):
    cfg = CONFIGS[cid]
    i = len(df) - 1
    ts = df.index[i]
    if ts.weekday() >= 5 or not (ORA_INIZIO_UTC <= ts.hour <= ORA_FINE_UTC):
        return None
    piv = ultimi_pivot(df, cfg["pivot"])
    if piv is None:
        return None
    hh1, hh2, hl1, hl2 = piv

    o, h, l, c = (df[x].values for x in ["Open", "High", "Low", "Close"])
    prezzo = float(c[i])
    ema = float(df["EMA200"].iloc[i])
    rsi = float(df["RSI"].iloc[i])
    if np.isnan(ema) or np.isnan(rsi):
        return None

    if cfg["onde"] == 2:
        s_long, s_short = hh2 > hh1 and hl2 > hl1, hh2 < hh1 and hl2 < hl1
    else:
        s_long = s_short = True

    b1, b2 = abs(c[i-1] - o[i-1]), abs(c[i] - o[i])
    r1 = h[i-1] - l[i-1]
    eok = b2 >= b1 * (1 + MIN_BODY) and r1 > 0 and b1 >= r1 * 0.3
    e_long  = c[i-1] < o[i-1] and c[i] > o[i] and c[i] > o[i-1] and o[i] < c[i-1] and eok
    e_short = c[i-1] > o[i-1] and c[i] < o[i] and c[i] < o[i-1] and o[i] > c[i-1] and eok

    fib_l = hh2 - (hh2 - hl2) * cfg["fib"]
    fib_s = hl2 + (hh2 - hl2) * cfg["fib"]
    ema_l = prezzo > ema if cfg["ema"] else True
    ema_s = prezzo < ema if cfg["ema"] else True

    seg = None
    if s_long and prezzo <= fib_l and e_long and rsi < cfg["rsi_lmax"] and ema_l:
        sl = prezzo - (prezzo - hl2) * SL_FATTORE
        if prezzo - sl > 0:
            seg = {"dir": "LONG", "entrata": prezzo, "sl": sl,
                   "tp1": prezzo + (prezzo - sl) * TP1_R,
                   "tp2": prezzo + (prezzo - sl) * TP2_R}
    elif s_short and prezzo >= fib_s and e_short and rsi > cfg["rsi_smin"] and ema_s:
        sl = prezzo + (hh2 - prezzo) * SL_FATTORE
        if sl - prezzo > 0:
            seg = {"dir": "SHORT", "entrata": prezzo, "sl": sl,
                   "tp1": prezzo - (sl - prezzo) * TP1_R,
                   "tp2": prezzo - (sl - prezzo) * TP2_R}
    if seg is None:
        return None
    seg.update({
        "asset": nome, "config": cid,
        "apertura": ts.strftime("%Y-%m-%d %H:%M"),
        "chiave": f"{cid}_{nome}_{ts.isoformat()}",
        "id": f"{cid}-{nome.replace('/', '')}-{ts.strftime('%Y%m%d-%H%M')}",
        "sl_corrente": seg["sl"], "fase": "OPEN", "rsi": rsi,
    })
    return seg


# ------------------- VERIFICA ESITI (worst-case) -------------------
def verifica_esito(seg, df):
    apertura = pd.Timestamp(seg["apertura"], tz="UTC")
    dopo = df[df.index > apertura]
    for ts, row in dopo.iterrows():
        hi, lo = float(row["High"]), float(row["Low"])
        if seg["dir"] == "LONG":
            sl_hit, tp1_hit, tp2_hit = (lo <= seg["sl_corrente"],
                                        hi >= seg["tp1"], hi >= seg["tp2"])
        else:
            sl_hit, tp1_hit, tp2_hit = (hi >= seg["sl_corrente"],
                                        lo <= seg["tp1"], lo <= seg["tp2"])
        if sl_hit:
            return ("SL" if seg["fase"] == "OPEN" else "TP1_BE", ts)
        if seg["fase"] == "OPEN" and tp1_hit:
            seg["fase"] = "RUNNER"
            seg["sl_corrente"] = seg["entrata"]
            if tp2_hit:
                return ("TP2", ts)
        elif seg["fase"] == "RUNNER" and tp2_hit:
            return ("TP2", ts)
    return None


# ------------------- MESSAGGI -------------------
def dec(nome):
    if nome == "XAU/USD":
        return 2
    return 3 if "JPY" in nome else 5


def msg_segnale(seg, tv, avvisi):
    cfg = CONFIGS[seg["config"]]
    d = dec(seg["asset"])
    e = "🟢" if seg["dir"] == "LONG" else "🔴"
    testo = (f"{e} {cfg['emoji']} SEGNALE {seg['dir']} — {seg['asset']}\n"
             f"Strategia {seg['config']} ({cfg['nome']})\n"
             f"━━━━━━━━━━━━━━━\n"
             f"📍 Entrata: {seg['entrata']:.{d}f}\n"
             f"🛑 SL: {seg['sl']:.{d}f}\n"
             f"🎯 TP1: {seg['tp1']:.{d}f}  (meta', poi SL a entrata)\n"
             f"🎯 TP2: {seg['tp2']:.{d}f}\n"
             f"📊 RSI: {seg['rsi']:.1f}")
    if tv:
        testo += f"\n📺 TradingView 1h: {tv}"
    for a in avvisi:
        testo += f"\n⚠️ NEWS: {a}"
    testo += f"\n🆔 {seg['id']}"
    return testo


def msg_esito(seg, esito, r):
    icone = {"SL": "❌", "TP1_BE": "🟡", "TP2": "✅"}
    nomi = {"SL": "STOP LOSS", "TP1_BE": "TP1 + Breakeven", "TP2": "TP2 PIENO"}
    cfg = CONFIGS[seg["config"]]
    return (f"{icone[esito]} {cfg['emoji']} CHIUSO — {seg['asset']} {seg['dir']}\n"
            f"Esito: {nomi[esito]}  ({r:+.1f}R)\n🆔 {seg['id']}")


# ------------------- CICLO -------------------
def ciclo(stato, sh):
    for nome, ticker in COPPIE.items():
        try:
            df = scarica_dati(ticker)
            if df is None:
                print(f"{nome}: dati non disponibili")
                continue

            # 1) esiti dei segnali aperti (di entrambe le config)
            for seg in [s for s in stato["segnali"] if s["asset"] == nome]:
                ris = verifica_esito(seg, df)
                if ris:
                    esito, ts = ris
                    r = ESITO_R[esito]
                    manda_messaggio(msg_esito(seg, esito, r))
                    sheet_chiudi_segnale(sh, seg, esito, r,
                                         ts.strftime("%Y-%m-%d %H:%M"))
                    stato["segnali"].remove(seg)
                    salva_stato(stato)
                    sheet_ricalcola_bankroll(sh)
                    print(f"{nome} [{seg['config']}]: chiuso -> {esito} ({r:+.1f}R)")
                else:
                    salva_stato(stato)

            # 2) nuovi segnali: una posizione per asset PER CONFIG
            for cid in CONFIGS:
                if any(s["asset"] == nome and s["config"] == cid
                       for s in stato["segnali"]):
                    continue
                seg = cerca_segnale(nome, df, cid)
                if seg is None or seg["chiave"] in stato["gia_segnalati"]:
                    continue
                tv = rating_tradingview(nome)
                avvisi = news_vicine(nome)
                manda_messaggio(msg_segnale(seg, tv, avvisi))
                seg["riga_sheet"] = sheet_aggiungi_segnale(sh, seg)
                stato["segnali"].append(seg)
                stato["gia_segnalati"].append(seg["chiave"])
                salva_stato(stato)
                print(f"{nome} [{cid}]: NUOVO SEGNALE {seg['dir']} ({seg['id']})")

            p = float(df["Close"].iloc[-1])
            print(f"{nome}: prezzo {p:.{dec(nome)}f} | "
                  f"RSI {float(df['RSI'].iloc[-1]):.1f} | "
                  f"aperti: {sum(1 for s in stato['segnali'] if s['asset'] == nome)}")

        except Exception as e:
            print(f"❌ Errore {nome}: {e}")


def main():
    stato = carica_stato()
    sh = apri_foglio()
    manda_messaggio(
        "🤖 SCALP Bot V14 avviato!\n"
        f"🅰️ Qualita' (pivot 3, EMA200)  |  🅱️ Diversificazione (pivot 5)\n"
        f"📊 Asset: {', '.join(COPPIE)}\n"
        "🕐 Sessione 08-21 ITA | Solo candele H1 chiuse\n"
        f"📄 Google Sheets: {'attivo ✅' if sh else 'NON attivo ⚠️'}\n"
        f"📌 Segnali aperti ripristinati: {len(stato['segnali'])}"
    )
    print("Bot V14 avviato.")
    while True:
        print(f"\n--- CICLO {datetime.now(timezone.utc).strftime('%H:%M UTC')} ---")
        ciclo(stato, sh)
        if sh is None:
            sh = apri_foglio()
        print(f"--- attendo {CICLO_SECONDI // 60} minuti ---")
        time.sleep(CICLO_SECONDI)


if __name__ == "__main__":
    main()
