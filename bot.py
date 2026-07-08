# ============================================================
#  SCALP BOT V15 LIVE — tripla strategia A/B/C in parallelo
#
#  🅰️ QUALITA'         H1  onde=2 pivot=3 fib=50% RSI 70/30 EMA200=SI
#  🅱️ DIVERSIFICAZIONE H1  onde=2 pivot=5 fib=50% RSI 70/30 EMA200=NO
#  🅲  SPERIMENTALE    M15 onde=2 pivot=5 fib=50% RSI 70/30 EMA200=SI
#  Tutte: TP1=1R (poi SL a entrata), TP2=3R, sessione 07-19 ITA
#  (orario Europe/Rome: il cambio ora legale/solare e' automatico)
#
#  Comandi Telegram (risposta immediata, thread dedicato):
#  /help /winrate /trend /stats /stato /prezzi /bankroll /scan /ping /restart
#
#  Ciclo ogni 15 min: la C valuta ogni candela M15 chiusa,
#  A e B vengono valutate solo quando chiude una nuova candela H1.
#  Gli esiti di TUTTI i segnali sono verificati sui dati M15
#  (piu' precisi del worst-case su H1).
#
#  Ogni segnale e' etichettato 🅰️ 🅱️ 🅲 cosi' decidi quanto rischiare.
#  Il foglio Google traccia esiti e bankroll PER CONFIG e PER ASSET.
#  ⚠️ La C e' in forward test: rischio minimo finche' non ha storico.
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
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
CICLO_SECONDI    = int(os.environ.get("CICLO_SECONDI", "900"))   # 15 min

COPPIE = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "XAU/USD": "GC=F",
    "GBP/JPY": "GBPJPY=X",
}

# --------- LE TRE STRATEGIE ---------
# A e B: validate da ottimizza.py su H1 (128 config, split temporale).
# C: sperimentale su M15, parametri prudenti NON ancora ottimizzati.
CONFIGS = {
    "A": {"nome": "Qualita'",         "emoji": "🅰️", "onde": 2, "pivot": 3,
          "fib": 0.50, "rsi_lmax": 70, "rsi_smin": 30, "ema": True,
          "tf": "1h"},
    "B": {"nome": "Diversificazione", "emoji": "🅱️", "onde": 2, "pivot": 5,
          "fib": 0.50, "rsi_lmax": 70, "rsi_smin": 30, "ema": False,
          "tf": "1h"},
    "C": {"nome": "Sperimentale M15", "emoji": "🅲",  "onde": 2, "pivot": 5,
          "fib": 0.50, "rsi_lmax": 70, "rsi_smin": 30, "ema": True,
          "tf": "15m"},
}

SECONDI_TF = {"1h": 3600, "15m": 900}   # durata candela per timeframe

MIN_BODY       = 0.3
EMA_PERIODO    = 200
TZ_ITA         = ZoneInfo("Europe/Rome")   # gestisce da solo ora legale/solare
ORA_INIZIO_ITA = 7      # sessione 07-19 ora italiana
ORA_FINE_ITA   = 19     # esclusa: ultima candela valutata apre alle 18:45/18:00
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


def scarica_dati(ticker, intervallo="1h"):
    df = yf.download(ticker, interval=intervallo, period="60d",
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.index = (df.index.tz_localize("UTC") if df.index.tz is None
                else df.index.tz_convert("UTC"))
    adesso = datetime.now(timezone.utc)
    durata = SECONDI_TF.get(intervallo, 3600)
    if (adesso - df.index[-1].to_pydatetime()).total_seconds() < durata:
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
    ts_ita = ts.tz_convert(TZ_ITA)
    if ts_ita.weekday() >= 5 or not (ORA_INIZIO_ITA <= ts_ita.hour < ORA_FINE_ITA):
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
        "asset": nome, "config": cid, "tf": cfg["tf"],
        "apertura": ts.strftime("%Y-%m-%d %H:%M"),
        "chiave": f"{cid}_{nome}_{ts.isoformat()}",
        "id": f"{cid}-{nome.replace('/', '')}-{ts.strftime('%Y%m%d-%H%M')}",
        "sl_corrente": seg["sl"], "fase": "OPEN", "rsi": rsi,
    })
    return seg


