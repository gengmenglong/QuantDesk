import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';

const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密现货', poly: '预测市场' };
const MARKETS = ['us', 'cn', 'crypto', 'poly'];

function age(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return '-';
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`;
  return `${Math.floor(seconds / 86400)} 天`;
}

export function Markets() {
  const [rows, setRows] = useState<any[]>([]);
  const [markets, setMarkets] = useState<Record<string, any>>({});
  const [market, setMarket] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      const res = await api.marketStatus();
      setRows(res.items);
      setMarkets(res.markets);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); const i = setInterval(load, 30000); return () => clearInterval(i); }, [load]);

  const act = async (kind: 'fetch' | 'backfill', mkt: string, sym: string) => {
    const key = `${kind}:${mkt}:${sym}`;
    setBusy(key);
    setMessage('');
    try {
      if (kind === 'fetch') {
        const r = await api.fetchSnapshot(mkt, sym);
        setMessage(`已采集 ${sym} 最新报价 ${r.price}`);
      } else {
        const r = await api.backfill(mkt, sym);
        setMessage(`${sym} 历史补齐：拉取 ${r.fetched} 条，新增 ${r.stored} 条（重复行已跳过）`);
      }
      await load();
    } catch (e: any) {
      // A blocked entitlement or unreachable vendor is reported, never papered over with demo data.
      setMessage(`${sym} ${kind === 'fetch' ? '采集' : '补齐'}失败：${e.message}`);
    }
    setBusy(null);
  };

  const filtered = market ? rows.filter(r => r.market === market) : rows;

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-between items-center">
        <div className="page-title" style={{ marginBottom: 0 }}>行情数据与新鲜度</div>
        <div className="flex gap-2">
          {['', ...MARKETS].map(m => (
            <button key={m} onClick={() => setMarket(m)} style={{
              padding: '4px 12px', fontSize: 11,
              borderColor: market === m ? 'var(--accent)' : 'var(--border)',
              color: market === m ? 'var(--accent)' : 'var(--text-dim)',
            }}>
              {m ? MARKET_LABELS[m] : '全部'}
            </button>
          ))}
        </div>
      </div>

      {/* Per-market health summary */}
      <div className="grid-4">
        {MARKETS.map(m => {
          const h = markets[m] || { total: 0, ok: 0, blocked: 0, no_data: 0 };
          const tone = h.no_data === h.total ? 'badge-gray' : h.blocked > 0 ? 'badge-yellow' : 'badge-green';
          return (
            <div key={m} className="card">
              <div className="flex justify-between items-center" style={{ marginBottom: 6 }}>
                <span style={{ fontWeight: 600 }}>{MARKET_LABELS[m]}</span>
                <span className={`badge ${tone}`}>{h.ok}/{h.total} 可交易</span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                阻塞 {h.blocked} · 无数据 {h.no_data}
              </div>
            </div>
          );
        })}
      </div>

      {message && <div className="card" style={{ fontSize: 12 }}>{message}</div>}

      <div className="card" style={{ padding: 0 }}>
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>市场</th><th>标的</th><th>最新价</th><th>数据源</th><th>质量</th>
                <th>事件时间</th><th>账龄</th><th>声明延迟</th><th>可交易性</th><th>估值可用性</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={11} className="empty">
                    暂无行情数据。请在设置中配置标的白名单，worker 启动时会自动补齐历史日线。
                  </td>
                </tr>
              ) : filtered.map((r, i) => (
                <tr key={`${r.market}:${r.symbol}:${i}`}>
                  <td><span className="badge badge-blue">{MARKET_LABELS[r.market] || r.market}</span></td>
                  <td style={{ fontFamily: 'var(--font)' }}>{r.symbol}</td>
                  <td style={{ fontWeight: 600 }}>
                    {r.state === 'no_data' ? '-' : Number(r.price).toFixed(r.market === 'poly' ? 4 : 2)}
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--text-dim)' }}>{r.source || '-'}</td>
                  <td>
                    {r.quality
                      ? <span className={`badge ${r.quality === 'real' ? 'badge-green' : r.quality === 'delayed' ? 'badge-yellow' : 'badge-red'}`}>{r.quality}</span>
                      : <span className="badge badge-gray">-</span>}
                  </td>
                  <td style={{ fontSize: 11 }}>{r.event_time ? new Date(r.event_time).toLocaleString() : '-'}</td>
                  <td style={{ fontSize: 11 }}>{age(r.age_seconds)}</td>
                  <td style={{ fontSize: 11 }}>{r.delay_seconds ? `${r.delay_seconds}s` : '0s'}</td>
                  <td style={{ fontSize: 11, maxWidth: 220 }}>
                    {r.state === 'no_data'
                      ? <span className="badge badge-gray">无数据</span>
                      : r.execution_problem
                        ? <span className="badge badge-yellow" title={r.execution_problem}>{r.execution_problem}</span>
                        : <span className="badge badge-green">可交易</span>}
                  </td>
                  <td style={{ fontSize: 11, maxWidth: 220 }}>
                    {r.state === 'no_data'
                      ? <span className="badge badge-gray">-</span>
                      : r.valuation_problem
                        ? <span className="badge badge-red" title={r.valuation_problem}>{r.valuation_problem}</span>
                        : <span className="badge badge-green">可用于估值</span>}
                  </td>
                  <td>
                    <div className="flex gap-2">
                      <button onClick={() => act('fetch', r.market, r.symbol)}
                        disabled={busy === `fetch:${r.market}:${r.symbol}`}
                        style={{ fontSize: 11, padding: '3px 10px' }}>
                        {busy === `fetch:${r.market}:${r.symbol}` ? '...' : '刷新'}
                      </button>
                      <button onClick={() => act('backfill', r.market, r.symbol)}
                        disabled={busy === `backfill:${r.market}:${r.symbol}`}
                        style={{ fontSize: 11, padding: '3px 10px' }}>
                        {busy === `backfill:${r.market}:${r.symbol}` ? '...' : '补齐历史'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
        「可交易性」是执行判定：收盘、停牌或数据过期都会阻塞新单。「估值可用性」是记账判定：
        正常隔夜与节假日的最后一笔收盘价仍然有效，不会被误判为断流。真实数据不可用时不会自动填入样例数据。
      </div>
    </div>
  );
}
