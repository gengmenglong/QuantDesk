"""Read-only market adapters. All provider URLs are fixed; no order or signing API exists."""
import json
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import httpx
from .schemas import MarketSnapshot
from .db import now

D = Decimal


class ProviderError(Exception):
    pass


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class Provider:
    def __init__(self, client=None, secrets=None):
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)
        self.secrets = secrets or {}

    def request(self, method, url, **kwargs):
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Do not include request headers, token-bearing bodies or vendor echo text.
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            raise ProviderError(f'data request failed ({status or type(exc).__name__})') from None

    def required(self, name):
        if not self.secrets.get(name):
            raise ProviderError(f'missing credential: {name}')
        return self.secrets[name]

    def history(self, symbol, start, end):
        """Return daily bars as snapshots. `start`/`end` are aware UTC datetimes."""
        raise ProviderError('historical backfill unsupported for this market')


def discontinuity_safe(previous_close, open_price, tolerance=D('.25')):
    """Conservative corporate-action screen: flag unexplained gaps, never invent adjustments."""
    try:
        return bool(previous_close) and bool(open_price) and abs(D(str(open_price)) / D(str(previous_close)) - 1) < tolerance
    except (ArithmeticError, ValueError, TypeError):
        return False


class Coinbase(Provider):
    def fetch(self, symbol):
        if symbol not in ('BTC-USD', 'ETH-USD'):
            raise ProviderError('unsupported initial crypto universe')
        root = f'https://api.exchange.coinbase.com/products/{symbol}'
        product = self.request('GET', root)
        data = self.request('GET', root + '/book', params={'level': 1})
        if not data.get('bids') or not data.get('asks') or not data.get('time'):
            raise ProviderError('timestamp or two-sided depth unavailable')
        bid, ask = data['bids'][0], data['asks'][0]
        return MarketSnapshot(market='crypto', symbol=symbol, source='Coinbase Exchange L1', currency='USD',
            event_time=timestamp(data['time']), price=(D(bid[0])+D(ask[0]))/2,
            bid=bid[0], ask=ask[0], bid_size=bid[1], ask_size=ask[1],
            tradable=product.get('status') == 'online' and not product.get('trading_disabled', True) and not data.get('auction_mode', False))

    def history(self, symbol, start, end):
        if symbol not in ('BTC-USD', 'ETH-USD'):
            raise ProviderError('unsupported initial crypto universe')
        rows = self.request('GET', f'https://api.exchange.coinbase.com/products/{symbol}/candles',
            params={'granularity': 86400, 'start': start.isoformat(), 'end': end.isoformat()})
        if not isinstance(rows, list):
            raise ProviderError('unexpected candle payload')
        out, previous = [], None
        # Coinbase returns newest first; walk oldest first so the gap screen is causal.
        for time_, low, high, open_, close, volume in sorted(rows, key=lambda r: r[0]):
            at = datetime.fromtimestamp(int(time_), timezone.utc)
            safe = discontinuity_safe(previous, open_) if previous is not None else True
            out.append(MarketSnapshot(market='crypto', symbol=symbol, source='Coinbase Exchange candles',
                currency='USD', event_time=at, price=D(str(close)), open=D(str(open_)), size=D(str(volume)),
                daily=True, session=at.date().isoformat(), quality='real', corporate_action_safe=safe,
                reason='' if safe else 'price discontinuity / corporate action requires review'))
            previous = close
        return out


