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
#  Ciclo allineato alle chiusure candela M15 (:00 :15 :30 :45, +45s
#  buffer dati): la C valuta ogni candela M15 chiusa, A e B solo
#  quando arriva una nuova candela H1 (con retry se Yahoo ritarda).
#  Gli esiti di TUTTI i segnali sono verificati sui dati M15
#  (piu' precisi del worst-case su H1). Notifica quando TP1 sposta
#  lo SL a breakeven. Lo stato ha backup nella tab "Stato" del
#  foglio Google: sopravvive ai redeploy Railway (filesystem effimero).
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
# Se CICLO_SECONDI e' impostato su Railway usa quello; altrimenti il
# ciclo si allinea da solo alle chiusure candela M15 (:00 :15 :30 :45).
CICLO_FISSO      = os.environ.get("CICLO_SECONDI")
BUFFER_DATI_SEC  = 45   # attesa dopo la chiusura: Yahoo pubblica con ritardo

COPPIE = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "XAU/USD": "GC=F",
    "GBP/JPY": "GBPJPY=X",
}

# --------- LE STRATEGIE ---------
# A e B: trade automatici validati da ottimizza.py su H1.
# D: SEGNALATORE di inversione su M15 (metodo Mirko: BOS + Fib 0.886).
#    Il bot rileva il cambio di struttura e AVVISA: la valutazione e
#    l'eventuale entrata col pendente sono MANUALI. Nessun trade
#    automatico, nessun tracking esiti per la D.
CONFIGS = {
    "A": {"nome": "Qualita'",         "emoji": "🅰️", "onde": 2, "pivot": 3,
          "fib": 0.50, "rsi_lmax": 70, "rsi_smin": 30, "ema": True,
          "tf": "1h"},
    "B": {"nome": "Diversificazione", "emoji": "🅱️", "onde": 2, "pivot": 5,
          "fib": 0.50, "rsi_lmax": 70, "rsi_smin": 30, "ema": False,
          "tf": "1h"},
}

# parametri del segnalatore D
PIVOT_D = 5          # finestra pivot per gli swing su M15
FIB_D   = 0.886      # livello di riferimento per il pendente

# Coppie dei SEGNALATORI (SCALPING M15 + THE BOAT H4).
# A/B restano trade automatici SOLO sui 4 asset storici di COPPIE.
COPPIE_D = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/CAD": "USDCAD=X",
    "XAU/USD": "GC=F",
    "GBP/JPY": "GBPJPY=X",
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

# --------- FILTRO STOP MINIMO (sanita', non ottimizzazione) ---------
# Uno stop piu' stretto di poche volte lo spread non e' eseguibile
# nella realta' (lo spread da solo vale piu' R dello stop). Trade
# con stop microscopici vengono scartati per TUTTE le strategie.
SPREAD_TIPICO = {"EUR/USD": 0.00010, "GBP/USD": 0.00015,
                 "USD/CAD": 0.00018, "XAU/USD": 0.30, "GBP/JPY": 0.020}
