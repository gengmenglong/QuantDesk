"""FastAPI application. REST endpoints for all seven workbench pages."""
from __future__ import annotations
import asyncio
import csv
import hashlib
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from .config import settings
from .db import (
    engine, Runtime, Account, Snapshot, Order, Position, Fill,
    Ledger, Event, Strategy, Decision, Job, Mutation, now,
)
from .core import (
    setup as core_setup, pause as core_pause, resume as core_resume,
    submit, cancel, match, reconcile, backfill, ingest,
    quote_problem, valuation_problem, equity, portfolio,
)
from .schemas import Setup, MarketSnapshot, OrderIntent, WorkspaceConfig
from .security import valid_token, encrypt, decrypt
from .jobs import enqueue, recover
from .ai import check_budget
from .evaluation import rollback_strategy
from .providers import PROVIDERS, ProviderError

WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


# ---------------------------------------------------------------------------
# Session & auth
# ---------------------------------------------------------------------------

def require_token(request: Request):
    token = request.headers.get('authorization', '').removeprefix('Bearer ').strip()
    if settings().mode != 'demo' and not valid_token(token):
        raise HTTPException(status_code=401, detail='invalid session token')
    return True


def require_stream_token(request: Request):
    """EventSource cannot set headers, so only the stream accepts ?token=. Every other
    endpoint keeps requiring the Authorization header."""
    token = request.headers.get('authorization', '').removeprefix('Bearer ').strip()
    if not token:
        token = request.query_params.get('token', '').strip()
    if settings().mode != 'demo' and not valid_token(token):
        raise HTTPException(status_code=401, detail='invalid session token')
    return True


# ---------------------------------------------------------------------------
# Write idempotency
# ---------------------------------------------------------------------------

def idempotency_key(request: Request, payload=None) -> tuple[str | None, str]:
    """Resolve the client key plus a payload fingerprint. The fingerprint comes from the
    parsed payload rather than raw bytes so endpoints stay synchronous and keep blocking DB
    work off the event loop.

    A bodyless request (pause/resume) yields no fallback key: those are already idempotent in
    the core, and caching them would freeze a later pause/resume cycle behind a stale response.
    """
    header_key = request.headers.get('idempotency-key', '').strip()
    if payload is None:
        return (header_key[:160] or None), request.url.path
    canonical = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return (header_key or f'{request.url.path}:{digest}')[:160], digest


def cached_mutation(s, key: str | None, fingerprint: str):
    """Return a stored response for this key, or None. A reused key with a different body
    is a client bug and must not silently apply the new payload."""
    if not key:
        return None
    row = s.get(Mutation, key)
    if row is None:
        return None
    if row.fingerprint != fingerprint:
        raise HTTPException(status_code=409, detail='idempotency key reused with a different payload')
    return row.response


def record_mutation(s, key: str | None, fingerprint: str, response: dict):
    if not key:
        return
    s.add(Mutation(key=key, fingerprint=fingerprint, response=response))
    s.flush()


# ---------------------------------------------------------------------------
# SSE stream (database-backed)
# ---------------------------------------------------------------------------

