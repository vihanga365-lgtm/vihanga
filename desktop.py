"""Windows desktop launcher for automatic crypto and forex trading."""
import json
import hashlib
import math
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import ccxt
import pandas as pd
import bot

DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CrowTradingBot'
MAGIC = 487211


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError('Amounts and intervals must be finite and greater than zero.')
    return number


class Engine:
    def __init__(self, config, emit, stop):
        self.c, self.emit, self.stop = config, emit, stop
        self.mt5 = None
        self.exchange = ccxt.binance({'enableRateLimit': True, 'timeout': 15000,
            'apiKey': config.get('key', ''), 'secret': config.get('secret', '')})
        self.path = DATA / ('live-state.json' if config['mode'] == 'Live' else 'demo-state.json')
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.state.setdefault('positions', {})
        self.state.setdefault('seen', {})
        self.state.setdefault('cash', {'Crypto': 10000.0, 'Forex': 10000.0})
        if config['mode'] == 'Demo':
            for key, position in self.state['positions'].items():
                position.setdefault('entry_fee', position['entry'] * position['quantity'] * 0.001
                    if key.startswith('Crypto:') else 0.0)
        if self.state.get('pending'):
            raise RuntimeError('An earlier order has an uncertain result. Reconcile it in your broker/exchange and inspect the state file before restarting: ' + str(self.path))

    def save(self):
        DATA.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, indent=2), encoding='utf-8')
        temp.replace(self.path)

    def log(self, text):
        text = datetime.now(timezone.utc).strftime('%H:%M:%S UTC  ') + text
        DATA.mkdir(parents=True, exist_ok=True)
        with (DATA / 'activity.log').open('a', encoding='utf-8') as file:
            file.write(text + '\n')
        self.emit(text)

    def publish_performance(self):
        rows = []
        live = self.c['mode'] == 'Live'
        for market in ('Crypto', 'Forex'):
            positions = [p for k, p in self.state['positions'].items() if k.startswith(market + ':')]
            if live:
                realized = self.state.get('realized_pnl', {}).get(market, 0.0)
            else:
                realized = self.state['cash'][market] + sum(
                    p['reserved'] + p.get('entry_fee', 0.0) for p in positions) - 10000.0
            unrealized = 0.0
            priced = True
            for key, position in self.state['positions'].items():
                if not key.startswith(market + ':'):
                    continue
                mark = self.state.get('marks', {}).get(key)
                if mark is None or (live and market == 'Forex'):
                    priced = False
                    continue
                value = mark * position['quantity'] * position['contract']
                unrealized += (mark - position['entry']) * position['quantity'] * position['contract']
                if not live:
                    unrealized -= position.get('entry_fee', 0.0)
                    unrealized -= value * (0.001 if market == 'Crypto' else 0)
            rows.append((market, None if live and market == 'Forex' else realized,
                unrealized if priced else None,
                ('Broker currency' if live else 'USD') if market == 'Forex'
                else self.c.get('crypto_symbol', 'BTC/USDT').split('/')[-1]))
        self.emit({'performance': rows, 'mode': self.c['mode'],
            'updated': datetime.now(timezone.utc).strftime('%H:%M:%S UTC')})

    def connect(self):
        enabled_keys = set()
        if self.c['crypto']:
            enabled_keys.add('Crypto:' + self.c['crypto_symbol'])
        if self.c['forex']:
            enabled_keys.add('Forex:' + self.c['forex_symbol'])
        if set(self.state['positions']) - enabled_keys:
            raise RuntimeError('Resume the markets and symbols with open positions before changing settings.')
        if self.c['crypto']:
            if self.c['mode'] == 'Live' and not (self.c['key'] and self.c['secret']):
                raise ValueError('Live crypto requires a Binance API key and secret.')
            self.exchange.load_markets()
            market = self.exchange.market(self.c['crypto_symbol'])
            if not market['spot']:
                raise ValueError('Only Binance spot markets are supported.')
            if self.c['mode'] == 'Live':
                self.exchange.fetch_balance()
                identity = hashlib.sha256(self.c['key'].encode()).hexdigest()
                if self.state['positions'] and self.state.get('crypto_account') not in (None, identity):
                    raise RuntimeError('Resume with the same Binance API key while positions are open.')
                self.state['crypto_account'] = identity
        if self.c['forex']:
            import MetaTrader5 as mt5
            self.mt5 = mt5
            ok = mt5.initialize(path=self.c['mt5_path']) if self.c['mt5_path'] else mt5.initialize()
            if not ok:
                raise RuntimeError('Open MT5 and sign in first: ' + str(mt5.last_error()))
            account = mt5.account_info()
            if account is None:
                raise RuntimeError('MT5 account unavailable.')
            self.account = account.login
            identity = f'{account.server}:{account.login}'
            if self.state['positions'] and self.state.get('forex_account') not in (None, identity):
                raise RuntimeError('Resume the same MT5 account while positions are open.')
            self.state['forex_account'] = identity
            if self.c['mode'] == 'Live' and account.trade_mode != mt5.ACCOUNT_TRADE_MODE_REAL:
                raise RuntimeError('Live mode requires an MT5 real account.')
            if not mt5.symbol_select(self.c['forex_symbol'], True):
                raise ValueError('Forex symbol not found in MT5 Market Watch.')
            # Local demo simulation can read prices from either type of MT5 account.
            if self.c['mode'] == 'Live':
                positions = mt5.positions_get(symbol=self.c['forex_symbol'])
                if positions is None:
                    raise RuntimeError('Cannot reconcile MT5 positions.')
                if any(p.magic != MAGIC for p in positions):
                    raise RuntimeError('Use a forex symbol with no other positions to avoid netting unrelated trades.')
                tracked = self.state['positions'].get('Forex:' + self.c['forex_symbol'])
                if any(p.magic == MAGIC for p in positions) and not tracked:
                    raise RuntimeError('Untracked bot position in MT5. Reconcile it before starting.')
        self.save()
        self.publish_performance()
        self.log(self.c['mode'] + ' automatic trading started. Strategy: long-only EMA/RSI/ADX/ATR.')

    def data(self, market, symbol):
        if market == 'Crypto':
            frame = bot.candles_to_dataframe(self.exchange.fetch_ohlcv(symbol, self.c['timeframe'], limit=300))
            tick = self.exchange.fetch_ticker(symbol)
            price = float(tick['last'])
            quote_time = tick.get('timestamp')
            if quote_time and datetime.now(timezone.utc).timestamp() * 1000 - quote_time > 120000:
                raise RuntimeError('Crypto quote is stale.')
        else:
            mt5 = self.mt5
            account = mt5.account_info()
            if account is None or account.login != self.account:
                raise RuntimeError('MT5 account changed. Stop and reconnect.')
            mapping = {'15m': mt5.TIMEFRAME_M15, '30m': mt5.TIMEFRAME_M30,
                '1h': mt5.TIMEFRAME_H1, '4h': mt5.TIMEFRAME_H4, '1d': mt5.TIMEFRAME_D1}
            rates = mt5.copy_rates_from_pos(symbol, mapping[self.c['timeframe']], 0, 300)
            if rates is None or len(rates) < 202:
                raise RuntimeError('Load at least 202 candles in MT5.')
            frame = pd.DataFrame(rates)
            frame['time'] = pd.to_datetime(frame['time'], unit='s', utc=True)
            frame = bot.indicators(frame)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None or datetime.now(timezone.utc).timestamp() - tick.time > 120:
                raise RuntimeError('Forex quote is stale or market is closed.')
            price = float(tick.bid)
        if len(frame) < 202 or not math.isfinite(price) or price <= 0:
            raise RuntimeError('Insufficient candles or invalid price.')
        return frame, price

    def forex_order(self, symbol, quantity, selling, stop_price, ticket=None):
        mt5 = self.mt5
        account = mt5.account_info()
        if account is None or account.login != self.account or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_REAL:
            raise RuntimeError('MT5 real account changed or unavailable.')
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise RuntimeError('MT5 quote unavailable.')
        steps = quantity / info.volume_step
        if not info.volume_min <= quantity <= info.volume_max or abs(steps - round(steps)) > 1e-6:
            raise ValueError('Forex lots must match the broker minimum, maximum and volume step.')
        positions = mt5.positions_get(symbol=symbol)
        if positions is None or any(p.magic != MAGIC for p in positions):
            raise RuntimeError('Cannot trade alongside other positions in this forex symbol.')
        filling = mt5.ORDER_FILLING_FOK if info.filling_mode & 1 else mt5.ORDER_FILLING_IOC
        request = {'action': mt5.TRADE_ACTION_DEAL, 'symbol': symbol, 'volume': quantity,
            'type': mt5.ORDER_TYPE_SELL if selling else mt5.ORDER_TYPE_BUY,
            'price': tick.bid if selling else tick.ask, 'deviation': 20,
            'magic': MAGIC, 'comment': 'Crow automatic bot',
            'type_time': mt5.ORDER_TIME_GTC, 'type_filling': filling}
        if selling:
            request['position'] = ticket
        else:
            request['sl'] = round(stop_price, info.digits)
        check = mt5.order_check(request)
        if check is None or check.retcode != 0:
            raise RuntimeError('MT5 order check failed; inspect broker settings.')
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError('MT5 order result requires reconciliation; no automatic retry.')
        return result

    def trade(self, market, symbol, action, price, atr):
        key = market + ':' + symbol
        position = self.state['positions'].get(key)
        selling = action == 'SELL'
        if selling and not position or not selling and position:
            return
        live = self.c['mode'] == 'Live'
        if self.stop.is_set() and not selling:
            return
        quantity = position['quantity'] if selling else (
            self.c['allocation'] / price if market == 'Crypto' else self.c['lots'])
        if not selling and self.state['cash'][market] < self.c['allocation'] * 1.002 and not live:
            self.log(market + ': demo allocation exceeds available cash.')
            return
        stop_price = price - self.c['atr_stop'] * atr
        if not selling and (not math.isfinite(stop_price) or stop_price <= 0):
            raise ValueError('Invalid ATR stop price.')
        if live:
            if market == 'Crypto':
                quantity = float(self.exchange.amount_to_precision(symbol, quantity))
                limits = self.exchange.market(symbol)['limits']
                for value, limit in [(quantity, limits['amount']), (quantity * price, limits['cost'])]:
                    if value <= 0 or (limit.get('min') and value < limit['min']) or (limit.get('max') and value > limit['max']):
                        raise ValueError('Crypto order does not meet exchange amount/notional limits.')
            # Persist intent before submitting: never retry an ambiguous order automatically.
            self.state['pending'] = {'market': market, 'symbol': symbol, 'side': action, 'quantity': quantity}
            self.save()
            if market == 'Crypto':
                order = self.exchange.create_order(symbol, 'market', action.lower(), quantity)
                if order.get('status') != 'closed' or not order.get('filled'):
                    raise RuntimeError('Exchange order is not confirmed filled. Reconcile before restarting.')
                price = float(order.get('average') or price)
                quantity = float(order['filled'])
                if selling and quantity < position['quantity'] * 0.999:
                    raise RuntimeError('Partial exit requires reconciliation.')
                if not selling:
                    base = self.exchange.market(symbol)['base']
                    quantity -= sum(float(f['cost']) for f in (order.get('fees') or []) if f.get('currency') == base)
            else:
                result = self.forex_order(symbol, quantity, selling, stop_price,
                    position.get('ticket') if selling else None)
                price = result.price
                if not selling:
                    positions = self.mt5.positions_get(symbol=symbol)
                    owned = [p for p in (positions or []) if p.magic == MAGIC]
                    if len(owned) != 1:
                        raise RuntimeError('MT5 fill requires reconciliation.')
                    ticket = owned[0].ticket
        else:
            # Demo assumes USD-quoted forex; contract size is read from the broker.
            contract = self.mt5.symbol_info(symbol).trade_contract_size if market == 'Forex' else 1
            if market == 'Forex' and self.mt5.symbol_info(symbol).currency_profit != 'USD':
                raise ValueError('Demo forex currently requires a USD profit currency, e.g. EURUSD.')
            price *= 0.9995 if selling else 1.0005
        if selling:
            if not live:
                pnl = (price - position['entry']) * quantity * position['contract']
                self.state['cash'][market] += position['reserved'] + pnl - price * quantity * position['contract'] * (0.001 if market == 'Crypto' else 0)
            elif market == 'Crypto':
                totals = self.state.setdefault('realized_pnl', {})
                totals[market] = totals.get(market, 0.0) + (price - position['entry']) * quantity
            del self.state['positions'][key]
        else:
            self.state['positions'][key] = {'quantity': quantity, 'entry': price,
                'stop': stop_price, 'ticket': ticket if live and market == 'Forex' else None,
                'contract': contract if not live else 1, 'reserved': self.c['allocation']}
            self.state['positions'][key]['entry_fee'] = price * quantity * 0.001 if not live and market == 'Crypto' else 0.0
            if not live:
                self.state['cash'][market] -= self.c['allocation'] + (price * quantity * 0.001 if market == 'Crypto' else 0)
        self.state.pop('pending', None)
        self.save()
        self.publish_performance()
        self.log(f'{market} {symbol}: {action} {quantity:.8g} @ {price:.8g} ({self.c["mode"]})')

    def scan(self, market, symbol):
        frame, price = self.data(market, symbol)
        previous, row = frame.iloc[-3], frame.iloc[-2]
        key = market + ':' + symbol
        self.state.setdefault('marks', {})[key] = price
        self.publish_performance()
        position = self.state['positions'].get(key)
        if position and market == 'Forex' and self.c['mode'] == 'Live':
            positions = self.mt5.positions_get(ticket=position['ticket'])
            if positions is None:
                raise RuntimeError('Cannot reconcile MT5 position.')
            if not positions:
                del self.state['positions'][key]
                self.save()
                position = None
                self.log('Forex position closed by broker or user.')
        if position and price <= position['stop']:
            self.trade(market, symbol, 'SELL', price, float(row.atr14))
            self.state['seen'][key] = str(row.time)
            self.save()
            return
        if self.state['seen'].get(key) == str(row.time):
            return
        action = bot.signal(previous, row, self.c['adx'], self.c['crypto_atr'] if market == 'Crypto' else self.c['forex_atr'])
        self.log(f'{market} {symbol}: {price:.8g} | {action} | closed candle {row.time}')
        self.trade(market, symbol, action, price, float(row.atr14)) if action != 'WAIT' else None
        self.state['seen'][key] = str(row.time)
        self.save()

    def run(self):
        try:
            self.connect()
            while not self.stop.is_set():
                for market, enabled, symbol in [('Crypto', self.c['crypto'], self.c['crypto_symbol']), ('Forex', self.c['forex'], self.c['forex_symbol'])]:
                    if enabled and not self.stop.is_set():
                        self.scan(market, symbol)
                self.stop.wait(self.c['interval'])
        finally:
            if self.mt5:
                self.mt5.shutdown()
            self.log('Bot stopped. Existing live positions remain at the broker/exchange.')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Crow Trading Bot — Crypto & Forex')
        self.geometry('980x780')
        self.minsize(860, 680)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.vars = {}
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TButton', padding=8)
        style.configure('TLabel', font=('Segoe UI', 10))
        main = ttk.Frame(self, padding=20)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='Crow Trading Bot', font=('Segoe UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(main, text='Automatic strategy trading • Binance spot + MetaTrader 5 forex').pack(anchor='w', pady=(0, 12))
        tabs = ttk.Notebook(main)
        tabs.pack(fill='x')
        settings = ttk.Frame(tabs, padding=12)
        strategy = ttk.Frame(tabs, padding=12)
        tabs.add(settings, text='Markets & connection')
        tabs.add(strategy, text='Strategy & sizing')
        defaults = {'mode': 'Demo', 'crypto_symbol': 'BTC/USDT', 'forex_symbol': 'EURUSD',
            'mt5_path': '', 'timeframe': '15m', 'interval': '30', 'allocation': '100',
            'lots': '0.01', 'atr_stop': '2', 'adx': '20', 'crypto_atr': '0.5', 'forex_atr': '0.05'}
        settings_path = DATA / 'settings.json'
        try:
            saved = json.loads(settings_path.read_text()) if settings_path.exists() else {}
            defaults.update({k: v for k, v in saved.items() if k in defaults and k != 'mode'})
        except (ValueError, OSError):
            pass
        fields = [('mode', 'Trading mode', ['Demo', 'Live']), ('crypto_symbol', 'Binance spot pair', None),
            ('forex_symbol', 'MT5 forex symbol', None), ('key', 'Binance API key', None),
            ('secret', 'Binance API secret', None), ('mt5_path', 'MT5 terminal64.exe path (optional)', None)]
        for i, (name, label, choices) in enumerate(fields):
            self.field(settings, i, name, label, defaults.get(name, ''), choices)
        self.vars['crypto'] = tk.BooleanVar(value=True)
        self.vars['forex'] = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text='Enable crypto', variable=self.vars['crypto']).grid(row=6, column=0, sticky='w', pady=8)
        ttk.Checkbutton(settings, text='Enable forex (MT5 must be open)', variable=self.vars['forex']).grid(row=6, column=1, sticky='w')
        strategy_fields = [('timeframe', 'Candle timeframe', ['15m', '30m', '1h', '4h', '1d']),
            ('interval', 'Scan interval (seconds)', None), ('allocation', 'Crypto quote amount / demo forex reserve', None),
            ('lots', 'Forex order size (lots)', None), ('atr_stop', 'Initial stop distance (ATR multiple)', None),
            ('adx', 'Minimum ADX', None), ('crypto_atr', 'Crypto minimum ATR (%)', None), ('forex_atr', 'Forex minimum ATR (%)', None)]
        for i, (name, label, choices) in enumerate(strategy_fields):
            self.field(strategy, i, name, label, defaults[name], choices)
        ttk.Label(main, text='Demo uses live prices and local simulated trades ($10,000 per market).\nLive places real orders automatically. Crypto stops require this app and internet to stay running.\nStop halts the bot; it does not close positions. Forex SELL closes the bot’s long position.', wraplength=900).pack(anchor='w', pady=12)
        controls = ttk.Frame(main)
        controls.pack(fill='x')
        self.start_button = ttk.Button(controls, text='▶ Start bot', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='■ Stop bot', command=self.halt, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        ttk.Button(controls, text='Open logs & state', command=lambda: (DATA.mkdir(parents=True, exist_ok=True), os.startfile(DATA))).pack(side='left')
        self.status = tk.StringVar(value='Ready • Demo mode')
        ttk.Label(controls, textvariable=self.status).pack(side='right')
        performance = ttk.LabelFrame(main, text='Profit / Loss', padding=8)
        performance.pack(fill='x', pady=(12, 0))
        self.performance_note = tk.StringVar(value='Start the bot to load saved totals and update market prices.')
        ttk.Label(performance, textvariable=self.performance_note).pack(anchor='w')
        self.performance = ttk.Treeview(performance, columns=('market', 'realized', 'open', 'total', 'currency'),
            show='headings', height=2)
        for name, title in [('market', 'Market'), ('realized', 'Closed P/L'), ('open', 'Open P/L'),
                ('total', 'Total P/L'), ('currency', 'Currency')]:
            self.performance.heading(name, text=title)
            self.performance.column(name, width=130, anchor='w' if name == 'market' else 'e')
        self.performance.pack(fill='x', pady=(6, 0))
        ttk.Label(performance, text='Demo includes simulated fees. Live crypto is gross tracked P/L, excluding fees; live forex P/L is unavailable.\nOpen P/L uses the latest scan price. Totals remain at the last update when stopped.',
            wraplength=880).pack(anchor='w', pady=(6, 0))
        self.log = tk.Text(main, height=12, bg='#101c2b', fg='#d7e8f7', font=('Consolas', 10), state='disabled', wrap='word')
        self.log.pack(fill='both', expand=True, pady=(12, 0))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(150, self.poll)

    def field(self, parent, row, name, label, default, choices):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 20), pady=4)
        self.vars[name] = tk.StringVar(value=default)
        widget = ttk.Combobox(parent, textvariable=self.vars[name], values=choices, state='readonly') if choices else ttk.Entry(parent, textvariable=self.vars[name], show='•' if name in ('key', 'secret') else '')
        widget.grid(row=row, column=1, sticky='ew', pady=4)
        parent.columnconfigure(1, weight=1)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            config = {k: v.get() for k, v in self.vars.items()}
            for key in ('interval', 'allocation', 'lots', 'atr_stop', 'adx', 'crypto_atr', 'forex_atr'):
                config[key] = positive(config[key])
            if config['interval'] < 5:
                raise ValueError('Use a scan interval of at least 5 seconds.')
            if not config['crypto'] and not config['forex']:
                raise ValueError('Enable crypto, forex, or both.')
            if config['mode'] == 'Live' and not messagebox.askyesno('Enable automatic LIVE trading', 'This bot will place real orders automatically using your configured amounts. Losses are possible. Start live trading?'):
                return
            DATA.mkdir(parents=True, exist_ok=True)
            (DATA / 'settings.json').write_text(json.dumps({k: v for k, v in config.items() if k not in ('key', 'secret')}, indent=2))
            self.stop.clear()
            def work():
                try:
                    Engine(config, self.events.put, self.stop).run()
                except Exception as error:
                    # Exchange errors can contain request details: do not log credentials.
                    text = str(error)
                    for secret in (config['key'], config['secret']):
                        if secret:
                            text = text.replace(secret, '[redacted]')
                    self.events.put('STOPPED: ' + text)
            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            self.status.set(config['mode'] + ' • Running')
        except Exception as error:
            messagebox.showerror('Check settings', str(error))

    def halt(self):
        self.stop.set()
        self.status.set('Stopping after current operation…')

    def poll(self):
        while not self.events.empty():
            event = self.events.get()
            if isinstance(event, dict) and 'performance' in event:
                self.performance.delete(*self.performance.get_children())
                for market, realized, unrealized, currency in event['performance']:
                    total = realized + unrealized if realized is not None and unrealized is not None else None
                    values = [market] + ['Unavailable' if value is None else f'{value:+,.2f}'
                        for value in (realized, unrealized, total)] + [currency]
                    self.performance.insert('', 'end', values=values)
                self.performance_note.set(event['mode'] + ' | Updated ' + event['updated'])
                continue
            self.log.config(state='normal')
            self.log.insert('end', event + '\n')
            self.log.see('end')
            self.log.config(state='disabled')
        if self.worker and not self.worker.is_alive():
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.status.set('Stopped • Check activity log')
        self.after(150, self.poll)

    def close(self):
        if self.worker and self.worker.is_alive():
            self.halt()
            self.after(250, self.close)
        else:
            self.destroy()


if __name__ == '__main__':
    import sys
    # Prevent two app processes from managing the same persistent trading state.
    import msvcrt
    DATA.mkdir(parents=True, exist_ok=True)
    lock = (DATA / 'app.lock').open('a+b')
    lock.seek(0)
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('Crow Trading Bot', 'Another instance is already running.')
        root.destroy()
        sys.exit(1)
    app = App()
    if '--smoke-test' in sys.argv:
        app.after(1000, app.destroy)
    app.mainloop()
