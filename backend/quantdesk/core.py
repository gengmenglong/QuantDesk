"""Transactional paper execution. Callers own commit; every mutation takes the runtime lock."""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from .db import Runtime, Account, Snapshot, Order, Position, Fill, Ledger, Event, Job, now
from .schemas import MarketSnapshot, OrderIntent, Setup

D = Decimal
ACTIVE = ('pending', 'partial')
MARKETS = {'us': 'USD', 'cn': 'CNY', 'crypto': 'USD', 'poly': 'USD'}
# Continuous markets have no session close to fall back on, so a mark older than
# this is no longer a usable valuation. Session markets are judged by calendar instead.
VALUATION_MAX_AGE = {'crypto': 3600, 'poly': 7 * 86400}


def dec(value):
    return D(str(value))


def rounded(value):
    return value.quantize(D('0.0000000001'), rounding=ROUND_DOWN)


def lock(s):
    runtime = s.scalar(select(Runtime).where(Runtime.id == 1).with_for_update())
    if runtime is None:
        from sqlalchemy import text
        s.execute(text("INSERT INTO runtime (id, paused, initialized, epoch, version, config, secrets) VALUES (1, true, false, 0, 0, '{}', '') ON CONFLICT DO NOTHING"))
        s.flush()
        runtime = s.scalar(select(Runtime).where(Runtime.id == 1).with_for_update())
    if runtime is None:
        raise ValueError('database not initialized; run migrations')
    return runtime


def event(s, kind, payload, trace=None):
    e = Event(kind=kind, payload=payload, trace_id=trace or uuid4().hex)
    s.add(e)
    s.flush()
    return e


def book(s, account, amount, kind, key, trace, details=None):
    account.cash += amount
    row = Ledger(key=key, market=account.market, trace_id=trace, kind=kind,
                 amount=amount, balance=account.cash, details=details or {})
    s.add(row)
    s.flush()


def setup(s, request: Setup):
    r = lock(s)
    if r.initialized:
        raise ValueError('already initialized')
    r.config = request.config.model_dump(mode='json')
    for market, currency in MARKETS.items():
        amount = request.capital[market]
        a = Account(market=market, currency=currency, cash=D(0), reserved=D(0), initial=amount,
                    high_water=amount, day_equity=amount, day=now().date().isoformat())
        s.add(a)
        s.flush()
        book(s, a, amount, 'initial_capital', f'init:{market}', 'setup')
    r.initialized = True
    r.version += 1
    event(s, 'workspace.initialized', {'markets': list(MARKETS), 'simulation': True})


def latest(s, market, symbol):
    return s.scalar(select(Snapshot).where(Snapshot.market == market, Snapshot.symbol == symbol)
                    .order_by(Snapshot.event_time.desc()).limit(1))


def ingest(s, q: MarketSnapshot, received_at=None):
    lock(s)
    existing = s.scalar(select(Snapshot).where(Snapshot.market == q.market, Snapshot.symbol == q.symbol,
                                              Snapshot.source == q.source, Snapshot.event_time == q.event_time))
    if existing:
        return existing
    row = Snapshot(market=q.market, symbol=q.symbol, source=q.source, event_time=q.event_time,
                   data=q.model_dump(mode='json'), consumed=D(0))
    if received_at is not None:
        row.received_at = received_at
    s.add(row)
    s.flush()
    return row


def backfill(s, rows):
    """Persist historical bars. received_at is pinned to event_time so backfilled rows can
    never leak into the shadow window, which selects on received_at."""
    lock(s)
    stored = 0
    for q in rows:
        before = s.scalar(select(Snapshot.id).where(Snapshot.market == q.market, Snapshot.symbol == q.symbol,
                                                    Snapshot.source == q.source, Snapshot.event_time == q.event_time))
        if before:
            continue
        ingest(s, q, received_at=q.event_time)
        stored += 1
    return stored


def calendar(market):
    import exchange_calendars as xc
    return xc.get_calendar('XSHG' if market == 'cn' else 'XNYS')


def regular_session(market, at):
    try:
        import pandas as pd
        cal = calendar(market)
        return bool(cal.is_open_on_minute(pd.Timestamp(at).floor('min')))
    except (ValueError, IndexError):
        return False


