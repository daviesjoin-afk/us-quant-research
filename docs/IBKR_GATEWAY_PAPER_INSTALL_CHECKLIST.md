# IB Gateway Paper 安装与 4002 恢复检查清单

本清单用于把 `127.0.0.1:4002` 从 CLOSED 恢复到可采集状态，从而解除真实行情瓶颈。

**边界（必须遵守）：**

- 本清单只恢复连接，**不修改任何交易代码**：不碰策略、风控、生命周期、下单逻辑，也不新增替代交易路径。
- 不把 Gateway 安装变成新的 GitHub PR；不编写自动登录脚本；不保存账户凭据到仓库、日志或导出。
- 不开启自动下单。`paper_order_submission_enabled` 保持 `false`，Gateway 的 Read-Only API 保持开启。
- Stage 6-F 保持 BLOCKED，直到 Stage 6-D operational 完成。

**当前真实状态（2026-10-07 核验）：**

| 项 | 状态 |
|---|---|
| `127.0.0.1:4002` / `4001` / `7497` / `7496` | 全部 CLOSED（connect TimeoutError） |
| `ibgateway` / `tws` 进程 | 无 |
| `C:\Jts`、`D:\Jts`、`C:\IBGateway`、`D:\IBGateway` | 均不存在 |
| `%LOCALAPPDATA%\Programs` | 无 Gateway 条目 |
| `D:\TWS API\source\pythonclient` | **存在** |
| `.venv` 内 `ibapi` | **已安装** |

结论：**客户端侧已就绪，缺的是 Gateway 进程本身。** 这一步必须由操作员在本机完成，仓库内没有替代方案。

---

## 1. 取得安装程序并确认来源

1. 从 IBKR 官方渠道取得 **IB Gateway**（不是 TWS，也不是第三方镜像）。
2. 确认是 **Windows 64 位稳定版**，并记录版本号与下载来源 URL。
3. 记录安装程序哈希（SHA256），保存到操作员本地记录，不提交仓库。
4. 若官方同时提供 TWS：本项目按 `4002` 设计，**优先 IB Gateway**；TWS 的 Paper 端口是 `7497`，与现有配置不一致。

## 2. 确认 Paper 账户与行情权限

1. 确认可登录的是 **Paper 模拟账户**（`DU` 前缀），不是 Live 账户。
2. 确认该账户具备美股与 ETF 的 **实时行情订阅**（SPY / QQQ / AAPL / NVDA 至少这四个）。
3. 确认账户已启用 **API 使用权限**（IBKR 账户管理 → API → 允许连接）。
4. 记录账户号时**必须脱敏**，只保留末 4 位或 masked alias，不写入仓库。

## 3. 配置 API socket 与只读

在 Gateway 的 `Configure → Settings → API → Settings` 中：

1. `Socket port` = **`4002`**（Paper 端口；不要填 4001）。
2. 勾选 **`Read-Only API`**（本轮只做行情采集）。
3. `Trusted IPs` 只保留 **`127.0.0.1`**，不要留空（留空表示允许任意本机地址）。
4. 关闭 `Download open orders on connection`（本轮不下单，也不需要订单面）。
5. 关闭自动重启以外的自动化选项；不要开启任何下单相关能力。

> 注意：本轮采集**不需要**关闭 Read-Only API。只有将来运行 Paper 自动下单时才需要，且那属于另一份授权流程。

## 4. 确认配置与仓库一致

安装登录后，确认 `configs/paper.toml` 与预期一致（**不要为了让连接成功而改配置**）：

```toml
[broker]
provider = "ibkr"
host = "127.0.0.1"
port = 4002
client_id = 17
api_read_only = true
paper_order_submission_enabled = false
connection_timeout_seconds = "2"
```

若实际需要不同的 `client_id`，必须走一次显式变更审查，不得就地静默修改。

## 5. 按顺序验证（每步通过才进入下一步）

### 5.1 端口监听

```powershell
Test-NetConnection 127.0.0.1 -Port 4002
```

要求 `TcpTestSucceeded = True`。为 `False` 时停止，回到第 3 步检查 socket 配置与登录状态。

### 5.2 本地 socket 与配置（不是 API 握手）

```powershell
Set-Location D:\Codex\USQuant-stage6
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m us_quant.paper_evidence_readiness
```

此时应看到 `socket: PASS`、`readonly: PASS`、`order_submission: DISABLED`。

**必须理解这三行的确切含义，不要过度解读：**

- `socket: PASS` 只表示 **`127.0.0.1:4002` 上有一个 TCP 监听者**。诊断调用的是
  `probe_ibkr_socket()`，它只做 `socket.create_connection`，**明确不做 IBKR 协议
  握手、不认证、不读账户**。因此任何占用 4002 的程序都能让这一行显示 PASS。
- `socket: FAIL` 只表示**端口不可达**，不代表「握手失败」。要确认是 Gateway 而不是
  别的进程在监听，只能靠 5.3 的真实行情回调。
- `readonly: PASS` 读的是**本地 `configs/paper.toml` 的 `api_read_only` 值**，不是
  Gateway 里的设置。它证明的是「本地配置是只读的」，不是「远端已启用只读」。

协议握手与真实权限的唯一可信证据是 5.3 的 `broker_api_connected` 与
`market_stream_realtime`。

### 5.2b 覆盖度检查需要 health 输入

> **注意：`paper_evidence_readiness` 默认不读 health。**
> `--health-log` 的默认值是 `None`，所以不带 health 参数直接运行时，market stream
> 恒为 `observed=False`，报告里必然出现 `MARKET_STREAM_NOT_OBSERVED`。

