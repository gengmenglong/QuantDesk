# 执行进度

计划：`PLAN.md`（四市场 AI 模拟交易工作台开发计划）。
基线：`d4c6d70`（仅产品文档、HTML 原型与许可证），工作分支 `codex/quantdesk-workbench`。

---

## 已落地范围

### M1 工程与持久化 —— 基本完成

- 前后端骨架：FastAPI + SQLAlchemy 2 + Alembic；React 19 + TypeScript + Vite
- `alembic/versions/0001_initial.py` 建表并插入 runtime 行
- 金额一律 `Numeric(30,10)` 定点，时间一律 `DateTime(timezone=True)` UTC
- 凭证 Fernet 加密，主密钥独立于数据库；会话令牌 `compare_digest` 校验
- 写操作 Origin 白名单校验（CORS 头不足以阻止跨站表单提交）
- SPA 静态服务做 `resolve()` + `is_relative_to()` 路径遍历防护
- 非 demo 模式缺 `QD_MASTER_KEY` 时 API 启动即失败，而不是首次写凭证时 500
- Docker Compose：db → migrate（成功退出）→ web（构建前端）→ api / worker

**未做**：CI（`.github/workflows/` 为空）、前端自动化测试、OpenAPI 生成类型（`src/api-types.d.ts` 未生成）。

### M2 四市场行情 —— 代码完成，真实数据未验证

- 四个只读适配器：Alpaca IEX、Tushare 日线、Coinbase Exchange L1、Polymarket Gamma + CLOB
- 统一 `MarketSnapshot` 契约：source、event_time、received_at、delay_seconds、quality、tradable、reason
- 执行级新鲜度 `quote_problem()` 与估值级新鲜度 `valuation_problem()` 分离：
  收盘后的最后一笔收盘价对记账仍有效，只阻塞新单执行；正常隔夜与节假日不判定为断流
- 公司行动保守筛查（美股开盘价 vs 前收偏离 > 25%；A 股相邻 `adj_factor` 比对）
- 历史补齐：四个 provider 的 `history()`，`received_at` 钉为 `event_time` 以免污染影子窗口
- 供应商异常消息脱敏，不回显请求头、带 token 的请求体或供应商文本
- 真实数据失败时不降级为 demo；`QD_MODE` 与数据 quality 不一致直接拒绝下单

**阻塞**：真实凭证、权限与网络可达性尚未在本机验证（PLAN M0 出口条件）。

### M3 账本、撮合与风控 —— 基本完成

- 四市场独立账户与记账币种；本金调整只能走资金流水，不能覆盖余额
- 订单状态：pending / partial / filled / cancelled / expired / rejected
- 撮合用决策之后的新报价，同快照与早于下单时刻的报价不成交
- 分市场费用与滑点；盘口限量 + `Snapshot.consumed` 累计占用；A 股 0.1% 参与率上限
- A 股：更晚的完整日线才成交、以开盘价为参考、整手/整股、T+1 lots、涨跌停元数据缺失即保守撤单
- 预测市场：`Polymarket.state()` 三态区分 open / closed 待裁定 / resolved；
  `core.settle()` 仅在 payout 为 0 或 1 且带 resolution_id 时结算，账本键幂等；
  `worker.poll_resolutions()` 轮询，待裁定时撤销挂单并保留持仓估值
- 风控：现金含冻结、持仓与敞口上限、单日亏损、账户与组合级回撤熔断、白名单、行情状态、FX 时效
- 待成交订单占用资金与风险额度；撮合前用当前限额复验（排除自身冻结）
- 暂停幂等且推进 epoch，撤销挂单、取消未执行 AI job、拒绝迟到结果；恢复必须显式操作
- `reconcile()` 校验现金 == 账本合计、冻结 == 活跃订单冻结合计、现金 ≥ 冻结 ≥ 0
- 写操作幂等：`Mutation` 表按 key + 请求体指纹，命中返回首次响应，同键不同体返回 409

### M4 AI 与策略评估 —— 基本完成

- OpenAI 兼容客户端 + `DECISION_SCHEMA` 结构化输出；超时/HTTP 错误/解析失败/校验失败
  一律记为 `failed` 并记录原因，不伪装成 `hold`，不产生订单
- `apply_result()`：epoch 失效丢弃、跨市场越权拒绝、幂等键、风控拒绝被记录而非抛出
- 调度节奏：美股/加密/预测 15 分钟批次；A 股按已完成日线 session 一批次；无新数据不调用
- 预算：调用次数 / token / 费用三重上限，日界按 UTC 零点（此前用字符串日期比较 timestamptz，
  日界会随数据库时区漂移）
