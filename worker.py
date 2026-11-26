import os
import time
import requests
from datetime import datetime, timezone
from supabase import create_client

# =========================
# SCALPX PRO BACKGROUND WORKER
# PAPER TRADING ONLY
# =========================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

BINANCE_URL = "https://data-api.binance.vision/api/v3/ticker/price"

CHECK_INTERVAL = 2  # seconds


def connect_db():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase credentials missing")

    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_price(symbol):
    response = requests.get(
        BINANCE_URL,
        params={"symbol": symbol},
        timeout=10
    )
    response.raise_for_status()

    return float(response.json()["price"])


def heartbeat(db, status, message):
    try:
        db.table("worker_status").update({
            "status": status,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "last_message": message
        }).eq("id", 1).execute()

    except Exception as e:
        print("Heartbeat error:", e)


def close_paper_trade(db, trade, exit_price, reason):
    entry = float(trade["entry_price"])
    qty = float(trade["quantity"])
    fee_pct = float(trade["fee_pct"])

    # Calculate profit/loss
    gross_pnl = (exit_price - entry) * qty

    # Buy and sell fees
    buy_fee = entry * qty * (fee_pct / 100)
    sell_fee = exit_price * qty * (fee_pct / 100)

    net_pnl = gross_pnl - buy_fee - sell_fee

    # Close trade in database
    result = db.table("paper_trades").update({
        "status": "CLOSED",
        "exit_price": exit_price,
        "pnl": round(net_pnl, 8),
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "close_reason": reason
    }).eq("id", trade["id"]).eq("status", "OPEN").execute()

    if not result.data:
        print("Trade already closed:", trade["id"])
        return

    # Update paper account balance
    account = (
        db.table("paper_account")
        .select("*")
        .eq("id", 1)
        .single()
        .execute()
    )

    new_balance = float(account.data["balance"]) + net_pnl

    db.table("paper_account").update({
        "balance": round(new_balance, 8),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", 1).execute()

    # Save trade history
    try:
        db.table("paper_history").insert({
            "symbol": trade["symbol"],
            "strategy": trade["strategy"],
            "entry_price": entry,
            "exit_price": exit_price,
            "quantity": qty,
            "pnl": round(net_pnl, 8),
            "close_reason": reason,
            "closed_at": datetime.now(timezone.utc).isoformat()
        }).execute()

    except Exception as e:
        print("History save error:", e)

    print(
        f"CLOSED {trade['symbol']} | {reason} | "
        f"Exit={exit_price} | Net PnL={net_pnl:.6f}"
    )


def monitor_trades(db):
    result = (
        db.table("paper_trades")
        .select("*")
        .eq("status", "OPEN")
        .execute()
    )

    trades = result.data or []

    for trade in trades:
        try:
            symbol = trade["symbol"]
            entry = float(trade["entry_price"])

            target_pct = float(trade["target_pct"])
            stop_pct = float(trade["stop_pct"])

            target_price = entry * (1 + target_pct / 100)
            stop_price = entry * (1 - stop_pct / 100)

            current_price = get_price(symbol)

            print(
                f"{symbol} | Entry={entry} | "
                f"Current={current_price} | "
                f"TP={target_price} | SL={stop_price}"
            )

            if current_price >= target_price:
                close_paper_trade(
                    db, trade, current_price, "TAKE PROFIT"
                )

            elif current_price <= stop_price:
                close_paper_trade(
                    db, trade, current_price, "STOP LOSS"
                )

        except Exception as e:
            print(
                f"Monitor error for "
                f"{trade.get('symbol')}: {e}"
            )


def main():
    db = connect_db()

    print("ScalpX Pro Worker Started")

    heartbeat(db, "RUNNING", "Background worker started")

    while True:
        try:
            monitor_trades(db)

            heartbeat(
                db,
                "RUNNING",
                "Monitoring paper trades"
            )

        except Exception as e:
            print("Worker loop error:", e)

            heartbeat(
                db,
                "ERROR",
                str(e)
            )

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