def quote_problem(q, at=None):
    at = at or now()
    data = q.data
    if data['quality'] == 'unavailable' or not data['tradable'] or not data['corporate_action_safe']:
        return data.get('reason') or 'market unavailable'
    from .config import settings
    if (data['quality'] == 'demo') != (settings().mode == 'demo'):
        return 'data environment mismatch'
    age = (at - q.event_time).total_seconds()
    if age < -5:
        return 'future quote'
    if q.market == 'cn':
        try:
            cal = calendar('cn')
            session = data['session']
            if not data.get('daily') or not data.get('open') or not cal.is_session(session):
                return 'invalid daily session'
            if at < cal.session_close(session).to_pydatetime():
                return 'daily bar not completed'
            # The most recently completed session remains valid through holidays/overnight.
            local_day = at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            expected = cal.date_to_session(local_day, direction='previous')
            if at < cal.session_close(expected).to_pydatetime():
                expected = cal.previous_session(expected)
            if str(expected.date()) != session:
                return 'stale daily bar'
        except (ValueError, IndexError):
            return 'unknown trading calendar'
    elif age > data.get('delay_seconds', 0) + 120:
        return 'stale quote'
    if q.market == 'us' and not regular_session('us', at):
        return 'outside regular session'
    return ''


def valuation_problem(q, at=None):
    """Freshness for marking holdings. A closed session's last close stays usable, so
    overnight gaps, weekends and holidays are not treated as a data outage. Only marks
    that are genuinely unusable are rejected; tradability is an execution concern and is
    deliberately not checked here."""
    at = at or now()
    data = q.data
    if data['quality'] == 'unavailable' or not data['corporate_action_safe']:
        return data.get('reason') or 'market unavailable'
    from .config import settings
    if (data['quality'] == 'demo') != (settings().mode == 'demo'):
        return 'data environment mismatch'
    age = (at - q.event_time).total_seconds()
    if age < -5:
        return 'future quote'
    if q.market in VALUATION_MAX_AGE:
        if age > VALUATION_MAX_AGE[q.market] + data.get('delay_seconds', 0):
            return 'stale valuation mark'
        return ''
    try:
        cal = calendar(q.market)
        tz = ZoneInfo('Asia/Shanghai' if q.market == 'cn' else 'America/New_York')
        expected = cal.date_to_session(at.astimezone(tz).date().isoformat(), direction='previous')
        if q.event_time.astimezone(tz).date() < expected.date():
            return 'valuation mark older than last session'
    except (ValueError, IndexError):
        return 'unknown trading calendar'
    return ''


def equity(s, a):
    value = a.cash
    for p in s.scalars(select(Position).where(Position.market == a.market, Position.quantity > 0)):
        q = latest(s, a.market, p.symbol)
        value += p.quantity * dec(q.data['price']) if q else p.cost
    return value


def portfolio(s, r):
    fx = dec(r.config['fx_cny_per_usd'])
    result = D(0)
    for a in s.scalars(select(Account)):
        result += equity(s, a) / (fx if a.currency == 'CNY' else D(1))
    return result


