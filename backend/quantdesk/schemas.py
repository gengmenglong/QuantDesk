from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Market = Literal['us', 'cn', 'crypto', 'poly']
Positive = __import__('typing').Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=10)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False, validate_default=True)


class MarketSnapshot(Strict):
    market: Market
    symbol: str = Field(min_length=1, max_length=160)
    source: str
    currency: str
    event_time: datetime
    price: Positive
    bid: Positive | None = None
    ask: Positive | None = None
    size: Decimal = Field(default=0, ge=0)
    bid_size: Decimal = Field(default=0, ge=0)
    ask_size: Decimal = Field(default=0, ge=0)
    delay_seconds: int = Field(default=0, ge=0)
    quality: Literal['real', 'delayed', 'demo', 'unavailable'] = 'real'
    tradable: bool = True
    reason: str = ''
    daily: bool = False
    open: Positive | None = None
    session: str = ''
    upper_limit: Positive | None = None
    lower_limit: Positive | None = None
    corporate_action_safe: bool = True
    outcome: Literal['Yes', 'No'] | None = None
    condition_id: str | None = None

    @model_validator(mode='after')
    def aware_time(self):
        if self.event_time.tzinfo is None:
            raise ValueError('timezone required')
        if self.market == 'poly' and self.price > 1:
            raise ValueError('prediction price exceeds one')
        return self


class OrderIntent(Strict):
    market: Market
    symbol: str
    side: Literal['buy', 'sell']
    quantity: Positive
    kind: Literal['market', 'limit'] = 'market'
    limit_price: Positive | None = None
    snapshot_id: int
    strategy_id: int | None = None

    @model_validator(mode='after')
    def limit_required(self):
        if self.kind == 'limit' and self.limit_price is None:
            raise ValueError('limit price required')
        if self.market == 'cn' and self.kind != 'market':
            raise ValueError('daily simulation accepts market orders only')
        return self


class RiskLimits(Strict):
    max_order_pct: Decimal = Field(gt=0, le=100)
    max_position_pct: Decimal = Field(gt=0, le=100)
    max_exposure_pct: Decimal = Field(gt=0, le=100)
    max_daily_loss_pct: Decimal = Field(gt=0, le=100)
    max_drawdown_pct: Decimal = Field(gt=0, le=100)
    fee_bps: Decimal = Field(default=10, ge=0, le=1000)
    slippage_bps: Decimal = Field(default=5, ge=0, le=1000)


class ModelConfig(Strict):
    base_url: str = 'https://api.openai.com/v1'
    model: str = ''
    max_calls_day: int = Field(ge=1, le=10000)
    max_tokens_day: int = Field(ge=1000, le=100000000)
    max_cost_day: Decimal = Field(gt=0)
    input_cost_million: Decimal | None = Field(default=None, ge=0)
    output_cost_million: Decimal | None = Field(default=None, ge=0)
    max_output_tokens: int = Field(default=1000, ge=256, le=4000)


class EvaluationConfig(Strict):
    min_samples: int = Field(default=60, ge=20)
    min_trades: int = Field(default=10, ge=1)
    shadow_samples: int = Field(default=20, ge=5)
    max_drawdown_pct: Decimal = Field(default=10, gt=0, le=100)
    min_excess_return_pct: Decimal = Field(default=0, ge=0)


class WorkspaceConfig(Strict):
    whitelist: dict[Market, list[str]]
    risk: dict[Market, RiskLimits]
    model: ModelConfig
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    fx_cny_per_usd: Positive
    fx_at: datetime
    fx_max_age_hours: int = Field(default=168, ge=1, le=8760)
    portfolio_max_drawdown_pct: Decimal = Field(gt=0, le=100)
    portfolio_max_daily_loss_pct: Decimal = Field(gt=0, le=100)
    @model_validator(mode='after')
    def all_markets(self):
        if set(self.risk) != {'us', 'cn', 'crypto', 'poly'} or set(self.whitelist) != set(self.risk):
            raise ValueError('configure all four markets')
        if self.fx_at.tzinfo is None:
            raise ValueError('FX timestamp must include timezone')
        return self


class Setup(Strict):
    capital: dict[Market, Positive]
    config: WorkspaceConfig
    @model_validator(mode='after')
    def capital_all(self):
        if set(self.capital) != {'us', 'cn', 'crypto', 'poly'}:
            raise ValueError('capital required for four markets')
        return self


class StrategySpec(Strict):
    name: str = Field(min_length=1, max_length=80)
    market: Market
    symbols: list[str] = Field(min_length=1, max_length=30)
    template: Literal['trend', 'mean_reversion', 'probability']
    lookback: int = Field(default=20, ge=5, le=200)
    threshold: Decimal = Field(default=2, gt=0, le=50)
    allocation_pct: Decimal = Field(default=5, gt=0, le=25)


class ModelDecision(Strict):
    action: Literal['hold', 'order', 'strategy']
    reason: str = Field(min_length=1, max_length=2000)
    order: OrderIntent | None = None
    strategy: StrategySpec | None = None
    @model_validator(mode='after')
    def consistent(self):
        if (self.action == 'order') != (self.order is not None):
            raise ValueError('order/action mismatch')
        if (self.action == 'strategy') != (self.strategy is not None):
            raise ValueError('strategy/action mismatch')
        return self
