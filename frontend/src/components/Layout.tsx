import { Outlet, NavLink, useLocation } from 'react-router-dom';
import { useEffect, useState, useCallback } from 'react';
import { api, subscribeEvents, latestEventId } from '../api';

const NAV = [
  { path: '/dashboard', label: '工作台', icon: '◉' },
  { path: '/markets', label: '行情', icon: '◇' },
  { path: '/trading', label: '交易', icon: '△' },
  { path: '/strategies', label: '策略', icon: '⬡' },
  { path: '/ai', label: 'AI 评估', icon: '◎' },
  { path: '/ledger', label: '账本', icon: '▤' },
  { path: '/settings', label: '设置', icon: '⚙' },
];

export function Layout() {
  const [runtime, setRuntime] = useState<any>(null);
  const [lastEvent, setLastEvent] = useState<any>(null);
  const location = useLocation();

  const refresh = useCallback(async () => {
    try {
      const r = await api.runtime();
      setRuntime(r);
    } catch {}
  }, []);

  useEffect(() => {
    refresh();
    let unsub = () => {};
    let cancelled = false;
    // Start from the newest stored event so a page load does not replay the whole audit trail,
    // while the browser's own reconnects resume via Last-Event-ID.
    latestEventId().then((cursor) => {
      if (cancelled) return;
      unsub = subscribeEvents((e) => {
        setLastEvent(e);
        if (['runtime.paused', 'runtime.resumed', 'workspace.initialized',
             'order.filled', 'order.cancelled', 'strategy.published',
             'strategy.rolled_back', 'market.settled'].includes(e.kind)) {
          refresh();
        }
      }, cursor);
    });
    const interval = setInterval(refresh, 15000);
    return () => { cancelled = true; unsub(); clearInterval(interval); };
  }, [refresh]);

  const initialized = runtime?.initialized ?? false;
  const paused = runtime?.paused ?? true;

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      {/* Sidebar */}
      <nav style={{
        width: 200, minWidth: 200, background: 'var(--bg-card)', borderRight: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', padding: '12px 0',
      }}>
        <div style={{ padding: '0 16px 16px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 15, fontWeight: 700, letterSpacing: 1 }}>QuantDesk</div>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 2 }}>AI 模拟交易工作台</div>
        </div>
        <div style={{ flex: 1, padding: '8px 0' }}>
          {NAV.map(n => (
            <NavLink key={n.path} to={n.path} style={({ isActive }) => ({
              display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px',
              color: isActive ? 'var(--text)' : 'var(--text-dim)',
              background: isActive ? 'var(--bg-hover)' : 'transparent',
              borderLeft: isActive ? '3px solid var(--accent)' : '3px solid transparent',
              fontSize: 13, textDecoration: 'none',
            })}>
              <span style={{ fontSize: 14, width: 18, textAlign: 'center' }}>{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </div>
        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
          {!initialized ? (
            <span className="badge badge-gray">未初始化</span>
          ) : paused ? (
            <span className="badge badge-yellow">已暂停</span>
          ) : (
            <span className="badge badge-green">运行中</span>
          )}
          <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>
            epoch {runtime?.epoch ?? '-'} · v{runtime?.version ?? '-'}
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
        {/* Top bar */}
        <header style={{
          padding: '8px 20px', borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: 'var(--bg-card)', minHeight: 40,
        }}>
          <div className="sim-banner">SIMULATION ONLY · 全部为模拟盘</div>
          {lastEvent && (
            <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
              {lastEvent.kind} · {lastEvent.created_at ? new Date(lastEvent.created_at).toLocaleTimeString() : ''}
            </div>
          )}
        </header>

        {/* Page content */}
        <div style={{ flex: 1, padding: 20, overflow: 'auto' }}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
