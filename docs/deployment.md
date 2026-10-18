# 部署与运维

QuantDesk 设计为**本机单人运行**：浏览器访问 127.0.0.1，电脑休眠或关机时停止运行。
不支持多用户、云部署或对外暴露。全部订单与资金均为模拟盘。

---

## 一、前置依赖

| 依赖 | 版本 | 用途 |
|---|---|---|
| Docker + Docker Compose | Compose v2 | 一键启动数据库、迁移、前端构建、API、worker |
| 或本地 Python | ≥ 3.12 | 不走容器时直接运行后端 |
| 或本地 Node | ≥ 20 | 不走容器时构建前端 |

供应商凭证需你自行准备（详见 `docs/limitations.md` 的权限说明）：

- Alpaca —— 美股 IEX 行情（免费档即可，仅覆盖单一交易所）
- Tushare —— A 股日线、涨跌停与复权因子（需要相应积分权限）
- Coinbase Exchange —— 加密现货公共行情（无需凭证）
- Polymarket —— Gamma 目录 + CLOB 公共行情（无需凭证）
- 任意 OpenAI 兼容模型 API —— AI 决策

---

## 二、配置

复制环境变量模板并填入真实值：

```bash
cp .env.example .env
```

生成两个必需的密钥：

```bash
# 凭证加密主密钥（独立于数据库；丢失后已保存的凭证无法解密）
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 本机会话令牌
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

写入 `.env`：

```bash
QD_MASTER_KEY=<上面生成的 Fernet 密钥>
QD_SESSION_TOKEN=<上面生成的会话令牌>
QD_MODE=live
```

`.env` 已在 `.gitignore` 中，不要提交。

**非 demo 模式下缺少 `QD_MASTER_KEY`，API 会在启动时直接失败**并给出明确报错，
而不是等到首次写凭证时返回 500 —— 这是刻意的，避免凭证被以未加密方式写入。

变量说明：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `QD_DATABASE_URL` | compose 内部地址 | PostgreSQL 连接串（psycopg 3） |
| `QD_MASTER_KEY` | 空 | Fernet 主密钥，用于加密供应商与模型凭证 |
| `QD_SESSION_TOKEN` | 空 | 本机会话令牌，所有 API 需 `Authorization: Bearer <token>` |
| `QD_MODE` | `live` | `live` 要求真实数据；`demo` 用于隔离的演示环境 |
| `QD_ORIGINS` | 本机 5173/8000 | 允许的来源，写操作会校验 Origin |
| `QD_FRONTEND_DIR` | `frontend/dist` | 前端构建产物目录 |

`QD_MODE` 必须与行情数据的 `quality` 一致：`live` 环境下收到 `demo` 数据（或反之）会被判定为
`data environment mismatch` 并拒绝下单。演示数据不会自动填补真实数据的缺失。

---

## 三、启动

```bash
docker compose up -d --build
```

启动顺序由 compose 依赖保证：

1. `db` —— PostgreSQL，健康检查通过后才继续
2. `migrate` —— 一次性执行 `alembic upgrade head`，**成功退出**后才启动应用
3. `web` —— 构建前端并把 `dist/` 发布到共享卷，**成功退出**后才启动 API
4. `api` —— FastAPI，绑定 127.0.0.1:8000
5. `worker` —— 行情采集、AI 调度、撮合、评估、结算

查看状态与日志：

```bash
docker compose ps
docker compose logs -f api worker
```

打开工作台：<http://127.0.0.1:8000/>

首次访问需要输入会话令牌（即 `QD_SESSION_TOKEN`），保存在浏览器 localStorage。

worker 启动时会自动为白名单标的补齐历史日线（默认回溯 400 天），
以便策略评估有足够样本。该操作幂等，重启不会重复写入。

---

## 四、首次初始化

初始化前 AI 保持关闭：不采集行情、不调用模型、不生成订单。

在「设置 → 首次配置引导」按六步完成：

1. **本金** —— 四市场各自的模拟本金与记账币种（A 股为 CNY，其余 USD）
2. **数据源凭证** —— Alpaca / Tushare / 模型 Key，加密保存且不回显
3. **标的池** —— 白名单，AI 只能在这些标的上下单
4. **模型与预算** —— API 地址、模型名、每日调用与费用上限
5. **风险与汇率** —— 分市场限额、组合级回撤/单日亏损上限、CNY/USD 折算汇率
6. **确认** —— 复核后初始化

初始化是一次性操作：完成后账户本金与初始状态不能再通过界面覆盖，
调整本金只能通过资金流水记录。

初始化后系统仍处于**暂停**状态，需在「设置 → 运行控制」显式点击「恢复运行」。
恢复必须由用户明确操作，不能靠再次点击暂停来恢复。

---

## 五、本地开发（不走容器）

```bash
# 数据库
docker compose up -d db

# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'
export QD_DATABASE_URL=postgresql+psycopg://quantdesk:quantdesk-local-test@127.0.0.1:55432/quantdesk
export QD_MASTER_KEY=<Fernet 密钥>
export QD_SESSION_TOKEN=<令牌>
alembic upgrade head
uvicorn quantdesk.api:app --host 127.0.0.1 --port 8000 --reload