async def event_stream(request: Request, since_id: int):
    """Poll the events table instead of an in-process hub: the worker writes events from a
    separate process, so the database is the only shared channel. Starting from since_id also
    gives a reconnecting client its catch-up point."""
    stream_session = sessionmaker(engine, expire_on_commit=False)
    last_id = since_id
    idle = 0
    while True:
        if await request.is_disconnected():
            break
        try:
            with stream_session() as s:
                rows = [serialize_event(e) for e in s.scalars(
                    select(Event).where(Event.id > last_id).order_by(Event.id).limit(200))]
        except Exception:
            rows = []
        if rows:
            idle = 0
            for row in rows:
                last_id = row['id']
                yield f"id: {row['id']}\ndata: {json.dumps(row)}\n\n"
        else:
            idle += 1
            if idle >= 30:
                idle = 0
                yield ': keepalive\n\n'
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure runtime row exists and recover jobs on startup."""
    if settings().mode != 'demo' and not settings().master_key:
        # Fail at startup rather than 500ing on the first credential write.
        raise RuntimeError('QD_MASTER_KEY must be set outside demo mode; credentials cannot be encrypted without it')
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session.begin() as s:
        from sqlalchemy import text
        existing = s.execute(text('SELECT id FROM runtime WHERE id = 1')).scalar()
        if not existing:
            s.execute(text("INSERT INTO runtime (id, paused, initialized, epoch, version, config, secrets) VALUES (1, true, false, 0, 0, '{}', '')"))
        recover(s)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title='QuantDesk', version='0.1.0', lifespan=lifespan)

    origins = [o.strip() for o in settings().origins.split(',') if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.middleware('http')
    async def reject_cross_origin_writes(request: Request, call_next):
        """CORS headers do not stop a cross-site form POST, so writes are checked here.
        Requests without an Origin header (curl, scripts) are left alone."""
        if request.method in WRITE_METHODS:
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') not in {o.rstrip('/') for o in origins}:
                return JSONResponse({'detail': 'origin not allowed'}, status_code=403)
        return await call_next(request)

    return app


app = create_app()

Session = sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db():
    with Session.begin() as s:
        yield s


def require_init(s) -> Runtime:
    r = s.get(Runtime, 1)
    if not r or not r.initialized:
        raise HTTPException(status_code=409, detail='workspace not initialized')
    return r


def serialize_row(row):
    """Convert SQLAlchemy row to JSON-safe dict."""
    d = {k: getattr(row, k) for k in row.__table__.columns.keys()}
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, Decimal):
            d[k] = str(v)
    return d


def serialize_event(e: Event) -> dict:
    return {
        'id': e.id,
        'kind': e.kind,
        'payload': e.payload,
        'trace_id': e.trace_id,
        'created_at': e.created_at.isoformat() if e.created_at else None,
    }


# ---------------------------------------------------------------------------
# Health & status
# ---------------------------------------------------------------------------

@app.get('/api/v1/health')
def health():
    try:
        with Session() as s:
            r = s.get(Runtime, 1)
            return {
                'ok': True,
                'initialized': r.initialized if r else False,
                'paused': r.paused if r else True,
                'epoch': r.epoch if r else 0,
                'mode': settings().mode,
                'simulation': True,
            }
    except Exception as e:
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=503)


@app.get('/api/v1/runtime')
def get_runtime(auth=Depends(require_token)):
    with Session() as s:
        r = s.get(Runtime, 1)
        if not r:
            raise HTTPException(404, 'runtime not found')
        config = dict(r.config)
        # Never return secrets in plaintext.
        config.pop('secrets', None)
        return {
            'initialized': r.initialized,
            'paused': r.paused,
            'epoch': r.epoch,
            'version': r.version,
            'config': config,
            'has_secrets': bool(r.secrets),
            'simulation': True,
        }


# ---------------------------------------------------------------------------
# Setup & credentials
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    capital: dict[str, str]
    config: dict
    secrets: dict = Field(default_factory=dict)


@app.post('/api/v1/setup')
def do_setup(body: SetupRequest, request: Request, auth=Depends(require_token)):
    try:
        config = WorkspaceConfig(**body.config)
        setup_obj = Setup(capital=dict(body.capital), config=config)
    except Exception as e:
        raise HTTPException(422, str(e))

    key, fingerprint = idempotency_key(request, body.model_dump(mode='json'))
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        try:
            core_setup(s, setup_obj)
        except ValueError as e:
            raise HTTPException(409, str(e))
        if body.secrets:
            r = s.get(Runtime, 1)
            r.secrets = encrypt(body.secrets)
        response = {'ok': True, 'message': 'workspace initialized (simulation only)'}
        record_mutation(s, key, fingerprint, response)
    return response


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    secrets: dict


@app.put('/api/v1/credentials')
def update_credentials(body: CredentialsRequest, request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request, body.model_dump(mode='json'))
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        r = require_init(s)
        r.secrets = encrypt(body.secrets)
        r.version += 1
        response = {'ok': True, 'version': r.version}
        record_mutation(s, key, fingerprint, response)
    return response


@app.delete('/api/v1/credentials')
def clear_credentials(request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request)
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        r = require_init(s)
        r.secrets = ''
        r.version += 1
        response = {'ok': True, 'version': r.version}
        record_mutation(s, key, fingerprint, response)
    return response


# ---------------------------------------------------------------------------
# Config update (with version check)
# ---------------------------------------------------------------------------

class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    config: dict
    expected_version: int


@app.put('/api/v1/config')
def update_config(body: ConfigUpdate, request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request, body.model_dump(mode='json'))
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        r = require_init(s)
        if r.version != body.expected_version:
            raise HTTPException(409, f'version conflict: expected {body.expected_version}, got {r.version}')
        try:
            validated = WorkspaceConfig(**body.config)
        except Exception as e:
            raise HTTPException(422, str(e))
        r.config = validated.model_dump(mode='json')
        r.version += 1
        response = {'ok': True, 'version': r.version}
        record_mutation(s, key, fingerprint, response)
    return response


# ---------------------------------------------------------------------------
# Run control
# ---------------------------------------------------------------------------

@app.post('/api/v1/pause')
def do_pause(request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request)
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        r = require_init(s)
        core_pause(s)
        response = {'ok': True, 'epoch': r.epoch}
        record_mutation(s, key, fingerprint, response)
    return response


@app.post('/api/v1/resume')
def do_resume(request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request)
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        r = s.get(Runtime, 1)
        if not r:
            raise HTTPException(404, 'runtime not found')
        core_resume(s)
        response = {'ok': True, 'epoch': r.epoch}
        record_mutation(s, key, fingerprint, response)
    return response


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

@app.get('/api/v1/snapshots')
def list_snapshots(
    market: str | None = None,
    symbol: str | None = None,
    limit: int = 50,
    offset: int = 0,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Snapshot).order_by(Snapshot.event_time.desc())
        if market:
            q = q.filter(Snapshot.market == market)
        if symbol:
            q = q.filter(Snapshot.symbol == symbol)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            'total': total,
            'items': [serialize_row(r) for r in rows],
        }


@app.get('/api/v1/snapshots/latest')
def latest_snapshots(auth=Depends(require_token)):
    """Latest snapshot per (market, symbol)."""
    with Session() as s:
        subq = s.query(
            Snapshot.market, Snapshot.symbol, func.max(Snapshot.id).label('max_id')
        ).group_by(Snapshot.market, Snapshot.symbol).subquery()
        rows = s.query(Snapshot).join(
            subq, Snapshot.id == subq.c.max_id
        ).all()
        items = []
        for r in rows:
            d = serialize_row(r)
            d['problem'] = quote_problem(r) if r else ''
            items.append(d)
        return {'items': items}


@app.post('/api/v1/snapshots/ingest')
def ingest_snapshot(body: MarketSnapshot, request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request, body.model_dump(mode='json'))
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        require_init(s)
        row = ingest(s, body)
        # Attempt matching with the new data (worker does this in production).
        fills = match(s, row.id)
        response = {'id': row.id, 'fills': len(fills)}
        record_mutation(s, key, fingerprint, response)
    return response


@app.post('/api/v1/snapshots/fetch')
def fetch_snapshot(market: str, symbol: str, auth=Depends(require_token)):
    """Trigger a live fetch from the provider for the given market/symbol."""
    with Session.begin() as s:
        r = require_init(s)
        secrets_data = decrypt(r.secrets) if r.secrets else {}
        provider_cls = PROVIDERS.get(market)
        if not provider_cls:
            raise HTTPException(400, f'unknown market: {market}')
        provider = provider_cls(secrets=secrets_data)
        try:
            q = provider.fetch(symbol)
        except ProviderError as e:
            raise HTTPException(502, str(e))
        row = ingest(s, q)
    return {'id': row.id, 'market': market, 'symbol': symbol, 'price': str(q.price)}


class BackfillRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    market: str
    symbol: str
    days: int = Field(default=400, ge=1, le=3650)


@app.post('/api/v1/backfill')
def do_backfill(body: BackfillRequest, request: Request, auth=Depends(require_token)):
    """Pull historical daily bars for one instrument. Idempotent: rows already stored are
    skipped, so a repeated call reports zero new rows instead of duplicating history."""
    key, fingerprint = idempotency_key(request, body.model_dump(mode='json'))
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        r = require_init(s)
        provider_cls = PROVIDERS.get(body.market)
        if not provider_cls:
            raise HTTPException(400, f'unknown market: {body.market}')
        provider = provider_cls(secrets=decrypt(r.secrets) if r.secrets else {})
        end = now()
        try:
            rows = provider.history(body.symbol, end - timedelta(days=body.days), end)
        except ProviderError as e:
            # A missing entitlement or unreachable vendor is a blocked backfill, not a 500.
            raise HTTPException(502, str(e))
        stored = backfill(s, rows)
        response = {'market': body.market, 'symbol': body.symbol, 'fetched': len(rows), 'stored': stored}
        record_mutation(s, key, fingerprint, response)
    return response


# ---------------------------------------------------------------------------
# Accounts & positions
# ---------------------------------------------------------------------------

@app.get('/api/v1/accounts')
def list_accounts(auth=Depends(require_token)):
    with Session() as s:
        r = s.get(Runtime, 1)
        accounts = s.query(Account).all()
        items = []
        for a in accounts:
            d = serialize_row(a)
            d['equity'] = str(equity(s, a))
            items.append(d)
        total = portfolio(s, r) if r and r.initialized else Decimal(0)
        return {'items': items, 'portfolio_value': str(total), 'simulation': True}


@app.get('/api/v1/positions')
def list_positions(
    market: str | None = None,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Position).filter(Position.quantity > 0)
        if market:
            q = q.filter(Position.market == market)
        rows = q.all()
        return {'items': [serialize_row(r) for r in rows]}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

class OrderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    market: str
    symbol: str
    side: str
    quantity: str
    kind: str = 'market'
    limit_price: str | None = None
    strategy_id: int | None = None


@app.post('/api/v1/orders')
def create_order(body: OrderRequest, request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request, body.model_dump(mode='json'))
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        r = require_init(s)
        snap = s.query(Snapshot).filter(
            Snapshot.market == body.market,
            Snapshot.symbol == body.symbol,
        ).order_by(Snapshot.event_time.desc()).first()
        if not snap:
            raise HTTPException(400, 'no snapshot available for this symbol')

        try:
            intent = OrderIntent(
                market=body.market,
                symbol=body.symbol,
                side=body.side,
                quantity=body.quantity,
                kind=body.kind,
                limit_price=body.limit_price,
                snapshot_id=snap.id,
                strategy_id=body.strategy_id,
            )
        except Exception as e:
            raise HTTPException(422, str(e))

        # Derive the order key from the idempotency key so a retried submit returns the same
        # order instead of reserving cash twice.
        order = submit(s, intent, key=f'manual:{key}', epoch=r.epoch)
        response = serialize_row(order)
        record_mutation(s, key, fingerprint, response)
    return response


@app.post('/api/v1/orders/{order_id}/cancel')
def cancel_order(order_id: int, request: Request, auth=Depends(require_token)):
    key, fingerprint = idempotency_key(request)
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        require_init(s)
        order = cancel(s, order_id)
        response = serialize_row(order)
        record_mutation(s, key, fingerprint, response)
    return response


@app.get('/api/v1/orders')
def list_orders(
    market: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Order).order_by(Order.created_at.desc())
        if market:
            q = q.filter(Order.market == market)
        if symbol:
            q = q.filter(Order.symbol == symbol)
        if status:
            q = q.filter(Order.status == status)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {'total': total, 'items': [serialize_row(r) for r in rows]}


@app.get('/api/v1/orders/{order_id}')
def get_order(order_id: int, auth=Depends(require_token)):
    with Session() as s:
        order = s.get(Order, order_id)
        if not order:
            raise HTTPException(404, 'order not found')
        return serialize_row(order)


# ---------------------------------------------------------------------------
# Fills
# ---------------------------------------------------------------------------

@app.get('/api/v1/fills')
def list_fills(
    market: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Fill).order_by(Fill.simulated_at.desc())
        if market:
            q = q.join(Order, Fill.order_id == Order.id).filter(Order.market == market)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {'total': total, 'items': [serialize_row(r) for r in rows]}


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

@app.get('/api/v1/ledger')
def list_ledger(
    market: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Ledger).order_by(Ledger.created_at.desc())
        if market:
            q = q.filter(Ledger.market == market)
        if kind:
            q = q.filter(Ledger.kind == kind)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {'total': total, 'items': [serialize_row(r) for r in rows]}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@app.get('/api/v1/reconcile')
def reconcile_all(auth=Depends(require_token)):
    results = []
    with Session() as s:
        for market in ['us', 'cn', 'crypto', 'poly']:
            a = s.get(Account, market)
            if a:
                results.append(reconcile(s, market))
    return {'items': results}


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@app.get('/api/v1/strategies')
def list_strategies(
    market: str | None = None,
    status: str | None = None,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Strategy).order_by(Strategy.created_at.desc())
        if market:
            q = q.filter(Strategy.market == market)
        if status:
            q = q.filter(Strategy.status == status)
        rows = q.all()
        return {'items': [serialize_row(r) for r in rows]}


@app.get('/api/v1/strategies/{strategy_id}')
def get_strategy(strategy_id: int, auth=Depends(require_token)):
    with Session() as s:
        strategy = s.get(Strategy, strategy_id)
        if not strategy:
            raise HTTPException(404, 'strategy not found')
        return serialize_row(strategy)


@app.post('/api/v1/strategies/{strategy_id}/evaluate')
def evaluate(strategy_id: int, request: Request, auth=Depends(require_token)):
    """Queue the replay rather than running it here: a full evaluation walks thousands of bars
    and must not block a request thread."""
    key, fingerprint = idempotency_key(request)
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        require_init(s)
        strategy = s.get(Strategy, strategy_id)
        if not strategy:
            raise HTTPException(404, 'strategy not found')
        if strategy.status not in ('candidate', 'shadow'):
            raise HTTPException(409, f'strategy status {strategy.status} is not evaluable')
        job = enqueue(s, 'evaluate', {'strategy_id': strategy_id}, f'evaluate:{strategy_id}:manual:{fingerprint}')
        response = {'ok': True, 'queued': True, 'job_id': job.id, 'strategy_status': strategy.status}
        record_mutation(s, key, fingerprint, response)
    return response


@app.post('/api/v1/strategies/{strategy_id}/rollback')
def rollback(strategy_id: int, request: Request, auth=Depends(require_token)):
    """User-triggered rollback. One-shot: only an active strategy can be rolled back."""
    key, fingerprint = idempotency_key(request)
    with Session.begin() as s:
        cached = cached_mutation(s, key, fingerprint)
        if cached is not None:
            return cached
        require_init(s)
        strategy = rollback_strategy(s, strategy_id)
        if not strategy:
            raise HTTPException(404, 'strategy not found')
        if strategy.status != 'rolled_back':
            raise HTTPException(409, f'strategy status {strategy.status} cannot be rolled back')
        response = {'ok': True, 'strategy_id': strategy_id, 'status': strategy.status,
                    'restored_id': (strategy.evaluation or {}).get('restored_id')}
        record_mutation(s, key, fingerprint, response)
    return response


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@app.get('/api/v1/decisions')
def list_decisions(
    market: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Decision).order_by(Decision.created_at.desc())
        if market:
            q = q.filter(Decision.market == market)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {'total': total, 'items': [serialize_row(r) for r in rows]}


# ---------------------------------------------------------------------------
# AI budget
# ---------------------------------------------------------------------------

@app.get('/api/v1/ai/budget')
def ai_budget(auth=Depends(require_token)):
    """Today's model usage against the configured caps, in UTC like the enforcement path."""
    with Session() as s:
        r = s.get(Runtime, 1)
        if not r or not r.initialized:
            raise HTTPException(409, 'workspace not initialized')
        model_cfg = r.config.get('model', {})
        day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = list(s.scalars(select(Decision).where(Decision.created_at >= day_start)))
        used_cost = sum((d.cost or Decimal(0)) for d in rows)
        used_tokens = sum(d.tokens or 0 for d in rows)
        ok, blocked_reason = check_budget(s)
        return {
            'utc_day': day_start.date().isoformat(),
            'used': {'calls': len(rows), 'tokens': used_tokens, 'cost': str(used_cost)},
            'limits': {
                'max_calls_day': model_cfg.get('max_calls_day', 0),
                'max_tokens_day': model_cfg.get('max_tokens_day', 0),
                'max_cost_day': str(model_cfg.get('max_cost_day', '0')),
            },
            'allowed': ok,
            'blocked_reason': blocked_reason,
            'simulation': True,
        }


