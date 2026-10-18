from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import create_engine, String, Numeric, DateTime, JSON, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Runtime(Base):
    __tablename__ = 'runtime'
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(default=True)
    initialized: Mapped[bool] = mapped_column(default=False)
    epoch: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=0)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secrets: Mapped[str] = mapped_column(Text, default='')
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Account(Base):
    __tablename__ = 'accounts'
    market: Mapped[str] = mapped_column(String(16), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    cash: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    reserved: Mapped[Decimal] = mapped_column(Numeric(30, 10), default=0)
    initial: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    high_water: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    day_equity: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    day: Mapped[str] = mapped_column(String(10), default='')
    halted: Mapped[bool] = mapped_column(default=False)


class Snapshot(Base):
    __tablename__ = 'snapshots'
    __table_args__ = (UniqueConstraint('market', 'symbol', 'source', 'event_time'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(160), index=True)
    source: Mapped[str] = mapped_column(String(80))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSON)
    consumed: Mapped[Decimal] = mapped_column(Numeric(30, 10), default=0)


class Order(Base):
    __tablename__ = 'orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(160))
    side: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    filled: Mapped[Decimal] = mapped_column(Numeric(30, 10), default=0)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    reserve: Mapped[Decimal] = mapped_column(Numeric(30, 10), default=0)
    status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    reason: Mapped[str] = mapped_column(Text, default='')
    snapshot_id: Mapped[int] = mapped_column(Integer)
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Position(Base):
    __tablename__ = 'positions'
    __table_args__ = (UniqueConstraint('market', 'symbol'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(160))
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10), default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(30, 10), default=0)
    realized: Mapped[Decimal] = mapped_column(Numeric(30, 10), default=0)
    lots: Mapped[list] = mapped_column(JSON, default=list)


class Fill(Base):
    __tablename__ = 'fills'
    __table_args__ = (UniqueConstraint('order_id', 'snapshot_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    price: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    reference: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    fee: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    simulated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Ledger(Base):
    __tablename__ = 'ledger'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    balance: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Strategy(Base):
    __tablename__ = 'strategies'
    __table_args__ = (UniqueConstraint('name', 'version'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(16), default='candidate')
    spec: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Decision(Base):
    __tablename__ = 'decisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(120))
    epoch: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    inputs: Mapped[dict] = mapped_column(JSON)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    tokens: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str] = mapped_column(Text, default='')


class Mutation(Base):
    __tablename__ = 'mutations'
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)


engine = create_engine(settings().database_url, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