STOP_MIN_SPREAD = 5.0   # lo stop deve valere almeno 5x lo spread

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
    """Invio con retry e spezzatura (limite Telegram: 4096 caratteri)."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    pezzi = [testo[i:i + 4000] for i in range(0, len(testo), 4000)] or [""]
    for pezzo in pezzi:
        for tentativo in (1, 2):
            try:
                r = requests.post(url, data={"chat_id": CHAT_ID,
                                             "text": pezzo}, timeout=15)
                if r.status_code == 200:
                    break
                print(f"Telegram HTTP {r.status_code} "
                      f"(tentativo {tentativo}/2)")
            except Exception as e:
                print(f"Telegram errore (tentativo {tentativo}/2): {e}")
            time.sleep(2)


# ------------------- STATO -------------------
# Railway ha filesystem EFFIMERO: il file locale sparisce ad ogni
# deploy/riavvio. Backup su una tab "Stato" del foglio Google, cosi'
# i segnali aperti sopravvivono e vengono chiusi correttamente.
def carica_stato(sh=None):
    if os.path.exists(FILE_STATO):
        try:
            with open(FILE_STATO) as f:
                return json.load(f)
        except Exception:
            pass
    if sh is not None:
        try:
            val = sh.worksheet("Stato").acell("A1").value
            if val:
                stato = json.loads(val)
                stato.setdefault("segnali", [])
                stato.setdefault("gia_segnalati", [])
                print("Stato ripristinato dal backup su Google Sheets.")
                return stato
        except Exception as e:
            print(f"Nessun backup stato su Sheets: {e}")
    return {"segnali": [], "gia_segnalati": []}


def salva_stato(stato, sh=None):
    try:
        stato["gia_segnalati"] = stato["gia_segnalati"][-400:]
        with open(FILE_STATO, "w") as f:
            json.dump(stato, f, indent=1)
    except Exception as e:
        print(f"Errore salvataggio stato: {e}")
    if sh is not None:
        try:
            blob = json.dumps(stato)
            if len(blob) > 49000:      # limite 50k caratteri per cella
                ridotto = dict(stato)
                ridotto["gia_segnalati"] = stato["gia_segnalati"][-150:]
                blob = json.dumps(ridotto)
            sh.worksheet("Stato").update(values=[[blob]],
                                         range_name="A1")
        except Exception as e:
            print(f"Backup stato su Sheets fallito: {e}")


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
        if "Stato" not in titoli:
            sh.add_worksheet("Stato", rows=2, cols=1)   # backup stato bot
        if "Segnalazioni" not in titoli:
            ws = sh.add_worksheet("Segnalazioni", rows=1000, cols=11)
            ws.append_row(["Data/ora ITA", "Strategia", "Timeframe",
                           "Asset", "Pattern", "Direzione", "Neckline",
                           "Testa", "Fib 0.886", "RSI", "Giudizio (tuo)"])
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
            # argomenti con nome: in gspread 6.x l'ordine posizionale
            # (range, valori) e' deprecato e verra' rimosso
            ws.update(values=[[esito, r, quando]],
                      range_name=f"J{riga}:L{riga}")
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
        ws.update(values=righe, range_name="A1",
                  value_input_option="USER_ENTERED")
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
    df = None
    for tentativo in range(2):        # 1 retry sui fallimenti transitori
        try:
            df = yf.download(ticker, interval=intervallo, period="60d",
                             progress=False, auto_adjust=False)
        except Exception as e:
            print(f"yfinance errore {ticker} ({intervallo}): {e}")
            df = None
        if df is not None and not df.empty:
            break
        if tentativo == 0:
            time.sleep(3)
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


# ------------------- SEGNALATORE D / SCALPING (testa e spalle M15) ---------
# (le vecchie pivot_multipli/_comprimi sono state sostituite da
#  _sequenza_estremi, che riconosce la forma completa W/M)


def h1_a_h4(df_h1):
    """Costruisce candele H4 aggregando le H1 (Yahoo non ha H4 native).
    Scarta l'ultima H4 se incompleta (meno di 4 ore dalla sua apertura)."""
    if df_h1 is None or len(df_h1) < 40:
        return None
    df = df_h1[["Open", "High", "Low", "Close"]].resample("4h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    ).dropna()
    adesso = datetime.now(timezone.utc)
    if len(df) and (adesso - df.index[-1].to_pydatetime()).total_seconds() < 4 * 3600:
        df = df.iloc[:-1]                 # H4 ancora in formazione
    if len(df) < 30:
        return None
    df["RSI"] = rsi_wilder(df["Close"])
    return df


def _sequenza_estremi(df, finestra):
    """Ritorna la sequenza ordinata nel tempo di massimi e minimi swing
    confermati: lista di (indice, prezzo, tipo) con tipo 'H'/'L'.
    Serve per riconoscere la forma a W (T&S rovesciato) o M (classico)."""
    h, l = df["High"].values, df["Low"].values
    estremi = []
    for p in range(finestra, len(df) - finestra):
        lo, hi = p - finestra, p + finestra + 1
        if h[p] == h[lo:hi].max():
            estremi.append((p, h[p], "H"))
        if l[p] == l[lo:hi].min():
            estremi.append((p, l[p], "L"))
    estremi.sort(key=lambda x: x[0])
    # comprimo estremi consecutivi dello stesso tipo tenendo il piu' estremo
    puliti = []
    for e in estremi:
        if puliti and puliti[-1][2] == e[2]:
            if (e[2] == "H" and e[1] > puliti[-1][1]) or \
               (e[2] == "L" and e[1] < puliti[-1][1]):
                puliti[-1] = e
        else:
            puliti.append(e)
    return puliti


def cerca_inversione(nome, df, filtra_sessione=True):
    """Rileva la ROTTURA DI STRUTTURA che anticipa il testa e spalle,
    sull'ultima candela chiusa. L'allerta arriva QUI, non a pattern
    completato (sarebbe troppo tardi): la spalla destra si former√†
    dopo, ed e' li' che Mirko entra col pendente sul ritraccio.

    SHORT (T&S classico in formazione):
      minimo piu' alto, massimo piu' alto, minimo piu' alto,
      MASSIMO piu' alto (=TESTA), poi chiusura SOTTO il minimo
      precedente -> allerta: traccia il Fib dalla testa alla rottura.
    LONG (T&S rovesciato in formazione): speculare.

    Filtro anti-rumore: la gamba testa-rottura >= 10x spread."""
    i = len(df) - 1
    ts_ita = df.index[i].tz_convert(TZ_ITA)
    if ts_ita.weekday() >= 5:
        return None
    if filtra_sessione and not (ORA_INIZIO_ITA <= ts_ita.hour < ORA_FINE_ITA):
        return None

    seq = _sequenza_estremi(df.iloc[:-1], PIVOT_D)   # solo candele chiuse
    if len(seq) < 4:
        return None
    chiusura = float(df["Close"].iloc[i])
    rsi = float(df["RSI"].iloc[i])

    MIN_PATTERN = 10.0    # gamba testa-rottura >= 10x spread (anti-rumore)
    sp_min = SPREAD_TIPICO.get(nome, 0) * MIN_PATTERN

    # La rottura puo' aver gia' generato un estremo in coda: provo le
    # ultime due finestre di 4 estremi.
    for base in (seq[-4:], seq[-5:-1] if len(seq) >= 5 else None):
        if base is None or len(base) < 4:
            continue
        tipi = "".join(e[2] for e in base)

        # --- SHORT: struttura rialzista L-H-L-H rotta al ribasso ---
        # base = [minimo, massimo, minimo piu' alto, MASSIMO piu' alto]
        if tipi == "LHLH":
            l1, h1, l2, testa = (e[1] for e in base)
            if (l2 > l1 and testa > h1          # minimi e massimi crescenti
                    and chiusura < l2):          # rottura del minimo precedente
                gamba = testa - l2
                if gamba > sp_min:
                    return {"dir": "SHORT",
                            "pattern": "Testa e Spalle in formazione",
                            "rotto": l2, "origine": testa,
                            "fib886": l2 + gamba * FIB_D,
                            "rsi": rsi, "ts": df.index[i]}

        # --- LONG: struttura ribassista H-L-H-L rotta al rialzo ---
        # base = [massimo, minimo, massimo piu' basso, MINIMO piu' basso]
        if tipi == "HLHL":
            h1, l1, h2, testa = (e[1] for e in base)
            if (h2 < h1 and testa < l1          # massimi e minimi calanti
                    and chiusura > h2):          # rottura del massimo precedente
                gamba = h2 - testa
                if gamba > sp_min:
                    return {"dir": "LONG",
                            "pattern": "Testa e Spalle rovesciato in formazione",
                            "rotto": h2, "origine": testa,
                            "fib886": h2 - gamba * FIB_D,
                            "rsi": rsi, "ts": df.index[i]}
    return None


def sheet_logga_segnalazione(sh, strategia, tf, nome, inv):
    """Registra ogni allerta T&S nella tab Segnalazioni: e' il registro
    per le statistiche settimanali (colonna 'Giudizio' da compilare a
    mano: buono/borderline/spazzatura + esito)."""
    if sh is None:
        return
    try:
        d = dec(nome)
        sh.worksheet("Segnalazioni").append_row([
            inv["ts"].tz_convert(TZ_ITA).strftime("%Y-%m-%d %H:%M"),
            strategia, tf, nome, inv["pattern"], inv["dir"],
            f"{inv['rotto']:.{d}f}", f"{inv['origine']:.{d}f}",
            f"{inv['fib886']:.{d}f}", f"{inv['rsi']:.1f}", ""])
    except Exception as e:
        print(f"Sheets errore (segnalazioni): {e}")


def msg_inversione(nome, inv, strategia="SCALPING", tf="M15", emoji="🎯"):
    d = dec(nome)
    verso = "📈 possibile LONG" if inv["dir"] == "LONG" else "📉 possibile SHORT"
    if inv["dir"] == "LONG":
        cosa_rotto = "Rotto il massimo precedente"
        dove_stop = "sotto la testa"
    else:
        cosa_rotto = "Rotto il minimo precedente"
        dove_stop = "sopra la testa"
    return (f"{emoji} {strategia} — {nome} ({tf})\n"
            f"👤 {inv['pattern']}!\n"
            f"{verso} (inversione)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💥 {cosa_rotto} (in chiusura): {inv['rotto']:.{d}f}\n"
            f"🗣 Testa ({dove_stop} va lo stop): {inv['origine']:.{d}f}\n"
            f"📐 Fib 0.886 di riferimento: {inv['fib886']:.{d}f}\n"
            f"📊 RSI {tf}: {inv['rsi']:.1f}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👉 Traccia il fibo dalla testa alla rottura e metti il\n"
            f"    pendente sul ritraccio: la spalla destra e' l'entrata.\n"
            f"⚠️ Se fa nuovi estremi, ritraccia il fibo: il livello si aggiorna.\n"
            f"🕐 Candela {tf} "
            f"{inv['ts'].tz_convert(TZ_ITA).strftime('%d/%m %H:%M')}")


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
    # stop troppo stretto rispetto allo spread = trade non eseguibile
    rischio = abs(seg["entrata"] - seg["sl"])
    if rischio < SPREAD_TIPICO.get(nome, 0) * STOP_MIN_SPREAD:
        print(f"{nome} [{cid}]: segnale scartato, stop troppo stretto "
              f"({rischio:.{dec(nome)}f} < {STOP_MIN_SPREAD:.0f}x spread)")
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
    cfg = CONFIGS.get(seg["config"], {"emoji": "❓", "nome": seg["config"]})
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
    cfg = CONFIGS.get(seg["config"], {"emoji": "❓"})
    return (f"{icone[esito]} {cfg['emoji']} CHIUSO — {seg['asset']} {seg['dir']}\n"
            f"Esito: {nomi[esito]}  ({r:+.1f}R)\n🆔 {seg['id']}")


# ------------------- COMANDI TELEGRAM -------------------
# Thread separato in long polling: risponde SUBITO, indipendente
# dal ciclo di scansione. /scan sveglia il ciclo all'istante.
EVENTO_SCAN = threading.Event()
SCAN_MANUALE = threading.Event()   # /scan: a fine ciclo manda un riepilogo

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
    f"🅰️ Qualita' H1  🅱️ Diversificazione H1 (trade automatici)\n"
    f"🎯 SCALPING (M15) e ⛵ THE BOAT (H4): avvisano quando si completa\n"
    f"    un testa e spalle; l'entrata a 0.886 la valuti TU sul grafico\n"
    f"🕐 Sessione {ORA_INIZIO_ITA:02d}-{ORA_FINE_ITA} ITA | "
    "Ciclo automatico a ogni chiusura M15"
)