# ------------------- VERIFICA ESITI (worst-case) -------------------
def verifica_esito(seg, df):
    apertura = pd.Timestamp(seg["apertura"], tz="UTC")
    # L'entrata avviene alla CHIUSURA della candela di segnale:
    # si verifica solo il prezzo successivo a quel momento.
    durata = SECONDI_TF.get(seg.get("tf", "1h"), 3600)
    dopo = df[df.index >= apertura + pd.Timedelta(seconds=durata)]
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


# ------------------- COMANDI TELEGRAM -------------------
# Thread separato in long polling: risponde SUBITO, indipendente
# dal ciclo di scansione. /scan sveglia il ciclo all'istante.
EVENTO_SCAN = threading.Event()

TESTO_HELP = (
    "📖 COMANDI DISPONIBILI\n"
    "━━━━━━━━━━━━━━━\n"
    "/help — questo elenco\n"
    "/winrate — win rate per strategia: trade, WR% e profitto %\n"
    "/trend — trend attuale di ogni pair (struttura + EMA200 + RSI)\n"
    "/stats — analisi complessiva delle operazioni (per asset e direzione)\n"
    "/stato — segnali attualmente aperti\n"
    "/prezzi — prezzo e RSI attuali dei 4 asset\n"
    "/bankroll — riepilogo bankroll dal foglio Google\n"
    "/scan — forza SUBITO un ciclo di analisi completo\n"
    "/ping — verifica che il bot sia vivo\n"
    "/restart — riavvia il bot (torna su in ~20 secondi)\n"
    "━━━━━━━━━━━━━━━\n"
    f"🅰️ Qualita' H1  🅱️ Diversificazione H1  🅲 Sperimentale M15\n"
    f"🕐 Sessione {ORA_INIZIO_ITA:02d}-{ORA_FINE_ITA} ITA | Ciclo automatico 15 min"
)


def cmd_stato(stato):
    aperti = stato.get("segnali", [])
    if not aperti:
        return "📭 Nessun segnale aperto al momento."
    righe = [f"📌 SEGNALI APERTI: {len(aperti)}", "━━━━━━━━━━━━━━━"]
    for s in aperti:
        cfg = CONFIGS.get(s["config"], {})
        d = dec(s["asset"])
        righe.append(f"{cfg.get('emoji', '?')} {s['asset']} {s['dir']} "
                     f"[{s.get('fase', 'OPEN')}]\n"
                     f"   entrata {s['entrata']:.{d}f} | "
                     f"SL {s['sl_corrente']:.{d}f} | TP2 {s['tp2']:.{d}f}\n"
                     f"   🆔 {s['id']}")
    return "\n".join(righe)