# ---------------------------------------------------------------------------
# Market status & freshness
# ---------------------------------------------------------------------------

@app.get('/api/v1/market-status')
def market_status(auth=Depends(require_token)):
    """Per-instrument freshness, split into an execution verdict and a valuation verdict.
    A closed session is normal for valuation and only blocks execution."""
    with Session() as s:
        r = s.get(Runtime, 1)
        whitelist = (r.config.get('whitelist', {}) if r and r.initialized else {})
        items = []
        for market, symbols in whitelist.items():
            for symbol in symbols:
                row = s.scalar(select(Snapshot).where(Snapshot.market == market, Snapshot.symbol == symbol)
                               .order_by(Snapshot.event_time.desc()).limit(1))
                if not row:
                    items.append({'market': market, 'symbol': symbol, 'state': 'no_data',
                                  'reason': 'no snapshot stored yet', 'simulation': True})
                    continue
                data = row.data or {}
                execution = quote_problem(row)
                items.append({
                    'market': market, 'symbol': symbol, 'source': row.source,
                    'event_time': row.event_time.isoformat() if row.event_time else None,
                    'received_at': row.received_at.isoformat() if row.received_at else None,
                    'age_seconds': int((now() - row.event_time).total_seconds()) if row.event_time else None,
                    'quality': data.get('quality'), 'delay_seconds': data.get('delay_seconds', 0),
                    'daily': bool(data.get('daily')), 'session': data.get('session', ''),
                    'price': str(data.get('price', '')),
                    'execution_problem': execution,
                    'valuation_problem': valuation_problem(row),
                    'state': 'blocked' if execution else 'ok',
                })
        by_market = {}
        for item in items:
            bucket = by_market.setdefault(item['market'], {'total': 0, 'ok': 0, 'blocked': 0, 'no_data': 0})
            bucket['total'] += 1
            bucket[item['state']] += 1
        return {'items': items, 'markets': by_market, 'simulation': True}