因此做「运行中 recorder 是否健康」这类判断时，**必须把 recorder 的 health 输出接进来**：

- 方式 A（推荐，可回看最新一行）：让 recorder 把 stdout 重定向到文件，再把该文件
  传给 `--health-log`；
- 方式 B（实时快照）：用管道直接接到 `--health-stdin`，诊断取第一个可解析的 health
  对象就返回，不会挂在 EOF 上。

只做 5.2 那种「端口 + 本地配置」检查时，不带 health 参数是正常的。

### 5.3 四个标的的 realtime 行情

recorder 的 health 打印在 **stdout**，不是日志文件，所以先把它重定向到文件，
这样 5.5 的覆盖度检查可以直接读这个文件：

```powershell
New-Item -ItemType Directory -Force runtime\appdata\runtime | Out-Null

.\.venv\Scripts\python.exe -m us_quant.market_evidence_capture `
 --source ibkr `
 --symbols SPY,QQQ,AAPL,NVDA `
 --db runtime/appdata/runtime/minute_quotes.sqlite3 `
 *>&1 | Tee-Object -FilePath runtime\appdata\runtime\capture.health.jsonl
```

（`Tee-Object` 让 health 既留在屏幕上便于观察，又落盘供 5.5 读取。若只想落盘，
可换成 `> runtime\appdata\runtime\capture.health.jsonl`。）

要求 health JSON 中：

- `broker_api_connected` = `true`
- `market_stream_realtime` = `true`
- `symbols_realtime` 覆盖全部四个标的
- `status` = `RUNNING`

> **重要：recorder 在 4002 未连通时不会失败退出。** 它会正常启动并每秒/每分钟打印
> `"status": "DISCONNECTED"`、`rows_written: 0` 的 health 行。因此必须由操作员主动
> 停止，不要期待它自行报错退出。

### 5.4 启动采集

确认 5.3 全绿后再让 recorder 持续运行，并保持它作为唯一采集进程（不要并行开第二个）。

### 5.5 确认覆盖度增长

另开一个窗口，定期检查覆盖度（按 5.2b 接好 health 输入）：

```powershell
.\.venv\Scripts\python.exe -m us_quant.paper_evidence_readiness `
 --health-log runtime\appdata\runtime\capture.health.jsonl
```

**`Status` 会随采集进度变化，不要把正常进度读成故障：**

| 阶段 | 每个标的 `n/25` | Status |
|---|---|---|
| 尚未采到任何完整会话 | 全部为 0 | `BLOCKED_DATA_COLLECTION` |
| 已开始累积但未满 25 | 部分递增，未达 25 | `BLOCKED`（blocker 为 `EVIDENCE_INCOMPLETE`） |
| 全部达到 25 | 全部 25 | `READY` |

也就是说：`BLOCKED_DATA_COLLECTION` **只在所有标的都还是 0** 时出现。一旦
`n/25` 开始增长但还没到 25，状态会变成 `BLOCKED` + `EVIDENCE_INCOMPLETE` ——
这是**采集中间的正常状态**，不是失败。判断是否正常应看 `n/25` 是否在递增，而不是
看 Status 是否等于 `BLOCKED_DATA_COLLECTION`。

采集期跨越交易日（每标的需 25 个完整常规交易日）。

## 6. 采集期间禁止事项

- 不修改策略、风控、生命周期或下单代码。
- 不为了让结果好看而放宽 `MarketEvidenceQuality` 的门槛。
- 不用其他 provider 的数据冒充 `captured_stream`（`evidence_origin` 必须保持 `captured_stream`）。
- 不在同一台机器上并行运行第二个 recorder 或第二个客户端占用同一 `client_id`。
- 不删除或重建 `minute_quotes.sqlite3`。

## 7. 达标后的下一步

四个标的各达到 **25/25** 后：

1. 生成 TargetedReview；
2. 走 auth / gate / coverage / lifecycle 治理链；
3. 运行监督式 Paper shadow canary；
4. 完成跨进程 operational proof；
5. Stage 6-D operational 才标记 COMPLETE，之后才解锁 Stage 6-F。

## 8. 故障速查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `socket: FAIL`，但 `Test-NetConnection` 为 True | 端口未监听；或监听者不是 Gateway | 确认 Gateway 已登录并应用了 socket 设置 |
| `socket: PASS` 但 `realtime: FAIL` | 监听者可能不是 Gateway，或订阅未生效 | 以 5.3 的 `broker_api_connected` / `market_stream_realtime` 为准 |
| `MARKET_STREAM_NOT_OBSERVED` | 没有提供 health 输入（默认 `--health-log=None`） | 按 5.2b 传入 `--health-log` 或 `--health-stdin` |
| `realtime: FAIL` | 行情订阅未生效或延迟数据 | 检查第 2 步的市场数据订阅 |
| `SYMBOL_NOT_REALTIME` | 某标的未订阅或无权限 | 逐标的确认权限 |
| health 一直是 `DISCONNECTED` | Gateway 未登录或只读配置未保存 | 重新登录并确认设置已应用 |
| `PROVIDER_MISMATCH` | recorder 与诊断使用了不同 source | 两者都用 `--source ibkr` |
| `EVIDENCE_STORE_UNAVAILABLE` | 数据库路径不存在 | 确认 recorder 已至少运行过一次并写入 |
| Status 变成 `BLOCKED` + `EVIDENCE_INCOMPLETE` | `n/25` 已开始增长但未满 25 | 正常采集中间状态，不是故障（见 5.5） |
| Status 一直是 `BLOCKED_DATA_COLLECTION` | 所有标的仍为 0 | 确认 5.3 已全绿且 recorder 持续运行 |
