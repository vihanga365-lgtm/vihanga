import argparse
import csv
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd


EXCHANGE = ccxt.binance({"enableRateLimit": True})
DEFAULT_SYMBOL = "BTC/USDT"
DEFAULT_FOREX_SYMBOL = "EURUSD"
DEFAULT_TIMEFRAME = "15m"
FEE_RATE = 0.001
DEFAULT_SLIPPAGE_RATE = 0.0005
START_CASH = 1000.0
TRADE_ALLOCATION = 100.0
WARMUP_BARS = 200


def indicators(dataframe):
    result = dataframe.copy()
    result["ema20"] = result["close"].ewm(span=20, adjust=False).mean()
    result["ema50"] = result["close"].ewm(span=50, adjust=False).mean()
    result["ema200"] = result["close"].ewm(span=200, adjust=False).mean()

    delta = result["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()
    result["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    previous_close = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr14"] = true_range.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()
    upward_move = result["high"].diff()
    downward_move = -result["low"].diff()
    positive_dm = upward_move.where(
        (upward_move > downward_move) & (upward_move > 0), 0.0
    )
    negative_dm = downward_move.where(
        (downward_move > upward_move) & (downward_move > 0), 0.0
    )
    positive_di = 100 * positive_dm.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean() / result["atr14"]
    negative_di = 100 * negative_dm.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean() / result["atr14"]
    directional_index = (
        100
        * (positive_di - negative_di).abs()
        / (positive_di + negative_di).replace(0, float("nan"))
    )
    result["adx14"] = directional_index.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()
    return result


def candles_to_dataframe(candles):
    dataframe = pd.DataFrame(
        candles, columns=["time", "open", "high", "low", "close", "volume"]
    )
    dataframe["time"] = pd.to_datetime(dataframe["time"], unit="ms", utc=True)
    return indicators(dataframe)


def signal(previous_row, current_row, adx_minimum=20.0, minimum_atr_percent=0.5):
    if pd.isna(current_row.rsi) or pd.isna(current_row.adx14):
        return "WAIT"
    bullish_crossover = (
        previous_row.ema20 <= previous_row.ema50
        and current_row.ema20 > current_row.ema50
    )
    bearish_crossover = (
        previous_row.ema20 >= previous_row.ema50
        and current_row.ema20 < current_row.ema50
    )
    atr_percent = 100 * current_row.atr14 / current_row.close
    if (
        bullish_crossover
        and current_row.close > current_row.ema200
        and current_row.ema200 > previous_row.ema200
        and current_row.adx14 >= adx_minimum
        and atr_percent >= minimum_atr_percent
        and 50 < current_row.rsi < 70
    ):
        return "BUY"
    if (
        bearish_crossover
        or current_row.close < current_row.ema200
        or current_row.rsi >= 75
    ):
        return "SELL"
    return "WAIT"


def get_crypto(symbol=DEFAULT_SYMBOL, timeframe=DEFAULT_TIMEFRAME, limit=500, since=None):
    candles = EXCHANGE.fetch_ohlcv(
        symbol, timeframe=timeframe, limit=limit, since=since
    )
    return candles_to_dataframe(candles)


def get_crypto_history(symbol, days, timeframe=DEFAULT_TIMEFRAME):
    since = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
    )
    timeframe_milliseconds = EXCHANGE.parse_timeframe(timeframe) * 1000
    candles = []

    while True:
        batch = EXCHANGE.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since, limit=1000
        )
        if not batch:
            break
        candles.extend(batch)
        next_since = batch[-1][0] + timeframe_milliseconds
        if len(batch) < 1000 or next_since <= since:
            break
        since = next_since

    if not candles:
        raise RuntimeError(f"No Binance candles returned for {symbol}")

    unique_candles = {candle[0]: candle for candle in candles}
    return candles_to_dataframe(
        [unique_candles[timestamp] for timestamp in sorted(unique_candles)]
    )


