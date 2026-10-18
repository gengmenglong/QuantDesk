# 验收映射 REQ-001 ~ REQ-009

本文件把 `quantDesk.md` 的需求映射到实现位置，并如实标注验证状态。

验证状态图例：

- **自动化** — 有 pytest 用例覆盖，`cd backend && pytest` 可复现
- **构建验证** — 通过 `tsc -b` / `vite build` / `ruff` / 字节码编译验证
- **人工待验** — 代码已实现，但需要真实供应商凭证或实盘运行才能确认，尚未验证
- **阻塞** — 首版发布阻塞项，见 `docs/limitations.md`

当前自动化基线：17 个 pytest 用例通过（账本、风控并发、暂停幂等、适配器、AI 异常、评估样本门槛）。
前端 `tsc -b && vite build` 通过。四市场真实数据端到端闭环**尚未**在真实凭证下运行验证。

---

## REQ-001 多市场工作台

页面可查看美股、A 股、加密现货和 Polymarket，并区分市场账户、币种和交易状态。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/db.py` `Account` | 四市场独立账户，主键为 market，各自 `currency`（USD/CNY/USD/USD） |
| `backend/quantdesk/core.py` `MARKETS` | 市场与记账币种映射，`setup()` 为四个市场各建账户并记 `initial_capital` 流水 |
| `backend/quantdesk/api.py` `/accounts` `/positions` `/market-status` | 分市场账户、持仓、行情状态 |
| `frontend/src/pages/Dashboard.tsx` | 四市场账户卡片，分币种展示净值、现金、冻结、初始、高点 |
| `frontend/src/pages/Markets.tsx` | 按市场筛选，展示市场标签与状态 |

状态：**构建验证**（分币种展示与账户隔离由 `setup()` 逻辑保证；四账户初始化在 `tests/conftest.py` 的 Harness 中已覆盖）。

---

## REQ-002 真实行情接入

行情显示来源、覆盖范围、采集时间和延迟；失效/过期状态可见，失效时不继续产生新模拟订单。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/providers.py` | 四个只读适配器：Alpaca IEX（标注 single venue 覆盖范围）、Tushare 日线、Coinbase Exchange L1、Polymarket Gamma+CLOB |
| `backend/quantdesk/schemas.py` `MarketSnapshot` | 统一记录 source、event_time、delay_seconds、quality、tradable、reason |
| `backend/quantdesk/db.py` `Snapshot.received_at` | 采集时间独立于事件时间存储 |
| `backend/quantdesk/core.py` `quote_problem()` | 执行级新鲜度：未来报价、过期、A 股未完成日线、美股非正常交易时段、公司行动不安全 |
| `backend/quantdesk/core.py` `valuation_problem()` | 估值级新鲜度：正常隔夜/节假日收盘价仍有效，不误判为断流 |
| `backend/quantdesk/api.py` `/market-status` | 同时返回 `execution_problem` 与 `valuation_problem`、账龄、质量、声明延迟 |
| `frontend/src/pages/Markets.tsx` | 分级展示可交易性与估值可用性，直出不可用原因文本 |

状态：**人工待验**。供应商权限、网络可达性与真实样本尚未在本机验证 —— 这是 PLAN 的 M0 出口条件，属首版发布阻塞项。
自动化覆盖：`tests/test_adapters.py` 验证 Coinbase 使用源时间戳与盘口深度、错误不降级为 demo、Polymarket 盘口排序且不臆断结算、Tushare 权限错误显式暴露。

---

## REQ-003 AI 自主决策与模拟下单