# ---------------------------------------------------------------------------
# Risk state
# ---------------------------------------------------------------------------

@app.get('/api/v1/risk')
def risk_status(limit: int = 20, auth=Depends(require_token)):
    """Current limit configuration, circuit-breaker state, and recent rejections so a refusal
    can be traced back to the rule that produced it."""
    with Session() as s:
        r = s.get(Runtime, 1)
        if not r or not r.initialized:
            raise HTTPException(409, 'workspace not initialized')
        accounts = [{
            'market': a.market, 'currency': a.currency, 'cash': str(a.cash),
            'reserved': str(a.reserved), 'equity': str(equity(s, a)),
            'high_water': str(a.high_water), 'day_equity': str(a.day_equity),
            'day': a.day, 'halted': a.halted,
        } for a in s.scalars(select(Account))]
        rejected = list(s.scalars(select(Order).where(Order.status == 'rejected')
                                  .order_by(Order.created_at.desc()).limit(limit)))
        return {
            'paused': r.paused, 'epoch': r.epoch,
            'limits': r.config.get('risk', {}),
            'portfolio_limits': {
                'max_drawdown_pct': str(r.config.get('portfolio_max_drawdown_pct', '')),
                'max_daily_loss_pct': str(r.config.get('portfolio_max_daily_loss_pct', '')),
                'high': str(r.config.get('portfolio_high', '')),
                'day_equity': str(r.config.get('portfolio_day_equity', '')),
                'day': r.config.get('portfolio_day', ''),
            },
            'fx': {'cny_per_usd': str(r.config.get('fx_cny_per_usd', '')), 'at': r.config.get('fx_at', ''),
                   'max_age_hours': r.config.get('fx_max_age_hours', 0)},
            'accounts': accounts,
            'recent_rejections': [{'order_id': o.id, 'market': o.market, 'symbol': o.symbol,
                                   'side': o.side, 'reason': o.reason, 'trace_id': o.trace_id,
                                   'created_at': o.created_at.isoformat() if o.created_at else None}
                                  for o in rejected],
            'simulation': True,
        }