class Alpaca(Provider):
    def headers(self):
        return {'APCA-API-KEY-ID': self.required('alpaca_key'), 'APCA-API-SECRET-KEY': self.required('alpaca_secret')}

    def fetch(self, symbol):
        if not re.fullmatch(r'[A-Z][A-Z.]{0,9}', symbol):
            raise ProviderError('invalid US equity symbol')
        data = self.request('GET', f'https://data.alpaca.markets/v2/stocks/{symbol}/snapshot',
                            params={'feed': 'iex'}, headers=self.headers())
        q = data.get('latestQuote') or {}
        if not q.get('t') or not q.get('bp') or not q.get('ap'):
            raise ProviderError('two-sided IEX quote unavailable')
        # Detect unadjusted discontinuities conservatively; do not invent corporate-action adjustments.
        previous, daily = data.get('prevDailyBar') or {}, data.get('dailyBar') or {}
        safe = bool(previous.get('c') and daily.get('o') and abs(D(str(daily['o'])) / D(str(previous['c'])) - 1) < D('.25'))
        return MarketSnapshot(market='us', symbol=symbol, source='Alpaca IEX (single venue)', currency='USD',
            event_time=timestamp(q['t']), price=(D(str(q['bp']))+D(str(q['ap'])))/2,
            bid=q['bp'], ask=q['ap'], bid_size=q.get('bs', 0), ask_size=q.get('as', 0),
            corporate_action_safe=safe, reason='' if safe else 'price discontinuity / corporate action requires review')

    def history(self, symbol, start, end):
        if not re.fullmatch(r'[A-Z][A-Z.]{0,9}', symbol):
            raise ProviderError('invalid US equity symbol')
        data = self.request('GET', f'https://data.alpaca.markets/v2/stocks/{symbol}/bars',
            params={'feed': 'iex', 'timeframe': '1Day', 'start': start.date().isoformat(),
                    'end': end.date().isoformat(), 'adjustment': 'raw'},
            headers=self.headers())
        rows = (data.get('bars') or {}).get(symbol) or []
        nyse = ZoneInfo('America/New_York')
        out, previous = [], None
        for bar in rows:
            at = timestamp(bar['t']).astimezone(nyse)
            # Bars close the session, so mark the row at the session close, not the UTC instant.
            event = at.replace(hour=16, minute=0, second=0, microsecond=0)
            safe = discontinuity_safe(previous, bar['o']) if previous is not None else True
            out.append(MarketSnapshot(market='us', symbol=symbol, source='Alpaca IEX daily bars', currency='USD',
                event_time=event, price=D(str(bar['c'])), open=D(str(bar['o'])), size=D(str(bar['v'])),
                daily=True, session=at.date().isoformat(), quality='delayed',
                corporate_action_safe=safe, reason='' if safe else 'price discontinuity / corporate action requires review'))
            previous = bar['c']
        return out


class Tushare(Provider):
    SYMBOL_RE = r'(600|601|603|605)\d{3}\.SH|(000|001|002|003)\d{3}\.SZ'

    def query(self, api, **params):
        data = self.request('POST', 'https://api.tushare.pro', json={'api_name':api,
            'token':self.required('tushare_token'), 'params':params, 'fields':''})
        if data.get('code') != 0:
            raise ProviderError(f'Tushare permission or query error ({data.get("code")})')
        result = data.get('data') or {}
        return [dict(zip(result.get('fields', []), row)) for row in result.get('items', [])]

    def check_symbol(self, symbol):
        if not re.fullmatch(self.SYMBOL_RE, symbol):
            raise ProviderError('only Shanghai/Shenzhen main-board shares supported')

    def snapshot(self, symbol, bar, limit, safe):
        """Build one daily-bar snapshot. `safe` must already be resolved by the caller."""
        dt = datetime.strptime(bar['trade_date'], '%Y%m%d').replace(hour=15, tzinfo=ZoneInfo('Asia/Shanghai'))
        return MarketSnapshot(market='cn', symbol=symbol, source='Tushare daily', currency='CNY',
            event_time=dt, price=bar['close'], open=bar['open'], size=D(str(bar['vol']))*100,
            daily=True, session=dt.date().isoformat(), quality='delayed', delay_seconds=0,
            upper_limit=limit.get('up_limit'), lower_limit=limit.get('down_limit'),
            corporate_action_safe=safe, tradable=bool(limit) and D(str(bar['vol'])) > 0,
            reason='' if safe and limit else 'corporate action or price-limit metadata unavailable')

    def fetch(self, symbol):
        self.check_symbol(symbol)
        end = now().astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
        start = (now() - timedelta(days=14)).strftime('%Y%m%d')
        bars = sorted(self.query('daily', ts_code=symbol, start_date=start, end_date=end), key=lambda b:b['trade_date'])
        if not bars:
            raise ProviderError('no daily bars')
        b = bars[-1]
        day = b['trade_date']
        limits = self.query('stk_limit', ts_code=symbol, trade_date=day)
        factors = sorted(self.query('adj_factor', ts_code=symbol, start_date=start, end_date=end), key=lambda x:x['trade_date'])
        safe = len(factors) >= 2 and factors[-1]['adj_factor'] == factors[-2]['adj_factor']
        limit = next((row for row in limits if row['trade_date'] == day), {})
        return self.snapshot(symbol, b, limit, safe)

    def history(self, symbol, start, end):
        self.check_symbol(symbol)
        tz = ZoneInfo('Asia/Shanghai')
        start_date, end_date = start.astimezone(tz).strftime('%Y%m%d'), end.astimezone(tz).strftime('%Y%m%d')
        bars = sorted(self.query('daily', ts_code=symbol, start_date=start_date, end_date=end_date),
                      key=lambda b: b['trade_date'])
        if not bars:
            return []
        limits = {row['trade_date']: row for row in
                  self.query('stk_limit', ts_code=symbol, start_date=start_date, end_date=end_date)}
        factors = {row['trade_date']: row['adj_factor'] for row in
                   self.query('adj_factor', ts_code=symbol, start_date=start_date, end_date=end_date)}
        out, previous = [], None
        for bar in bars:
            day = bar['trade_date']
            factor = factors.get(day)
            # The first bar has no prior factor to compare against, so it stays unverified.
            safe = previous is not None and factor is not None and factor == previous
            out.append(self.snapshot(symbol, bar, limits.get(day, {}), safe))
            previous = factor if factor is not None else previous
        return out


