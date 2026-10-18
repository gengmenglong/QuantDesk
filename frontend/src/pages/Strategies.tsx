import { useEffect, useState, useCallback, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { api } from '../api';

const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密', poly: '预测' };
const STATUS_BADGE: Record<string, string> = {
  candidate: 'badge-yellow', shadow: 'badge-blue', active: 'badge-green',
  retired: 'badge-gray', rolled_back: 'badge-red',
};
const STATUS_LABEL: Record<string, string> = {
  candidate: '候选', shadow: '影子运行', active: '已发布', retired: '已退役', rolled_back: '已回滚',
};
/** Spec fields worth diffing; the rest is shown raw. */
const SPEC_FIELDS = ['market', 'template', 'lookback', 'threshold', 'allocation_pct', 'symbols'];

function CurveChart({ title, curve, color }: { title: string; curve: string[]; color: string }) {
  const data = useMemo(
    () => (curve || []).map((v, i) => ({ i, v: Number(v) })),
    [curve],
  );
  if (data.length === 0) {
    return (
      <div>
        <div className="section-title" style={{ fontSize: 12 }}>{title}</div>
        <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>无曲线数据</div>
      </div>
    );
  }
  return (
    <div>
      <div className="section-title" style={{ fontSize: 12 }}>{title}</div>
      <div style={{ width: '100%', height: 140 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#30363d" strokeDasharray="2 2" />
            <XAxis dataKey="i" tick={{ fontSize: 9, fill: '#8b949e' }} />
            <YAxis domain={['auto', 'auto']} tick={{ fontSize: 9, fill: '#8b949e' }} width={52} />
            <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', fontSize: 11 }} />
            <Line type="monotone" dataKey="v" stroke={color} dot={false} strokeWidth={1.5} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function SpecDiff({ current, previous }: { current: any; previous: any | null }) {
  if (!previous) {
    return <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>无更早版本可比较</div>;
  }
  return (
    <dl className="kv">
      {SPEC_FIELDS.map(field => {
        const a = JSON.stringify(previous?.[field]);
        const b = JSON.stringify(current?.[field]);
        const changed = a !== b;
        return (
          <div key={field} style={{ display: 'contents' }}>
            <dt>{field}</dt>
            <dd className={changed ? 'diff-added' : 'diff-same'}>
              {changed ? <>{a} → <strong>{b}</strong></> : b}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function Strategies() {
  const [strategies, setStrategies] = useState<any[]>([]);
  const [lifecycle, setLifecycle] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      const [res, published, rolledBack, evaluated] = await Promise.all([
        api.strategies(),
        api.events({ kind: 'strategy.published', limit: 50 }),
        api.events({ kind: 'strategy.rolled_back', limit: 50 }),
        api.events({ kind: 'strategy.evaluated', limit: 50 }),
      ]);
      setStrategies(res.items);
      const merged = [...published.items, ...rolledBack.items, ...evaluated.items]
        .sort((a, b) => Number(b.id) - Number(a.id));
      setLifecycle(merged);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const viewDetail = async (id: number) => {
    try {
      setSelected(await api.strategy(id));
    } catch {}
  };

  const doEvaluate = async (id: number) => {
    setBusy(id);
    setMessage('');
    try {
      await api.evaluateStrategy(id);
      setMessage('评估任务已入队，由 worker 执行回放；完成后状态会自动推进。');
      await load();
    } catch (e: any) { setMessage(`评估失败：${e.message}`); }
    setBusy(null);
  };

  const doRollback = async (id: number) => {
    setBusy(id);
    setMessage('');
    try {
      const r = await api.rollbackStrategy(id);
      setMessage(r.restored_id
        ? `已回滚策略 ${id}，恢复上一版本 #${r.restored_id} 为已发布。`
        : `已回滚策略 ${id}；无更早的已退役版本可恢复，该市场暂无已发布策略。`);
      await load();
      if (selected?.id === id) await viewDetail(id);
    } catch (e: any) { setMessage(`回滚失败：${e.message}`); }
    setBusy(null);
  };

  const previousVersion = useMemo(() => {
    if (!selected) return null;
    return strategies.find(s => s.name === selected.name && s.market === selected.market
      && s.version === selected.version - 1) || null;
  }, [selected, strategies]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  const ev = selected?.evaluation || {};

  return (
    <div className="flex flex-col gap-4">
      <div className="page-title">策略管理与生命周期</div>

      {message && <div className="card" style={{ fontSize: 12 }}>{message}</div>}

      <div className="flex gap-4" style={{ alignItems: 'flex-start' }}>
        <div className="card" style={{ flex: 1, padding: 0, overflowX: 'auto' }}>
          <table>
            <thead>
              <tr><th>ID</th><th>名称</th><th>市场</th><th>版本</th><th>状态</th><th>创建时间</th><th>操作</th></tr>
            </thead>
            <tbody>
              {strategies.length === 0 ? (
                <tr><td colSpan={7} className="empty">暂无策略，AI 模块运行后将自动创建候选策略</td></tr>
              ) : strategies.map(s => (
                <tr key={s.id} style={{ cursor: 'pointer', background: selected?.id === s.id ? 'var(--bg-hover)' : '' }}
                  onClick={() => viewDetail(s.id)}>
                  <td>{s.id}</td>
                  <td style={{ fontFamily: 'var(--font)' }}>{s.name}</td>
                  <td><span className="badge badge-blue">{MARKET_LABELS[s.market] || s.market}</span></td>
                  <td>v{s.version}</td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[s.status] || 'badge-gray'}`}>
                      {STATUS_LABEL[s.status] || s.status}
                    </span>
                  </td>
                  <td style={{ fontSize: 11 }}>{s.created_at ? new Date(s.created_at).toLocaleString() : ''}</td>
                  <td>
                    <div className="flex gap-2">
                      <button onClick={e => { e.stopPropagation(); doEvaluate(s.id); }}
                        disabled={busy === s.id || !['candidate', 'shadow'].includes(s.status)}
                        style={{ fontSize: 11, padding: '2px 8px' }}>
                        {busy === s.id ? '...' : '评估'}
                      </button>
                      <button className="danger" onClick={e => { e.stopPropagation(); doRollback(s.id); }}
                        disabled={busy === s.id || s.status !== 'active'}
                        style={{ fontSize: 11, padding: '2px 8px' }}>
                        回滚
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {selected && (
          <div className="flex flex-col gap-3" style={{ width: 460, minWidth: 460 }}>
            <div className="card">
              <div className="section-title">{selected.name} v{selected.version}</div>
              <dl className="kv">
                <dt>状态</dt>
                <dd>
                  <span className={`badge ${STATUS_BADGE[selected.status] || 'badge-gray'}`}>
                    {STATUS_LABEL[selected.status] || selected.status}
                  </span>
                </dd>
                <dt>市场</dt><dd>{MARKET_LABELS[selected.market] || selected.market}</dd>
                <dt>提出依据</dt><dd style={{ color: 'var(--text-dim)' }}>{selected.reason}</dd>
                {ev.eligible !== undefined && (
                  <>
                    <dt>门槛判定</dt>
                    <dd>
                      <span className={`badge ${ev.eligible ? 'badge-green' : 'badge-yellow'}`}>
                        {ev.eligible ? '达标' : '未达标'}
                      </span>
                    </dd>
                  </>
                )}
                {ev.rollback_reason && (
                  <>
                    <dt>回滚原因</dt><dd className="diff-removed">{ev.rollback_reason}</dd>
                    <dt>恢复版本</dt><dd>{ev.restored_id ?? '无可恢复版本'}</dd>
                  </>
                )}
              </dl>
            </div>

            <div className="card">
              <div className="section-title">与上一版本差异</div>
              <SpecDiff current={selected.spec} previous={previousVersion?.spec} />
            </div>

            <div className="card">
              <div className="section-title">评估证据</div>
              {Object.keys(ev).length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>尚未评估</div>
              ) : (
                <div className="flex flex-col gap-3">
                  {ev.symbols && Object.entries(ev.symbols).map(([symbol, r]: [string, any]) => (
                    <div key={symbol}>
                      <div style={{ fontSize: 12, marginBottom: 4 }}>
                        <strong>{symbol}</strong>{' '}
                        <span className={`badge ${r.eligible ? 'badge-green' : 'badge-yellow'}`}>
                          {r.eligible ? '达标' : r.reason}
                        </span>
                        <span style={{ color: 'var(--text-dim)', fontSize: 11, marginLeft: 8 }}>
                          样本 {r.samples}
                          {r.holdout ? ` · 验证收益 ${Number(r.holdout.return_pct).toFixed(2)}% · 回撤 ${Number(r.holdout.max_drawdown_pct).toFixed(2)}% · 交易 ${r.holdout.trades}` : ''}
                        </span>
                      </div>
                      {r.train?.curve && <CurveChart title="训练段资金曲线" curve={r.train.curve} color="#58a6ff" />}
                      {r.holdout?.curve && <CurveChart title="独立验证段资金曲线" curve={r.holdout.curve} color="#3fb950" />}
                    </div>
                  ))}
                  {ev.shadow?.map((sh: any, i: number) => (
                    <div key={`shadow-${i}`}>
                      <div style={{ fontSize: 12, marginBottom: 4 }}>
                        影子运行 #{i + 1}
                        <span style={{ color: 'var(--text-dim)', fontSize: 11, marginLeft: 8 }}>
                          样本 {sh.samples} · 收益 {Number(sh.return_pct).toFixed(2)}% · 回撤 {Number(sh.max_drawdown_pct).toFixed(2)}% · 交易 {sh.trades}
                        </span>
                      </div>
                      {sh.curve && <CurveChart title="影子运行资金曲线" curve={sh.curve} color="#d29922" />}
                    </div>
                  ))}
                  {ev.monitor?.map((mo: any, i: number) => (
                    <div key={`monitor-${i}`}>
                      <div style={{ fontSize: 12, marginBottom: 4 }}>
                        发布后监控 #{i + 1}
                        <span style={{ color: 'var(--text-dim)', fontSize: 11, marginLeft: 8 }}>
                          样本 {mo.samples} · 收益 {Number(mo.return_pct).toFixed(2)}% · 回撤 {Number(mo.max_drawdown_pct).toFixed(2)}%
                        </span>
                      </div>
                      {mo.curve && <CurveChart title="监控窗口资金曲线" curve={mo.curve} color="#f85149" />}
                    </div>
                  ))}
                  <details className="raw-json">
                    <summary>原始评估数据（JSON）</summary>
                    <pre>{JSON.stringify(ev, null, 2)}</pre>
                  </details>
                </div>
              )}
            </div>

            <div className="card">
              <div className="section-title">发布与回滚记录</div>
              {lifecycle.length === 0 ? (
                <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>暂无生命周期记录</div>
              ) : (
                <div style={{ maxHeight: 240, overflow: 'auto' }}>
                  {lifecycle.filter(e => String(e.payload?.strategy_id ?? '') === String(selected.id)
                    || String(e.payload?.previous_id ?? '') === String(selected.id)
                    || String(e.payload?.restored_id ?? '') === String(selected.id))
                    .map(e => (
                      <div key={e.id} style={{ padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 11 }}>
                        <span className={`badge ${e.kind === 'strategy.rolled_back' ? 'badge-red' : e.kind === 'strategy.published' ? 'badge-green' : 'badge-gray'}`}
                          style={{ marginRight: 8 }}>
                          {e.kind === 'strategy.published' ? '发布' : e.kind === 'strategy.rolled_back' ? '回滚' : '评估'}
                        </span>
                        {e.payload?.status || e.payload?.reason || ''}
                        <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>
                          {e.created_at ? new Date(e.created_at).toLocaleString() : ''}
                        </span>
                      </div>
                    ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