def risk_reason(s, r, i, q, a, reserve, at):
    if r.paused or not r.initialized:
        return 'AI paused or workspace not initialized'
    if i.symbol not in r.config['whitelist'].get(i.market, []):
        return 'symbol not whitelisted'
    if q.market != i.market or q.symbol != i.symbol:
        return 'snapshot does not match instrument'
    problem = quote_problem(q, at)
    if problem:
        return problem
    if latest(s, i.market, i.symbol).id != q.id:
        return 'decision snapshot superseded'
    fx_at = datetime.fromisoformat(r.config['fx_at'])
    if (at - fx_at).total_seconds() > r.config['fx_max_age_hours'] * 3600 or fx_at > at + timedelta(seconds=5):
        return 'FX stale or invalid'
    # Marks must be valuation-grade. A closed session's last close qualifies, so a normal
    # overnight or holiday gap in one market never blocks another market's orders.
    for held in s.scalars(select(Position).where(Position.quantity > 0)):
        mark = latest(s, held.market, held.symbol)
        if not mark or valuation_problem(mark, at):
            return 'held asset valuation unavailable' if held.market == i.market else 'portfolio valuation unavailable'
    cfg = r.config['risk'][i.market]
    eq = equity(s, a)
    day = at.astimezone(ZoneInfo('Asia/Shanghai' if i.market == 'cn' else 'America/New_York' if i.market == 'us' else 'UTC')).date().isoformat()
    if a.day != day:
        a.day, a.day_equity = day, eq
    a.high_water = max(a.high_water, eq)
    if eq <= 0 or (a.high_water - eq) / a.high_water * 100 >= dec(cfg['max_drawdown_pct']):
        a.halted = True
    if a.day_equity > 0 and (a.day_equity - eq) / a.day_equity * 100 >= dec(cfg['max_daily_loss_pct']):
        a.halted = True
    if a.halted:
        return 'account circuit breaker'
    total = portfolio(s, r)
    c = dict(r.config)
    high = max(dec(c.get('portfolio_high', total)), total)
    day_start = dec(c.get('portfolio_day_equity', total)) if c.get('portfolio_day') == at.date().isoformat() else total
    c.update(portfolio_high=str(high), portfolio_day_equity=str(day_start), portfolio_day=at.date().isoformat())
    r.config = c
    if (high > 0 and (high - total) / high * 100 >= dec(c['portfolio_max_drawdown_pct'])) or (day_start > 0 and (day_start - total) / day_start * 100 >= dec(c['portfolio_max_daily_loss_pct'])):
        pause(s, 'portfolio circuit breaker')
        return 'portfolio circuit breaker'
    price = dec(q.data['price'])
    if i.quantity * price > eq * dec(cfg['max_order_pct']) / 100:
        return 'order exposure limit'
    p = s.scalar(select(Position).where(Position.market == i.market, Position.symbol == i.symbol))
    if i.market in ('us', 'cn') and i.quantity != i.quantity.to_integral_value():
        return 'whole shares required'
    if i.market == 'cn' and i.side == 'buy' and i.quantity % 100:
        return 'A-share buy lot must be 100'
    if i.side == 'buy':
        if a.cash - a.reserved < reserve:
            return 'insufficient cash including pending reservations'
        pending = sum((o.quantity - o.filled) * price for o in s.scalars(select(Order).where(
            Order.market == i.market, Order.symbol == i.symbol, Order.side == 'buy', Order.status.in_(ACTIVE))))
        if ((p.quantity if p else D(0)) + i.quantity) * price + pending > eq * dec(cfg['max_position_pct']) / 100:
            return 'position exposure limit'
        if eq - a.cash + a.reserved + reserve > eq * dec(cfg['max_exposure_pct']) / 100:
            return 'account exposure limit'
    else:
        available = p.quantity if p else D(0)
        if p and i.market == 'cn':
            available = sum(dec(l['quantity']) for l in p.lots if l['day'] < day)
        pending = sum(o.quantity - o.filled for o in s.scalars(select(Order).where(
            Order.market == i.market, Order.symbol == i.symbol, Order.side == 'sell', Order.status.in_(ACTIVE))))
        if available - pending < i.quantity:
            return 'insufficient sellable position'
    return ''


def submit(s, i: OrderIntent, key, epoch=None, trace=None, at=None):
    r = lock(s)
    existing = s.scalar(select(Order).where(Order.key == key))
    if existing:
        return existing
    at = at or now()
    q = s.get(Snapshot, i.snapshot_id)
    if not q:
        raise ValueError('snapshot not found')
    a = s.get(Account, i.market)
    if not a:
        raise ValueError('account not initialized')
    cfg = r.config['risk'][i.market]
    price = i.limit_price or dec(q.data.get('ask') or q.data['price'])
    reserve = rounded(i.quantity * price * (1 + (dec(cfg['fee_bps']) + dec(cfg['slippage_bps']) + D(100)) / 10000)) if i.side == 'buy' else D(0)
    reason = 'stale runtime epoch' if epoch is not None and epoch != r.epoch else risk_reason(s, r, i, q, a, reserve, at)
    o = Order(key=key, trace_id=trace or uuid4().hex, market=i.market, symbol=i.symbol, side=i.side,
              kind=i.kind, quantity=i.quantity, filled=D(0), limit_price=i.limit_price,
              reserve=D(0) if reason else reserve, status='rejected' if reason else 'pending', reason=reason,
              snapshot_id=q.id, strategy_id=i.strategy_id, created_at=at,
              expires_at=at + timedelta(days=10) if i.market == 'cn' else at + timedelta(hours=24))
    s.add(o)
    if not reason:
        a.reserved += reserve
    s.flush()
    event(s, 'risk.rejected' if reason else 'order.accepted', {'order_id': o.id, 'market': i.market, 'reason': reason, 'simulation': True}, o.trace_id)
    return o


