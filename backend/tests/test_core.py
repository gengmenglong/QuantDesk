"""These assertions detect double spending, look-ahead fills and ineffective pause."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util


def test_core_is_available():
    assert importlib.util.find_spec('quantdesk.core') is not None, 'transactional core has not been implemented'


def test_reservation_fill_dedup_and_ledger(core):
    q = core.quote('crypto', 'BTC-USD', '100', size='2')
    order = core.order('crypto', 'BTC-USD', 'buy', '2', q)
    assert order.status == 'pending'
    assert core.cash('crypto') == Decimal('1000')
    assert core.reserved('crypto') > Decimal('200')
    core.match(q)
    assert core.cash('crypto') == Decimal('1000')  # no same-snapshot execution
    newer = core.quote('crypto', 'BTC-USD', '100', size='1', seconds=1)
    core.match(newer)
    core.match(newer)
    assert core.get_order(order.id).filled == Decimal('1')
    assert core.get_order(order.id).status == 'partial'
    assert core.reconcile('crypto')['ok']
    core.cancel(order.id)
    assert core.reserved('crypto') == 0


def test_pending_orders_cannot_double_spend(core):
    q = core.quote('crypto', 'BTC-USD', '100', size='20')
    first = core.order('crypto', 'BTC-USD', 'buy', '7', q)
    second = core.order('crypto', 'BTC-USD', 'buy', '7', q)
    assert first.status == 'pending'
    assert second.status == 'rejected'
    assert 'cash' in second.reason


def test_pause_is_idempotent_and_invalidates_late_results(core):
    q = core.quote('crypto', 'BTC-USD', '100')
    order = core.order('crypto', 'BTC-USD', 'buy', '1', q)
    epoch = core.epoch()
    core.pause()
    core.pause()
    assert core.get_order(order.id).status == 'cancelled'
    core.resume()
    late = core.order('crypto', 'BTC-USD', 'buy', '1', q, epoch=epoch)
    assert late.status == 'rejected'
    assert core.reserved('crypto') == 0


def test_bad_quote_and_unlisted_symbol_rejected(core):
    q = core.quote('crypto', 'BTC-USD', '100', seconds=-600)
    assert core.order('crypto', 'BTC-USD', 'buy', '1', q).status == 'rejected'
    q = core.quote('crypto', 'DOGE-USD', '1')
    assert core.order('crypto', 'DOGE-USD', 'buy', '1', q).status == 'rejected'


def test_sell_cannot_exceed_position(core):
    q = core.quote('crypto', 'BTC-USD', '100')
    assert core.order('crypto', 'BTC-USD', 'sell', '1', q).status == 'rejected'


def test_duplicate_order_key_returns_original(core):
    q = core.quote('crypto', 'BTC-USD', '100')
    a = core.order('crypto', 'BTC-USD', 'buy', '1', q, key='same')
    b = core.order('crypto', 'BTC-USD', 'buy', '1', q, key='same')
    assert a.id == b.id
    assert core.reserved('crypto') < 102


def test_limit_does_not_fill_above_price(core):
    q = core.quote('crypto', 'BTC-USD', '100')
    o = core.order('crypto', 'BTC-USD', 'buy', '1', q, limit='95')
    core.match(core.quote('crypto', 'BTC-USD', '100', seconds=1))
    assert core.get_order(o.id).filled == 0
    core.match(core.quote('crypto', 'BTC-USD', '90', seconds=2))
    assert core.get_order(o.id).status == 'filled'
