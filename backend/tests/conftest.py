from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from quantdesk import core as service
from quantdesk.db import Base, Runtime, Account, Order, now
from quantdesk.schemas import Setup, MarketSnapshot, OrderIntent

TEST_URL = os.environ.get('QD_TEST_DATABASE_URL', 'postgresql+psycopg://quantdesk:quantdesk-local-test@127.0.0.1:55432/quantdesk_test')


def configuration():
    risk = {k: dict(max_order_pct=100, max_position_pct=100, max_exposure_pct=100,
                     max_daily_loss_pct=50, max_drawdown_pct=50, fee_bps=10, slippage_bps=5)
            for k in service.MARKETS}
    return dict(whitelist={'us': ['AAPL'], 'cn': ['600519.SH'], 'crypto': ['BTC-USD', 'ETH-USD'], 'poly': ['yes-token']},
                risk=risk, model=dict(base_url='https://example.com/v1', model='test', max_calls_day=100,
                                     max_tokens_day=100000, max_cost_day='1'),
                fx_cny_per_usd='7', fx_at=now().isoformat(), portfolio_max_drawdown_pct=50,
                portfolio_max_daily_loss_pct=50)


@pytest.fixture
def session_factory():
    engine = create_engine(TEST_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as s:
        s.add(Runtime(id=1, config={}))
    yield factory
    engine.dispose()


class Harness:
    def __init__(self, factory):
        self.factory = factory
        self.at = now()
        with factory.begin() as s:
            service.setup(s, Setup(capital={k: '1000' for k in service.MARKETS}, config=configuration()))
            service.resume(s)

    def quote(self, market, symbol, price, size='10', seconds=0, **kwargs):
        with self.factory.begin() as s:
            return service.ingest(s, MarketSnapshot(market=market, symbol=symbol, source='fixture',
                currency=service.MARKETS[market], price=price, size=size,
                event_time=self.at + timedelta(seconds=seconds), **kwargs))

    def order(self, market, symbol, side, quantity, q, key=None, epoch=None, limit=None):
        with self.factory.begin() as s:
            return service.submit(s, OrderIntent(market=market, symbol=symbol, side=side, quantity=quantity,
                snapshot_id=q.id, kind='limit' if limit else 'market', limit_price=limit), key or uuid4().hex,
                epoch=epoch, at=self.at)

    def match(self, q):
        with self.factory.begin() as s:
            return service.match(s, q.id, at=max(self.at, q.event_time))

    def cash(self, market):
        with self.factory() as s:
            return s.get(Account, market).cash

    def reserved(self, market):
        with self.factory() as s:
            return s.get(Account, market).reserved

    def get_order(self, id):
        with self.factory() as s:
            return s.get(Order, id)

    def reconcile(self, market):
        with self.factory() as s:
            return service.reconcile(s, market)

    def epoch(self):
        with self.factory() as s:
            return s.get(Runtime, 1).epoch

    def pause(self):
        with self.factory.begin() as s:
            service.pause(s)

    def resume(self):
        with self.factory.begin() as s:
            service.resume(s)

    def cancel(self, id):
        with self.factory.begin() as s:
            service.cancel(s, id)


@pytest.fixture
def core(session_factory):
    return Harness(session_factory)