def calculate_backtest(
    dataframe,
    slippage_rate=DEFAULT_SLIPPAGE_RATE,
    fee_rate=FEE_RATE,
    atr_stop_multiple=2.0,
    atr_trailing_multiple=3.0,
    adx_minimum=20.0,
    minimum_atr_percent=0.5,
):
    if not 0 <= fee_rate < 1:
        raise ValueError("fee_rate must be between 0 and 1")
    if not 0 <= slippage_rate < 1:
        raise ValueError("slippage_rate must be between 0 and 1")
    if len(dataframe) <= WARMUP_BARS:
        raise ValueError(f"Backtest requires more than {WARMUP_BARS} completed candles")

    cash = START_CASH
    quantity = 0.0
    entry_cost = None
    entry_price = None
    entry_time = None
    stop_price = None
    highest_price = None
    total_fees = 0.0
    equity = []
    trade_records = []

    def close_position(exit_price, exit_time, reason):
        nonlocal cash, quantity, entry_cost, entry_price, entry_time
        nonlocal stop_price, highest_price, total_fees

        gross_proceeds = quantity * exit_price
        exit_fee = gross_proceeds * fee_rate
        proceeds = gross_proceeds - exit_fee
        profit = proceeds - entry_cost
        cash += proceeds
        total_fees += exit_fee
        trade_records.append(
            {
                "entry_time": entry_time,
                "exit_time": exit_time,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": profit,
                "return_percent": 100 * profit / entry_cost,
                "exit_reason": reason,
            }
        )
        quantity = 0.0
        entry_cost = None
        entry_price = None
        entry_time = None
        stop_price = None
        highest_price = None

    for index in range(WARMUP_BARS, len(dataframe)):
        signal_previous_bar = dataframe.iloc[index - 2]
        signal_bar = dataframe.iloc[index - 1]
        current_bar = dataframe.iloc[index]
        action = signal(
            signal_previous_bar,
            signal_bar,
            adx_minimum,
            minimum_atr_percent,
        )
        exited_this_bar = False

        if quantity > 0 and action == "SELL":
            exit_price = float(current_bar.open) * (1 - slippage_rate)
            close_position(exit_price, current_bar.time, "signal")
            exited_this_bar = True
        elif quantity > 0 and float(current_bar.low) <= stop_price:
            raw_exit = min(float(current_bar.open), stop_price)
            exit_price = raw_exit * (1 - slippage_rate)
            close_position(exit_price, current_bar.time, "atr_stop")
            exited_this_bar = True

        if quantity == 0 and action == "BUY" and not exited_this_bar:
            allocation = min(cash, TRADE_ALLOCATION)
            if allocation > 0:
                fill_price = float(current_bar.open) * (1 + slippage_rate)
                entry_fee = allocation * fee_rate
                quantity = (allocation - entry_fee) / fill_price
                cash -= allocation
                entry_cost = allocation
                entry_price = fill_price
                entry_time = current_bar.time
                highest_price = fill_price
                stop_price = fill_price - atr_stop_multiple * float(signal_bar.atr14)
                total_fees += entry_fee

        if quantity > 0:
            highest_price = max(highest_price, float(current_bar.high))
            trailing_stop = highest_price - atr_trailing_multiple * float(current_bar.atr14)
            stop_price = max(stop_price, trailing_stop)

        equity.append(cash + quantity * float(current_bar.close))

    if quantity:
        exit_price = float(dataframe.iloc[-1].close) * (1 - slippage_rate)
        close_position(exit_price, dataframe.iloc[-1].time, "end_of_test")
        equity[-1] = cash

    equity_series = pd.Series(equity, dtype="float64")
    if equity_series.empty:
        drawdown = 0.0
    else:
        drawdown = ((equity_series / equity_series.cummax()) - 1).min()

    first_price = float(dataframe.iloc[WARMUP_BARS].open)
    last_price = float(dataframe.iloc[-1].close)
    return_rate = cash / START_CASH - 1
    benchmark_final = (
        START_CASH
        - TRADE_ALLOCATION
        + TRADE_ALLOCATION * (1 - fee_rate) * (last_price / first_price) * (1 - fee_rate)
    )
    buy_hold_return = benchmark_final / START_CASH - 1
    profits = [trade["pnl"] for trade in trade_records]
    winning_profits = [profit for profit in profits if profit > 0]
    losing_profits = [profit for profit in profits if profit <= 0]
    gross_profit = sum(winning_profits)
    gross_loss = -sum(losing_profits)
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
    trades = len(trade_records)
    wins = len(winning_profits)

    return {
        "final_value": cash,
        "return_rate": return_rate,
        "buy_hold_return": buy_hold_return,
        "closed_trades": trades,
        "winning_trades": wins,
        "win_rate": wins / trades if trades else 0.0,
        "profit_factor": profit_factor,
        "average_win": sum(winning_profits) / wins if wins else 0.0,
        "average_loss": sum(losing_profits) / len(losing_profits) if losing_profits else 0.0,
        "expectancy": sum(profits) / trades if trades else 0.0,
        "total_fees": total_fees,
        "max_drawdown": drawdown,
        "trades": trade_records,
    }


