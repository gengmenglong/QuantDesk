# QuantDesk

四市场 AI 模拟交易工作台 —— 美股、A 股、加密现货与 Polymarket 二元预测市场。

**全部订单、成交、持仓与资金均为模拟盘。** 行情可以是真实的，但不接入任何真实交易、
钱包签名、杠杆或做空能力。设计为本机单人运行，浏览器访问 127.0.0.1，电脑休眠或关机时停止运行。

---

## 核心特性

- **四市场真实行情** —— Alpaca IEX（美股）、Tushare 日线（A 股）、Coinbase Exchange（加密现货）、
  Polymarket Gamma + CLOB（预测市场）。统一记录来源、事件时间、采集时间、声明延迟与质量状态。
- **AI 自主决策** —— 接入任意 OpenAI 兼容 API，在受约束的策略配置与标的白名单内自主决策。
  输出经 JSON Schema + Pydantic 双层校验，**不执行任意生成代码**。
- **独立确定性风控** —— 由代码而非模型执行：现金含冻结、持仓与敞口上限、单日亏损、回撤、
  白名单、行情状态、整手/整股、A 股 T+1 与可卖数量。AI 无权修改风控门槛。
- **可追溯账本** —— 十进制定点记账，决策 / 订单 / 成交 / 分录 / 事件共享 trace_id，
  一次查询即可回溯完整链路；对账校验现金与账本合计一致。
- **策略生命周期** —— 候选 → 历史评估 → 影子运行 → 自动发布 → 持续监控 → 回滚或暂停。
  评估与正式模拟共用同一套撮合、费用与风控规则；样本不足时保持候选，不降低门槛凑发布。
- **幂等与暂停语义** —— 所有写操作支持幂等；全局暂停幂等且推进 epoch 使迟到的 AI 结果失效，
  恢复必须由用户显式操作。

---

## 技术栈

| 层级 | 方案 |
|---|---|
| 前端 | React 19、TypeScript、Vite、recharts |
| API | Python 3.12、FastAPI、Pydantic v2 |
| 后台进程 | 与 API 共用领域模块的独立 worker |
| 数据库 | PostgreSQL 16、SQLAlchemy 2、Alembic |
| 页面更新 | REST + SSE（事件表轮询，支持断线补取） |
| 部署 | Docker Compose，仅绑定 127.0.0.1 |

模块化单体架构。后台任务使用数据库持久化队列 + 租约 + 幂等键，不引入 Redis。
金额与数量一律十进制定点，时间一律 UTC 存储。

---

## 快速开始

```bash
# 1. 配置密钥
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 把两个输出分别写入 .env 的 QD_MASTER_KEY 与 QD_SESSION_TOKEN

# 2. 启动（数据库 → 迁移 → 前端构建 → API → worker）
docker compose up -d --build

# 3. 打开工作台
open http://127.0.0.1:8000/
```

首次访问输入 `.env` 中的会话令牌。随后在「设置 → 首次配置引导」按六步完成初始化：
本金 → 数据源凭证 → 标的池 → 模型与预算 → 风险与汇率 → 确认。

**初始化完成前 AI 保持关闭**，不采集行情、不调用模型、不生成订单。
初始化后系统仍处于暂停状态，需显式点击「恢复运行」。

完整步骤、本地开发方式、备份恢复与故障排查见 **[docs/deployment.md](docs/deployment.md)**。

---

## 七个页面

| 页面 | 内容 |
|---|---|
| 工作台 | 组合估值、四市场账户（分币种净值/现金/冻结/高点）、资金曲线、持仓、最近事件 |
| 行情 | 分市场健康汇总、来源与质量、事件时间 vs 采集时间、账龄、声明延迟、**可交易性与估值可用性分级**、手动采集与历史补齐 |
| 交易 | 手动下单（模拟）、订单与成交明细、风控拒绝原因、全链路追溯 |
| 策略 | 版本列表、字段级差异对比、训练/验证/影子/监控各段资金曲线、评估证据、发布与回滚记录、手动回滚 |
| AI 评估 | 当日调用/token/费用对预算的进度、决策列表与依据、失败与丢弃原因、追溯 |
| 账本 | 四市场对账状态、资金流水分页、CSV 导出、按 trace_id 追溯 |
| 设置 | 首次配置向导、凭证加密管理、风控参数、运行控制 |

任意订单、成交、分录或决策都可点开追溯抽屉，按
决策 → 订单/风控 → 成交 → 资金分录 → 审计事件 的顺序查看完整链路。

---

## 项目结构

```
backend/
  quantdesk/
    core.py         # 交易内核：账本、撮合、风控、暂停、结算、对账
    providers.py    # 四个只读行情适配器（含历史补齐）
    ai.py           # 决策应用与预算校验
    llm.py          # OpenAI 兼容客户端与结构化输出 Schema
    evaluation.py   # 确定性回放、评估、影子对比、监控与回滚
    jobs.py         # 数据库持久化任务队列（租约 + 幂等键）
    worker.py       # 后台进程：采集、结算、补齐、调度、评估
    api.py          # FastAPI REST + SSE
    db.py           # SQLAlchemy 模型
    schemas.py      # Pydantic 契约
    security.py     # Fernet 凭证加密与会话令牌
    config.py       # 环境配置
  alembic/          # 数据库迁移
  tests/            # pytest（账本、风控、暂停、适配器、AI 异常、评估门槛）
frontend/
  src/
    api.ts          # 接口封装、幂等键、SSE 断点续传
    components/     # Layout、TracePanel
    pages/          # 七个业务页面
docs/
  deployment.md     # 部署、初始化、本地开发、备份恢复、故障排查
  limitations.md    # 已知限制与未验证的阻塞项
  acceptance.md     # REQ-001 ~ REQ-009 验收映射
  progress.md       # 执行进度
```

---

## 测试

```bash
cd backend
export QD_TEST_DATABASE_URL=postgresql+psycopg://quantdesk:quantdesk-local-test@127.0.0.1:55432/quantdesk_test
pytest -q        # 账本正确性、风控、暂停幂等、适配器、AI 异常、评估门槛
ruff check .
```

```bash
cd frontend
npx tsc -b && npm run build
```

测试需要本机 PostgreSQL（`docker compose up -d db` 即可）。
测试会重建测试库，不要把 `QD_TEST_DATABASE_URL` 指向真实数据库。

---

## 当前状态与已知限制

首版尚未发布。**阻塞项**：

1. 四数据源的真实权限、可达性与样本尚未在本机验证（PLAN M0 出口条件）。
   适配器测试使用固定响应样本，自动化测试不等于真实供应商认证。
2. 尚未完成 5 个股票交易日的运行观察。
3. Polymarket 自然结算、恢复能力演练（断网/崩溃/休眠/备份恢复）尚未执行。

主要固有限制：美股为 IEX 单交易所覆盖（非全市场 SIP）；A 股仅日线低频模拟，
订单在下一个交易日以开盘价估算成交；折算汇率需手工录入；预测市场历史价格点不携带盘口深度。

完整清单见 **[docs/limitations.md](docs/limitations.md)**，
需求映射与验证状态见 **[docs/acceptance.md](docs/acceptance.md)**。

---

## 设计参考

- `quantDesk.md` —— 产品范围、模拟边界、AI 策略工作流、风控与分阶段交付计划
- `PLAN.md` —— 开发计划与技术架构
- `quantDesk-prototype.html` —— 独立交互式 UI 原型（本地演示数据，无构建步骤，
  不连接任何模型、行情供应商或券商）。保留为视觉参考，正式应用已迁移其布局与交互。

## License

See [LICENSE](LICENSE).
