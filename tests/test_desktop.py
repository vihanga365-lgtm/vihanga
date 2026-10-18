import threading

import pytest

import desktop


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'DATA', tmp_path)
    return desktop.Engine({'mode': 'Demo', 'allocation': 100, 'lots': 0.01,
        'atr_stop': 2}, lambda text: None, threading.Event())


def test_demo_buy_sell_never_submits_live_order(engine, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Demo must never submit an exchange order')
    monkeypatch.setattr(engine.exchange, 'create_order', forbidden)
    engine.trade('Crypto', 'BTC/USDT', 'BUY', 100, 2)
    cash = engine.state['cash']['Crypto']
    engine.trade('Crypto', 'BTC/USDT', 'BUY', 100, 2)
    assert engine.state['cash']['Crypto'] == cash
    engine.trade('Crypto', 'BTC/USDT', 'SELL', 110, 2)
    assert not engine.state['positions']
    assert engine.state['cash']['Crypto'] > 10000


def test_stopped_engine_does_not_open_position(engine):
    engine.stop.set()
    engine.trade('Crypto', 'BTC/USDT', 'BUY', 100, 2)
    assert not engine.state['positions']


def test_demo_performance_includes_entry_and_exit_fees_and_survives_restart(engine):
    events = []
    engine.emit = events.append
    engine.trade('Crypto', 'BTC/USDT', 'BUY', 100, 2)
    engine.state.setdefault('marks', {})['Crypto:BTC/USDT'] = 110
    engine.publish_performance()
    _, realized, unrealized, _ = events[-1]['performance'][0]
    position = engine.state['positions']['Crypto:BTC/USDT']
    expected = (110 - position['entry']) * position['quantity'] - position['entry_fee'] - 110 * position['quantity'] * 0.001
    assert realized == pytest.approx(0)
    assert unrealized == pytest.approx(expected)
    engine.trade('Crypto', 'BTC/USDT', 'SELL', 90, 2)
    engine.publish_performance()
    _, realized, unrealized, _ = events[-1]['performance'][0]
    assert realized == pytest.approx(engine.state['cash']['Crypto'] - 10000)
    assert realized < 0
    assert unrealized == 0
    restored = desktop.Engine(engine.c, events.append, threading.Event())
    restored.publish_performance()
    assert events[-1]['performance'][0][1] == pytest.approx(realized)


def test_missing_open_price_is_unavailable(engine):
    events = []
    engine.emit = events.append
    engine.trade('Crypto', 'BTC/USDT', 'BUY', 100, 2)
    engine.publish_performance()
    assert events[-1]['performance'][0][2] is None


def test_pending_order_blocks_restart(engine):
    engine.state['pending'] = {'symbol': 'BTC/USDT'}
    engine.save()
    with pytest.raises(RuntimeError, match='uncertain result'):
        desktop.Engine(engine.c, lambda text: None, threading.Event())


def test_open_positions_block_symbol_changes(engine):
    engine.trade('Crypto', 'BTC/USDT', 'BUY', 100, 2)
    engine.c.update(crypto=True, forex=False, crypto_symbol='ETH/USDT')
    with pytest.raises(RuntimeError, match='open positions'):
        engine.connect()


def test_ambiguous_live_submission_persists_pending(engine, monkeypatch):
    engine.c['mode'] = 'Live'
    monkeypatch.setattr(engine.exchange, 'amount_to_precision', lambda symbol, amount: str(amount))
    monkeypatch.setattr(engine.exchange, 'market', lambda symbol: {
        'limits': {'amount': {'min': 0.001}, 'cost': {'min': 5}}})
    def timeout(*args):
        raise TimeoutError('Unknown exchange result')
    monkeypatch.setattr(engine.exchange, 'create_order', timeout)
    with pytest.raises(TimeoutError):
        engine.trade('Crypto', 'BTC/USDT', 'BUY', 100, 2)
    assert engine.state['pending']['side'] == 'BUY'
    assert 'pending' in engine.path.read_text()
    assert not engine.state['positions']


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '0'])
def test_invalid_numeric_settings(value):
    with pytest.raises(ValueError):
        desktop.positive(value)
