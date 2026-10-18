import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';

const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密', poly: '预测' };

/** Ordered stages of the chain, so a refusal can be read from decision down to ledger. */
const STAGES = [
  { key: 'decisions', label: 'AI 决策' },
  { key: 'orders', label: '订单与风控' },
  { key: 'fills', label: '模拟成交' },
  { key: 'ledger', label: '资金分录' },
  { key: 'events', label: '审计事件' },
] as const;

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  return String(value);
}

function time(value?: string | null): string {
  if (!value) return '-';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

export function TracePanel({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [trace, setTrace] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setTrace(await api.trace(traceId));
    } catch (e: any) {
      setError(e.message);
    }
    setLoading(false);
  }, [traceId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const orders = trace?.orders ?? [];
  const rejected = orders.filter((o: any) => o.status === 'rejected');

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={e => e.stopPropagation()}>
        <div className="flex justify-between items-center" style={{ marginBottom: 16 }}>
          <div>
            <div className="page-title" style={{ marginBottom: 4 }}>全链路追溯</div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)', wordBreak: 'break-all' }}>trace {traceId}</div>
          </div>
          <button onClick={onClose}>关闭</button>
        </div>

        <div className="sim-banner" style={{ marginBottom: 16 }}>SIMULATION ONLY · 模拟盘链路</div>

        {loading && <div className="empty"><span className="spinner" /></div>}
        {error && <div className="error-msg">{error}</div>}

        {!loading && !error && trace && (
          <div className="flex flex-col gap-3">
            {rejected.length > 0 && (
              <div className="card" style={{ borderColor: 'var(--red)' }}>
                <div className="section-title" style={{ color: 'var(--red)' }}>风控拒绝</div>
                {rejected.map((o: any) => (
                  <div key={o.id} style={{ fontSize: 12 }}>
                    <span className="badge badge-red" style={{ marginRight: 8 }}>订单 {o.id}</span>
                    {MARKET_LABELS[o.market] || o.market} · {o.symbol} · {o.side} {fmt(o.quantity)}
                    <div style={{ color: 'var(--text-dim)', marginTop: 4 }}>原因：{fmt(o.reason)}</div>
                  </div>
                ))}
              </div>
            )}

            {STAGES.map(stage => {
              const rows = trace[stage.key] ?? [];
              return (
                <div key={stage.key} className="card">
                  <div className="section-title">
                    {stage.label}
                    <span className="badge badge-gray" style={{ marginLeft: 8 }}>{rows.length}</span>
                  </div>
                  {rows.length === 0 ? (
                    <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>该环节无记录</div>
                  ) : (
                    <div className="flex flex-col gap-3">
                      {rows.map((row: any, i: number) => (
                        <dl className="kv" key={row.id ?? i}>
                          {stage.key === 'decisions' && <>
                            <dt>决策 #{fmt(row.id)}</dt><dd>{fmt(row.status)}</dd>
                            <dt>市场 / 模型</dt><dd>{MARKET_LABELS[row.market] || row.market} · {fmt(row.model)}</dd>
                            <dt>Epoch</dt><dd>{fmt(row.epoch)}</dd>
                            <dt>动作</dt><dd>{fmt(row.output?.action)}</dd>
                            <dt>依据</dt><dd>{fmt(row.output?.reason)}</dd>
                            <dt>Token / 费用</dt><dd>{fmt(row.tokens)} / ${Number(row.cost || 0).toFixed(6)}</dd>
                            <dt>时间</dt><dd>{time(row.created_at)}</dd>
                          </>}
                          {stage.key === 'orders' && <>
                            <dt>订单 #{fmt(row.id)}</dt>
                            <dd><span className={`badge ${row.status === 'rejected' ? 'badge-red' : row.status === 'filled' ? 'badge-green' : 'badge-yellow'}`}>{fmt(row.status)}</span></dd>
                            <dt>标的</dt><dd>{MARKET_LABELS[row.market] || row.market} · {fmt(row.symbol)}</dd>
                            <dt>方向 / 数量</dt><dd>{fmt(row.side)} {fmt(row.quantity)}（成交 {fmt(row.filled)}）</dd>
                            <dt>类型 / 限价</dt><dd>{fmt(row.kind)} · {fmt(row.limit_price)}</dd>
                            <dt>冻结资金</dt><dd>{fmt(row.reserve)}</dd>
                            <dt>风控原因</dt><dd>{fmt(row.reason)}</dd>
                            <dt>快照 ID</dt><dd>{fmt(row.snapshot_id)}</dd>
                            <dt>创建 / 过期</dt><dd>{time(row.created_at)} → {time(row.expires_at)}</dd>
                          </>}
                          {stage.key === 'fills' && <>
                            <dt>成交 #{fmt(row.id)}</dt><dd>订单 {fmt(row.order_id)}</dd>
                            <dt>数量 / 价格</dt><dd>{fmt(row.quantity)} @ {fmt(row.price)}</dd>
                            <dt>参考价</dt><dd>{fmt(row.reference)}</dd>
                            <dt>手续费</dt><dd>{fmt(row.fee)}</dd>
                            <dt>模拟时间</dt><dd>{time(row.simulated_at)}</dd>
                            <dt>处理时间</dt><dd>{time(row.processed_at)}</dd>
                          </>}
                          {stage.key === 'ledger' && <>
                            <dt>分录 #{fmt(row.id)}</dt><dd>{fmt(row.kind)}</dd>
                            <dt>金额 / 余额</dt>
                            <dd>
                              <span style={{ color: Number(row.amount) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                                {Number(row.amount) >= 0 ? '+' : ''}{fmt(row.amount)}
                              </span>
                              {' → '}{fmt(row.balance)}
                            </dd>
                            <dt>市场</dt><dd>{MARKET_LABELS[row.market] || row.market}</dd>
                            <dt>幂等键</dt><dd>{fmt(row.key)}</dd>
                            <dt>时间</dt><dd>{time(row.created_at)}</dd>
                          </>}
                          {stage.key === 'events' && <>
                            <dt>事件 #{fmt(row.id)}</dt><dd><span className="badge badge-blue">{fmt(row.kind)}</span></dd>
                            <dt>载荷</dt><dd>{JSON.stringify(row.payload)}</dd>
                            <dt>时间</dt><dd>{time(row.created_at)}</dd>
                          </>}
                        </dl>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
