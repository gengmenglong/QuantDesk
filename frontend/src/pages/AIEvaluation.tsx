import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';
import { TracePanel } from '../components/TracePanel';

const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密', poly: '预测' };
const STATUS_BADGE: Record<string, string> = {
  running: 'badge-yellow', applied: 'badge-green', rejected: 'badge-red',
  discarded: 'badge-gray', failed: 'badge-red',
};
const STATUS_LABEL: Record<string, string> = {
  running: '执行中', applied: '已执行', rejected: '已拒绝', discarded: '已丢弃（过期 epoch）', failed: '调用失败',
};

function BudgetBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const tone = pct >= 100 ? 'over' : pct >= 80 ? 'warn' : '';
  return (
    <div>
      <div className="flex justify-between" style={{ fontSize: 11, marginBottom: 4 }}>
        <span style={{ color: 'var(--text-dim)' }}>{label}</span>
        <span>{used.toLocaleString()} / {limit.toLocaleString()}</span>
      </div>
      <div className="bar-track"><div className={`bar-fill ${tone}`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export function AIEvaluation() {
  const [decisions, setDecisions] = useState<any[]>([]);
  const [budget, setBudget] = useState<any>(null);
  const [market, setMarket] = useState('');
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [traceId, setTraceId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.decisions({ market: market || undefined, limit: 100 });
      setDecisions(res.items);
      setTotal(res.total);
    } catch {}
    try {
      setBudget(await api.aiBudget());
    } catch {
      // Budget is unavailable until the workspace is initialized; the table still renders.
      setBudget(null);
    }
    setLoading(false);
  }, [market]);

  useEffect(() => { load(); const i = setInterval(load, 15000); return () => clearInterval(i); }, [load]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  const applied = decisions.filter(d => d.status === 'applied').length;
  const rejected = decisions.filter(d => d.status === 'rejected').length;
  const failed = decisions.filter(d => d.status === 'failed' || d.status === 'discarded').length;

  return (
    <div className="flex flex-col gap-4">
      <div className="page-title">AI 决策与评估</div>

      {traceId && <TracePanel traceId={traceId} onClose={() => setTraceId(null)} />}

      {/* Daily budget against configured caps */}
      <div className="card">
        <div className="flex justify-between items-center" style={{ marginBottom: 12 }}>
          <div className="section-title" style={{ marginBottom: 0 }}>当日模型预算（UTC {budget?.utc_day || '-'}）</div>
          {budget && (
            <span className={`badge ${budget.allowed ? 'badge-green' : 'badge-red'}`}>
              {budget.allowed ? '允许调用' : `已阻断：${budget.blocked_reason}`}
            </span>
          )}
        </div>
        {budget ? (
          <div className="grid-3">
            <BudgetBar label="调用次数" used={budget.used.calls} limit={budget.limits.max_calls_day} />
            <BudgetBar label="Token" used={budget.used.tokens} limit={budget.limits.max_tokens_day} />
            <BudgetBar label="费用 (USD)" used={Number(budget.used.cost)} limit={Number(budget.limits.max_cost_day)} />
          </div>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>工作区未初始化，暂无预算数据</div>
        )}
      </div>

      <div className="grid-4">
        <div className="card" style={{ textAlign: 'center' }}>
          <div className="stat-label">总决策数</div>
          <div className="stat-value">{total}</div>
        </div>
        <div className="card" style={{ textAlign: 'center' }}>
          <div className="stat-label">已执行</div>
          <div className="stat-value" style={{ color: 'var(--green)' }}>{applied}</div>
        </div>
        <div className="card" style={{ textAlign: 'center' }}>
          <div className="stat-label">风控拒绝</div>
          <div className="stat-value" style={{ color: 'var(--red)' }}>{rejected}</div>
        </div>
        <div className="card" style={{ textAlign: 'center' }}>
          <div className="stat-label">失败 / 丢弃</div>
          <div className="stat-value" style={{ color: 'var(--yellow)' }}>{failed}</div>
        </div>
      </div>

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

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>ID</th><th>市场</th><th>模型</th><th>Epoch</th><th>状态</th>
              <th>动作</th><th>依据 / 失败原因</th><th>Token</th><th>费用</th><th>时间</th><th>追溯</th>
            </tr>
          </thead>
          <tbody>
            {decisions.length === 0 ? (
              <tr><td colSpan={11} className="empty">暂无 AI 决策记录</td></tr>
            ) : decisions.map(d => {
              const action = d.output?.action || '-';
              const detail = d.output?.reason || '';
              return (
                <tr key={d.id}>
                  <td>{d.id}</td>
                  <td><span className="badge badge-blue">{MARKET_LABELS[d.market] || d.market}</span></td>
                  <td style={{ fontFamily: 'var(--font)', fontSize: 11 }}>{d.model}</td>
                  <td>{d.epoch}</td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[d.status] || 'badge-gray'}`}>
                      {STATUS_LABEL[d.status] || d.status}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${action === 'order' ? 'badge-green' : action === 'strategy' ? 'badge-blue' : 'badge-gray'}`}>
                      {action}
                    </span>
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--text-dim)', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}
                    title={detail}>
                    {detail}
                  </td>
                  <td>{d.tokens?.toLocaleString() || 0}</td>
                  <td>${Number(d.cost || 0).toFixed(6)}</td>
                  <td style={{ fontSize: 11 }}>{d.created_at ? new Date(d.created_at).toLocaleString() : ''}</td>
                  <td>
                    {d.trace_id && (
                      <button className="trace-link" onClick={() => setTraceId(d.trace_id)}>链路</button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
        AI 决策由 worker 后台调度：美股/加密/预测市场每 15 分钟一批，A 股每个新交易日日线批次一次。
        超时、格式错误、越权动作或预算耗尽均不生成订单，并在此处记录原因。全部为模拟盘。
      </div>
    </div>
  );
}