def write_trade_log(output_path, trades):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "pnl",
        "return_percent",
        "exit_reason",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trades)


def print_backtest_result(label, result):
    print(f"[{label}]")
    print("Final virtual value:", round(result["final_value"], 2))
    print("Strategy return:", round(100 * result["return_rate"], 2), "%")
    print("Exposure-matched buy-and-hold:", round(100 * result["buy_hold_return"], 2), "%")
    print(
        "Closed trades:", result["closed_trades"],
        "Winning trades:", result["winning_trades"],
    )
    print("Win rate:", round(100 * result["win_rate"], 2), "%")
    profit_factor = result["profit_factor"]
    print("Profit factor:", "inf" if profit_factor == float("inf") else round(profit_factor, 2))
    print("Average win:", round(result["average_win"], 2))
    print("Average loss:", round(result["average_loss"], 2))
    print("Expectancy per trade:", round(result["expectancy"], 2))
    print("Estimated fees:", round(result["total_fees"], 2))
    print("Max drawdown (approx):", round(100 * result["max_drawdown"], 2), "%")


def evaluate_backtest(
    dataframe,
    slippage_rate=DEFAULT_SLIPPAGE_RATE,
    fee_rate=FEE_RATE,
    atr_stop_multiple=2.0,
    atr_trailing_multiple=3.0,
    trades_output="backtest_trades.csv",
    validation_split=0.3,
    validation_windows=3,
    adx_minimum=20.0,
    minimum_atr_percent=0.5,
):
    result = calculate_backtest(
        dataframe=dataframe,
        slippage_rate=slippage_rate,
        fee_rate=fee_rate,
        atr_stop_multiple=atr_stop_multiple,
        atr_trailing_multiple=atr_trailing_multiple,
        adx_minimum=adx_minimum,
        minimum_atr_percent=minimum_atr_percent,
    )
    print_backtest_result("FULL PERIOD", result)
    write_trade_log(trades_output, result["trades"])

    validation_start = int(len(dataframe) * (1 - validation_split))
    validation_size = len(dataframe) - validation_start
    window_size = validation_size // validation_windows if validation_windows else 0
    for window_index in range(validation_windows):
        window_start = validation_start + window_index * window_size
        window_end = (
            len(dataframe)
            if window_index == validation_windows - 1
            else window_start + window_size
        )
        validation_data = dataframe.iloc[
            max(0, window_start - WARMUP_BARS):window_end
        ].reset_index(drop=True)
        if validation_split > 0 and len(validation_data) > WARMUP_BARS:
            validation_result = calculate_backtest(
                dataframe=validation_data,
                slippage_rate=slippage_rate,
                fee_rate=fee_rate,
                atr_stop_multiple=atr_stop_multiple,
                atr_trailing_multiple=atr_trailing_multiple,
                adx_minimum=adx_minimum,
                minimum_atr_percent=minimum_atr_percent,
            )
            print_backtest_result(
                f"OUT-OF-SAMPLE WINDOW {window_index + 1}", validation_result
            )

    print("Trade log:", trades_output)
    print("Note: includes configured fees/slippage, but remains an estimate.")


def backtest(
    symbol=DEFAULT_SYMBOL,
    days=None,
    timeframe=DEFAULT_TIMEFRAME,
    slippage_rate=DEFAULT_SLIPPAGE_RATE,
    fee_rate=FEE_RATE,
    atr_stop_multiple=2.0,
    atr_trailing_multiple=3.0,
    trades_output="backtest_trades.csv",
    validation_split=0.3,
    validation_windows=3,
    adx_minimum=20.0,
    minimum_atr_percent=0.5,
):
    if days is None:
        dataframe = get_crypto(symbol, timeframe=timeframe)
    else:
        dataframe = get_crypto_history(symbol, days, timeframe)
    dataframe = dataframe.iloc[:-1].reset_index(drop=True)
    evaluate_backtest(
        dataframe,
        slippage_rate,
        fee_rate,
        atr_stop_multiple,
        atr_trailing_multiple,
        trades_output,
        validation_split,
        validation_windows,
        adx_minimum,
        minimum_atr_percent,
    )


