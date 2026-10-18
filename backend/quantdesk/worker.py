"""Background worker. Processes market data, AI decisions, matching and evaluation jobs."""
import logging
import signal
import time
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .db import engine, Runtime, Snapshot, Order, Position, Decision, Strategy, now
from .core import ACTIVE, event, ingest, match, backfill, cancel, settle
from .providers import PROVIDERS, Polymarket, ProviderError
from .jobs import claim, complete, enqueue, recover
from .ai import apply_result, check_budget
from .evaluation import evaluate_strategy, monitor_strategy

log = logging.getLogger('quantdesk.worker')

D = Decimal
OWNER = f'worker-{int(time.time())}'
# How far back to pull daily bars on first run, and how often to re-check resolutions.
BACKFILL_DAYS = 400
RESOLUTION_INTERVAL = 300
_last_resolution_poll = 0.0


def get_secrets(session):
    """Decrypt provider credentials from the runtime row."""
    from .security import decrypt
    r = session.get(Runtime, 1)
    if not r or not r.secrets:
        return {}
    try:
        return decrypt(r.secrets)
    except Exception:
        return {}


def fetch_market_data(session):
    """Fetch latest data from all configured providers for whitelisted symbols."""
    r = session.get(Runtime, 1)
    if not r or not r.initialized or r.paused:
        return

    secrets_data = get_secrets(session)
    config = r.config
    whitelist = config.get('whitelist', {})

    for market, symbols in whitelist.items():
        provider_cls = PROVIDERS.get(market)
        if not provider_cls:
            continue
        try:
            provider = provider_cls(secrets=secrets_data)
        except Exception as e:
            log.warning(f'provider init failed for {market}: {e}')
            continue

        for symbol in symbols:
            try:
                q = provider.fetch(symbol)
                ingest(session, q)
                # Attempt matching with new data.
                fills = match(session, q.id)
                for fill in fills:
                    log.info(f'fill: {fill.order_id} qty={fill.quantity} @ {fill.price}')
            except ProviderError as e:
                log.warning(f'data fetch failed for {market}/{symbol}: {e}')
                event(session, 'data.fetch_failed', {
                    'market': market, 'symbol': symbol, 'reason': str(e),
                })
            except Exception as e:
                log.error(f'unexpected error fetching {market}/{symbol}: {e}')


def poll_resolutions(session):
    """Settle prediction markets only on confirmed public resolution. Closed-but-unresolved
    markets stop trading and keep their mark; settlement itself is idempotent via ledger key."""
    global _last_resolution_poll
    if time.time() - _last_resolution_poll < RESOLUTION_INTERVAL:
        return
    _last_resolution_poll = time.time()

    r = session.get(Runtime, 1)
    if not r or not r.initialized:
        return
    symbols = r.config.get('whitelist', {}).get('poly', [])
    if not symbols:
        return

    provider = Polymarket(secrets=get_secrets(session))
    for symbol in symbols:
        try:
            state = provider.state(symbol)
        except ProviderError as e:
            log.warning(f'resolution check failed for {symbol}: {e}')
            event(session, 'data.fetch_failed', {'market': 'poly', 'symbol': symbol, 'reason': str(e)})
            continue
        except Exception as e:
            log.error(f'unexpected error checking resolution for {symbol}: {e}')
            continue

        if state['state'] == 'resolved':
            res = state['resolution']
            settle(session, symbol, res['payout'], res['resolution_id'], res['source'])
            log.info(f'settled {symbol} payout={res["payout"]}')
            continue
        if state['state'] != 'closed':
            continue
        # Awaiting resolution: stop new exposure, keep the position marked until payout.
        resting = list(session.scalars(select(Order).where(
            Order.market == 'poly', Order.symbol == symbol, Order.status.in_(ACTIVE))))
        for o in resting:
            cancel(session, o.id, 'market closed awaiting resolution')
        event(session, 'market.pending_resolution', {
            'symbol': symbol, 'reason': state.get('reason', ''), 'cancelled_orders': len(resting),
        })