class Polymarket(Provider):
    def market(self, token):
        data = self.request('GET', 'https://gamma-api.polymarket.com/markets', params={'clob_token_ids':token})
        for m in data:
            ids = m.get('clobTokenIds', '[]')
            ids = json.loads(ids) if isinstance(ids, str) else ids
            outcomes = m.get('outcomes', '[]')
            outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
            if token in ids and len(ids) == 2 and set(outcomes) == {'Yes','No'}:
                return m, ids.index(token), outcomes
        raise ProviderError('binary market metadata unavailable')

    def fetch(self, token):
        m, index, outcomes = self.market(token)
        data = self.request('GET', 'https://clob.polymarket.com/book', params={'token_id':token})
        bids = sorted(data.get('bids', []), key=lambda x:D(x['price']), reverse=True)
        asks = sorted(data.get('asks', []), key=lambda x:D(x['price']))
        if not bids or not asks or not data.get('timestamp'):
            raise ProviderError('two-sided prediction order book unavailable')
        return MarketSnapshot(market='poly', symbol=token, source='Polymarket CLOB', currency='USD',
            event_time=datetime.fromtimestamp(int(data['timestamp'])/1000, timezone.utc),
            price=(D(bids[0]['price'])+D(asks[0]['price']))/2, bid=bids[0]['price'], ask=asks[0]['price'],
            bid_size=bids[0]['size'], ask_size=asks[0]['size'], outcome=outcomes[index], condition_id=m.get('conditionId'),
            tradable=bool(m.get('active') and not m.get('closed') and m.get('acceptingOrders') and m.get('enableOrderBook')),
            reason='closed / not accepting orders' if m.get('closed') or not m.get('acceptingOrders') else '')

    def resolution(self, token):
        state = self.state(token)
        return state.get('resolution') if state['state'] == 'resolved' else None

    def state(self, token):
        """Three distinct states: open, closed awaiting resolution, and resolved.
        Closed alone is never sufficient to settle — that requires a terminal binary price."""
        m, index, _ = self.market(token)
        values = m.get('outcomePrices', '[]')
        values = json.loads(values) if isinstance(values, str) else values
        terminal = len(values) == 2 and sorted(D(str(v)) for v in values) == [D(0), D(1)]
        if m.get('closed') and m.get('umaResolutionStatus') == 'resolved' and terminal:
            return {'state': 'resolved', 'resolution': {
                'payout': str(values[index]), 'resolution_id': m['conditionId'],
                'source': 'Polymarket Gamma resolved'}}
        if m.get('closed') or not m.get('acceptingOrders'):
            reason = 'closed awaiting resolution' if m.get('closed') else 'not accepting orders'
            if m.get('umaResolutionStatus') == 'resolved':
                reason = 'resolved without terminal binary prices'
            return {'state': 'closed', 'reason': reason}
        return {'state': 'open'}

    def history(self, token, start, end):
        m, index, outcomes = self.market(token)
        data = self.request('GET', 'https://clob.polymarket.com/prices-history',
            params={'market': token, 'startTs': int(start.timestamp()), 'endTs': int(end.timestamp()),
                    'fidelity': 1440})
        points = data.get('history') or []
        out = []
        for point in sorted(points, key=lambda p: p['t']):
            price = D(str(point['p']))
            # A binary price outside (0, 1] is not a usable mark; skip rather than clamp.
            if not D(0) < price <= D(1):
                continue
            at = datetime.fromtimestamp(int(point['t']), timezone.utc)
            out.append(MarketSnapshot(market='poly', symbol=token, source='Polymarket CLOB history',
                currency='USD', event_time=at, price=price, daily=True, session=at.date().isoformat(),
                quality='real', outcome=outcomes[index], condition_id=m.get('conditionId'),
                tradable=bool(m.get('active') and not m.get('closed') and m.get('acceptingOrders')),
                reason='historical price point carries no order-book depth'))
        return out


PROVIDERS = {'us':Alpaca, 'cn':Tushare, 'crypto':Coinbase, 'poly':Polymarket}
