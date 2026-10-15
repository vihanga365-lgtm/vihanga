import pandas as pd

import bot
from bot import append_signal, candles_to_dataframe, get_crypto_history, signal


def test_candles_to_dataframe_adds_indicators():
    candles = [
        [1_700_000_000_000 + index * 900_000, 100, 102, 99, 100 + index, 10]
        for index in range(30)
    ]

    dataframe = candles_to_dataframe(candles)

    assert {"ema20", "ema50", "ema200", "rsi", "atr14", "adx14"}.issubset(
        dataframe.columns
    )
    assert str(dataframe.time.dt.tz) == "UTC"


def test_signal_rules():
    previous = pd.Series({"ema20": 1, "ema50": 2, "ema200": 89})
    buy = pd.Series(
        {
            "rsi": 55, "ema20": 3, "ema50": 2, "ema200": 90,
            "close": 100, "atr14": 1, "adx14": 25,
        }
    )
    sell = pd.Series(
        {
            "rsi": 50, "ema20": 1, "ema50": 2, "ema200": 90,
            "close": 100, "atr14": 1, "adx14": 25,
        }
    )
    no_trend = buy.copy()
    no_trend["ema200"] = 110

    assert signal(previous, buy) == "BUY"
    assert signal(
        pd.Series({"ema20": 3, "ema50": 2, "ema200": 89}), sell
    ) == "SELL"
    assert signal(previous, no_trend) == "SELL"


def test_append_signal_writes_one_header(tmp_path):
    output = tmp_path / "logs" / "signals.csv"
    timestamp = pd.Timestamp("2026-01-01", tz="UTC")

    append_signal(output, "BTC/USDT", timestamp, "BUY", 100.1234567)
    append_signal(output, "BTC/USDT", timestamp, "WAIT", 101)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "market,closed_bar_utc,signal,price"
    assert len(lines) == 3


def test_crypto_history_paginates_and_removes_duplicates(monkeypatch):
    base_timestamp = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000) - 90 * 86_400_000
    first_batch = [
        [base_timestamp + index * 900_000, 100, 101, 99, 100, 1]
        for index in range(1000)
    ]
    second_batch = [
        [base_timestamp + 999 * 900_000, 100, 101, 99, 100, 1],
        [base_timestamp + 1000 * 900_000, 100, 101, 99, 100, 1],
    ]
    responses = iter([first_batch, second_batch])
    monkeypatch.setattr(bot.EXCHANGE, "fetch_ohlcv", lambda *args, **kwargs: next(responses))

    dataframe = get_crypto_history("BTC/USDT", 90)

    assert len(dataframe) == 1001
    assert dataframe.time.is_monotonic_increasing