def cmd_prezzi():
    righe = ["💹 PREZZI ATTUALI", "━━━━━━━━━━━━━━━"]
    for nome, ticker in COPPIE.items():
        try:
            df = yf.download(ticker, interval="15m", period="5d",
                             progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            chiusure = df["Close"].dropna()
            prezzo = float(chiusure.iloc[-1])
            rsi = float(rsi_wilder(chiusure).iloc[-1])
            righe.append(f"{nome}: {prezzo:.{dec(nome)}f} | RSI M15 {rsi:.1f}")
        except Exception:
            righe.append(f"{nome}: dati non disponibili")
    return "\n".join(righe)


def cmd_bankroll(cont):
    sh = cont.get("sh")
    if sh is None:
        return "📄 Google Sheets non attivo: bankroll non disponibile."
    try:
        valori = sh.worksheet("Bankroll").get_all_values()
        if len(valori) < 2:
            return "📄 Tab Bankroll ancora vuota (nessun trade chiuso)."
        righe = ["💰 BANKROLL", "━━━━━━━━━━━━━━━"]
        for v in valori[1:]:
            if not any(v):
                continue
            if len(v) >= 7 and v[1]:
                righe.append(f"{v[0]}: {v[1]} trade | WR {v[4]} | "
                             f"{v[5]}R | {v[6]}")
            else:
                righe.append(" ".join(x for x in v if x))
        return "\n".join(righe)
    except Exception as e:
        return f"Errore lettura bankroll: {e}"


def leggi_chiusi(cont):
    """Trade chiusi dalla tab Segnali. None se il foglio non e' attivo."""
    sh = cont.get("sh")
    if sh is None:
        return None
    try:
        valori = sh.worksheet("Segnali").get_all_values()[1:]
    except Exception:
        return None
    chiusi = []
    for v in valori:
        if len(v) >= 11 and v[9] in ESITO_R:
            try:
                chiusi.append({"config": v[1], "asset": v[3], "dir": v[4],
                               "data": v[2],
                               "r": float(str(v[10]).replace(",", "."))})
            except ValueError:
                continue
    return chiusi


def _riepilogo(lista):
    """n, win, wr%, R totale, profitto% (compounding a rischio fisso)."""
    n = len(lista)
    if n == 0:
        return None
    win = sum(1 for c in lista if c["r"] > 0)
    r_tot = sum(c["r"] for c in lista)
    bk = BANKROLL_INIZ
    for c in sorted(lista, key=lambda x: x["data"]):
        bk += bk * (RISCHIO_PCT / 100) * c["r"]
    profit = (bk / BANKROLL_INIZ - 1) * 100
    return n, win, win / n * 100, r_tot, profit


def cmd_winrate(cont):
    chiusi = leggi_chiusi(cont)
    if chiusi is None:
        return "📄 Google Sheets non attivo: winrate non disponibile."
    if not chiusi:
        return "📭 Nessun trade chiuso ancora: winrate in costruzione."
    righe = ["🏆 WIN RATE PER STRATEGIA", "━━━━━━━━━━━━━━━"]
    for cid, cfg in CONFIGS.items():
        rs = _riepilogo([c for c in chiusi if c["config"] == cid])
        if rs is None:
            righe.append(f"{cfg['emoji']} {cid} ({cfg['nome']}): nessun trade")
            continue
        n, win, wr, r_tot, profit = rs
        righe.append(f"{cfg['emoji']} {cid} ({cfg['nome']})\n"
                     f"   {n} trade | {win} win | WR {wr:.1f}%\n"
                     f"   {r_tot:+.1f}R | profitto {profit:+.2f}%")
    n, win, wr, r_tot, profit = _riepilogo(chiusi)
    righe.append("━━━━━━━━━━━━━━━")
    righe.append(f"📊 TOTALE: {n} trade | WR {wr:.1f}% | "
                 f"{r_tot:+.1f}R | {profit:+.2f}%")
    return "\n".join(righe)


def cmd_trend():
    righe = ["📈 TREND ATTUALE (H1)", "━━━━━━━━━━━━━━━"]
    for nome, ticker in COPPIE.items():
        try:
            df = scarica_dati(ticker, "1h")
            if df is None:
                righe.append(f"{nome}: dati non disponibili")
                continue
            prezzo = float(df["Close"].iloc[-1])
            ema = float(df["EMA200"].iloc[-1])
            rsi = float(df["RSI"].iloc[-1])
            piv = ultimi_pivot(df, 3)
            if piv:
                hh1, hh2, hl1, hl2 = piv
                if hh2 > hh1 and hl2 > hl1:
                    strutt, e = "RIALZISTA (massimi e minimi crescenti)", "🟢"
                elif hh2 < hh1 and hl2 < hl1:
                    strutt, e = "RIBASSISTA (massimi e minimi calanti)", "🔴"
                else:
                    strutt, e = "LATERALE (struttura mista)", "🟡"
            else:
                strutt, e = "struttura indefinita", "⚪"
            pos_ema = "sopra" if prezzo > ema else "sotto"
            righe.append(f"{e} {nome}: {strutt}\n"
                         f"   prezzo {prezzo:.{dec(nome)}f} "
                         f"({pos_ema} EMA200) | RSI {rsi:.1f}")
            time.sleep(1)
        except Exception:
            righe.append(f"{nome}: errore analisi")
    return "\n".join(righe)


def cmd_stats(cont, stato):
    chiusi = leggi_chiusi(cont)
    if chiusi is None:
        return "📄 Google Sheets non attivo: statistiche non disponibili."
    if not chiusi:
        return "📭 Nessun trade chiuso ancora: statistiche in costruzione."
    righe = ["📊 STATISTICHE COMPLESSIVE", "━━━━━━━━━━━━━━━"]
    n, win, wr, r_tot, profit = _riepilogo(chiusi)
    righe.append(f"Totale: {n} trade | WR {wr:.1f}% | "
                 f"{r_tot:+.1f}R | profitto {profit:+.2f}%")
    esiti = {"TP2": 0, "TP1_BE": 0, "SL": 0}
    for c in chiusi:          # ricostruzione esiti dal valore R
        if c["r"] >= ESITO_R["TP2"]:
            esiti["TP2"] += 1
        elif c["r"] > 0:
            esiti["TP1_BE"] += 1
        else:
            esiti["SL"] += 1
    righe.append(f"✅ TP2: {esiti['TP2']} | 🟡 TP1+BE: {esiti['TP1_BE']} | "
                 f"❌ SL: {esiti['SL']}")
    righe.append("\n— PER ASSET —")
    for asset in COPPIE:
        rs = _riepilogo([c for c in chiusi if c["asset"] == asset])
        if rs is None:
            righe.append(f"{asset}: nessun trade")
            continue
        n, win, wr, r_tot, _ = rs
        righe.append(f"{asset}: {n} trade | WR {wr:.1f}% | {r_tot:+.1f}R")
    righe.append("\n— PER DIREZIONE —")
    for d in ("LONG", "SHORT"):
        rs = _riepilogo([c for c in chiusi if c["dir"] == d])
        if rs is None:
            righe.append(f"{d}: nessun trade")
            continue
        n, win, wr, r_tot, _ = rs
        righe.append(f"{d}: {n} trade | WR {wr:.1f}% | {r_tot:+.1f}R")
    righe.append(f"\n📌 Segnali aperti ora: {len(stato.get('segnali', []))}")
    return "\n".join(righe)


def cmd_restart():
    manda_messaggio("♻️ Riavvio in corso... torno su tra ~20 secondi.")
    print("Riavvio via /restart: esco con codice 1, Railway riavvia.")
    os._exit(1)


def gestisci_comando(testo, stato, cont):
    t = testo.strip().lower().split("@")[0]
    if t in ("/help", "/start"):
        return TESTO_HELP
    if t == "/winrate":
        return cmd_winrate(cont)
    if t == "/trend":
        return cmd_trend()
    if t == "/stats":
        return cmd_stats(cont, stato)
    if t == "/restart":
        cmd_restart()          # non ritorna: il processo esce
    if t == "/stato":
        return cmd_stato(stato)
    if t == "/prezzi":
        return cmd_prezzi()
    if t == "/bankroll":
        return cmd_bankroll(cont)
    if t == "/scan":
        EVENTO_SCAN.set()
        return "🔄 Ciclo di analisi forzato: parte ora, risultati in arrivo."
    if t == "/ping":
        adesso = datetime.now(TZ_ITA).strftime("%H:%M:%S")
        return f"🏓 Vivo! Ora italiana: {adesso}"
    if t.startswith("/"):
        return f"Comando sconosciuto: {testo}\nUsa /help per l'elenco."
    return None


def ascolta_comandi(stato, cont):
    offset = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"timeout": 50, "offset": offset}, timeout=60)
            for up in r.json().get("result", []):
                offset = up["update_id"] + 1
                msg = up.get("message") or {}
                if str((msg.get("chat") or {}).get("id", "")) != str(CHAT_ID):
                    continue          # ignora chiunque non sia Mirko
                risposta = gestisci_comando(msg.get("text", ""), stato, cont)
                if risposta:
                    manda_messaggio(risposta)
        except Exception as e:
            print(f"Comandi errore: {e}")
            time.sleep(5)


