"""Deterministic, time-ordered strategy replay with next-observation execution."""
from decimal import Decimal
from datetime import datetime
from sqlalchemy import select
from .db import Strategy, Snapshot, now
from .schemas import StrategySpec, EvaluationConfig, RiskLimits
from .core import dec, execution_price, event, lock

D = Decimal


def signal(prices, spec):
    if len(prices) < spec.lookback:
        return 'hold'
    current = prices[-1]
    mean = sum(prices[-spec.lookback:]) / spec.lookback
    if mean <= 0:
        return 'hold'
    deviation = (current / mean - 1) * 100
    if spec.template == 'trend':
        return 'buy' if deviation > spec.threshold else 'sell' if deviation < 0 else 'hold'
    if spec.template == 'mean_reversion':
        return 'buy' if deviation < -spec.threshold else 'sell' if deviation > 0 else 'hold'
    return 'buy' if current < spec.threshold / 100 else 'sell' if current > 1 - spec.threshold / 100 else 'hold'


def replay(rows, spec, risk):
    cash, qty, basis, high, drawdown, trades = D(10000), D(0), D(0), D(10000), D(0), 0
    prices, pending, curve = [], 'hold', []
    day_start, day, stopped = cash, '', False
    for row in rows:
        data = row['data']
        price = dec(data['price'])
        date = row['event_time'][:10]
        eq = cash + qty * price
        if date != day:
            day, day_start = date, eq
        high = max(high, eq)
        dd = (high-eq)/high*100
        drawdown = max(drawdown, dd)
        stopped = stopped or dd >= risk.max_drawdown_pct or (day_start-eq)/day_start*100 >= risk.max_daily_loss_pct
        can_trade = data.get('tradable', False) and data.get('corporate_action_safe', False) and not stopped
        if spec.market == 'cn':
            can_trade = can_trade and bool(data.get('upper_limit') and data.get('lower_limit'))
            if can_trade:
                can_trade = dec(data['lower_limit']) < dec(data['open']) < dec(data['upper_limit'])
        if pending != 'hold' and can_trade:
            _, fill, fee_rate = execution_price(data,pending,risk.fee_bps,risk.slippage_bps,spec.market=='cn')
            available = dec(data.get('ask_size' if pending=='buy' else 'bid_size') or 0)
            if available == 0 and not data.get('bid') and not data.get('ask'):
                available = dec(data.get('size',0))
            if spec.market == 'cn':
                available *= D('.001')
            if pending == 'buy' and qty == 0:
                allocation=min(spec.allocation_pct,risk.max_order_pct,risk.max_position_pct,risk.max_exposure_pct)/100
                amount=min(available,cash*allocation/(fill*(1+fee_rate)))
                if spec.market in ('us','cn'):
                    lot=100 if spec.market=='cn' else 1
                    amount=(amount//lot)*lot
                if amount>0:
                    basis=amount*fill*(1+fee_rate)
                    cash-=basis; qty=amount; trades+=1
            elif pending=='sell' and qty>0:
                amount=min(qty,available)
                if amount>0:
                    cash+=amount*fill*(1-fee_rate); basis*=1-amount/qty; qty-=amount; trades+=1
        prices.append(price)
        pending=signal(prices,spec)
        curve.append(str(cash+qty*price))
    final = cash + qty * (prices[-1] if prices else D(0))
    return {'return_pct':str((final/D(10000)-1)*100),'max_drawdown_pct':str(drawdown),'trades':trades,'curve':curve}


def evaluate_series(rows, spec, config, risk):
    rows=sorted(rows,key=lambda r:r['event_time'])
    if len(rows)<config.min_samples*2:
        return {'eligible':False,'reason':'insufficient_samples','samples':len(rows)}
    split=len(rows)//2
    train=replay(rows[:split],spec,risk)
    holdout=replay(rows[split:],spec,risk)
    eligible=(holdout['trades']>=config.min_trades and dec(holdout['max_drawdown_pct'])<=config.max_drawdown_pct
              and dec(holdout['return_pct'])>config.min_excess_return_pct)
    return {'eligible':eligible,'reason':'passed' if eligible else 'performance_or_trade_gate',
            'samples':len(rows),'train':train,'holdout':holdout,'split_at':rows[split]['event_time']}


def evaluate_strategy(s, id):
    r=lock(s); strategy=s.get(Strategy,id)
    if not strategy or strategy.status not in ('candidate','shadow'):
        return
    spec=StrategySpec.model_validate(strategy.spec)
    config=EvaluationConfig.model_validate(r.config['evaluation'])
    risk=RiskLimits.model_validate(r.config['risk'][spec.market])
    outcomes=[]
    for symbol in spec.symbols:
        rows=list(s.scalars(select(Snapshot).where(Snapshot.market==spec.market,Snapshot.symbol==symbol,
            Snapshot.event_time <= strategy.created_at).order_by(Snapshot.event_time.desc()).limit(4000)))
        outcomes.append(evaluate_series([{'data':x.data,'event_time':x.event_time.isoformat()} for x in rows],spec,config,risk))
    old=strategy.evaluation or {}
    evaluation={'symbols':dict(zip(spec.symbols,outcomes)), 'evaluated_at':now().isoformat(),
                'eligible':all(x['eligible'] for x in outcomes), 'shadow_started_at':old.get('shadow_started_at')}
    if evaluation['eligible']:
        if strategy.status=='candidate':
            strategy.status='shadow'
            evaluation['shadow_started_at']=now().isoformat()
        shadow_start=datetime.fromisoformat(evaluation['shadow_started_at'])
        shadow=[]
        for symbol in spec.symbols:
            rows=list(s.scalars(select(Snapshot).where(Snapshot.market==spec.market,Snapshot.symbol==symbol,
                Snapshot.received_at>shadow_start).order_by(Snapshot.event_time)))
            result=replay([{'data':x.data,'event_time':x.event_time.isoformat()} for x in rows],spec,risk)
            result['samples']=len(rows)
            shadow.append(result)
        evaluation['shadow']=shadow
        current=s.scalar(select(Strategy).where(Strategy.name==strategy.name,Strategy.status=='active'))
        # Compare on exactly the same shadow window and cost model.
        baseline=[]
        for symbol in spec.symbols:
            rows=list(s.scalars(select(Snapshot).where(Snapshot.market==spec.market,Snapshot.symbol==symbol,
                Snapshot.received_at>shadow_start).order_by(Snapshot.event_time)))
            baseline.append(replay([{'data':x.data,'event_time':x.event_time.isoformat()} for x in rows],
                StrategySpec.model_validate(current.spec),risk) if current else {'return_pct':'0'})
        if all(x['samples']>=config.shadow_samples and x['trades']>=config.min_trades and
               dec(x['max_drawdown_pct'])<=config.max_drawdown_pct and
               dec(x['return_pct'])>dec(b['return_pct'])+config.min_excess_return_pct for x,b in zip(shadow,baseline)):
            if current:
                current.status='retired'
            strategy.status='active'
            evaluation['published_at']=now().isoformat()
            event(s,'strategy.published',{'strategy_id':strategy.id,'previous_id':current.id if current else None})
    strategy.evaluation=evaluation
    event(s,'strategy.evaluated',{'strategy_id':id,'status':strategy.status,'eligible':evaluation['eligible']})


def rollback_strategy(s, id, reason='user rollback'):
    """Retire an active strategy and restore the previous version of the same name, once.
    The `status == 'active'` guard is what makes repeat calls no-ops."""
    lock(s); strategy=s.get(Strategy,id)
    if not strategy or strategy.status!='active':
        return strategy
    previous=s.scalar(select(Strategy).where(Strategy.name==strategy.name,Strategy.market==strategy.market,
        Strategy.status=='retired').order_by(Strategy.version.desc()).limit(1))
    strategy.status='rolled_back'
    if previous:
        previous.status='active'
    evaluation=dict(strategy.evaluation or {})
    evaluation['rolled_back_at']=now().isoformat()
    evaluation['rollback_reason']=reason
    evaluation['restored_id']=previous.id if previous else None
    strategy.evaluation=evaluation
    event(s,'strategy.rolled_back',{'strategy_id':id,'restored_id':previous.id if previous else None,'reason':reason})
    return strategy


def monitor_strategy(s, id):
    """Roll back an active strategy that breaches its gates on live simulated data.
    The `status == 'active'` guard makes this one-shot: a second run cannot roll back twice."""
    r=lock(s); strategy=s.get(Strategy,id)
    if not strategy or strategy.status!='active':
        return
    spec=StrategySpec.model_validate(strategy.spec)
    config=EvaluationConfig.model_validate(r.config['evaluation'])
    risk=RiskLimits.model_validate(r.config['risk'][spec.market])
    window=[]
    for symbol in spec.symbols:
        rows=list(s.scalars(select(Snapshot).where(Snapshot.market==spec.market,Snapshot.symbol==symbol,
            Snapshot.event_time>strategy.created_at).order_by(Snapshot.event_time)))
        result=replay([{'data':x.data,'event_time':x.event_time.isoformat()} for x in rows],spec,risk)
        result['samples']=len(rows)
        window.append(result)
    evaluation=dict(strategy.evaluation or {})
    evaluation['monitor']=window
    evaluation['monitored_at']=now().isoformat()
    # Without enough live evidence there is nothing to judge, so keep it running.
    if not window or not all(w['samples']>=config.shadow_samples for w in window):
        strategy.evaluation=evaluation
        event(s,'strategy.monitored',{'strategy_id':id,'status':'active','reason':'insufficient_monitor_samples'})
        return
    breached=any(dec(w['max_drawdown_pct'])>config.max_drawdown_pct for w in window)
    if not breached:
        strategy.evaluation=evaluation
        event(s,'strategy.monitored',{'strategy_id':id,'status':'active','reason':'within_gates'})
        return
    rollback_strategy(s,id,'max_drawdown_pct breached in live monitoring window')