def load_forex_history(symbol, days, timeframe, terminal_path=None):
    try:
        import MetaTrader5 as mt5
    except ImportError as error:
        raise RuntimeError("MetaTrader5 is required for forex backtests") from error

    initialized = (
        mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
    )
    if not initialized:
        raise RuntimeError("MT5 not connected: " + str(mt5.last_error()))

    timeframe_map = {
        "15m": mt5.TIMEFRAME_M15,
        "30m": mt5.TIMEFRAME_M30,
        "1h": mt5.TIMEFRAME_H1,
        "4h": mt5.TIMEFRAME_H4,
        "1d": mt5.TIMEFRAME_D1,
    }
    if timeframe not in timeframe_map:
        mt5.shutdown()
        raise ValueError("Forex timeframe must be one of: 15m, 30m, 1h, 4h, 1d")
    if not mt5.symbol_select(symbol, True):
        mt5.shutdown()
        raise RuntimeError(f"MT5 symbol not found: {symbol}")

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, timeframe_map[timeframe], date_from, date_to)
    error = mt5.last_error()
    mt5.shutdown()
    if rates is None or len(rates) <= WARMUP_BARS:
        raise RuntimeError(f"Insufficient MT5 history for {symbol}: {error}")

    dataframe = pd.DataFrame(rates)
    dataframe["time"] = pd.to_datetime(dataframe["time"], unit="s", utc=True)
    return indicators(dataframe).iloc[:-1].reset_index(drop=True)


def forex_backtest(
    symbol,
    days,
    timeframe,
    terminal_path,
    slippage_rate,
    fee_rate,
    atr_stop_multiple,
    atr_trailing_multiple,
    trades_output,
    validation_split,
    validation_windows,
    adx_minimum,
    minimum_atr_percent,
):
    dataframe = load_forex_history(symbol, days, timeframe, terminal_path)
    evaluate_backtest(
        dataframe,
        slippage_rate,
        fee_rate,
        atr_stop_multiple,
        atr_trailing_multiple,
        trades_output,
        validation_split,
        validation_windows,
        adx_minimum,
        minimum_atr_percent,
    )


def append_signal(output_path, market, bar_time, action, price):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if file.tell() == 0:
            writer.writerow(["market", "closed_bar_utc", "signal", "price"])
        writer.writerow([market, str(bar_time), action, round(price, 6)])


def paper(crypto_symbol, forex_symbol, output_path, interval, terminal_path=None):
    try:
        import MetaTrader5 as mt5
    except ImportError as error:
        raise RuntimeError("MetaTrader5 is required for paper mode") from error

    initialized = (
        mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
    )
    if not initialized:
        raise RuntimeError("MT5 not connected: " + str(mt5.last_error()))

    account = mt5.account_info()
    if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        mt5.shutdown()
        raise RuntimeError("STOP: switch MT5 to a DEMO account")

    seen = set()
    print("PAPER SIGNAL MODE ONLY. No orders are placed.")
    try:
        while True:
            try:
                dataframe = get_crypto(crypto_symbol, limit=250)
                previous_row = dataframe.iloc[-3]
                row = dataframe.iloc[-2]
                items = [
                    (
                        crypto_symbol,
                        row.time,
                        signal(previous_row, row),
                        float(row.close),
                    )
                ]

                if mt5.symbol_select(forex_symbol, True):
                    rates = mt5.copy_rates_from_pos(
                        forex_symbol, mt5.TIMEFRAME_M15, 0, 250
                    )
                    if rates is not None and len(rates) >= 30:
                        forex = pd.DataFrame(rates)
                        forex["time"] = pd.to_datetime(forex.time, unit="s", utc=True)
                        forex = indicators(forex)
                        forex_previous_row = forex.iloc[-3]
                        forex_row = forex.iloc[-2]
                        items.append(
                            (
                                forex_symbol,
                                forex_row.time,
                                signal(
                                    forex_previous_row,
                                    forex_row,
                                    minimum_atr_percent=0.05,
                                ),
                                float(forex_row.close),
                            )
                        )

                for market, bar_time, action, price in items:
                    key = (market, str(bar_time))
                    if key not in seen:
                        print(market, bar_time, action, price)
                        append_signal(output_path, market, bar_time, action, price)
                        seen.add(key)
            except Exception as error:
                print("Scan error:", error)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        mt5.shutdown()


