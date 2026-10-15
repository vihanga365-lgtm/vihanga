# Auto Trading Bot Starter

An educational Python project that downloads 15-minute Binance candles, calculates EMA(9), EMA(21), and RSI(14), backtests a simple long-only strategy, and scans Binance plus an MT5 demo account for paper signals.

It does **not** place real or demo orders. A signal is not an executed trade or a profit forecast.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer
- MetaTrader 5 with a demo account (paper mode only)

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run Python directly with `.\.venv\Scripts\python.exe`.

## Run

Backtest BTC/USDT using public Binance data:

```powershell
python bot.py backtest
```

Download a longer history using paginated requests:

```powershell
python bot.py backtest --days 90
```

Test a slower timeframe or change the assumed slippage:

```powershell
python bot.py backtest --days 365 --timeframe 1h --slippage-bps 5
```

The strategy enters only on an EMA(20)/EMA(50) bullish crossover when price is above a rising EMA(200), ADX confirms trend strength, ATR confirms sufficient volatility, and RSI is between 50 and 70. It exits on a bearish crossover, a close below EMA(200), RSI reaching 75, or an ATR protective stop. The trailing stop is updated using completed-bar data.

Backtests write each completed trade to `backtest_trades.csv` and report average win, average loss, expectancy, profit factor, fees, drawdown, an exposure-matched buy-and-hold baseline, and three rolling windows across the final 30% out-of-sample period.

ATR and validation settings are configurable:

```powershell
python bot.py backtest --days 730 --timeframe 4h --atr-stop 2 --atr-trail 3 --adx-min 20 --min-atr-percent 0.5 --validation-split 0.3 --validation-windows 3
```

Start the signal-only scanner after opening MT5 and signing in to a demo account:

```powershell
python bot.py paper
```

If your broker uses a symbol suffix, pass its exact Market Watch name:

```powershell
python bot.py paper --forex-symbol EURUSDm
```

Backtest historical forex candles from the connected MT5 terminal:

```powershell
python bot.py forex-backtest --forex-symbol EURUSD --days 730 --timeframe 4h --trades-output forex_trades.csv
```

Forex mode defaults to zero commission and one basis point of slippage per fill. Override these assumptions with `--fee-bps` and `--slippage-bps` to match your broker. If MT5 is installed in a nonstandard directory, pass `--mt5-path "C:\path\to\terminal64.exe"`.

Signals are appended to `signals.csv`. Other options are available through `python bot.py --help`.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Safety

- The program uses public Binance market data and does not require API keys.
- Paper mode refuses to run unless MT5 reports a demo account.
- Never add exchange secrets to source code or commit them to Git.
- Before considering live trading, add position sizing, maximum loss rules, spread and slippage modelling, order reconciliation, minimum-notional checks, persistent state, and a kill switch.
