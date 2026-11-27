import streamlit as st
import requests
import pandas as pd
import sqlite3
import json
from datetime import datetime
from pathlib import Path

st.set_page_config(page_title="ScalpX Pro | Paper", page_icon="⚡", layout="wide")
st.title("⚡ ScalpX Pro — Dual Paper Scanners")
st.caption("Binance Spot market data · Paper trading only · No real orders · Focus watchlist: 10 coins")

# ----- Focused watchlists -----
# The two original scanners use the original compact 10-coin list.
COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT",
    "TRXUSDT", "LINKUSDT", "DOGEUSDT", "SOLUSDT", "AVAXUSDT"
]
# Early Momentum also uses a focused, liquid list to reduce scan time.
MOMENTUM_COINS = COINS.copy()
# Pump Detector independently ranks the market by 24h quote volume.
PUMP_TOP_N = 30
BASE = "https://data-api.binance.vision/api/v3"
DB_PATH = Path(__file__).resolve().parent / "scalpx_paper.sqlite3"

# ----- SQLite persistence -----
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS paper_state (id INTEGER PRIMARY KEY CHECK(id=1), data_json TEXT NOT NULL, saved_at TEXT NOT NULL)")
    return conn

def default_state():
    return {
        "balance": 10000.0,
        "initial_balance": 10000.0,
        "positions": [],
        "history": {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []},
        "monitor_messages": [],
        "settings": {"target_pct": 0.30, "stop_pct": 0.20, "amount": 1000.0, "fee_pct": 0.10}
    }

def load_saved_state():
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT data_json FROM paper_state WHERE id=1").fetchone()
        if row:
            st.session_state._paper_db_has_saved_state = True
            loaded = json.loads(row[0])
            state = default_state()
            state.update(loaded)
            state["settings"] = {**default_state()["settings"], **loaded.get("settings", {})}
            state["history"] = {**default_state()["history"], **loaded.get("history", {})}
            return state
        st.session_state._paper_db_has_saved_state = False
    except Exception as err:
        st.session_state._paper_db_has_saved_state = False
        st.warning(f"Could not read saved paper account: {err}")
    return default_state()