def cancel(s, order_id, reason='user cancelled'):
    lock(s)
    o = s.get(Order, order_id)
    if not o:
        raise ValueError('order not found')
    if o.status in ACTIVE:
        a = s.get(Account, o.market)
        a.reserved -= o.reserve
        o.reserve = D(0)
        o.status = 'expired' if reason == 'expired' else 'cancelled'
        o.reason = reason
        event(s, 'order.cancelled', {'order_id': o.id, 'reason': reason}, o.trace_id)
    return o


def pause(s, reason='user pause'):
    r = lock(s)
    if not r.paused:
        r.epoch += 1
    r.paused = True
    for o in s.scalars(select(Order).where(Order.status.in_(ACTIVE))):
        cancel(s, o.id, reason)
    for j in s.scalars(select(Job).where(Job.kind == 'ai', Job.status.in_(['pending', 'running']))):
        j.status = 'cancelled'
    event(s, 'runtime.paused', {'reason': reason, 'epoch': r.epoch})


def resume(s):
    r = lock(s)
    if not r.initialized:
        raise ValueError('initialize workspace first')
    if r.paused:
        r.epoch += 1
        r.paused = False
        event(s, 'runtime.resumed', {'epoch': r.epoch})


def execution_price(data, side, fee_bps, slippage_bps, daily=False):
    reference = dec(data['open'] if daily else data.get('ask' if side == 'buy' else 'bid') or data['price'])
    price = rounded(reference * (1 + dec(slippage_bps) / 10000 * (1 if side == 'buy' else -1)))
    return reference, price, dec(fee_bps) / 10000