def cmd_stato(stato):
    aperti = list(stato.get("segnali", []))   # copia: letta da altro thread
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
    t = (testo or "").strip().lower().split("@")[0]
    if t in ("/help", "/start"):
        return TESTO_HELP
    if t == "/winrate":
        return cmd_winrate(cont)
    if t == "/trend":
        return cmd_trend()
    if t == "/stats":
        return cmd_stats(cont, stato)
    if t == "/stato":
        return cmd_stato(stato)
    if t == "/prezzi":
        return cmd_prezzi()
    if t == "/bankroll":
        return cmd_bankroll(cont)
    if t == "/scan":
        stato.setdefault("ultima_h1", {}).clear()  # forza anche l'analisi H1
        SCAN_MANUALE.set()
        EVENTO_SCAN.set()
        return ("🔄 Ciclo forzato in corso... a fine analisi ti mando "
                "un riepilogo (anche se non trovo segnali).")
    if t == "/ping":
        adesso = datetime.now(TZ_ITA).strftime("%H:%M:%S")
        return f"🏓 Vivo! Ora italiana: {adesso}"
    if t.startswith("/"):
        return f"Comando sconosciuto: {testo}\nUsa /help per l'elenco."
    return None


def ascolta_comandi(stato, cont):
    offset = 0
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    while True:
        try:
            r = requests.get(url, params={"timeout": 50, "offset": offset},
                             timeout=60)
            for up in r.json().get("result", []):
                offset = up["update_id"] + 1
                msg = up.get("message") or {}
                if str((msg.get("chat") or {}).get("id", "")) != str(CHAT_ID):
                    continue          # ignora chiunque non sia Mirko
                testo = (msg.get("text") or "").strip()
                if testo.lower().split("@")[0] == "/restart":
                    # CRITICO: confermare l'update PRIMA di uscire,
                    # altrimenti Telegram lo riconsegna al riavvio
                    # -> loop infinito di riavvii
                    try:
                        requests.get(url, params={"offset": offset,
                                                  "timeout": 0}, timeout=10)
                    except Exception:
                        pass
                    cmd_restart()     # non ritorna: il processo esce
                risposta = gestisci_comando(testo, stato, cont)
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

            # H1: riscaricato solo quando dovrebbe esistere una nuova
            # candela chiusa. Il marcatore e' la candela REALE ricevuta:
            # se Yahoo e' in ritardo non si marca nulla e si ritenta al
            # ciclo dopo (prima si perdeva l'intera ora di analisi A/B).
            df_h1 = None
            marcatori = stato.setdefault("ultima_h1", {})
            ultimo = marcatori.get(nome)
            attesa = (datetime.now(timezone.utc).replace(
                minute=0, second=0, microsecond=0)
                - pd.Timedelta(hours=1))   # apertura ultima H1 chiusa attesa
            if ultimo is None or pd.Timestamp(ultimo) < attesa:
                df_h1 = scarica_dati(ticker, "1h")
                time.sleep(2)          # gentilezza verso Yahoo
                if df_h1 is not None:
                    nuova = df_h1.index[-1]
                    if ultimo is not None and pd.Timestamp(ultimo) >= nuova:
                        df_h1 = None   # dati non aggiornati: ritento dopo
                    else:
                        marcatori[nome] = nuova.isoformat()

            # 1) esiti dei segnali aperti (tutte le config, dati M15:
            #    piu' granulari = worst-case piu' fedele anche per A e B)
            for seg in [s for s in stato["segnali"] if s["asset"] == nome]:
                fase_prima = seg.get("fase")
                ris = verifica_esito(seg, df_m15)
                if ris:
                    esito, ts = ris
                    r = ESITO_R[esito]
                    manda_messaggio(msg_esito(seg, esito, r))
                    sheet_chiudi_segnale(sh, seg, esito, r,
                                         ts.strftime("%Y-%m-%d %H:%M"))
                    stato["segnali"].remove(seg)
                    salva_stato(stato, sh)
                    sheet_ricalcola_bankroll(sh)
                    print(f"{nome} [{seg['config']}]: chiuso -> {esito} ({r:+.1f}R)")
                elif seg.get("fase") != fase_prima:
                    # TP1 raggiunto: SL spostato a breakeven -> persisto
                    manda_messaggio(f"🟡 {CONFIGS.get(seg['config'], {}).get('emoji', '❓')} "
                                    f"TP1 raggiunto — {seg['asset']} "
                                    f"{seg['dir']}: SL a breakeven\n"
                                    f"🆔 {seg['id']}")
                    salva_stato(stato, sh)

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
                salva_stato(stato, sh)
                print(f"{nome} [{cid}]: NUOVO SEGNALE {seg['dir']} ({seg['id']})")

            # 3) 🎯 SCALPING: testa e spalle su M15 (solo AVVISO).
            #    Anti-spam: una sola allerta per pattern.
            inv = cerca_inversione(nome, df_m15)
            if inv is not None:
                chiave_d = (f"D_{nome}_{inv['dir']}_"
                            f"{inv['origine']:.{dec(nome)}f}")
                if chiave_d not in stato["gia_segnalati"]:
                    manda_messaggio(msg_inversione(nome, inv))
                    sheet_logga_segnalazione(sh, "SCALPING", "M15", nome, inv)
                    stato["gia_segnalati"].append(chiave_d)
                    salva_stato(stato, sh)
                    print(f"{nome} [SCALPING]: {inv['pattern']} {inv['dir']}")

            # 4) ⛵ THE BOAT: testa e spalle su H4 (solo AVVISO).
            #    Valutato quando c'e' un H1 fresco (le H4 derivano dalle H1).
            if df_h1 is not None:
                df_h4 = h1_a_h4(df_h1)
                if df_h4 is not None:
                    inv_b = cerca_inversione(nome, df_h4,
                                             filtra_sessione=False)
                    if inv_b is not None:
                        chiave_b = (f"BOAT_{nome}_{inv_b['dir']}_"
                                    f"{inv_b['origine']:.{dec(nome)}f}")
                        if chiave_b not in stato["gia_segnalati"]:
                            manda_messaggio(msg_inversione(
                                nome, inv_b, strategia="THE BOAT",
                                tf="H4", emoji="⛵"))
                            sheet_logga_segnalazione(sh, "THE BOAT", "H4",
                                                     nome, inv_b)
                            stato["gia_segnalati"].append(chiave_b)
                            salva_stato(stato, sh)
                            print(f"{nome} [THE BOAT]: "
                                  f"{inv_b['pattern']} {inv_b['dir']}")

            p = float(df_m15["Close"].iloc[-1])
            print(f"{nome}: prezzo {p:.{dec(nome)}f} | "
                  f"RSI M15 {float(df_m15['RSI'].iloc[-1]):.1f} | "
                  f"aperti: {sum(1 for s in stato['segnali'] if s['asset'] == nome)}")
            time.sleep(2)              # gentilezza verso Yahoo

        except Exception as e:
            print(f"❌ Errore {nome}: {e}")

    # --- Coppie EXTRA: SCALPING M15 + THE BOAT H4 (solo segnalatori) ---
    for nome, ticker in COPPIE_D.items():
        if nome in COPPIE:
            continue               # gia' analizzata nel loop principale
        try:
            df_m15 = scarica_dati(ticker, "15m")
            if df_m15 is None:
                continue
            inv = cerca_inversione(nome, df_m15)
            if inv is not None:
                chiave_d = (f"D_{nome}_{inv['dir']}_"
                            f"{inv['origine']:.{dec(nome)}f}")
                if chiave_d not in stato["gia_segnalati"]:
                    manda_messaggio(msg_inversione(nome, inv))
                    sheet_logga_segnalazione(sh, "SCALPING", "M15", nome, inv)
                    stato["gia_segnalati"].append(chiave_d)
                    salva_stato(stato, sh)
                    print(f"{nome} [SCALPING]: {inv['pattern']} {inv['dir']}")

            # THE BOAT: H1 scaricato solo su nuova candela oraria
            # (stesso marcatore usato per gli asset principali)
            marcatori = stato.setdefault("ultima_h1", {})
            ultimo = marcatori.get(nome)
            attesa = (datetime.now(timezone.utc).replace(
                minute=0, second=0, microsecond=0)
                - pd.Timedelta(hours=1))
            if ultimo is None or pd.Timestamp(ultimo) < attesa:
                df_h1 = scarica_dati(ticker, "1h")
                time.sleep(1.5)
                if df_h1 is not None:
                    nuova = df_h1.index[-1]
                    if ultimo is None or pd.Timestamp(ultimo) < nuova:
                        marcatori[nome] = nuova.isoformat()
                        df_h4 = h1_a_h4(df_h1)
                        if df_h4 is not None:
                            inv_b = cerca_inversione(nome, df_h4,
                                                     filtra_sessione=False)
                            if inv_b is not None:
                                chiave_b = (f"BOAT_{nome}_{inv_b['dir']}_"
                                            f"{inv_b['origine']:.{dec(nome)}f}")
                                if chiave_b not in stato["gia_segnalati"]:
                                    manda_messaggio(msg_inversione(
                                        nome, inv_b, strategia="THE BOAT",
                                        tf="H4", emoji="⛵"))
                                    sheet_logga_segnalazione(
                                        sh, "THE BOAT", "H4", nome, inv_b)
                                    stato["gia_segnalati"].append(chiave_b)
                                    salva_stato(stato, sh)
                                    print(f"{nome} [THE BOAT]: "
                                          f"{inv_b['pattern']} {inv_b['dir']}")
            time.sleep(1.5)        # gentilezza verso Yahoo
        except Exception as e:
            print(f"❌ Errore segnalatori {nome}: {e}")