def backfill_history(session):
    """Pull daily bars for whitelisted symbols that lack evaluation depth. Idempotent: the
    snapshot unique constraint means a restart re-checks without duplicating rows."""
    r = session.get(Runtime, 1)
    if not r or not r.initialized:
        return
    secrets_data = get_secrets(session)
    needed = int(r.config.get('evaluation', {}).get('min_samples', 60)) * 2
    end = now()
    start = end - timedelta(days=BACKFILL_DAYS)

    for market, symbols in r.config.get('whitelist', {}).items():
        provider_cls = PROVIDERS.get(market)
        if not provider_cls:
            continue
        provider = provider_cls(secrets=secrets_data)
        for symbol in symbols:
            have = session.scalar(select(func.count()).select_from(Snapshot).where(
                Snapshot.market == market, Snapshot.symbol == symbol)) or 0
            if have >= needed:
                continue
            try:
                rows = provider.history(symbol, start, end)
            except ProviderError as e:
                log.warning(f'backfill blocked for {market}/{symbol}: {e}')
                event(session, 'data.backfill_blocked', {'market': market, 'symbol': symbol, 'reason': str(e)})
                continue
            except Exception as e:
                log.error(f'unexpected error backfilling {market}/{symbol}: {e}')
                continue
            stored = backfill(session, rows)
            log.info(f'backfill {market}/{symbol}: {stored} new rows of {len(rows)} fetched')
            event(session, 'data.backfilled', {'market': market, 'symbol': symbol, 'rows': stored})


