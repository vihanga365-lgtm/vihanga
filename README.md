# Crow Trading Bot for Windows

Desktop app: open CrowTradingBot.exe in the project folder, or run python desktop.py.

## Get the application from Git

On Windows, install 64-bit Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/) and enable **Add Python to PATH**. Clone or download this repository, then double-click **setup.bat**. It installs the dependencies and builds **CrowTradingBot.exe** in the project folder. An internet connection is required for the first build.

Once the build finishes, double-click **CrowTradingBot.exe** to use the app. Run `setup.bat` again after pulling code changes to rebuild it; close the app before rebuilding. Inno Setup is only needed for creating a distributable installer using `setup.ps1`.

The Profit / Loss panel shows closed, open and total P/L separately for crypto and forex, refreshing on each scan. Demo totals persist across restarts and include simulated fees (open P/L includes an estimated exit fee). Live crypto shows gross P/L for app-tracked trades recorded with this version, excluding exchange fees; live forex P/L is currently unavailable. Prices and totals remain at the last update when stopped. Rebuild the executable after changing the source.

The desktop bot automatically scans completed candles and places long-only entries and exits using the existing EMA(20/50/200), RSI, ADX and ATR strategy. Binance spot crypto and MT5 forex can run separately or together. Signals do not guarantee returns. The existing command-line backtest/scanner remains available below.

## Desktop quick start

1. Open the app. Choose **Demo** and enable crypto, forex, or both.
2. For forex, install/open MT5, sign into your broker and use the exact Market Watch symbol. MT5 supplies forex prices even in local demo mode.
3. Set symbols, timeframe, scan interval, crypto quote allocation, forex lots and strategy filters in the tabs.
4. Click **Start bot**. Activity shows prices, signals, orders and errors. **Stop bot** stops scanning after the current operation; it does not liquidate positions or cancel an order already being submitted.

Demo uses live market data and simulated fills, not Binance testnet or MT5 demo orders. It starts with a persistent virtual USD 10,000 per market, assumes 5 bps slippage and 10 bps crypto commission per fill. Forex simulation supports USD profit currency only; its reserve is not a broker margin model. Forex needs a fresh broker tick and sufficient chart history.

For **Live**, enter Binance trade-enabled API credentials for crypto (kept only in memory), and/or sign MT5 into a real account for forex. Confirm automatic real-money trading when starting. Crypto amount is in the pair quote currency; forex size is in broker lots. The app opens buys and closes its own longs; it does not short. Avoid manual trading in symbols managed by the bot. Broker/exchange restrictions may reject orders.

Initial stops use the configured ATR multiple. Forex live entries include a broker-side stop. Crypto stops are checked locally each scan: the app and internet must remain running. There is no trailing stop or daily loss limit in the desktop engine. Stop/closing the app leaves live positions open, and gaps can exceed a stop price.

Settings, positions, processed candles and activity logs are stored under %LOCALAPPDATA%/CrowTradingBot. Demo and live states are separate. Restart resumes positions and avoids processing the same candle twice. Do not change account or symbols while positions are open. Ambiguous live order results halt the bot with a persisted pending record: reconcile orders/holdings with the broker before manually repairing state. Never clear state to bypass an unresolved order.

## Build the Windows executable

On Windows with Python and dependencies installed:

```powershell
.\build.ps1
```

Output: CrowTradingBot.exe in the project folder. Python is bundled; the receiving PC still needs internet and an installed MT5 terminal for forex. This is an unsigned portable executable. Run it from a writable local directory. Launching a second copy is blocked.

## Build a Windows setup installer

Install [Inno Setup 6](https://jrsoftware.org/isdl.php), then run:

```powershell
.\setup.ps1 -Version 1.0.0
```

This builds the app using `build.ps1` and creates `CrowTradingBot-Setup-1.0.0.exe` in the project folder. The installer adds a Start menu shortcut, offers an optional desktop shortcut, and supports uninstall through Windows Settings. It installs for the current user without administrator rights. Python is bundled; MT5 must be installed separately for forex.

To package the existing application without rebuilding:

```powershell
.\setup.ps1 -Version 1.0.0 -ExecutablePath .\CrowTradingBot.exe
```

If Inno Setup is installed elsewhere, add `-CompilerPath 'C:\path\to\ISCC.exe'`. Close the installed app yourself before upgrading; setup does not automatically stop the bot or start trading. Uninstall preserves saved settings and trading state in `%LOCALAPPDATA%/CrowTradingBot`.

## Verification

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python desktop.py --smoke-test
```

Live orders require account-specific testing; automated tests simulate execution and never use real credentials.

---

# Auto Trading Bot Starter

The original command-line interface downloads Binance candles, calculates EMA(20/50/200), RSI(14), ADX and ATR, backtests a long-only strategy, and scans Binance plus an MT5 demo account for paper signals.

The CLI scanner does **not** place orders. The desktop app described above adds automatic simulated and live execution.

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

## CLI scanner notes

- The CLI scanner uses public Binance market data and does not require API keys.
- Paper mode refuses to run unless MT5 reports a demo account.
- Never add exchange secrets to source code or commit them to Git.
- The desktop bot adds live execution, sizing, persistent state and stop controls; it still requires broker-specific validation and has the limitations documented above.
