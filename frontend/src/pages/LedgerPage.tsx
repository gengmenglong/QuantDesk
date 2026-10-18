import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';
import { TracePanel } from '../components/TracePanel';

const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密', poly: '预测' };

export function LedgerPage() {
  const [entries, setEntries] = useState<any[]>([]);
  const [reconcile, setReconcile] = useState<any[]>([]);
  const [market, setMarket] = useState('');
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [traceId, setTraceId] = useState<string | null>(null);
  const pageSize = 50;

  const load = useCallback(async () => {
    try {
      const [l, r] = await Promise.all([
        api.ledger({ market: market || undefined, limit: pageSize, offset: page * pageSize }),
        api.reconcile(),
      ]);
      setEntries(l.items);
      setTotal(l.total);
      setReconcile(r.items);
    } catch {}
    setLoading(false);
  }, [market, page]);

  useEffect(() => { load(); }, [load]);

  const exportCsv = async (type: 'ledger' | 'fills') => {
    try {
      const blob = type === 'ledger'
        ? await api.exportLedger(market || undefined)
        : await api.exportFills(market || undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${type}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) { alert(e.message); }
  };

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-between items-center">
        <div className="page-title" style={{ marginBottom: 0 }}>账本与审计</div>
        <div className="flex gap-2">
          <button onClick={() => exportCsv('ledger')} style={{ fontSize: 11 }}>导出资金流水 CSV</button>
          <button onClick={() => exportCsv('fills')} style={{ fontSize: 11 }}>导出成交记录 CSV</button>
        </div>
      </div>

      {traceId && <TracePanel traceId={traceId} onClose={() => setTraceId(null)} />}

      {/* Reconciliation status */}
      <div className="grid-4">
        {reconcile.map(r => (
          <div key={r.market} className="card" style={{ textAlign: 'center' }}>
            <div className="stat-label">{MARKET_LABELS[r.market] || r.market}</div>
            <div className={`badge ${r.ok ? 'badge-green' : 'badge-red'}`} style={{ marginTop: 6, fontSize: 13 }}>
              {r.ok ? '对账通过' : '对账异常'}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 6 }}>
              现金 {Number(r.cash).toFixed(4)} · 冻结 {Number(r.reserved).toFixed(4)}
            </div>
          </div>
        ))}
      </div>

      {/* Market filter */}
      <div className="flex gap-2">
        {['', 'us', 'cn', 'crypto', 'poly'].map(m => (
          <button key={m} onClick={() => { setMarket(m); setPage(0); }} style={{
            padding: '4px 12px', fontSize: 11,
            borderColor: market === m ? 'var(--accent)' : 'var(--border)',
            color: market === m ? 'var(--accent)' : 'var(--text-dim)',
          }}>
            {m ? MARKET_LABELS[m] : '全部'}
          </button>
        ))}
      </div>

      {/* Ledger entries */}
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>ID</th><th>市场</th><th>类型</th><th>金额</th><th>余额</th><th>追踪 ID</th><th>时间</th><th>详情</th></tr>
          </thead>
          <tbody>
            {entries.length === 0 ? (
              <tr><td colSpan={8} className="empty">暂无资金流水</td></tr>
            ) : entries.map(e => (
              <tr key={e.id}>
                <td>{e.id}</td>
                <td><span className="badge badge-blue">{MARKET_LABELS[e.market] || e.market}</span></td>
                <td>
                  <span className={`badge ${e.kind === 'fill' ? 'badge-green' : e.kind === 'initial_capital' ? 'badge-blue' : e.kind === 'settlement' ? 'badge-yellow' : 'badge-gray'}`}>
                    {e.kind}
                  </span>
                </td>
                <td style={{ color: Number(e.amount) >= 0 ? 'var(--green)' : 'var(--red)', fontFamily: 'var(--font)' }}>
                  {Number(e.amount) >= 0 ? '+' : ''}{Number(e.amount).toFixed(6)}
                </td>
                <td style={{ fontFamily: 'var(--font)' }}>{Number(e.balance).toFixed(6)}</td>
                <td style={{ fontFamily: 'var(--font)', fontSize: 11 }}>
                  {e.trace_id
                    ? <button className="trace-link" onClick={() => setTraceId(e.trace_id)}>{e.trace_id.slice(0, 12)}</button>
                    : '-'}
                </td>
                <td style={{ fontSize: 11 }}>{e.created_at ? new Date(e.created_at).toLocaleString() : ''}</td>
                <td style={{ fontSize: 11, color: 'var(--text-dim)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {e.details ? JSON.stringify(e.details).slice(0, 80) : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > pageSize && (
        <div className="flex gap-2 items-center" style={{ justifyContent: 'center' }}>
          <button disabled={page === 0} onClick={() => setPage(p => p - 1)}>上一页</button>
          <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            第 {page + 1} / {Math.ceil(total / pageSize)} 页 · 共 {total} 条
          </span>
          <button disabled={(page + 1) * pageSize >= total} onClick={() => setPage(p => p + 1)}>下一页</button>
        </div>
      )}
    </div>
  );
}