def decision_bucket(session, market):
    """Cadence key for a market, or None when there is nothing new to decide on.
    A-share evaluates once per completed daily session; continuous markets on a 15-minute
    bucket. Either way, no new data means no model call."""
    newest = session.scalar(select(Snapshot).where(Snapshot.market == market)
                            .order_by(Snapshot.event_time.desc()).limit(1))
    if not newest:
        return None
    last = session.scalar(select(Decision).where(Decision.market == market)
                          .order_by(Decision.created_at.desc()).limit(1))
    seen = (last.inputs or {}).get('bucket') if last else None
    if market == 'cn':
        day = (newest.data or {}).get('session')
        if not day or seen == day:
            return None
        return day
    bucket = str(int(time.time()) // 900)
    if seen == bucket:
        return None
    if last and newest.event_time <= last.created_at:
        return None
    return bucket


def schedule_ai_decisions(session):
    """Create AI decision jobs for each market with active strategies."""
    r = session.get(Runtime, 1)
    if not r or not r.initialized or r.paused:
        return

    epoch = r.epoch

    for market in ['us', 'cn', 'crypto', 'poly']:
        active_strategies = list(session.scalars(select(Strategy).where(
            Strategy.market == market,
            Strategy.status == 'active',
        )))
        if not active_strategies:
            continue

        bucket = decision_bucket(session, market)
        if not bucket:
            continue

        # Check budget before scheduling.
        ok, reason = check_budget(session)
        if not ok:
            log.info(f'skipping AI for {market}: {reason}')
            event(session, 'ai.budget_exhausted', {'market': market, 'reason': reason})
            continue

        enqueue(session, 'ai', {
            'market': market,
            'epoch': epoch,
            'bucket': bucket,
            'strategy_ids': [s.id for s in active_strategies],
        }, f'ai:{market}:{bucket}:{epoch}')


def schedule_evaluations(session):
    """Queue evaluation and monitoring work so the heavy replay never runs inside a request."""
    r = session.get(Runtime, 1)
    if not r or not r.initialized:
        return
    # A full replay over thousands of bars is not a per-tick task: every 6 hours.
    for st in session.scalars(select(Strategy).where(Strategy.status.in_(['candidate', 'shadow']))):
        enqueue(session, 'evaluate', {'strategy_id': st.id}, f'evaluate:{st.id}:{int(time.time()) // 21600}')
    for st in session.scalars(select(Strategy).where(Strategy.status == 'active')):
        enqueue(session, 'monitor', {'strategy_id': st.id}, f'monitor:{st.id}:{now().date().isoformat()}')


def process_ai_job(session, job):
    """Process a single AI decision job with real LLM integration."""
    from .llm import make_decision

    payload = job.payload
    market = payload['market']
    epoch = payload.get('epoch', 0)

    r = session.get(Runtime, 1)
    if not r or r.paused or epoch != r.epoch:
        job.status = 'cancelled'
        return

    # Gather latest snapshots for whitelisted symbols.
    whitelist = r.config.get('whitelist', {}).get(market, [])
    snapshots = []
    snapshot_ids = []
    for symbol in whitelist:
        snap = session.scalar(select(Snapshot).where(
            Snapshot.market == market,
            Snapshot.symbol == symbol,
        ).order_by(Snapshot.event_time.desc()).limit(1))
        if snap:
            snapshots.append(snap)
            snapshot_ids.append(snap.id)

    positions = list(session.scalars(select(Position).where(Position.market == market)))

    # Gather active strategies for this market.
    strategies = list(session.scalars(select(Strategy).where(
        Strategy.market == market,
        Strategy.status == 'active',
    )))

    # Create decision record. The key derives from the job key so a replayed job collides
    # instead of silently deciding twice.
    trace = uuid4().hex
    d = Decision(
        key=f'ai-decision:{job.key}',
        trace_id=trace,
        market=market,
        model=r.config.get('model', {}).get('model', 'unknown'),
        epoch=epoch,
        status='running',
        inputs={'snapshots': snapshot_ids, 'strategy_ids': [s.id for s in strategies],
                'bucket': payload.get('bucket', '')},
        tokens=0,
        cost=D(0),
    )
    session.add(d)
    session.flush()

    # Call LLM for decision.
    decision, tokens, cost = make_decision(
        config=r.config,
        secrets=r.secrets or '',
        market=market,
        snapshots=snapshots,
        positions=positions,
        strategies=strategies,
    )
    d.tokens, d.cost = tokens, cost

    if decision is None:
        # A failed call records why and produces no order; it is not dressed up as a hold.
        d.status = 'failed'
        d.output = {'reason': 'model call failed, timed out, or returned output that failed schema validation'}
        event(session, 'decision.failed', {'decision_id': d.id, 'reason': d.output['reason']}, d.trace_id)
        log.warning(f'LLM call failed for {market}: no order generated')
        return

    action = apply_result(session, d, decision)
    log.info(f'AI decision {d.id} for {market}: action={action}, tokens={tokens}, cost=${cost:.6f}')


def process_evaluation_job(session, job):
    """Process a strategy evaluation job."""
    strategy_id = job.payload.get('strategy_id')
    if strategy_id:
        evaluate_strategy(session, strategy_id)


def process_monitor_job(session, job):
    """Monitor an active strategy and roll it back once if it breaches its gates."""
    strategy_id = job.payload.get('strategy_id')
    if strategy_id:
        monitor_strategy(session, strategy_id)


def process_match_job(session, job):
    """Process a match job for a specific snapshot."""
    snapshot_id = job.payload.get('snapshot_id')
    if snapshot_id:
        fills = match(session, snapshot_id)
        for fill in fills:
            log.info(f'match fill: order={fill.order_id} qty={fill.quantity}')


def tick():
    """Single worker tick: claim and process one job, then do periodic tasks."""
    Session = sessionmaker(engine, expire_on_commit=False)

    with Session.begin() as s:
        recover(s)

    with Session.begin() as s:
        job = claim(s, OWNER)
        if job:
            try:
                if job.kind == 'ai':
                    process_ai_job(s, job)
                elif job.kind == 'evaluate':
                    process_evaluation_job(s, job)
                elif job.kind == 'monitor':
                    process_monitor_job(s, job)
                elif job.kind == 'match':
                    process_match_job(s, job)
                else:
                    log.warning(f'unknown job kind: {job.kind}')
                complete(s, job.id, OWNER)
            except Exception as e:
                log.error(f'job {job.id} failed: {e}')
                complete(s, job.id, OWNER, error=str(e))

    # Each periodic task gets its own transaction so one failure cannot roll back the rest.
    for task in (fetch_market_data, poll_resolutions, schedule_evaluations, schedule_ai_decisions):
        with Session.begin() as s:
            try:
                task(s)
            except Exception as e:
                log.error(f'{task.__name__} failed: {e}')


def main(interval=60):
    """Main worker loop. Runs until SIGTERM/SIGINT."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    log.info(f'worker started (owner={OWNER}, interval={interval}s)')

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        log.info(f'received signal {sig}, stopping')
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Historical depth is a one-shot startup task, not a per-tick API call.
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session.begin() as s:
        try:
            backfill_history(s)
        except Exception as e:
            log.error(f'backfill_history failed: {e}')

    while running:
        try:
            tick()
        except Exception as e:
            log.error(f'tick failed: {e}')
        time.sleep(interval)

    log.info('worker stopped')


if __name__ == '__main__':
    main()