# ---------------------------------------------------------------------------
# Job queue
# ---------------------------------------------------------------------------

@app.get('/api/v1/jobs')
def list_jobs(status: str | None = None, limit: int = 50, auth=Depends(require_token)):
    with Session() as s:
        q = s.query(Job).order_by(Job.id.desc())
        if status:
            q = q.filter(Job.status == status)
        rows = q.limit(limit).all()
        counts = {status_name: count for status_name, count in
                  s.execute(select(Job.status, func.count()).group_by(Job.status)).all()}
        return {'items': [serialize_row(r) for r in rows], 'counts': counts}


# ---------------------------------------------------------------------------
# Events (audit trail)
# ---------------------------------------------------------------------------

@app.get('/api/v1/events')
def list_events(
    kind: str | None = None,
    trace_id: str | None = None,
    since_id: int = 0,
    limit: int = 200,
    auth=Depends(require_token),
):
    with Session() as s:
        q = s.query(Event).order_by(Event.id.desc())
        if kind:
            q = q.filter(Event.kind == kind)
        if trace_id:
            q = q.filter(Event.trace_id == trace_id)
        if since_id:
            q = q.filter(Event.id > since_id)
        rows = q.limit(limit).all()
        return {'items': [serialize_event(e) for e in reversed(rows)]}


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@app.get('/api/v1/stream')
async def stream(request: Request, since_id: int = 0, auth=Depends(require_stream_token)):
    # Honour the browser's own reconnect cursor when it sends one, so a dropped
    # connection resumes instead of replaying history from zero.
    header_id = request.headers.get('last-event-id', '')
    start = max(since_id, int(header_id)) if header_id.isdigit() else since_id
    return StreamingResponse(event_stream(request, start), media_type='text/event-stream')


