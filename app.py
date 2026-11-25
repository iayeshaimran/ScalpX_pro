import streamlit as st
import requests
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="ScalpX Pro | Paper", page_icon="⚡", layout="wide")
st.title("⚡ ScalpX Pro — Dual Paper Scanners")
st.caption("Binance Spot market data · Paper trading only · No real orders")

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "TRXUSDT", "LINKUSDT", "AVAXUSDT"]
BASE = "https://data-api.binance.vision/api/v3"

def init(key, value):
    if key not in st.session_state:
        st.session_state[key] = value

init("balance", 1000.0)
init("initial_balance", 1000.0)
init("positions", [])
init("history", {"HA Micro": [], "5-Candle Breakout": []})
init("scan_results", {"HA Micro": [], "5-Candle Breakout": []})
init("scan_errors", {"HA Micro": [], "5-Candle Breakout": []})
init("scanner_running", {"HA Micro": False, "5-Candle Breakout": False})
init("scan_time", {"HA Micro": None, "5-Candle Breakout": None})
init("monitor_last_check", None)
init("monitor_messages", [])

with st.sidebar:
    st.header("⚙️ Settings")
    target_pct = st.number_input("Target %", min_value=0.05, max_value=5.0, value=0.30, step=0.05)
    stop_pct = st.number_input("Stop loss %", min_value=0.05, max_value=5.0, value=0.20, step=0.05)
    amount = st.number_input("Paper trade amount (USDT)", min_value=5.0, value=100.0, step=10.0)
    fee_pct = st.number_input("Simulated fee per side (%)", min_value=0.0, max_value=2.0, value=0.10, step=0.01)
    refresh_sec = st.selectbox("Scanner refresh interval", [5, 10, 15, 30], index=1)
    auto_refresh = st.checkbox("Auto-refresh active scanner", value=False)
    monitor_sec = st.selectbox("Open-trade monitor interval", [2, 3, 5, 10], index=1)
    st.caption("Signals experimental. Paper trades only. Monitoring requires this browser/app session to remain open.")

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
    return {"Coin": symbol, "Signal": signal, "Score": int(score), "Price": float(df.close.iloc[-2]),
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
    return {"Coin": symbol, "Signal": signal, "Score": score, "Price": float(current.close),
            "Block High": block_high, "Block Low": block_low, "Current High": float(current.high),
            "Volume OK": bool(volume_ok), "Chart": f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"}

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

# Continuous open-position monitoring fragment: runs independently of scanner selection.
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
    # Rerun app UI after automatic exits so balance/history immediately reflect them.
    if any(x.get("Status") in ("TARGET", "STOP LOSS") for x in checked):
        st.rerun()

st.subheader("🔎 Choose and run a scanner")
scanner_name = st.radio("Scanner", ["HA Micro", "5-Candle Breakout"], horizontal=True)
sc1, sc2, sc3 = st.columns([1, 1, 2])
with sc1:
    if st.button(f"▶ Start {scanner_name}", use_container_width=True):
        st.session_state.scanner_running[scanner_name] = True
with sc2:
    if st.button(f"⏹ Stop {scanner_name}", use_container_width=True):
        st.session_state.scanner_running[scanner_name] = False
with sc3:
    if st.button(f"🔍 Scan {scanner_name} now", use_container_width=True):
        st.session_state.scanner_running[scanner_name] = True
        get_klines.clear()
        results, errors = [], []
        progress = st.progress(0)
        for i, coin in enumerate(COINS):
            try:
                results.append(analyze_ha(coin) if scanner_name == "HA Micro" else analyze_block(coin))
            except Exception as exc:
                errors.append(f"{coin}: {exc}")
            progress.progress((i+1)/len(COINS))
        progress.empty()
        st.session_state.scan_results[scanner_name] = results
        st.session_state.scan_errors[scanner_name] = errors
        st.session_state.scan_time[scanner_name] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()

if auto_refresh and st.session_state.scanner_running[scanner_name]:
    @st.fragment(run_every=refresh_sec)
    def auto_scan_fragment():
        results, errors = [], []
        for coin in COINS:
            try:
                results.append(analyze_ha(coin) if scanner_name == "HA Micro" else analyze_block(coin))
            except Exception as exc:
                errors.append(f"{coin}: {exc}")
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
    st.subheader("🛒 Manual Paper BUY — Any Scanned Coin")
    st.caption("BUY is available for every scanned coin, including WAIT signals. Paper trades only.")
    for r in results:
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        with c1:
            st.write(f"**{r['Coin']}**")
            st.caption(f"{r['Signal']} | Score: {r['Score']}")
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
for strategy in ["HA Micro", "5-Candle Breakout"]:
    hist = st.session_state.history[strategy]
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

if st.button("♻️ Reset paper account and strategy histories"):
    st.session_state.balance = 1000.0
    st.session_state.positions = []
    st.session_state.history = {"HA Micro": [], "5-Candle Breakout": []}
    st.session_state.scan_results = {"HA Micro": [], "5-Candle Breakout": []}
    st.session_state.scan_errors = {"HA Micro": [], "5-Candle Breakout": []}
    st.session_state.monitor_messages = []
    st.rerun()

st.caption("Paper simulation only. No Binance order is sent. Auto-monitoring runs only while this Streamlit session is active; keep the app/browser open. If the app/server is stopped or the connection sleeps, checks pause. Signals can be false and prices can move fast.")