"""AI decision processing. Deterministic validation and order creation from model output."""
from decimal import Decimal
from sqlalchemy import select
from .db import Strategy, Decision, now
from .schemas import ModelDecision
from .core import lock, submit, event

D = Decimal


def _log_cost(config, tokens):
    """Estimate cost from token count and configured per-million rates."""
    model_cfg = config.get('model', {})
    input_rate = model_cfg.get('input_cost_million')
    output_rate = model_cfg.get('output_cost_million')
    if not input_rate or not output_rate:
        return D(0)
    # Conservative: treat all tokens as output (higher rate) for budget safety.
    return D(str(output_rate)) * D(str(tokens)) / D('1000000')


def apply_result(s, decision, result: ModelDecision, at=None):
    """Apply a validated model decision. Returns the action string ('hold', 'order_submitted', etc.).

    Checks:
    - epoch freshness (stale epoch → discard)
    - cross-market consistency (decision market must match action market)
    - duplicate keys (idempotent)
    - order rejection from risk layer is captured, not raised
    """
    r = lock(s)
    at = at or now()

    # Stale epoch: pause/resume changed the epoch after this decision was created.
    if decision.epoch != r.epoch:
        decision.status = 'discarded'
        event(s, 'decision.discarded', {
            'decision_id': decision.id,
            'reason': 'stale_epoch',
            'decision_epoch': decision.epoch,
            'runtime_epoch': r.epoch,
        }, decision.trace_id)
        return 'discarded'

    # Already processed (idempotent guard).
    if decision.status != 'running':
        return decision.status

    action = result.action

    # Cross-market consistency check.
    if action == 'order' and result.order and result.order.market != decision.market:
        decision.status = 'rejected'
        event(s, 'decision.rejected', {
            'decision_id': decision.id,
            'reason': 'cross_market',
            'decision_market': decision.market,
            'order_market': result.order.market,
        }, decision.trace_id)
        return 'rejected'

    if action == 'strategy' and result.strategy and result.strategy.market != decision.market:
        decision.status = 'rejected'
        event(s, 'decision.rejected', {
            'decision_id': decision.id,
            'reason': 'cross_market_strategy',
        }, decision.trace_id)
        return 'rejected'

    if action == 'order' and result.order:
        key = f"ai:{decision.key}:{result.order.symbol}:{result.order.side}"
        order = submit(s, result.order, key=key, epoch=r.epoch, trace=decision.trace_id, at=at)
        decision.status = 'rejected' if order.status == 'rejected' else 'applied'
        decision.output = result.model_dump(mode='json')
        event(s, 'decision.applied' if order.status != 'rejected' else 'decision.order_rejected', {
            'decision_id': decision.id,
            'order_id': order.id,
            'order_status': order.status,
            'reason': order.reason or '',
        }, decision.trace_id)
        return 'order_submitted' if order.status != 'rejected' else 'order_rejected'

    if action == 'strategy' and result.strategy:
        existing = s.scalar(select(Strategy).where(
            Strategy.name == result.strategy.name,
            Strategy.market == result.strategy.market,
            Strategy.status.in_(['candidate', 'shadow', 'active']),
        ))
        if not existing:
            version = 1
        else:
            max_ver = s.scalar(select(Strategy.version).where(
                Strategy.name == result.strategy.name
            ).order_by(Strategy.version.desc()).limit(1)) or 0
            version = max_ver + 1

        strategy = Strategy(
            name=result.strategy.name,
            market=result.strategy.market,
            version=version,
            status='candidate',
            spec=result.strategy.model_dump(mode='json'),
            reason=result.reason,
        )
        s.add(strategy)
        s.flush()
        decision.status = 'applied'
        decision.output = result.model_dump(mode='json')
        event(s, 'decision.strategy_created', {
            'decision_id': decision.id,
            'strategy_id': strategy.id,
        }, decision.trace_id)
        return 'strategy_created'

    # action == 'hold'
    decision.status = 'applied'
    decision.output = result.model_dump(mode='json')
    event(s, 'decision.hold', {'decision_id': decision.id}, decision.trace_id)
    return 'hold'


def check_budget(s):
    """Check if the daily AI budget is exhausted. Returns (ok, reason)."""
    r = lock(s)
    if not r.initialized:
        return False, 'not initialized'
    cfg = r.config
    model_cfg = cfg.get('model', {})
    max_cost = D(str(model_cfg.get('max_cost_day', 0)))
    max_calls = model_cfg.get('max_calls_day', 0)
    max_tokens = model_cfg.get('max_tokens_day', 0)

    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    day_decisions = list(s.scalars(select(Decision).where(
        Decision.created_at >= today,
    )))
    total_cost = sum((d.cost or D(0)) for d in day_decisions)
    total_calls = len(day_decisions)
    total_tokens = sum(d.tokens or 0 for d in day_decisions)

    if total_cost >= max_cost:
        return False, 'daily cost budget exhausted'
    if total_calls >= max_calls:
        return False, 'daily call limit reached'
    if total_tokens >= max_tokens:
        return False, 'daily token limit reached'
    return True, ''