无需人工逐笔审批；AI 可在配置的标的池内选标的、发起买卖模拟单，风控服务独立校验。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/llm.py` | OpenAI 兼容 API 调用，`DECISION_SCHEMA` 结构化输出，超时/HTTP 错误/解析失败均返回 None |
| `backend/quantdesk/schemas.py` `ModelDecision` | 第二层 Pydantic 校验，action 与 order/strategy 一致性约束 |
| `backend/quantdesk/ai.py` `apply_result()` | epoch 失效丢弃、跨市场越权拒绝、幂等键、风控拒绝被记录而非抛出 |
| `backend/quantdesk/core.py` `risk_reason()` | 独立确定性风控：白名单、行情状态、现金含冻结、持仓与敞口上限、单日亏损、回撤、整手/整股、A 股 100 股买入单位与 T+1 可卖数量 |
| `backend/quantdesk/worker.py` `schedule_ai_decisions()` | 美股/加密/预测 15 分钟批次，A 股按已完成日线 session 一批次；无新数据不调用 |
| `frontend/src/pages/AIEvaluation.tsx` | 决策列表含状态、动作、依据、token、费用与追溯入口 |

状态：**自动化**（部分）。
覆盖：`tests/test_services.py` 验证跨市场越权不产生订单、迟到模型结果不创建策略、过期 AI job 不重放。
未覆盖：真实模型调用、预算耗尽路径、并发风控竞争 —— 需人工验证或补充用例。

---

## REQ-004 AI 策略全权管理

AI 可新增、修改、启停策略及参数；策略更新版本化，保留理由、变更差异和回滚能力。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/schemas.py` `StrategySpec` | 有界参数：trend/mean_reversion/probability 模板、lookback 5–200、threshold、allocation_pct ≤ 25 |
| `backend/quantdesk/db.py` `Strategy` | `(name, version)` 唯一约束，status、spec、reason、evaluation 全量留存 |
| `backend/quantdesk/ai.py` | AI 提出策略时自增版本号，落为 `candidate`，保留 `reason` |
| `backend/quantdesk/evaluation.py` `rollback_strategy()` | 一次性回滚：仅 `active` 可回滚，置 `rolled_back` 并恢复同名最近的 `retired` 版本 |
| `backend/quantdesk/api.py` `/strategies/{id}/rollback` | 用户手动回滚，状态守卫防止重复生效 |
| `frontend/src/pages/Strategies.tsx` | 同名相邻版本的字段级 spec 差异、发布/回滚/评估记录时间线 |

边界：AI 不能修改风控、密钥、数据库或撮合实现 —— `StrategySpec` 是白名单参数集，`apply_result()` 拒绝跨市场动作。

状态：**构建验证 + 部分自动化**。回滚的一次性生效由状态守卫保证，尚无专门用例。

---

## REQ-005 策略自动优化

候选策略与当前版本滚动评估，通过成本后表现和风险门槛后自动发布；记录评估窗口、样本量、指标与结论。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/evaluation.py` `replay()` | 含费用与滑点的确定性回放，下一根观测成交（无同快照成交），流动性受盘口/成交量约束，A 股 0.1% 参与率上限 |
| `evaluation.py` `evaluate_series()` | 前后对半切分为训练段与独立验证段，样本不足直接 `insufficient_samples` |
| `evaluation.py` `evaluate_strategy()` | candidate → shadow → active 自动推进；影子窗口与基准使用完全相同的窗口与成本模型对比 |
| `evaluation.py` `monitor_strategy()` | 发布后监控，监控窗口回撤越界触发自动回滚 |
| `backend/quantdesk/worker.py` `schedule_evaluations()` | 候选/影子每 6 小时评估一次，已发布每日监控一次；均通过 job 队列执行 |
| `frontend/src/pages/Strategies.tsx` | 训练/验证/影子/监控各段资金曲线、样本量、收益、回撤、交易数与原始 JSON 证据 |

门槛由用户在 `EvaluationConfig` 设置（min_samples、min_trades、shadow_samples、max_drawdown_pct、min_excess_return_pct），AI 无权修改。
不以实现盈利作为软件验收条件：样本不足时保持候选，不降低门槛凑发布。

状态：**自动化**（部分）。`tests/test_services.py` 验证样本不足不发布。
**人工待验**：真实数据下的完整 candidate → shadow → active 推进依赖历史补齐深度，尚未实盘跑通。

---

## REQ-006 多市场模拟撮合

股票、加密现货和预测市场使用各自行情、交易时段、计价单位、费用、滑点和结算模型。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/core.py` `match()` | 用决策之后的新报价撮合；同快照或早于下单时刻的报价不成交（`q.event_time <= o.created_at`） |
| `core.py` `execution_price()` | 按 `fee_bps`/`slippage_bps` 分市场计费与滑点，买卖方向相反 |
| `core.py` A 股分支 | 仅在更晚的完整日线成交，以开盘价为参考；缺涨跌停元数据或开盘即触板时保守撤单 |
| `core.py` 流动性约束 | 有盘口时按 `ask_size`/`bid_size` 限量，`Snapshot.consumed` 累计占用；无盘口回落到成交量，不假设无限流动性 |
| `core.py` `settle()` | 二元市场仅在公开结果确认后结算，payout 必须为 0 或 1，账本键幂等 |
| `backend/quantdesk/providers.py` `Polymarket.state()` | 三态区分：open / closed 待裁定 / resolved；`closed` 单独不足以结算 |
| `backend/quantdesk/worker.py` `poll_resolutions()` | 轮询结算；待裁定时撤销挂单并保留持仓估值 |
| `core.py` 订单状态 | pending / partial / filled / cancelled / expired / rejected |