def attesa_prossimo_ciclo():
    """Secondi fino alla prossima chiusura M15 + buffer dati Yahoo.
    Cosi' il ciclo parte ~45s dopo ogni :00 :15 :30 :45 e i segnali
    arrivano subito, non fino a 15 minuti dopo."""
    if CICLO_FISSO:
        return int(CICLO_FISSO)
    ora = datetime.now(timezone.utc)
    passati = (ora.minute % 15) * 60 + ora.second
    return (900 - passati) + BUFFER_DATI_SEC


def main():
    sh = apri_foglio()
    stato = carica_stato(sh)   # il foglio serve PRIMA: ripristino backup
    cont = {"sh": sh}          # contenitore condiviso col thread comandi
    threading.Thread(target=ascolta_comandi, args=(stato, cont),
                     daemon=True).start()
    manda_messaggio(
        "🤖 SCALP Bot V15 avviato!\n"
        f"🅰️ Qualita' H1 (pivot 3, EMA200)  |  🅱️ Diversificazione H1 (pivot 5)\n"
        f"🎯 SCALPING (M15) + ⛵ THE BOAT (H4): testa e spalle — valuti tu\n"
        f"📊 Asset: {', '.join(COPPIE)}\n"
        f"🕐 Sessione {ORA_INIZIO_ITA:02d}-{ORA_FINE_ITA} ITA | "
        "Solo candele chiuse | Ciclo a ogni chiusura M15\n"
        f"📄 Google Sheets: {'attivo ✅' if sh else 'NON attivo ⚠️'}\n"
        f"📌 Segnali aperti ripristinati: {len(stato['segnali'])}\n"
        "💬 Scrivi /help per i comandi"
    )
    print("Bot V15 avviato.")
    while True:
        try:
            print(f"\n--- CICLO "
                  f"{datetime.now(timezone.utc).strftime('%H:%M UTC')} ---")
            prima = len(stato["segnali"])
            ciclo(stato, cont["sh"])
            if cont["sh"] is None:
                cont["sh"] = apri_foglio()
            if SCAN_MANUALE.is_set():
                SCAN_MANUALE.clear()
                nuovi = len(stato["segnali"]) - prima
                ora_ita = datetime.now(TZ_ITA)
                in_sessione = (ora_ita.weekday() < 5 and
                               ORA_INIZIO_ITA <= ora_ita.hour < ORA_FINE_ITA)
                extra = ("" if in_sessione else
                         "\n⚠️ Fuori sessione (07-19 ITA, lun-ven): "
                         "nuovi segnali disattivati, verifico solo gli esiti.")
                manda_messaggio(
                    f"✅ Scan completato: {len(COPPIE)} asset analizzati "
                    f"su M15 e H1.\n"
                    f"🆕 Nuovi segnali trovati: {max(nuovi, 0)}\n"
                    f"📌 Segnali aperti: {len(stato['segnali'])}{extra}")
        except Exception as e:
            # un errore imprevisto non deve uccidere il processo
            print(f"❌ Errore ciclo (continuo): {e}")
            if SCAN_MANUALE.is_set():
                SCAN_MANUALE.clear()
                manda_messaggio(f"⚠️ Scan interrotto da un errore: {e}")
        secondi = attesa_prossimo_ciclo()
        print(f"--- prossimo ciclo tra {secondi // 60}m{secondi % 60:02d}s "
              "(o /scan) ---")
        EVENTO_SCAN.wait(timeout=secondi)  # /scan interrompe l'attesa
        EVENTO_SCAN.clear()


if __name__ == "__main__":
    main()