def save_state_to_db():
    payload = {
        "balance": float(st.session_state.balance),
        "initial_balance": float(st.session_state.initial_balance),
        "positions": st.session_state.positions,
        "history": st.session_state.history,
        "monitor_messages": st.session_state.monitor_messages,
        "settings": {
            "target_pct": float(st.session_state.get("setting_target_pct", 0.30)),
            "stop_pct": float(st.session_state.get("setting_stop_pct", 0.20)),
            "amount": float(st.session_state.get("setting_amount", 1000.0)),
            "fee_pct": float(st.session_state.get("setting_fee_pct", 0.10)),
        }
    }
    with db_connect() as conn:
        conn.execute("INSERT INTO paper_state(id, data_json, saved_at) VALUES(1, ?, ?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json, saved_at=excluded.saved_at",
                     (json.dumps(payload, ensure_ascii=False), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

if "persistence_initialized" not in st.session_state:
    # If the previous version is still running in this Streamlit session, migrate its
    # in-memory paper account on first launch of this persistent version.
    saved = load_saved_state()
    if not st.session_state.get("_paper_db_has_saved_state", False) and all(k in st.session_state for k in ("balance", "positions", "history")):
        saved["balance"] = st.session_state.balance
        saved["initial_balance"] = st.session_state.get("initial_balance", 1000.0)
        saved["positions"] = st.session_state.positions
        saved["history"] = st.session_state.history
        saved["monitor_messages"] = st.session_state.get("monitor_messages", [])
    st.session_state.balance = saved["balance"]
    st.session_state.initial_balance = saved["initial_balance"]
    st.session_state.positions = saved["positions"]
    st.session_state.history = saved["history"]
    st.session_state.monitor_messages = saved.get("monitor_messages", [])
    st.session_state.setting_target_pct = saved["settings"]["target_pct"]
    st.session_state.setting_stop_pct = saved["settings"]["stop_pct"]
    st.session_state.setting_amount = saved["settings"]["amount"]
    st.session_state.setting_fee_pct = saved["settings"]["fee_pct"]
    st.session_state.persistence_initialized = True

# Session-only scan state (paper account and trade records are persisted separately).
def init_session(key, value):
    if key not in st.session_state:
        st.session_state[key] = value

init_session("scan_results", {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []})
init_session("scan_errors", {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []})
init_session("scanner_running", {"HA Micro": False, "5-Candle Breakout": False, "HA Early Momentum": False})
init_session("scan_time", {"HA Micro": None, "5-Candle Breakout": None, "HA Early Momentum": None})
init_session("monitor_last_check", None)
# Ensure keys are present when upgrading an older Streamlit session.
for _key, _defaults in [("scan_results", {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []}),
                         ("scan_errors", {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []}),
                         ("scanner_running", {"HA Micro": False, "5-Candle Breakout": False, "HA Early Momentum": False}),
                         ("scan_time", {"HA Micro": None, "5-Candle Breakout": None, "HA Early Momentum": None})]:
    for _name, _value in _defaults.items():
        st.session_state[_key].setdefault(_name, _value)
init_session("pump_results", [])
init_session("pump_errors", [])
init_session("pump_checked", None)

with st.sidebar:
    st.header("⚙️ Settings")
    target_pct = st.number_input("Target %", min_value=0.05, max_value=5.0, value=float(st.session_state.setting_target_pct), step=0.05)
    stop_pct = st.number_input("Stop loss %", min_value=0.05, max_value=5.0, value=float(st.session_state.setting_stop_pct), step=0.05)
    amount = st.number_input("Paper trade amount (USDT)", min_value=5.0, value=float(st.session_state.setting_amount), step=50.0)
    fee_pct = st.number_input("Simulated fee per side (%)", min_value=0.0, max_value=2.0, value=float(st.session_state.setting_fee_pct), step=0.01)
    refresh_sec = st.selectbox("Scanner refresh interval", [5, 10, 15, 30], index=1)
    auto_refresh = st.checkbox("Auto-refresh active scanner", value=False)
    monitor_sec = st.selectbox("Open-trade monitor interval", [2, 3, 5, 10], index=1)
    st.caption("Paper account saves to a local SQLite file. The laptop must stay awake for monitoring to continue.")

# Persist settings changes (and make the current fee available to exit calculations).
st.session_state.setting_target_pct = target_pct
st.session_state.setting_stop_pct = stop_pct
st.session_state.setting_amount = amount
st.session_state.setting_fee_pct = fee_pct
save_state_to_db()

@st.cache_data(ttl=2)
def get_klines(symbol, interval="1m", limit=300):
    r = requests.get(f"{BASE}/klines", params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=12)
    r.raise_for_status()
    data = r.json()
    cols = ["time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c])
    return df

@st.cache_data(ttl=1)
def get_price(symbol):
    r = requests.get(f"{BASE}/ticker/price", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

def heikin_ashi(df):
    ha = pd.DataFrame(index=df.index)
    ha["close"] = (df.open + df.high + df.low + df.close) / 4
    ha["open"] = 0.0
    ha.loc[ha.index[0], "open"] = (df.open.iloc[0] + df.close.iloc[0]) / 2
    for i in range(1, len(df)):
        ha.loc[ha.index[i], "open"] = (ha.open.iloc[i-1] + ha.close.iloc[i-1]) / 2
    ha["high"] = pd.concat([df.high, ha.open, ha.close], axis=1).max(axis=1)
    ha["low"] = pd.concat([df.low, ha.open, ha.close], axis=1).min(axis=1)
    ha["volume"] = df.volume
    return ha

def analyze_ha(symbol):
    df = get_klines(symbol, "1m", 300)
    ha = heikin_ashi(df)
    closed = ha.iloc[:-1].copy()
    prev, last = closed.iloc[-2], closed.iloc[-1]
    green_prev = prev.close > prev.open
    green_last = last.close > last.open
    body_ratio = abs(last.close - last.open) / max(last.high - last.low, 1e-12)
    upper_ratio = (last.high - max(last.open, last.close)) / max(last.high - last.low, 1e-12)
    volume_mean = df.volume.iloc[-21:-1].mean()
    volume_ok = df.volume.iloc[-2] >= volume_mean
    ema9 = df.close.ewm(span=9, adjust=False).mean().iloc[-2]
    ema33 = df.close.ewm(span=33, adjust=False).mean().iloc[-2]
    ema200 = df.close.ewm(span=200, adjust=False).mean().iloc[-2]
    delta = df.close.diff()
    gains = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = gains / losses.replace(0, float("nan"))
    rsi = (100 - 100/(1+rs)).iloc[-2]
    score = 0
    if green_prev and green_last: score += 30
    if body_ratio >= 0.50: score += 20
    if upper_ratio <= 0.20: score += 10
    if volume_ok: score += 15
    if ema9 > ema33: score += 15
    if df.close.iloc[-2] > ema200: score += 10
    if green_prev and green_last and body_ratio >= 0.35 and upper_ratio <= 0.35:
        signal = "🟢 CONFIRMED HA BUY"
    elif ha.iloc[-1].close > ha.iloc[-1].open:
        signal = "🟡 EARLY ALERT"
    else:
        signal = "WAIT"
    return {"Coin": symbol, "Priority": "FOCUS 10", "Signal": signal, "Score": int(score), "Price": float(df.close.iloc[-2]),
            "RSI": float(rsi) if pd.notna(rsi) else 50.0, "Body %": round(body_ratio*100, 1),
            "Upper wick %": round(upper_ratio*100, 1), "Volume OK": bool(volume_ok),
            "EMA9>EMA33": bool(ema9>ema33), "EMA200 trend": bool(df.close.iloc[-2]>ema200),
            "Chart": f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"}

def analyze_block(symbol):
    df = get_klines(symbol, "1m", 100)
    block = df.iloc[-6:-1]
    current = df.iloc[-1]
    block_high, block_low = float(block.high.max()), float(block.low.min())
    breakout = current.close > block_high and current.close > current.open
    crossed = current.high > block_high
    score = 0
    if crossed: score += 35
    if breakout: score += 35
    if current.close > current.open: score += 10
    avg_vol = df.volume.iloc[-21:-1].mean()
    volume_ok = current.volume > avg_vol
    if volume_ok: score += 20
    signal = "🚀 BLOCK BREAKOUT BUY" if breakout else ("🟡 BREAKOUT WATCH" if crossed else "WAIT")
    return {"Coin": symbol, "Priority": "FOCUS 10", "Signal": signal, "Score": score, "Price": float(current.close),
            "Block High": block_high, "Block Low": block_low, "Current High": float(current.high),
            "Volume OK": bool(volume_ok), "Chart": f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"}

def analyze_early_momentum(symbol):
    """Fast HA transition scan using the current (still-forming) 1m candle."""
    df = get_klines(symbol, "1m", 120)
    ha = heikin_ashi(df)
    # Last row is the live/forming candle. Previous rows are closed candles.
    live, prev = ha.iloc[-1], ha.iloc[-2]
    raw_live = df.iloc[-1]
    candle_range = max(float(live.high-live.low), 1e-12)
    body_ratio = abs(float(live.close-live.open))/candle_range
    upper_ratio = (float(live.high)-max(float(live.open),float(live.close)))/candle_range
    green_live = float(live.close) > float(live.open)
    prev_red = float(prev.close) <= float(prev.open)
    # Soft trend filter: EMA20 proximity/slope, not a strict gate.
    ema20 = df.close.ewm(span=20, adjust=False).mean()
    ema20_now, ema20_prev = float(ema20.iloc[-1]), float(ema20.iloc[-2])
    soft_trend_ok = float(raw_live.close) >= ema20_now * 0.997 or ema20_now >= ema20_prev
    vol_avg = float(df.volume.iloc[-21:-1].mean()) if len(df) >= 21 else float(df.volume.mean())
    vol_live = float(raw_live.volume)
    volume_ratio = vol_live / max(vol_avg, 1e-12)
    # Avoid very late alerts: count consecutive green HA candles ending at live candle.
    greens = 0
    for j in range(len(ha)-1, max(-1, len(ha)-8), -1):
        if float(ha.iloc[j].close) > float(ha.iloc[j].open): greens += 1
        else: break
    early = green_live and prev_red and body_ratio >= 0.22 and upper_ratio <= 0.50 and soft_trend_ok
    momentum = green_live and body_ratio >= 0.42 and upper_ratio <= 0.30 and volume_ratio >= 1.15 and soft_trend_ok and greens <= 2
    confirmed = momentum and float(raw_live.close) > float(df.high.iloc[-2])
    signal = "🟢 CONFIRMED MOMENTUM" if confirmed else ("🟠 MOMENTUM" if momentum else ("🟡 EARLY REVERSAL" if early else "WAIT"))
    score = (25 if green_live else 0) + (20 if prev_red else 0) + (20 if body_ratio >= .42 else 10 if body_ratio >= .22 else 0) + (15 if volume_ratio >= 1.15 else 0) + (10 if soft_trend_ok else 0) + (10 if greens <= 2 else 0)
    return {"Coin":symbol, "Signal":signal, "Score":score, "Price":float(raw_live.close), "Live HA Green":green_live, "Green Streak":greens, "Body %":round(body_ratio*100,1), "Upper Wick %":round(upper_ratio*100,1), "Volume x":round(volume_ratio,2), "Soft EMA20 Filter":soft_trend_ok, "Chart":f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"}

@st.cache_data(ttl=15)
def get_top_volume_symbols(limit=30):
    r=requests.get(f"{BASE}/ticker/24hr",timeout=15)
    r.raise_for_status()
    rows=r.json()
    candidates=[x for x in rows if x.get("symbol", "").endswith("USDT") and not x["symbol"].endswith("UPUSDT") and not x["symbol"].endswith("DOWNUSDT")]
    candidates.sort(key=lambda x:float(x.get("quoteVolume",0)),reverse=True)
    return candidates[:limit]

def analyze_pump_watch(symbol):
    df=get_klines(symbol,"1m",35)
    if len(df)<25: raise ValueError("Not enough candles")
    current=df.iloc[-1]
    prev1=df.iloc[-2]
    prev5=df.iloc[-6]
    change1=(float(current.close)/float(prev1.close)-1)*100
    change5=(float(current.close)/float(prev5.close)-1)*100
    avg_vol=float(df.volume.iloc[-21:-1].mean())
    vol_ratio=float(current.volume)/max(avg_vol,1e-12)
    status="WATCH"
    if vol_ratio>=2.0 and change1>=0.15 and change5>=0.30: status="🚀 PUMP WATCH"
    elif vol_ratio>=1.5 and change1>=0.08: status="🟡 VOLUME SPIKE"
    return {"Coin":symbol,"Status":status,"1m %":round(change1,2),"5m %":round(change5,2),"Volume x":round(vol_ratio,2),"24h Quote Vol":None,"Chart":f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"}

def close_position(pos, exit_price, reason):
    gross = pos["Quantity"] * exit_price
    sell_fee = gross * fee_pct / 100
    net = gross - sell_fee
    pnl = net - pos["Amount"] - pos["Buy Fee"]
    st.session_state.balance += net
    record = {"Coin": pos["Coin"], "Strategy": pos["Strategy"], "Entry": pos["Entry"],
              "Exit": exit_price, "Amount": pos["Amount"], "Fees": pos["Buy Fee"] + sell_fee,
              "P/L USDT": pnl, "Exit Reason": reason, "Opened": pos["Opened"],
              "Closed": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    st.session_state.history[pos["Strategy"]].insert(0, record)
    st.session_state.positions.remove(pos)
    st.session_state.monitor_messages.insert(0, f"{pos['Coin']} closed: {reason} | P/L {pnl:+.4f} USDT")
    st.session_state.monitor_messages = st.session_state.monitor_messages[:20]
    save_state_to_db()

@st.fragment(run_every=monitor_sec)
def trade_monitor():
    if not st.session_state.positions:
        st.caption("🟢 Trade monitor active — no open paper positions.")
        st.session_state.monitor_last_check = datetime.now().strftime("%H:%M:%S")
        return
    checked = []
    for pos in st.session_state.positions[:]:
        try:
            current = get_price(pos["Coin"])
            reason = "TARGET" if current >= pos["Target"] else ("STOP LOSS" if current <= pos["Stop"] else None)
            checked.append({"Coin": pos["Coin"], "Price": current, "Target": pos["Target"], "Stop": pos["Stop"], "Status": reason or "OPEN"})
            if reason:
                close_position(pos, current, reason)
        except Exception as exc:
            checked.append({"Coin": pos["Coin"], "Status": f"PRICE ERROR: {exc}"})
    st.session_state.monitor_last_check = datetime.now().strftime("%H:%M:%S")
    st.write(f"🟢 Monitor checked at {st.session_state.monitor_last_check}")
    if checked:
        st.dataframe(pd.DataFrame(checked), hide_index=True, width="stretch")
    if st.session_state.monitor_messages:
        st.caption("Recent auto-close events:")
        for msg in st.session_state.monitor_messages[:5]:
            st.write("• " + msg)
    if any(x.get("Status") in ("TARGET", "STOP LOSS") for x in checked):
        st.rerun()

st.subheader("🔎 Choose and run a scanner")
scanner_name = st.radio("Scanner", ["HA Micro", "5-Candle Breakout", "HA Early Momentum"], horizontal=True)
scan_coins = MOMENTUM_COINS if scanner_name == "HA Early Momentum" else COINS
sc1, sc2, sc3 = st.columns([1, 1, 2])
with sc1:
    if st.button(f"▶ Start {scanner_name}", use_container_width=True): st.session_state.scanner_running[scanner_name] = True
with sc2:
    if st.button(f"⏹ Stop {scanner_name}", use_container_width=True): st.session_state.scanner_running[scanner_name] = False
with sc3:
    if st.button(f"🔍 Scan {scanner_name} now", use_container_width=True):
        st.session_state.scanner_running[scanner_name] = True
        get_klines.clear()
        results, errors = [], []
        progress = st.progress(0)
        for i, coin in enumerate(scan_coins):
            try:
                if scanner_name == "HA Micro": results.append(analyze_ha(coin))
                elif scanner_name == "5-Candle Breakout": results.append(analyze_block(coin))
                else: results.append(analyze_early_momentum(coin))
            except Exception as exc: errors.append(f"{coin}: {exc}")
            progress.progress((i+1)/len(scan_coins))
        progress.empty()
        results.sort(key=lambda x: -x["Score"])
        st.session_state.scan_results[scanner_name] = results
        st.session_state.scan_errors[scanner_name] = errors
        st.session_state.scan_time[scanner_name] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()

if auto_refresh and st.session_state.scanner_running[scanner_name]:
    @st.fragment(run_every=refresh_sec)
    def auto_scan_fragment():
        results, errors = [], []
        for coin in scan_coins:
            try:
                if scanner_name == "HA Micro": results.append(analyze_ha(coin))
                elif scanner_name == "5-Candle Breakout": results.append(analyze_block(coin))
                else: results.append(analyze_early_momentum(coin))
            except Exception as exc: errors.append(f"{coin}: {exc}")
        results.sort(key=lambda x: -x["Score"])
        st.session_state.scan_results[scanner_name] = results
        st.session_state.scan_errors[scanner_name] = errors
        st.session_state.scan_time[scanner_name] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"Auto scan updated: {st.session_state.scan_time[scanner_name]}")
    auto_scan_fragment()

results = st.session_state.scan_results[scanner_name]
if results:
    st.caption(f"Last scan: {st.session_state.scan_time[scanner_name] or '—'}")
    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True,
                 column_config={"Chart": st.column_config.LinkColumn("TradingView Chart", display_text="Open Chart")})
    # Side-panel Pump Detector: only scans when user presses its button.
    pump_col, main_col = st.columns([1, 2])
    with pump_col:
        st.subheader("🚀 Pump Detector")
        st.caption("Top-volume USDT coins · alert/watch only · not a buy signal")
        if st.button("🔎 Scan Pump Watch", key="pump_scan_button", use_container_width=True):
            with st.spinner("Checking high-volume coins..."):
                pump_rows, pump_errors = [], []
                try:
                    symbols=[x["symbol"] for x in get_top_volume_symbols(PUMP_TOP_N)]
                    for sym in symbols:
                        try: pump_rows.append(analyze_pump_watch(sym))
                        except Exception as ex: pump_errors.append(f"{sym}: {ex}")
                    pump_rows.sort(key=lambda x:(0 if "PUMP WATCH" in x["Status"] else 1, -x["Volume x"], -x["5m %"]))
                    st.session_state.pump_results=pump_rows
                    st.session_state.pump_errors=pump_errors
                    st.session_state.pump_checked=datetime.now().strftime("%H:%M:%S")
                except Exception as ex: st.error(f"Pump detector failed: {ex}")
        if st.session_state.pump_results:
            st.caption(f"Updated: {st.session_state.pump_checked}")
            st.dataframe(pd.DataFrame(st.session_state.pump_results), hide_index=True, width="stretch", column_config={"Chart":st.column_config.LinkColumn("Chart",display_text="Chart")})
            if st.session_state.pump_errors:
                with st.expander("Pump scan errors"): st.write(st.session_state.pump_errors)
        else: st.info("Press Scan Pump Watch to check the market.")
    with main_col:
        st.caption("Main scanner results and paper trading")
    st.subheader("🛒 Manual Paper BUY — Any Scanned Coin")
    st.caption("BUY is available for every scanned coin, including WAIT signals. Paper trades only.")
    for r in results:
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        with c1:
            st.write(f"**{r['Coin']}**")
            st.caption(f"{r['Signal']} | Score: {r['Score']} | {r['Priority']}")
        with c2:
            st.write(f"Price: {r['Price']:.8f}")
        with c3:
            st.markdown(f"[📈 TradingView Chart]({r['Chart']})")
        with c4:
            if st.button(f"🟢 BUY {r['Coin']}", key=f"manual_buy_{scanner_name}_{r['Coin']}", use_container_width=True):
                try:
                    price = get_price(r["Coin"])
                    buy_fee = amount * fee_pct / 100
                    debit = amount + buy_fee
                    already_open = any(p["Coin"] == r["Coin"] and p["Strategy"] == scanner_name for p in st.session_state.positions)
                    if already_open:
                        st.warning("This coin already has an open position for this scanner.")
                    elif debit > st.session_state.balance:
                        st.error("Paper balance insufficient.")
                    else:
                        qty = amount / price
                        st.session_state.balance -= debit
                        st.session_state.positions.append({"Coin": r["Coin"], "Strategy": scanner_name, "Entry": price,
                            "Quantity": qty, "Amount": amount, "Buy Fee": buy_fee,
                            "Target": price*(1+target_pct/100), "Stop": price*(1-stop_pct/100),
                            "Opened": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                        save_state_to_db()
                        st.success(f"Paper BUY opened: {r['Coin']} @ {price:.8f}")
                        st.rerun()
                except Exception as exc:
                    st.error(f"BUY failed: {exc}")

if st.session_state.scan_errors[scanner_name]:
    with st.expander("⚠️ Scan errors"):
        st.write(st.session_state.scan_errors[scanner_name])

st.divider()
st.subheader("💰 Paper Account")
cols = st.columns(4)
cols[0].metric("Available balance", f"{st.session_state.balance:.2f} USDT")
cols[1].metric("Open positions", len(st.session_state.positions))
cols[2].metric("Paper amount", f"{amount:.2f} USDT")
cols[3].metric("Target / Stop", f"+{target_pct:.2f}% / -{stop_pct:.2f}%")

st.subheader("📌 Open Paper Positions")
if st.session_state.positions:
    live_rows = []
    for p in st.session_state.positions:
        try:
            price = get_price(p["Coin"])
            est_pnl = p["Quantity"]*price - p["Amount"] - p["Buy Fee"] - (p["Quantity"]*price*fee_pct/100)
            live_rows.append({**p, "Live Price": price, "Estimated P/L": est_pnl,
                              "Chart": f"https://www.tradingview.com/chart/?symbol=BINANCE:{p['Coin']}"})
        except Exception as exc:
            live_rows.append({**p, "Price error": str(exc)})
    st.dataframe(pd.DataFrame(live_rows), width="stretch", hide_index=True,
                 column_config={"Chart": st.column_config.LinkColumn("Chart", display_text="Open Chart")})
    for p in st.session_state.positions[:]:
        if st.button(f"🔴 Manual Paper SELL · {p['Strategy']} · {p['Coin']}", key=f"manualsell_{p['Strategy']}_{p['Coin']}"):
            try:
                px = get_price(p["Coin"])
                close_position(p, px, "MANUAL")
                st.success(f"Paper SELL closed {p['Coin']} at {px:.8f}")
                st.rerun()
            except Exception as exc:
                st.error(f"SELL error: {exc}")
else:
    st.info("No open paper positions.")

st.subheader("⏱️ Automatic Trade Monitor")
st.caption(f"Checks open positions every {monitor_sec} seconds while this Streamlit page/session is open. Last check: {st.session_state.monitor_last_check or 'not checked yet'}")
trade_monitor()

st.subheader("📊 Strategy Results")
all_history = []
for strategy in ["HA Micro", "5-Candle Breakout", "HA Early Momentum"]:
    hist = st.session_state.history[strategy]
    all_history.extend(hist)
    with st.expander(f"{strategy} · {len(hist)} closed trades", expanded=False):
        if hist:
            hdf = pd.DataFrame(hist)
            wins = int((hdf["P/L USDT"] > 0).sum())
            winrate = wins/len(hdf)*100
            netpnl = float(hdf["P/L USDT"].sum())
            m1, m2, m3 = st.columns(3)
            m1.metric("Win rate", f"{winrate:.1f}%")
            m2.metric("Wins / Losses", f"{wins} / {len(hdf)-wins}")
            m3.metric("Net realized P/L", f"{netpnl:.4f} USDT")
            st.dataframe(hdf, width="stretch", hide_index=True)
        else:
            st.caption("No completed trades yet.")

# Save button + export download: account data is also auto-saved on every trade/settings change.
sv1, sv2 = st.columns(2)
with sv1:
    if st.button("💾 Save Paper Account Now", use_container_width=True):
        save_state_to_db()
        st.success(f"Saved to local database: {DB_PATH.name}")
with sv2:
    csv_data = pd.DataFrame(all_history).to_csv(index=False) if all_history else "Coin,Strategy,Entry,Exit,Amount,Fees,P/L USDT,Exit Reason,Opened,Closed\n"
    st.download_button("⬇️ Download Trade History CSV", data=csv_data, file_name="ScalpX_paper_trade_history.csv", mime="text/csv", use_container_width=True)

st.caption(f"Database file: {DB_PATH.name} · Paper account data is saved locally. Back up this file if moving the bot to another laptop.")

st.subheader("⚠️ Reset Paper Account")
confirm_reset = st.checkbox("I understand this will reset balance, open positions and history to a new paper account.")
if st.button("♻️ Reset account to 10,000 USDT", disabled=not confirm_reset):
    st.session_state.balance = 10000.0
    st.session_state.initial_balance = 10000.0
    st.session_state.positions = []
    st.session_state.history = {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []}
    st.session_state.monitor_messages = []
    st.session_state.scan_results = {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []}
    st.session_state.scan_errors = {"HA Micro": [], "5-Candle Breakout": [], "HA Early Momentum": []}
    save_state_to_db()
    st.success("Paper account reset to 10,000 USDT and saved.")
    st.rerun()

st.caption("Paper simulation only. No Binance order is sent. Auto-monitoring runs only while this Streamlit session is active; keep the app/browser open. If the app/server is stopped or the connection sleeps, checks pause. Signals are experimental and can be false.")