# ------------------- CICLO -------------------
def ciclo(stato, sh):
    for nome, ticker in COPPIE.items():
        try:
            # M15: scaricato SEMPRE (serve per la C e per gli esiti di tutti)
            df_m15 = scarica_dati(ticker, "15m")
            if df_m15 is None:
                print(f"{nome}: dati M15 non disponibili")
                continue

            # H1: scaricato solo se e' chiusa una nuova candela H1
            # rispetto all'ultima gia' processata per questo asset
            df_h1 = None
            ultima_h1 = stato.setdefault("ultima_h1", {}).get(nome)
            ora_chiusa = datetime.now(timezone.utc).replace(
                minute=0, second=0, microsecond=0).isoformat()
            if ultima_h1 != ora_chiusa:
                df_h1 = scarica_dati(ticker, "1h")
                if df_h1 is not None:
                    stato["ultima_h1"][nome] = ora_chiusa
                time.sleep(2)          # gentilezza verso Yahoo

            # 1) esiti dei segnali aperti (tutte le config, dati M15:
            #    piu' granulari = worst-case piu' fedele anche per A e B)
            for seg in [s for s in stato["segnali"] if s["asset"] == nome]:
                ris = verifica_esito(seg, df_m15)
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
            for cid, cfg in CONFIGS.items():
                df = df_h1 if cfg["tf"] == "1h" else df_m15
                if df is None:        # H1 non riscaricato in questo ciclo
                    continue
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

            p = float(df_m15["Close"].iloc[-1])
            print(f"{nome}: prezzo {p:.{dec(nome)}f} | "
                  f"RSI M15 {float(df_m15['RSI'].iloc[-1]):.1f} | "
                  f"aperti: {sum(1 for s in stato['segnali'] if s['asset'] == nome)}")
            time.sleep(2)              # gentilezza verso Yahoo

        except Exception as e:
            print(f"❌ Errore {nome}: {e}")


