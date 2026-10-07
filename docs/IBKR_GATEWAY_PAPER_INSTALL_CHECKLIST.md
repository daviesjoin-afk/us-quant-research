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

### 5.2 API 连接与只读

```powershell
Set-Location D:\Codex\USQuant-stage6
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m us_quant.paper_evidence_readiness
```

此时应看到 `socket: PASS`、`readonly: PASS`、`order_submission: DISABLED`。
如果 `socket: FAIL`，说明协议握手未完成（不只是端口未监听）。

### 5.3 四个标的的 realtime 行情

启动 recorder 并观察 health 行（health 打印在 **stdout**，不是日志文件）：

```powershell
.\.venv\Scripts\python.exe -m us_quant.market_evidence_capture `
 --source ibkr `
 --symbols SPY,QQQ,AAPL,NVDA `
 --db runtime/appdata/runtime/minute_quotes.sqlite3
```

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

另开一个窗口，定期检查覆盖度：

```powershell
.\.venv\Scripts\python.exe -m us_quant.paper_evidence_readiness
```

期望随交易日推进：`Status: BLOCKED_DATA_COLLECTION`，且每个标的的 `n/25` 递增。
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
| `socket: FAIL`，端口却是 True | 协议握手失败 / 客户端 ID 冲突 | 检查 `client_id` 是否被其他进程占用 |
| `realtime: FAIL` | 行情订阅未生效或延迟数据 | 检查第 2 步的市场数据订阅 |
| `SYMBOL_NOT_REALTIME` | 某标的未订阅或无权限 | 逐标的确认权限 |
| health 一直是 `DISCONNECTED` | Gateway 未登录或只读配置未保存 | 重新登录并确认设置已应用 |
| `PROVIDER_MISMATCH` | recorder 与诊断使用了不同 source | 两者都用 `--source ibkr` |
| `EVIDENCE_STORE_UNAVAILABLE` | 数据库路径不存在 | 确认 recorder 已至少运行过一次并写入 |
