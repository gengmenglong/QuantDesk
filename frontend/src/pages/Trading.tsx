import { useEffect, useState, useCallback } from 'react';
import { api, newIdempotencyKey } from '../api';
import { TracePanel } from '../components/TracePanel';

const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密', poly: '预测' };
const STATUS_BADGE: Record<string, string> = {
  pending: 'badge-yellow', partial: 'badge-yellow', filled: 'badge-green',
  cancelled: 'badge-gray', expired: 'badge-gray', rejected: 'badge-red', checking: 'badge-gray',
};

export function Trading() {
  const [orders, setOrders] = useState<any[]>([]);
  const [fills, setFills] = useState<any[]>([]);
  const [market, setMarket] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [traceId, setTraceId] = useState<string | null>(null);

  // Manual order form
  const [form, setForm] = useState({ market: 'crypto', symbol: 'BTC-USD', side: 'buy', quantity: '1', kind: 'market', limit_price: '' });

  const load = useCallback(async () => {
    try {
      const [o, f] = await Promise.all([
        api.orders({ market: market || undefined, limit: 50 }),
        api.fills({ market: market || undefined, limit: 50 }),
      ]);
      setOrders(o.items);
      setFills(f.items);
    } catch {}
    setLoading(false);
  }, [market]);

  useEffect(() => { load(); const i = setInterval(load, 10000); return () => clearInterval(i); }, [load]);

  const submitOrder = async () => {
    setSubmitting(true);
    try {
      const body: any = { market: form.market, symbol: form.symbol, side: form.side, quantity: form.quantity, kind: form.kind };
      if (form.kind === 'limit' && form.limit_price) body.limit_price = form.limit_price;
      // One key per click: a network retry of this action dedupes, a new click is a new order.
      const order = await api.createOrder(body, newIdempotencyKey());
      await load();
      if (order?.status === 'rejected') setTraceId(order.trace_id);
    } catch (e: any) {
      alert(e.message);
    }
    setSubmitting(false);
  };

  const cancelOrder = async (id: number) => {
    try { await api.cancelOrder(id); await load(); } catch (e: any) { alert(e.message); }
  };

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  return (
    <div className="flex flex-col gap-4">
      <div className="page-title">交易</div>

      {traceId && <TracePanel traceId={traceId} onClose={() => setTraceId(null)} />}

      {/* Manual order form */}
      <div className="card">
        <div className="section-title">手动下单（模拟）</div>
        <div className="flex gap-2 items-center" style={{ flexWrap: 'wrap' }}>
          <select value={form.market} onChange={e => setForm({ ...form, market: e.target.value, symbol: '' })}>
            <option value="us">美股</option>
            <option value="cn">A 股</option>
            <option value="crypto">加密</option>
            <option value="poly">预测</option>
          </select>
          <input value={form.symbol} onChange={e => setForm({ ...form, symbol: e.target.value })}
            placeholder="标的代码" style={{ width: 140 }} />
          <select value={form.side} onChange={e => setForm({ ...form, side: e.target.value })}>
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select>
          <input value={form.quantity} onChange={e => setForm({ ...form, quantity: e.target.value })}
            placeholder="数量" style={{ width: 80 }} type="number" step="any" />
          <select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}>
            <option value="market">市价</option>
            <option value="limit">限价</option>
          </select>
          {form.kind === 'limit' && (
            <input value={form.limit_price} onChange={e => setForm({ ...form, limit_price: e.target.value })}
              placeholder="限价" style={{ width: 80 }} type="number" step="any" />
          )}
          <button className="primary" onClick={submitOrder} disabled={submitting}>
            {submitting ? '提交中...' : '提交'}
          </button>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8 }}>
          订单以决策之后的新报价撮合，不会用生成决策的旧报价立即成交。A 股日线模式仅支持下一交易日市价模拟。
        </div>
      </div>

      {/* Market filter */}
      <div className="flex gap-2">
        {['', 'us', 'cn', 'crypto', 'poly'].map(m => (
          <button key={m} onClick={() => setMarket(m)} style={{
            padding: '4px 12px', fontSize: 11,
            borderColor: market === m ? 'var(--accent)' : 'var(--border)',
            color: market === m ? 'var(--accent)' : 'var(--text-dim)',
          }}>
            {m ? MARKET_LABELS[m] : '全部'}
          </button>
        ))}
      </div>

      {/* Orders table */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '12px 16px 0' }} className="section-title">订单</div>
        <table>
          <thead>
            <tr><th>ID</th><th>市场</th><th>标的</th><th>方向</th><th>数量</th><th>成交</th>
              <th>状态</th><th>原因</th><th>时间</th><th>追溯</th><th>操作</th></tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr><td colSpan={11} className="empty">暂无订单</td></tr>
            ) : orders.map(o => (
              <tr key={o.id}>
                <td>{o.id}</td>
                <td><span className="badge badge-blue">{MARKET_LABELS[o.market] || o.market}</span></td>
                <td style={{ fontFamily: 'var(--font)' }}>{o.symbol}</td>
                <td style={{ color: o.side === 'buy' ? 'var(--green)' : 'var(--red)' }}>
                  {o.side === 'buy' ? '买' : '卖'}
                </td>
                <td>{Number(o.quantity).toFixed(o.market === 'poly' ? 2 : 0)}</td>
                <td>{Number(o.filled).toFixed(o.market === 'poly' ? 2 : 0)}</td>
                <td><span className={`badge ${STATUS_BADGE[o.status] || 'badge-gray'}`}>{o.status}</span></td>
                <td style={{ fontSize: 11, color: o.status === 'rejected' ? 'var(--red)' : 'var(--text-dim)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>{o.reason}</td>
                <td style={{ fontSize: 11 }}>{o.created_at ? new Date(o.created_at).toLocaleTimeString() : ''}</td>
                <td>
                  {o.trace_id && (
                    <button className="trace-link" onClick={() => setTraceId(o.trace_id)}>链路</button>
                  )}
                </td>
                <td>
                  {['pending', 'partial'].includes(o.status) && (
                    <button onClick={() => cancelOrder(o.id)} style={{ fontSize: 11, padding: '2px 8px' }}>撤单</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Fills table */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '12px 16px 0' }} className="section-title">成交记录</div>
        <table>
          <thead>
            <tr><th>ID</th><th>订单</th><th>数量</th><th>成交价</th><th>参考价</th><th>手续费</th><th>模拟时间</th><th>追溯</th></tr>
          </thead>
          <tbody>
            {fills.length === 0 ? (
              <tr><td colSpan={8} className="empty">暂无成交</td></tr>
            ) : fills.map(f => (
              <tr key={f.id}>
                <td>{f.id}</td>
                <td>{f.order_id}</td>
                <td>{Number(f.quantity).toFixed(4)}</td>
                <td>{Number(f.price).toFixed(6)}</td>
                <td>{Number(f.reference).toFixed(6)}</td>
                <td>{Number(f.fee).toFixed(6)}</td>
                <td style={{ fontSize: 11 }}>{f.simulated_at ? new Date(f.simulated_at).toLocaleString() : ''}</td>
                <td>
                  {f.trace_id && (
                    <button className="trace-link" onClick={() => setTraceId(f.trace_id)}>链路</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-dim)' }}>
          模拟时间为行情事件时间，处理时间为本机实际执行时间；A 股日线模拟下两者会跨日。
        </div>
      </div>
    </div>
  );
}