def main():
    stato = carica_stato()
    sh = apri_foglio()
    cont = {"sh": sh}     # contenitore condiviso col thread comandi
    threading.Thread(target=ascolta_comandi, args=(stato, cont),
                     daemon=True).start()
    manda_messaggio(
        "🤖 SCALP Bot V15 avviato!\n"
        f"🅰️ Qualita' H1 (pivot 3, EMA200)  |  🅱️ Diversificazione H1 (pivot 5)\n"
        f"🅲 Sperimentale M15 (pivot 5, EMA200) — forward test\n"
        f"📊 Asset: {', '.join(COPPIE)}\n"
        f"🕐 Sessione {ORA_INIZIO_ITA:02d}-{ORA_FINE_ITA} ITA | "
        "Solo candele chiuse | Ciclo 15 min\n"
        f"📄 Google Sheets: {'attivo ✅' if sh else 'NON attivo ⚠️'}\n"
        f"📌 Segnali aperti ripristinati: {len(stato['segnali'])}\n"
        "💬 Scrivi /help per i comandi"
    )
    print("Bot V15 avviato.")
    while True:
        print(f"\n--- CICLO {datetime.now(timezone.utc).strftime('%H:%M UTC')} ---")
        ciclo(stato, cont["sh"])
        if cont["sh"] is None:
            cont["sh"] = apri_foglio()
        print(f"--- attendo {CICLO_SECONDI // 60} minuti (o /scan) ---")
        EVENTO_SCAN.wait(timeout=CICLO_SECONDI)  # /scan interrompe l'attesa
        EVENTO_SCAN.clear()


if __name__ == "__main__":
    main()
