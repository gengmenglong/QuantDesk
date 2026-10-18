import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';

const MARKETS = ['us', 'cn', 'crypto', 'poly'] as const;
const MARKET_LABELS: Record<string, string> = { us: '美股', cn: 'A 股', crypto: '加密现货', poly: '预测市场' };
const MARKET_CURRENCY: Record<string, string> = { us: 'USD', cn: 'CNY', crypto: 'USD', poly: 'USD' };
const SECRET_LABELS: Record<string, string> = {
  alpaca_key: 'Alpaca Key ID（美股行情）',
  alpaca_secret: 'Alpaca Secret（美股行情）',
  tushare_token: 'Tushare Token（A 股日线）',
  model_key: '模型 API Key（AI 决策）',
};
const SECRET_NAMES = ['alpaca_key', 'alpaca_secret', 'tushare_token', 'model_key'];
const STEPS = ['本金', '数据源凭证', '标的池', '模型与预算', '风险与汇率', '确认初始化'];

function defaultRisk() {
  return { max_order_pct: '10', max_position_pct: '30', max_exposure_pct: '80', max_daily_loss_pct: '5', max_drawdown_pct: '10', fee_bps: '10', slippage_bps: '5' };
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 4 }}>{label}</div>
      {children}
      {hint && <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

export function Settings() {
  const [runtime, setRuntime] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<'config' | 'credentials' | 'risk' | 'control'>('config');
  const [step, setStep] = useState(0);

  // Init form
  const [capital, setCapital] = useState({ us: '10000', cn: '50000', crypto: '5000', poly: '2000' });
  const [whitelist, setWhitelist] = useState({ us: 'AAPL,MSFT', cn: '600519.SH', crypto: 'BTC-USD,ETH-USD', poly: '' });
  const [fx, setFx] = useState('7.2');
  const [modelName, setModelName] = useState('');
  const [modelUrl, setModelUrl] = useState('https://api.openai.com/v1');
  const [maxCostDay, setMaxCostDay] = useState('1');
  const [maxCallsDay, setMaxCallsDay] = useState('100');
  const [portfolioDD, setPortfolioDD] = useState('20');
  const [portfolioDL, setPortfolioDL] = useState('5');

  // Credentials form
  const [secrets, setSecrets] = useState<Record<string, string>>(
    Object.fromEntries(SECRET_NAMES.map(k => [k, '']))
  );

  // Risk form
  const [risk, setRisk] = useState<Record<string, Record<string, string>>>(() =>
    Object.fromEntries(MARKETS.map(m => [m, defaultRisk()]))
  );

  const load = useCallback(async () => {
    try {
      const r = await api.runtime();
      setRuntime(r);
      if (r.initialized && r.config) {
        const c = r.config;
        if (c.whitelist) {
          setWhitelist(Object.fromEntries(MARKETS.map(m => [m, (c.whitelist[m] || []).join(',')])) as any);
        }
        if (c.risk) {
          setRisk(Object.fromEntries(MARKETS.map(m => [m, c.risk[m] ? Object.fromEntries(Object.entries(c.risk[m]).map(([k, v]) => [k, String(v)])) : defaultRisk()])) as any);
        }
        if (c.fx_cny_per_usd) setFx(String(c.fx_cny_per_usd));
        if (c.model) {
          setModelName(c.model.model || '');
          setModelUrl(c.model.base_url || 'https://api.openai.com/v1');
          setMaxCostDay(String(c.model.max_cost_day || '1'));
          setMaxCallsDay(String(c.model.max_calls_day || '100'));
        }
        setPortfolioDD(String(c.portfolio_max_drawdown_pct || '20'));
        setPortfolioDL(String(c.portfolio_max_daily_loss_pct || '5'));
      }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const buildConfig = () => ({
    whitelist: Object.fromEntries(MARKETS.map(m => [m, whitelist[m].split(',').map(s => s.trim()).filter(Boolean)])),
    risk: Object.fromEntries(MARKETS.map(m => [m, {
      max_order_pct: risk[m].max_order_pct, max_position_pct: risk[m].max_position_pct,
      max_exposure_pct: risk[m].max_exposure_pct, max_daily_loss_pct: risk[m].max_daily_loss_pct,
      max_drawdown_pct: risk[m].max_drawdown_pct, fee_bps: risk[m].fee_bps, slippage_bps: risk[m].slippage_bps,
    }])),
    model: { base_url: modelUrl, model: modelName, max_calls_day: Number(maxCallsDay), max_tokens_day: 100000, max_cost_day: maxCostDay, max_output_tokens: 1000 },
    evaluation: { min_samples: 60, min_trades: 10, shadow_samples: 20, max_drawdown_pct: 10, min_excess_return_pct: 0 },
    fx_cny_per_usd: fx,
    fx_at: new Date().toISOString(),
    fx_max_age_hours: 168,
    portfolio_max_drawdown_pct: portfolioDD,
    portfolio_max_daily_loss_pct: portfolioDL,
  });

  const doSetup = async () => {
    setSaving(true);
    try {
      await api.setup({ capital, config: buildConfig(), secrets });
      await load();
      alert('初始化成功（模拟盘）');
    } catch (e: any) { alert(e.message); }
    setSaving(false);
  };

  const doSaveConfig = async () => {
    if (!runtime) return;
    setSaving(true);
    try {
      await api.updateConfig(buildConfig(), runtime.version);
      await load();
      alert('配置已保存');
    } catch (e: any) { alert(e.message); }
    setSaving(false);
  };

  const doSaveCredentials = async () => {
    setSaving(true);
    try {
      const filtered = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v));
      await api.updateCredentials(filtered);
      setSecrets(Object.fromEntries(SECRET_NAMES.map(k => [k, ''])));
      await load();
      alert('凭证已加密保存（不回显）');
    } catch (e: any) { alert(e.message); }
    setSaving(false);
  };

  const doClearCredentials = async () => {
    setSaving(true);
    try { await api.clearCredentials(); await load(); } catch (e: any) { alert(e.message); }
    setSaving(false);
  };

  const doPause = async () => {
    try { await api.pause(); await load(); } catch (e: any) { alert(e.message); }
  };
  const doResume = async () => {
    try { await api.resume(); await load(); } catch (e: any) { alert(e.message); }
  };

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  const initialized = runtime?.initialized ?? false;
  const parsedWhitelist = Object.fromEntries(
    MARKETS.map(m => [m, whitelist[m].split(',').map(s => s.trim()).filter(Boolean)])
  ) as Record<string, string[]>;
  const secretsProvided = SECRET_NAMES.filter(k => Boolean(secrets[k]));

  const tabs = [
    { key: 'config', label: '配置' },
    { key: 'credentials', label: '凭证' },
    { key: 'risk', label: '风控参数' },
    { key: 'control', label: '运行控制' },
  ] as const;

  const riskFields = (m: string) => (
    <div key={m}>
      <div className="section-title" style={{ fontSize: 12, color: 'var(--accent)', marginTop: 12 }}>
        {MARKET_LABELS[m]}（{MARKET_CURRENCY[m]}）
      </div>
      <div className="grid-4">
        {Object.entries(risk[m]).map(([k, v]) => (
          <div key={k}>
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 2 }}>{k}</div>
            <input value={v} onChange={e => setRisk({ ...risk, [m]: { ...risk[m], [k]: e.target.value } })}
              style={{ width: '100%', fontSize: 11 }} />
          </div>
        ))}
      </div>
    </div>
  );

  // ---------------------------------------------------------------------
  // First-run wizard: nothing runs until capital, data sources, model and
  // risk limits are explicitly set. AI stays off until initialization.
  // ---------------------------------------------------------------------
  if (!initialized) {
    return (
      <div className="flex flex-col gap-4">
        <div className="page-title">首次配置引导</div>

        <div className="sim-banner">
          初始化完成前 AI 保持关闭，不会采集行情、不会调用模型、不会生成任何订单
        </div>

        <div className="wizard-steps">
          {STEPS.map((label, i) => (
            <button key={label} className={`wizard-step ${i === step ? 'active' : i < step ? 'done' : ''}`}
              style={{ background: 'none', cursor: i < step ? 'pointer' : 'default' }}
              onClick={() => i < step && setStep(i)}>
              {i < step ? '✓ ' : ''}{i + 1}. {label}
            </button>
          ))}
        </div>

        <div className="card flex flex-col gap-3">
          {step === 0 && (
            <>
              <div className="section-title">各市场模拟本金</div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                四个市场各自独立账户与记账币种。初始化后调整本金只能通过资金流水记录，不能直接覆盖余额。
              </div>
              <div className="grid-4">
                {MARKETS.map(m => (
                  <Field key={m} label={`${MARKET_LABELS[m]}（${MARKET_CURRENCY[m]}）`}>
                    <input value={capital[m]} onChange={e => setCapital({ ...capital, [m]: e.target.value })}
                      style={{ width: '100%' }} />
                  </Field>
                ))}
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <div className="section-title">数据源与模型凭证</div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                凭证由后端用独立主密钥加密保存，不写入仓库、不回显。缺少某个市场的权限时，
                该市场会显示阻塞原因而不会用演示数据替代。
              </div>
              <div className="grid-2">
                {SECRET_NAMES.map(k => (
                  <Field key={k} label={SECRET_LABELS[k]}>
                    <input type="password" value={secrets[k]} autoComplete="off"
                      onChange={e => setSecrets({ ...secrets, [k]: e.target.value })}
                      placeholder="留空表示暂不配置" style={{ width: '100%' }} />
                  </Field>
                ))}
              </div>
              <div style={{ fontSize: 11, color: 'var(--yellow)' }}>
                加密保存要求服务端已设置 QD_MASTER_KEY；未设置时 API 会拒绝启动。
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <div className="section-title">标的池（白名单）</div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                首版仅支持：美股普通股、沪深主板普通股、BTC/ETH 现货、二元预测市场。
                预测市场填入 CLOB token id。AI 只能在这些标的上下单。
              </div>
              <div className="grid-2">
                {MARKETS.map(m => (
                  <Field key={m} label={MARKET_LABELS[m]}
                    hint={m === 'crypto' ? '初始范围限 BTC-USD、ETH-USD' : m === 'cn' ? '如 600519.SH、000001.SZ' : m === 'poly' ? 'Yes/No 二元市场的 token id' : '如 AAPL、MSFT'}>
                    <input value={whitelist[m]} onChange={e => setWhitelist({ ...whitelist, [m]: e.target.value })}
                      placeholder="逗号分隔" style={{ width: '100%' }} />
                  </Field>
                ))}
              </div>
            </>
          )}

          {step === 3 && (
            <>
              <div className="section-title">模型与预算</div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                接入任意 OpenAI 兼容 API。输出经 JSON Schema 校验，模型不能修改风控、密钥、数据库或撮合实现。
              </div>
              <div className="grid-2">
                <Field label="模型 API 地址">
                  <input value={modelUrl} onChange={e => setModelUrl(e.target.value)} style={{ width: '100%' }} />
                </Field>
                <Field label="模型名称">
                  <input value={modelName} onChange={e => setModelName(e.target.value)} placeholder="如 gpt-4o"
                    style={{ width: '100%' }} />
                </Field>
                <Field label="每日调用上限">
                  <input value={maxCallsDay} onChange={e => setMaxCallsDay(e.target.value)} style={{ width: '100%' }} />
                </Field>
                <Field label="每日费用上限（USD）" hint="价格未知时按调用次数与 token 限额执行">
                  <input value={maxCostDay} onChange={e => setMaxCostDay(e.target.value)} style={{ width: '100%' }} />
                </Field>
              </div>
            </>
          )}

          {step === 4 && (
            <>
              <div className="section-title">风险限额与折算汇率</div>
              <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                风控由确定性代码执行，AI 无权修改这些门槛。汇率由你手工录入并带时间戳，
                过期后组合估值与依赖统一估值的新单会被阻断。
              </div>
              {MARKETS.map(riskFields)}
              <div className="section-title" style={{ fontSize: 12, color: 'var(--accent)', marginTop: 12 }}>组合级</div>
              <div className="grid-3">
                <Field label="CNY/USD 汇率">
                  <input value={fx} onChange={e => setFx(e.target.value)} style={{ width: '100%' }} />
                </Field>
                <Field label="组合最大回撤 %">
                  <input value={portfolioDD} onChange={e => setPortfolioDD(e.target.value)} style={{ width: '100%' }} />
                </Field>
                <Field label="组合单日最大亏损 %">
                  <input value={portfolioDL} onChange={e => setPortfolioDL(e.target.value)} style={{ width: '100%' }} />
                </Field>
              </div>
            </>
          )}

          {step === 5 && (
            <>
              <div className="section-title">确认并初始化</div>
              <dl className="kv">
                {MARKETS.map(m => (
                  <div key={m} style={{ display: 'contents' }}>
                    <dt>{MARKET_LABELS[m]}</dt>
                    <dd>
                      本金 {capital[m]} {MARKET_CURRENCY[m]} · 标的 {parsedWhitelist[m].length
                        ? parsedWhitelist[m].join(', ') : '（空，该市场不会下单）'}
                    </dd>
                  </div>
                ))}
                <dt>模型</dt><dd>{modelName || '（未配置，AI 不会产生决策）'} @ {modelUrl}</dd>
                <dt>预算</dt><dd>{maxCallsDay} 次/日 · {maxCostDay} USD/日</dd>
                <dt>汇率</dt><dd>1 USD = {fx} CNY（初始化时刻记录）</dd>
                <dt>凭证</dt>
                <dd>{secretsProvided.length ? `本次提交：${secretsProvided.join(', ')}` : '未提交任何凭证'}</dd>
              </dl>
              <div className="error-msg" style={{ background: '#3d2e00', color: 'var(--yellow)' }}>
                初始化是一次性操作：完成后本金与账户初始状态不可再通过界面覆盖。全部为模拟盘，不接入真实交易。
              </div>
            </>
          )}

          <div className="flex gap-2" style={{ marginTop: 8 }}>
            <button onClick={() => setStep(s => Math.max(0, s - 1))} disabled={step === 0}>上一步</button>
            {step < STEPS.length - 1 ? (
              <button className="primary" onClick={() => setStep(s => s + 1)}>下一步</button>
            ) : (
              <button className="primary" onClick={doSetup} disabled={saving}>
                {saving ? '初始化中...' : '初始化工作区'}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------
  // Initialized: tabbed editor
  // ---------------------------------------------------------------------
  return (
    <div className="flex flex-col gap-4">
      <div className="page-title">设置</div>

      <div className="flex gap-2" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} style={{
            padding: '6px 16px', fontSize: 12,
            borderColor: tab === t.key ? 'var(--accent)' : 'transparent',
            borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent',
            color: tab === t.key ? 'var(--accent)' : 'var(--text-dim)',
          }}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'config' && (
        <div className="card flex flex-col gap-3">
          <div className="section-title">运行配置</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            保存采用版本校验：若他处已修改配置会返回冲突而不是静默覆盖。本金不可在此修改。
          </div>

          <div className="section-title" style={{ fontSize: 12 }}>标的白名单（逗号分隔）</div>
          <div className="grid-2">
            {MARKETS.map(m => (
              <Field key={m} label={MARKET_LABELS[m]}>
                <input value={whitelist[m]} onChange={e => setWhitelist({ ...whitelist, [m]: e.target.value })}
                  style={{ width: '100%' }} />
              </Field>
            ))}
          </div>

          <div className="grid-2">
            <Field label="模型 API 地址">
              <input value={modelUrl} onChange={e => setModelUrl(e.target.value)} style={{ width: '100%' }} />
            </Field>
            <Field label="模型名称">
              <input value={modelName} onChange={e => setModelName(e.target.value)} style={{ width: '100%' }} />
            </Field>
            <Field label="每日调用上限">
              <input value={maxCallsDay} onChange={e => setMaxCallsDay(e.target.value)} style={{ width: '100%' }} />
            </Field>
            <Field label="每日费用上限（USD）">
              <input value={maxCostDay} onChange={e => setMaxCostDay(e.target.value)} style={{ width: '100%' }} />
            </Field>
          </div>

          <button className="primary" onClick={doSaveConfig} disabled={saving} style={{ width: 'fit-content' }}>
            {saving ? '保存中...' : '保存配置变更'}
          </button>
        </div>
      )}

      {tab === 'credentials' && (
        <div className="card flex flex-col gap-3">
          <div className="section-title">数据源凭证</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            凭证由后端加密保存，主密钥独立于数据库。保存成功以服务端持久化为准，密钥不回显。
          </div>
          {runtime?.has_secrets
            ? <div className="badge badge-green" style={{ width: 'fit-content' }}>已配置凭证</div>
            : <div className="badge badge-gray" style={{ width: 'fit-content' }}>尚未配置凭证</div>}
          <div className="grid-2">
            {SECRET_NAMES.map(k => (
              <Field key={k} label={SECRET_LABELS[k]}>
                <input type="password" value={secrets[k]} autoComplete="off"
                  onChange={e => setSecrets({ ...secrets, [k]: e.target.value })}
                  placeholder="留空则不修改" style={{ width: '100%' }} />
              </Field>
            ))}
          </div>
          <div className="flex gap-2">
            <button className="primary" onClick={doSaveCredentials} disabled={saving}>
              {saving ? '保存中...' : '加密保存凭证'}
            </button>
            <button className="danger" onClick={doClearCredentials} disabled={saving || !runtime?.has_secrets}>
              清除全部凭证
            </button>
          </div>
        </div>
      )}

      {tab === 'risk' && (
        <div className="card flex flex-col gap-3">
          <div className="section-title">风险限额与折算汇率</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            这些门槛由确定性风控代码执行，AI 无权修改。修改后新请求立即生效。
          </div>
          {MARKETS.map(riskFields)}
          <div className="section-title" style={{ fontSize: 12, color: 'var(--accent)', marginTop: 12 }}>组合级</div>
          <div className="grid-3">
            <Field label="CNY/USD 汇率" hint="手工录入，过期后组合估值与新单会被阻断">
              <input value={fx} onChange={e => setFx(e.target.value)} style={{ width: '100%' }} />
            </Field>
            <Field label="组合最大回撤 %">
              <input value={portfolioDD} onChange={e => setPortfolioDD(e.target.value)} style={{ width: '100%' }} />
            </Field>
            <Field label="组合单日最大亏损 %">
              <input value={portfolioDL} onChange={e => setPortfolioDL(e.target.value)} style={{ width: '100%' }} />
            </Field>
          </div>
          <button className="primary" onClick={doSaveConfig} disabled={saving} style={{ width: 'fit-content' }}>
            {saving ? '保存中...' : '保存风控变更'}
          </button>
        </div>
      )}

      {tab === 'control' && (
        <div className="card flex flex-col gap-3">
          <div className="section-title">运行控制</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            暂停会取消所有待成交订单和未执行的 AI 任务，并推进 epoch 使迟到的 AI 结果失效；
            行情采集与持仓展示继续进行。恢复必须由你明确操作，不会自动恢复，也不能靠再次点击暂停来恢复。
          </div>
          <div className="flex gap-2">
            <button onClick={doPause} disabled={runtime?.paused} className="danger">暂停 AI</button>
            <button onClick={doResume} disabled={!runtime?.paused} className="primary">恢复运行</button>
          </div>
          <div style={{ marginTop: 12, fontSize: 12 }}>
            当前状态：
            <span className={`badge ${runtime?.paused ? 'badge-yellow' : 'badge-green'}`} style={{ marginLeft: 8 }}>
              {runtime?.paused ? '已暂停' : '运行中'}
            </span>
            <span style={{ color: 'var(--text-dim)', marginLeft: 12 }}>
              epoch {runtime?.epoch ?? '-'} · 配置版本 {runtime?.version ?? '-'}
            </span>
          </div>
        </div>
      )}

      <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
        全部订单、成交与资金均为模拟盘。本机浏览器访问，电脑休眠或关机时停止运行；
        恢复运行后先校验行情与账户一致性，不补下休眠期间错过的订单。
      </div>
    </div>
  );
}
