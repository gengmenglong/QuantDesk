import { useEffect, useState, useCallback, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { api } from '../api';
import { TracePanel } from '../components/TracePanel';

const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密现货', poly: '预测市场' };
const CURRENCY: Record<string, string> = { us: 'USD', cn: 'CNY', crypto: 'USD', poly: 'USD' };
const MARKETS = ['us', 'cn', 'crypto', 'poly'];
const CURVE_COLOR: Record<string, string> = { us: '#58a6ff', cn: '#f85149', crypto: '#d29922', poly: '#3fb950' };

/** Per-market cash balance history. Charts are kept per currency rather than summed,
 * because a cross-currency total is only meaningful with a valid user-supplied FX rate. */
function BalanceChart({ market, entries }: { market: string; entries: any[] }) {
  const data = useMemo(
    () => entries
      .filter(e => e.market === market)
      .map(e => ({ t: e.created_at ? new Date(e.created_at).getTime() : 0, v: Number(e.balance) }))
      .sort((a, b) => a.t - b.t),
    [entries, market],
  );
  if (data.length === 0) {
    return <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>暂无资金流水</div>;
  }
  return (
    <div style={{ width: '100%', height: 120 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#30363d" strokeDasharray="2 2" />
          <XAxis dataKey="t" tick={{ fontSize: 9, fill: '#8b949e' }}
            tickFormatter={(v: number) => new Date(v).toLocaleDateString()} />
          <YAxis domain={['auto', 'auto']} tick={{ fontSize: 9, fill: '#8b949e' }} width={56} />
          <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', fontSize: 11 }}
            labelFormatter={(v: number) => new Date(v).toLocaleString()} />
          <Line type="stepAfter" dataKey="v" stroke={CURVE_COLOR[market]} dot={false}
            strokeWidth={1.5} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Dashboard() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  const [ledger, setLedger] = useState<any[]>([]);
  const [portfolio, setPortfolio] = useState('0');
  const [fxValid, setFxValid] = useState(true);
  const [loading, setLoading] = useState(true);
  const [traceId, setTraceId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, p, e, l] = await Promise.all([
        api.accounts(), api.positions(), api.events({ limit: 20 }), api.ledger({ limit: 200 }),
      ]);
      setAccounts(a.items);
      setPositions(p.items);
      setEvents(e.items);
      setLedger(l.items);
      setPortfolio(a.portfolio_value);
    } catch {}
    try {
      const r = await api.risk();
      // A stale or missing rate means the combined figure is not trustworthy.
      setFxValid(Boolean(r.fx?.cny_per_usd) && !r.blocked_reason);
    } catch {
      setFxValid(false);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); const i = setInterval(load, 15000); return () => clearInterval(i); }, [load]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  return (
    <div className="flex flex-col gap-4">
      <div className="page-title">工作台总览</div>

      {traceId && <TracePanel traceId={traceId} onClose={() => setTraceId(null)} />}

      <div className="card" style={{ textAlign: 'center' }}>
        <div className="stat-label">组合估值（USD 折算）</div>
        <div className="stat-value">
          {fxValid
            ? `$${Number(portfolio).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : '汇率缺失或已过期，仅展示分币种结果'}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>模拟盘 · 仅供学习研究</div>
      </div>

      <div className="grid-4">
        {accounts.map(a => (
          <div key={a.market} className="card">
            <div className="flex justify-between items-center" style={{ marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>{MARKET_LABELS[a.market] || a.market}</span>
              <span className={`badge ${a.halted ? 'badge-red' : 'badge-green'}`}>
                {a.halted ? '熔断' : '正常'}
              </span>
            </div>
            <div className="stat-value" style={{ fontSize: 18 }}>
              {Number(a.equity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              <span style={{ fontSize: 12, color: 'var(--text-dim)', marginLeft: 4 }}>{CURRENCY[a.market]}</span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
              现金 {Number(a.cash).toFixed(2)} · 冻结 {Number(a.reserved).toFixed(2)}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
              初始 {Number(a.initial).toFixed(2)} · 高点 {Number(a.high_water).toFixed(2)}
            </div>
            <div style={{ marginTop: 8 }}>
              <BalanceChart market={a.market} entries={ledger} />
            </div>
          </div>
        ))}
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="section-title">当前持仓</div>
          {positions.length === 0 ? (
            <div className="empty" style={{ padding: 20 }}>暂无持仓</div>
          ) : (
            <table>
              <thead><tr><th>市场</th><th>标的</th><th>数量</th><th>成本</th><th>已实现</th></tr></thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i}>
                    <td>{MARKET_LABELS[p.market] || p.market}</td>
                    <td style={{ fontFamily: 'var(--font)' }}>{p.symbol}</td>
                    <td>{Number(p.quantity).toFixed(p.market === 'poly' ? 2 : 0)}</td>
                    <td>{Number(p.cost).toFixed(4)}</td>
                    <td style={{ color: Number(p.realized) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {Number(p.realized).toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <div className="section-title">最近事件</div>
          {events.length === 0 ? (
            <div className="empty" style={{ padding: 20 }}>暂无事件</div>
          ) : (
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              {events.slice(0, 15).map((e, i) => (
                <div key={i} style={{
                  padding: '6px 0', borderBottom: '1px solid var(--border)',
                  display: 'flex', justifyContent: 'space-between', fontSize: 12,
                }}>
                  <span>
                    <span className={`badge ${e.kind.includes('filled') || e.kind.includes('settled') ? 'badge-green'
                      : e.kind.includes('rejected') || e.kind.includes('paused') || e.kind.includes('rolled_back') || e.kind.includes('failed')
                        ? 'badge-red' : 'badge-blue'}`}
                      style={{ marginRight: 8 }}>{e.kind}</span>
                    {e.trace_id && (
                      <button className="trace-link" onClick={() => setTraceId(e.trace_id)}>
                        {e.trace_id.slice(0, 8)}
                      </button>
                    )}
                  </span>
                  <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                    {e.created_at ? new Date(e.created_at).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
        资金曲线按市场分别以其记账币种展示，不做跨币种相加；组合估值仅在汇率有效时给出。
        四个市场账户 {MARKETS.map(m => MARKET_LABELS[m]).join(' / ')} 独立记账，全部为模拟盘。
      </div>
    </div>
  );
}