def parse_args():
    parser = argparse.ArgumentParser(description="Educational backtest and paper scanner")
    parser.add_argument("mode", choices=["backtest", "forex-backtest", "paper"])
    parser.add_argument("--crypto-symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--forex-symbol", default=DEFAULT_FOREX_SYMBOL)
    parser.add_argument("--output", default="signals.csv")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument(
        "--slippage-bps",
        type=float,
        help="estimated slippage per fill; defaults to 5 for crypto and 1 for forex",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        help="commission per fill; defaults to 10 for crypto and 0 for forex",
    )
    parser.add_argument("--atr-stop", type=float, default=2.0)
    parser.add_argument("--atr-trail", type=float, default=3.0)
    parser.add_argument("--trades-output", default="backtest_trades.csv")
    parser.add_argument("--validation-split", type=float, default=0.3)
    parser.add_argument("--validation-windows", type=int, default=3)
    parser.add_argument("--adx-min", type=float, default=20.0)
    parser.add_argument(
        "--min-atr-percent",
        type=float,
        help="minimum ATR as percent of price; defaults to 0.5 crypto, 0.05 forex",
    )
    parser.add_argument("--mt5-path", help="full path to terminal64.exe")
    parser.add_argument(
        "--days",
        type=int,
        help="historical days to download for backtest mode",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.days is not None and args.days < 1:
        raise SystemExit("--days must be at least 1")
    if args.slippage_bps is not None and args.slippage_bps < 0:
        raise SystemExit("--slippage-bps cannot be negative")
    if args.fee_bps is not None and args.fee_bps < 0:
        raise SystemExit("--fee-bps cannot be negative")
    if args.fee_bps is not None and args.fee_bps >= 10_000:
        raise SystemExit("--fee-bps must be below 10000")
    if args.slippage_bps is not None and args.slippage_bps >= 10_000:
        raise SystemExit("--slippage-bps must be below 10000")
    if args.atr_stop <= 0 or args.atr_trail <= 0:
        raise SystemExit("ATR multiples must be positive")
    if not 0 <= args.validation_split < 1:
        raise SystemExit("--validation-split must be between 0 and 1")
    if args.validation_windows < 1:
        raise SystemExit("--validation-windows must be at least 1")
    if args.adx_min < 0 or (
        args.min_atr_percent is not None and args.min_atr_percent < 0
    ):
        raise SystemExit("signal filter thresholds cannot be negative")
    if args.mode == "backtest":
        slippage_rate = (args.slippage_bps if args.slippage_bps is not None else 5) / 10_000
        fee_rate = (args.fee_bps if args.fee_bps is not None else 10) / 10_000
        minimum_atr_percent = (
            args.min_atr_percent if args.min_atr_percent is not None else 0.5
        )
        backtest(
            symbol=args.crypto_symbol,
            days=args.days,
            timeframe=args.timeframe,
            slippage_rate=slippage_rate,
            fee_rate=fee_rate,
            atr_stop_multiple=args.atr_stop,
            atr_trailing_multiple=args.atr_trail,
            trades_output=args.trades_output,
            validation_split=args.validation_split,
            validation_windows=args.validation_windows,
            adx_minimum=args.adx_min,
            minimum_atr_percent=minimum_atr_percent,
        )
    elif args.mode == "forex-backtest":
        slippage_rate = (args.slippage_bps if args.slippage_bps is not None else 1) / 10_000
        fee_rate = (args.fee_bps if args.fee_bps is not None else 0) / 10_000
        minimum_atr_percent = (
            args.min_atr_percent if args.min_atr_percent is not None else 0.05
        )
        forex_backtest(
            symbol=args.forex_symbol,
            days=args.days or 730,
            timeframe=args.timeframe,
            terminal_path=args.mt5_path,
            slippage_rate=slippage_rate,
            fee_rate=fee_rate,
            atr_stop_multiple=args.atr_stop,
            atr_trailing_multiple=args.atr_trail,
            trades_output=args.trades_output,
            validation_split=args.validation_split,
            validation_windows=args.validation_windows,
            adx_minimum=args.adx_min,
            minimum_atr_percent=minimum_atr_percent,
        )
    else:
        paper(
            args.crypto_symbol,
            args.forex_symbol,
            args.output,
            args.interval,
            args.mt5_path,
        )


if __name__ == "__main__":
    main()