# ---------------------------------------------------------------------------
# Trace (full chain for a trace_id)
# ---------------------------------------------------------------------------

@app.get('/api/v1/trace/{trace_id}')
def get_trace(trace_id: str, auth=Depends(require_token)):
    with Session() as s:
        decisions = s.query(Decision).filter(Decision.trace_id == trace_id).all()
        orders = s.query(Order).filter(Order.trace_id == trace_id).all()
        fills = s.query(Fill).filter(Fill.trace_id == trace_id).all()
        events = s.query(Event).filter(Event.trace_id == trace_id).order_by(Event.id).all()
        ledger = s.query(Ledger).filter(Ledger.trace_id == trace_id).all()
        return {
            'trace_id': trace_id,
            'decisions': [serialize_row(d) for d in decisions],
            'orders': [serialize_row(o) for o in orders],
            'fills': [serialize_row(f) for f in fills],
            'events': [serialize_event(e) for e in events],
            'ledger': [serialize_row(l) for l in ledger],
            'simulation': True,
        }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@app.get('/api/v1/export/ledger')
def export_ledger_csv(market: str | None = None, auth=Depends(require_token)):
    with Session() as s:
        q = s.query(Ledger).order_by(Ledger.created_at)
        if market:
            q = q.filter(Ledger.market == market)
        rows = q.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'market', 'kind', 'amount', 'balance', 'trace_id', 'created_at', 'details'])
    for r in rows:
        writer.writerow([r.id, r.market, r.kind, str(r.amount), str(r.balance),
                         r.trace_id, r.created_at.isoformat() if r.created_at else '', json.dumps(r.details)])
    content = output.getvalue()
    return Response(content, media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=ledger.csv'})


@app.get('/api/v1/export/fills')
def export_fills_csv(market: str | None = None, auth=Depends(require_token)):
    with Session() as s:
        q = s.query(Fill).join(Order, Fill.order_id == Order.id).order_by(Fill.simulated_at)
        if market:
            q = q.filter(Order.market == market)
        rows = q.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['fill_id', 'order_id', 'trace_id', 'symbol', 'side', 'quantity', 'price', 'fee', 'simulated_at'])
    for f in rows:
        order = s.get(Order, f.order_id)
        writer.writerow([f.id, f.order_id, f.trace_id, order.symbol if order else '',
                         order.side if order else '', str(f.quantity), str(f.price),
                         str(f.fee), f.simulated_at.isoformat() if f.simulated_at else ''])
    content = output.getvalue()
    return Response(content, media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=fills.csv'})


# ---------------------------------------------------------------------------
# Frontend static files (catch-all for SPA routing)
# ---------------------------------------------------------------------------

frontend_dir = Path(settings().frontend_dir)
if frontend_dir.exists():
    app.mount('/assets', StaticFiles(directory=str(frontend_dir / 'assets')), name='static')

    @app.get('/')
    @app.get('/{path:path}')
    async def serve_frontend(path: str = ''):
        root = frontend_dir.resolve()
        if path:
            candidate = (root / path).resolve()
            # A `{path:path}` route will happily carry ../ segments; refuse anything that escapes.
            if not candidate.is_relative_to(root):
                raise HTTPException(404, 'not found')
            if candidate.is_file():
                return FileResponse(candidate)
        index = root / 'index.html'
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(404, 'frontend not built')