- 评估：train / holdout 前后对半切分，样本不足直接 `insufficient_samples`；
  影子窗口与基准使用完全相同的窗口与成本模型对比
- 生命周期自动推进：worker 每 6 小时入队 candidate/shadow 评估，每日入队 active 监控；
  `evaluate` 端点改为入队而非在请求线程内跑最多 4000 行回放
- 回滚：`rollback_strategy()` 状态守卫保证一次性生效，恢复同名最近的 retired 版本；
  `monitor_strategy()` 在监控窗口回撤越界时自动触发；用户可手动回滚

**未做**：真实模型调用未验证；并发风控竞争、预算耗尽、`settle()` 幂等尚无专门用例。

### M5 工作台完整联调 —— 基本完成

- 七页面全部接入真实 API
- 追溯抽屉 `TracePanel`：决策 → 订单/风控 → 成交 → 分录 → 事件，风控拒绝高亮；
  入口挂在订单行、成交行、账本流水行与决策行
- recharts 资金曲线：策略的训练/验证/影子/监控各段，工作台的分市场资金曲线
- 策略字段级差异对比 + 发布/回滚/评估记录时间线 + 手动回滚按钮
- 行情页升级为新鲜度分级面板：市场健康汇总、来源、质量、事件时间 vs 采集时间、账龄、
  声明延迟、可交易性与估值可用性分别判定、不可用原因文本直出、历史补齐入口
- AI 评估页增加当日预算进度条（调用/token/费用 vs 上限）与阻断原因
- 首次配置改为六步向导，显式提示初始化前 AI 保持关闭
- SSE 修复：事件改为基于 events 表轮询（worker 与 API 分进程，内存 hub 收不到事件）；
  `/stream` 接受 `?token=`（EventSource 无法设自定义头），其余端点仍只认 Authorization；
  支持 `Last-Event-ID` 与 `since_id` 断线补取，前端以最新事件 id 为游标避免重放全量审计
- 工作台组合估值在汇率缺失或过期时改为分币种展示

**未做**：OpenAPI 生成类型替换 `any`；bundle 未做代码分割（约 725 kB）。

### M6 稳定性与交付 —— 部分完成

已完成：

- `docs/deployment.md`：前置依赖、`.env` 与密钥生成、启动顺序、首次初始化、本地开发、
  备份恢复（pg_dump / pg_restore）与恢复后必查项、停机重启语义、故障排查表
- `docs/limitations.md`：未验证阻塞项、行情固有限制、撮合模型限制、跨币种报表限制、
  AI 与策略限制、工程限制、明确不在范围内的事项、已保证的数据不变量
- `docs/acceptance.md`：REQ-001 ~ REQ-009 逐条映射到实现位置与验证状态
- `README.md` 重写为真实应用的说明
- compose 修复：api/worker 依赖 migrate 成功退出、前端构建服务、restart 策略、
  移除已废弃的顶层 `version` 键与 api 的 `--reload`、healthcheck
- 仓库卫生：`.gitignore` 补 egg-info 与 tsbuildinfo；清理 api.py / worker.py / ai.py / core.py
  的未用导入与死变量；中部 `import csv, io` 提到文件头

未完成：

- 故障演练（断网、数据库短暂不可用、worker 崩溃、本机休眠、备份恢复）
- 5 个交易日运行观察
- 性能检查
- 测试报告（现有 17 个 pytest 用例通过，但 PLAN §四 要求的八类覆盖尚不完整）

---

## 当前验证状态

| 检查 | 结果 |
|---|---|
| `pytest -q` | 17 passed |
| `python -m compileall quantdesk` | 通过 |
| `ruff check`（已改动文件） | 无 F401 / F841 / F811 / F821 / E9 |
| `ruff check .`（全仓库） | 既有基线告警仍在（B008 FastAPI Depends 惯用法、I001 导入排序、BLE001 宽异常等），非本轮引入 |
| `npx tsc -b` | 通过 |
| `npm run build` | 通过（725 kB，含 recharts，有 chunk 体积提示） |
| 真实数据端到端闭环 | **未验证** —— 需真实供应商凭证与模型 Key |
| `docker compose up` 全链路 | **未验证** —— 本轮未执行构建与启动 |

---

## 下一步（发布阻塞项）

1. 在本机配置四数据源凭证与模型 Key，验证权限、可达性与真实样本（PLAN M0）
2. `docker compose up -d --build` 走通全链路，执行 `docs/deployment.md` 的手工闭环演练
3. 用确定性历史样本验证 Polymarket 结算
4. 补齐 PLAN §四 要求的八类测试覆盖，建立 CI
5. 完成 5 个交易日运行观察与恢复演练，产出测试报告