状态：**自动化**（核心）。`tests/test_core.py` 覆盖同快照不成交、部分成交、成交去重、撤单释放冻结、限价不高于价格成交、卖出不超持仓、重复订单键返回原单、待成交订单不可重复动用资金。
**人工待验**：Polymarket 自然结算 —— PLAN 要求用确定性历史样本验证，不依赖观察期恰好出现结算。

---

## REQ-007 独立硬风控和紧急暂停

用户可设置账户级与分市场限制；AI 不可修改；触发熔断后阻止新单并记录告警，用户可一键暂停 AI。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/schemas.py` `RiskLimits` / `WorkspaceConfig` | 分市场限额 + 组合级回撤/单日亏损上限，全部带边界校验 |
| `backend/quantdesk/core.py` `risk_reason()` | 账户级熔断（回撤、单日亏损 → `Account.halted`）与组合级熔断（→ 全局暂停） |
| `core.py` `submit()` | 待成交订单占用资金与风险额度；撮合前用当前限额复验（排除自身冻结） |
| `core.py` `pause()` | 幂等：推进 epoch、撤销全部挂单、取消未执行 AI job、拒绝迟到结果 |
| `core.py` `resume()` | 必须显式调用，未初始化时拒绝；不存在"再次点击暂停即恢复" |
| `backend/quantdesk/api.py` `/risk` | 当前限额、熔断状态、账户明细与最近拒绝原因 |
| `backend/quantdesk/api.py` `reject_cross_origin_writes` | 写操作 Origin 白名单校验（CORS 头不足以阻止跨站表单提交） |
| `frontend/src/pages/Settings.tsx` | 风控参数页与运行控制页分离，明确说明暂停/恢复语义 |

状态：**自动化**。`tests/test_core.py` 验证暂停幂等、挂单被撤、冻结归零、迟到 epoch 被拒；`tests/test_services.py` 验证过期 AI job 不重放。
**人工待验**：多策略并发抢占资金的竞争路径（PLAN §四 第 4 项）尚无并发用例。

---

## REQ-008 服务端持久化与审计

策略、账户、订单、成交、决策和设置存入后端，浏览器刷新不丢失，可按交易链路回溯。

| 实现位置 | 说明 |
|---|---|
| `backend/alembic/versions/0001_initial.py` | 迁移建立全部表并插入 runtime 行 |
| `backend/quantdesk/db.py` | 金额一律 `Numeric(30,10)` 定点，时间一律 `DateTime(timezone=True)` UTC |
| `core.py` `book()` | 每笔资金变动写 `Ledger`，带幂等 `key` 与变动后 `balance` |
| `core.py` `event()` | 决策、订单、成交、暂停、结算、回滚全部落 `Event` 审计表 |
| trace_id 贯穿 | `Decision` / `Order` / `Fill` / `Ledger` / `Event` 共享同一 trace_id |
| `backend/quantdesk/api.py` `/trace/{trace_id}` | 一次返回该链路全部决策、订单、成交、分录与事件 |
| `backend/quantdesk/api.py` `/reconcile` | 现金 == 账本合计，冻结 == 活跃订单冻结合计，且现金 ≥ 冻结 ≥ 0 |
| `backend/quantdesk/api.py` `/export/ledger` `/export/fills` | CSV 导出 |
| `backend/quantdesk/api.py` `event_stream()` | SSE 基于 events 表轮询，worker 与 API 分进程也能推送；支持 `Last-Event-ID` 与 `since_id` 断线补取 |
| `backend/quantdesk/db.py` `Mutation` | 写操作幂等表，重试返回首次响应；同键不同体返回 409 |
| `frontend/src/components/TracePanel.tsx` | 追溯抽屉，按决策 → 订单/风控 → 成交 → 分录 → 事件顺序展示，拒绝原因高亮 |

状态：**自动化**（部分）。`tests/test_core.py` 验证对账通过、重复任务不重复记账。
**人工待验**：重启恢复（worker 崩溃、数据库短暂不可用、本机休眠）与备份恢复演练尚未执行。

---

## REQ-009 模拟与真实边界

所有订单均标记为模拟；系统无真实下单、钱包签名或链上交易能力，行情凭证只读并安全保存。

| 实现位置 | 说明 |
|---|---|
| `backend/quantdesk/providers.py` | 模块文档声明全部为只读适配器，供应商 URL 固定，不存在下单或签名 API |
| `core.py` `event()` 调用点 | `order.accepted` / `order.filled` / `risk.rejected` / `workspace.initialized` 载荷均带 `simulation: True` |
| `backend/quantdesk/security.py` | Fernet 加密凭证，`SECRET_NAMES` 白名单拒绝未知字段，主密钥独立于数据库，`compare_digest` 校验会话令牌 |
| `providers.py` `Provider.request()` | 异常消息脱敏，不含请求头、带 token 的 body 或供应商回显文本 |
| `backend/quantdesk/api.py` `/runtime` | 显式 `config.pop('secrets', None)`，只返回 `has_secrets` 布尔 |
| `backend/quantdesk/api.py` `lifespan()` | 非 demo 模式缺 `QD_MASTER_KEY` 时拒绝启动，而不是在首次写凭证时 500 |
| `backend/quantdesk/api.py` `serve_frontend()` | `resolve()` + `is_relative_to()` 阻止 `../` 路径遍历 |
| `backend/quantdesk/config.py` | 默认只绑定 127.0.0.1（compose 端口映射同样限定 127.0.0.1） |
| 前端全局 | `Layout.tsx` 顶栏常驻 `SIMULATION ONLY`；各页面重复标注模拟盘 |

状态：**自动化**（部分）。`tests/test_services.py` 验证明文不出现在密文中且可正确解密；`tests/test_adapters.py` 验证供应商报错不返回 demo 数据。

---

## 发布前仍需完成

1. **M0 供应商验证**（阻塞）：四数据源权限、可达性与真实样本，见 `docs/limitations.md`。
2. **5 个交易日运行观察**（PLAN §四）：覆盖四市场数据更新、AI 决策与交易链路。
3. **恢复演练**：断网、数据库短暂不可用、worker 崩溃、本机休眠、备份恢复后不重放旧订单。
4. **补充自动化用例**：API 层、A 股 T+1/涨跌停/交易日历与夏令时、并发风控、AI 预算耗尽、`settle()` 幂等、前端组件与 Playwright 用户流程。
5. **Polymarket 结算验证**：用确定性历史样本验证，不依赖观察期内自然结算。