# worker（另开终端）
python -m quantdesk.worker

# 前端（另开终端，Vite 已配置 /api 代理到 127.0.0.1:8000）
cd frontend
npm ci
npm run dev
```

开发模式访问 <http://127.0.0.1:5173/>。

测试：

```bash
cd backend
export QD_TEST_DATABASE_URL=postgresql+psycopg://quantdesk:quantdesk-local-test@127.0.0.1:55432/quantdesk_test
pytest -q
ruff check .
```

注意：测试会 `drop_all` + `create_all` 测试库，**不要**把 `QD_TEST_DATABASE_URL` 指向真实数据库。

---

## 六、备份与恢复

账本、订单、决策与审计事件全部在 PostgreSQL 中，备份即备份数据库。

### 备份

```bash
docker compose exec -T db pg_dump -U quantdesk -Fc quantdesk > backup-$(date +%Y%m%d-%H%M%S).dump
```

建议每次初始化后、以及每次调整本金或风险限额后各备份一次。

### 恢复

```bash
# 停止应用，保留数据库容器
docker compose stop api worker

# 恢复（--clean 会先删除同名对象）
docker compose exec -T db pg_restore -U quantdesk -d quantdesk --clean --if-exists < backup-YYYYMMDD-HHMMSS.dump

docker compose start api worker
```

### 恢复后必须检查

1. **对账** —— 「账本与审计」页四个市场都应显示「对账通过」（现金 == 账本合计，冻结 == 活跃订单冻结合计）。
   也可直接调 `GET /api/v1/reconcile`。
2. **行情新鲜度** —— 「行情数据」页确认四个市场的可交易性与估值可用性。
3. **仍处于暂停** —— 恢复运行后系统不会补下休眠或停机期间错过的订单；这是刻意行为。
   需你确认行情与账户一致后，手动点击「恢复运行」。
4. **凭证可解密** —— 若 `QD_MASTER_KEY` 与备份时不同，已保存的凭证无法解密，
   会报 `credential encryption key mismatch`，需重新录入凭证。主密钥不存储在数据库中，
   请单独妥善保管。

### 主密钥丢失

主密钥丢失后已加密的凭证不可恢复，但账本与订单数据不受影响。
重新在「设置 → 凭证」录入即可。

---

## 七、停机与重启

| 场景 | 行为 |
|---|---|
| 电脑休眠 / 关机 | worker 与 API 停止；不采集行情、不生成订单 |
| 恢复开机 | `docker compose up -d`；worker 重启后先补齐历史并校验，不补下错过的订单 |
| worker 崩溃 | `restart: unless-stopped` 自动重启；租约过期的 job 由 `recover()` 处理 |
| 过期 AI job | **取消而非重试** —— 无法确认模型调用是否已发生，重放可能重复决策 |
| 其他类型 job | 指数退避重试，最多 5 次 |
| 数据库短暂不可用 | 事务失败回滚；`pool_pre_ping` 使连接池自动重建 |

全局暂停是幂等的：推进 epoch、撤销全部挂单、取消未执行 AI job，并拒绝迟到的 AI 结果。
行情采集与持仓展示在暂停期间继续进行。

---

## 八、故障排查

| 现象 | 排查 |
|---|---|
| API 启动即退出，报 `QD_MASTER_KEY must be set` | `.env` 未设置主密钥，或 compose 未读到 `.env` |
| 所有请求 401 | 浏览器 localStorage 中的令牌与 `QD_SESSION_TOKEN` 不一致；清除后重新输入 |
| 写操作 403 `origin not allowed` | 访问来源不在 `QD_ORIGINS`；把实际使用的 origin 加进去 |
| 下单被拒 `data environment mismatch` | `QD_MODE` 与行情 quality 不一致（live 收到 demo 数据或反之） |
| 下单被拒 `held asset valuation unavailable` | 同市场持仓缺少可用估值标记，检查该市场行情 |
| 下单被拒 `portfolio valuation unavailable` | 其他市场持仓标记不可用，检查对应市场数据源 |
| 下单被拒 `FX stale or invalid` | 汇率超过 `fx_max_age_hours`，到「设置 → 风控参数」重新录入带时间戳的汇率 |
| 下单被拒 `outside regular session` | 美股非正常交易时段，属预期行为 |
| A 股订单一直不成交 | A 股只在**更晚的完整日线**到达后成交，需等下一个交易日 |
| 策略停在 candidate | 样本不足（需 ≥ `min_samples × 2` 条），用「行情数据 → 补齐历史」拉取日线 |
| 补齐历史报 `Tushare permission or query error` | Tushare 积分权限不足，属首版发布阻塞项，不会用演示数据替代 |
| SSE 无事件 | 确认 worker 在运行（事件由 worker 写入 events 表，API 轮询推送） |
| 前端 404 `frontend not built` | `web` 服务未成功构建；查看 `docker compose logs web` |

日志位置：

```bash
docker compose logs api      # REST 请求与应用错误
docker compose logs worker   # 行情采集、AI 调度、撮合、评估、结算
```

日志与供应商异常消息均已脱敏，不包含请求头、带 token 的请求体或供应商回显文本。