def match(s, snapshot_id, at=None):
    r = lock(s)
    q = s.get(Snapshot, snapshot_id)
    at = at or now()
    if not q or r.paused or quote_problem(q, at):
        return []
    if latest(s, q.market, q.symbol).id != q.id:
        return []
    fills = []
    cfg = r.config['risk'][q.market]
    for o in s.scalars(select(Order).where(Order.market == q.market, Order.symbol == q.symbol,
                                          Order.status.in_(ACTIVE)).order_by(Order.id)):
        if o.expires_at <= at:
            cancel(s, o.id, 'expired')
            continue
        original = s.get(Snapshot, o.snapshot_id)
        if q.event_time <= original.event_time or (q.market != 'cn' and q.event_time <= o.created_at):
            continue
        if s.scalar(select(Fill.id).where(Fill.order_id == o.id, Fill.snapshot_id == q.id)):
            continue
        if q.market == 'cn':
            if q.data['session'] <= original.data['session']:
                continue
            if not q.data.get('upper_limit') or not q.data.get('lower_limit'):
                cancel(s, o.id, 'daily limit metadata unavailable')
                continue
            if dec(q.data['open']) >= dec(q.data['upper_limit']) or dec(q.data['open']) <= dec(q.data['lower_limit']):
                cancel(s, o.id, 'daily limit execution uncertain')
                continue
        ref, price, fee_rate = execution_price(q.data, o.side, cfg['fee_bps'], cfg['slippage_bps'], q.market == 'cn')
        if o.market == 'poly' and not D(0) < price <= D(1):
            continue
        if o.kind == 'limit' and ((o.side == 'buy' and price > o.limit_price) or (o.side == 'sell' and price < o.limit_price)):
            continue
        liquidity = dec(q.data.get('ask_size' if o.side == 'buy' else 'bid_size', 0))
        if liquidity == 0 and not q.data.get('bid') and not q.data.get('ask'):
            liquidity = dec(q.data['size'])
        if q.market == 'cn':
            liquidity *= D('0.001')  # daily participation cap, not a claim of open auction liquidity
        qty = min(o.quantity - o.filled, max(D(0), liquidity - q.consumed))
        if q.market in ('us', 'cn'):
            qty = (qty // (100 if q.market == 'cn' and o.side == 'buy' else 1)) * (100 if q.market == 'cn' and o.side == 'buy' else 1)
        qty = rounded(qty)
        if qty <= 0:
            continue
        a = s.get(Account, o.market)
        # Revalidate with current limits, excluding this order's own reservation.
        saved_reserve, saved_status = o.reserve, o.status
        a.reserved -= saved_reserve
        o.status = 'checking'
        s.flush()
        intent = OrderIntent(market=o.market, symbol=o.symbol, side=o.side, quantity=qty, snapshot_id=q.id)
        cost = rounded(qty * price)
        fee = rounded(cost * fee_rate)
        reason = risk_reason(s, r, intent, q, a, cost + fee if o.side == 'buy' else D(0), at)
        o.status = saved_status
        a.reserved += saved_reserve
        if reason:
            cancel(s, o.id, reason)
            continue
        p = s.scalar(select(Position).where(Position.market == o.market, Position.symbol == o.symbol))
        if not p:
            p = Position(market=o.market, symbol=o.symbol, quantity=D(0), cost=D(0), realized=D(0), lots=[])
            s.add(p)
        before_remaining = o.quantity - o.filled
        released = saved_reserve * qty / before_remaining
        a.reserved -= released
        o.reserve -= released
        if o.side == 'buy':
            p.quantity += qty
            p.cost += cost + fee
            p.lots = p.lots + [{'day': q.data.get('session') or at.date().isoformat(), 'quantity': str(qty)}]
            delta = -(cost + fee)
        else:
            basis = p.cost * qty / p.quantity
            p.quantity -= qty
            p.cost -= basis
            p.realized += cost - fee - basis
            left, lots = qty, []
            for lot in p.lots:
                amount = dec(lot['quantity'])
                take = min(left, amount)
                left -= take
                if amount > take:
                    lots.append({**lot, 'quantity': str(amount - take)})
            p.lots = lots
            delta = cost - fee
        f = Fill(order_id=o.id, snapshot_id=q.id, trace_id=o.trace_id, quantity=qty, price=price,
                 reference=ref, fee=fee, simulated_at=q.event_time)
        s.add(f)
        s.flush()
        book(s, a, delta, 'fill', f'fill:{f.id}', o.trace_id,
             {'symbol': o.symbol, 'side': o.side, 'quantity': str(qty), 'fee': str(fee), 'fill_id': f.id})
        o.filled += qty
        q.consumed += qty
        o.status = 'filled' if o.filled == o.quantity else 'partial'
        event(s, 'order.filled', {'order_id': o.id, 'fill_id': f.id, 'quantity': str(qty), 'simulation': True}, o.trace_id)
        fills.append(f)
    s.flush()
    return fills


def reconcile(s, market):
    a = s.get(Account, market)
    ledger_total = s.scalar(select(func.coalesce(func.sum(Ledger.amount), 0)).where(Ledger.market == market))
    reserved = s.scalar(select(func.coalesce(func.sum(Order.reserve), 0)).where(Order.market == market, Order.status.in_(ACTIVE)))
    return {'market': market, 'ok': a.cash == ledger_total and abs(a.reserved - reserved) < D('0.00000001') and a.cash >= a.reserved >= 0,
            'cash': str(a.cash), 'ledger_total': str(ledger_total), 'reserved': str(a.reserved)}


def settle(s, symbol, payout, resolution_id, source):
    lock(s)
    payout = dec(payout)
    if payout not in (D(0), D(1)) or not source:
        raise ValueError('confirmed binary settlement required')
    key = f'settlement:{symbol}:{resolution_id}'
    if s.scalar(select(Ledger.id).where(Ledger.key == key)):
        return
    p = s.scalar(select(Position).where(Position.market == 'poly', Position.symbol == symbol))
    if not p or p.quantity == 0:
        return
    for o in s.scalars(select(Order).where(Order.market == 'poly', Order.symbol == symbol, Order.status.in_(ACTIVE))):
        cancel(s, o.id, 'market settled')
    a = s.get(Account, 'poly')
    proceeds = p.quantity * payout
    book(s, a, proceeds, 'settlement', key, resolution_id, {'symbol': symbol, 'source': source, 'payout': str(payout)})
    p.realized += proceeds - p.cost
    p.quantity, p.cost, p.lots = D(0), D(0), []
    event(s, 'market.settled', {'symbol': symbol, 'payout': str(payout), 'source': source}, resolution_id)
