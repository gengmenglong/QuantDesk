from datetime import timedelta
from decimal import Decimal
import httpx
import pytest
from sqlalchemy import select
from quantdesk.db import Runtime, Decision, Job, now
from quantdesk.schemas import ModelDecision


def test_secrets_round_trip_and_no_plaintext(monkeypatch):
    from cryptography.fernet import Fernet
    from quantdesk.security import encrypt, decrypt
    monkeypatch.setenv('QD_MASTER_KEY', Fernet.generate_key().decode())
    from quantdesk.config import settings
    settings.cache_clear()
    encrypted = encrypt({'model_key':'sensitive-value'})
    assert 'sensitive-value' not in encrypted
    assert decrypt(encrypted)['model_key'] == 'sensitive-value'
    settings.cache_clear()


def test_job_claim_only_once_and_expired_ai_not_replayed(core):
    from quantdesk.jobs import enqueue, claim, recover
    with core.factory.begin() as s:
        enqueue(s, 'ai', {'market':'crypto'}, 'job1')
        enqueue(s, 'ai', {'market':'crypto'}, 'job1')
    with core.factory.begin() as s:
        job = claim(s, 'worker1')
        assert job.key == 'job1'
        job.lease_until = now() - timedelta(seconds=10)
    with core.factory.begin() as s:
        recover(s)
        assert claim(s, 'worker2') is None
        assert s.scalar(select(Job)).status == 'cancelled'


def test_model_invalid_action_and_cross_market_are_not_orders(core):
    from quantdesk.ai import apply_result
    q = core.quote('crypto','BTC-USD','100')
    with core.factory.begin() as s:
        d = Decision(key='decision1',trace_id='trace',market='crypto',model='fixture',epoch=core.epoch(),
                     status='running',inputs={'snapshots':[q.id]}, tokens=0,cost=0)
        s.add(d); s.flush()
        result = ModelDecision(action='order', reason='test', order={'market':'us','symbol':'AAPL','side':'buy','quantity':'1','snapshot_id':q.id})
        apply_result(s, d, result)
        assert d.status == 'rejected'
    assert core.reserved('crypto') == 0


def test_late_model_result_does_not_create_strategy(core):
    from quantdesk.ai import apply_result
    with core.factory.begin() as s:
        d = Decision(key='d2',trace_id='trace',market='crypto',model='fixture',epoch=core.epoch(),
                     status='running',inputs={}, tokens=0,cost=0)
        s.add(d); s.flush(); did=d.id
    core.pause(); core.resume()
    with core.factory.begin() as s:
        d=s.get(Decision,did)
        apply_result(s,d,ModelDecision(action='strategy',reason='test',strategy={'name':'trend','market':'crypto','symbols':['BTC-USD'],'template':'trend'}))
        assert d.status == 'discarded'


def test_evaluation_requires_independent_samples():
    from quantdesk.evaluation import evaluate_series
    from quantdesk.schemas import StrategySpec, EvaluationConfig, RiskLimits
    spec=StrategySpec(name='test',market='crypto',symbols=['BTC-USD'],template='trend',lookback=5)
    result=evaluate_series([],spec,EvaluationConfig(),RiskLimits(max_order_pct=10,max_position_pct=20,
        max_exposure_pct=80,max_daily_loss_pct=5,max_drawdown_pct=10))
    assert result['eligible'] is False
    assert result['reason'] == 'insufficient_samples'
