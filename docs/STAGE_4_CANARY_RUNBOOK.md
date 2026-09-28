# Stage 4 Live Canary Runbook

## 当前状态

本文记录未来 supervised Live canary 的操作顺序，不会连接 broker 或提交真实订单。

**当前不得执行真实 canary。** Desktop 尚未接入 Live broker、精确账户 truth 与 startup-proof providers；Live arm 控件保持禁用。Feature flag 不能替代人工授权，也不能绕过 provider 缺失。

本 runbook 不存储凭据、账户原始标识或 API secret。真实 canary 需要 operator 在受控环境中提供授权和资金上限，并记录复核过的部署事实。

## 开始前的阻断条件

只有所有项目都已核实并由 operator 记录后，才可进入未来 canary 流程。任何未知、超时或不一致都意味着 **STOP / HALT**。

- Environment 明确为 `LIVE`，Live feature flag 显式开启；当前安装与指定 account fingerprint 绑定。
- Live adapter 是唯一选中的执行 adapter，endpoint 是已批准的 loopback IBKR Gateway profile `127.0.0.1:4001`。
- Persistent authorization 有效、未撤销、未过期；authorized strategy 与 limits strategy allowlist 的交集非空。
- Operator 在本次进程明确 session arm；重启后必须重新 arm。持久授权本身不恢复 session arm。
- 资金、单笔名义金额、每日亏损、持仓数、在途订单数均由 operator 明确填写且大于零；源码默认额度仍为零。
- 限定 1 个策略和 1 个 allowlisted symbol；只允许整股、LMT、long-only。不能使用 short、fractional、market order、overnight expansion 或额外 broker/account。
- 账户指纹精确匹配唯一 managed account；account truth、market truth、positions、open orders、fills、connection 与 reconciliation 均为新鲜且完整的事实。
- 没有 kill latch、recovery barrier、未解释的 intent/order/fill/position/cash 差异或未完成人工对账。
- 受监督的 operator 能在同一会话监控 broker ACK、partial fill、cancel、Kill 与受控退出。

任意一条不成立、无法读取或 proof 已过期：不 arm、不 reserve、不 submit；禁止切换到 Paper 或重试另一条路径。

## 未来第一次 Canary 的顺序

以下顺序是操作要求，不是现在执行的步骤：

1. 启动应用，确认版本、Stage 4 baseline、部署环境和日志目的地。
2. 将 Environment 设为 `LIVE`，确认 Live flag 显式开启。
3. 查看持久授权 ID、到期时间、账户掩码、账户 fingerprint、策略和全部 limits。
4. 连接唯一批准的 Live endpoint；确认恰好一个 managed account 与 fingerprint 精确匹配。
5. 获取当前 positions、open orders、fills/events、account truth 和 market truth。
6. 将本地 intent/order ledger 与 broker truth 对账。确认没有未知订单、重复订单、未归属成交或现金/持仓差异。
7. 确认 startup proof 新鲜、reconciliation clean、kill latch 与 recovery barrier 均未置位。
8. 再核对 1 strategy、1 symbol、资金上限、单笔上限、日亏损、1 position、1 open order、整股和 LMT 规则。
9. Operator 阅读并确认真实订单风险，在当前进程显式 session-arm；该确认不得持久化为下次启动的 arm。
10. 只提交一笔极小的 allowlisted LMT entry。不得连续发单或因等待 ACK 再次提交。
11. 等 broker ACK 并按 broker order id 对账；记录 partial fill、剩余数量、手续费、滑点和延迟。
12. 通过受控 long-only exit 平仓；确认 position 为 0、open orders 为 0、所有 fills 与现金变化可解释。
13. 保存 broker zero-state proof，再断开并 finalize；session arm 必须失效。
14. 复核 audit bundle、授权 revision、broker/local truth 对应关系和最终零状态后，结束监督会话。

## 必须保持 HALT 的情形

- Disconnect、stale/missing account 或 market truth、账户不匹配、未知订单/成交、reconciliation discrepancy。
- 明确拒单、超限、未授权策略/标的、short/fractional/non-LMT 请求。
- `placeOrder` 后结果不确定、process crash、restart、持久 store 不可读或 kill latch 触发。
- Partial fill、cancel、剩余数量、成交账户或本地 reservation 与 broker truth 不一致。

发生后保留 durable intent 和 broker order id，不重试、不 fallback、不自动 re-arm。先 HALT、撤销 session arm、保留 recovery barrier，再读取新鲜 broker truth 并显式对账。只有完全匹配的人工 reconciliation、当前 revision 的新 session arm 与新 startup proof 才能允许未来继续。

Kill latch 会阻止新增风险。允许的安全动作限于 broker truth 查询、对账、取消已跟踪 entry、减少已持有 long 仓位、断开与 finalize；SELL 数量不能超过已确认且未预留的持仓。清除 kill/recovery barrier 不会恢复旧 session arm。

## 故障演练与审计

在 fake broker / in-memory doubles 上单独演练 idle kill、reserve 前 kill、reserve 后 submit 前 kill、pending order、已有 position、restart、reconnect、refusal、uncertain submission、partial fill、cancel、account mismatch、stale truth、daily loss/capital cap 和 reconciliation discrepancy。CI 不能连接真实 Live endpoint。Stage 4 现有行为证据位于 `tests/test_live_safety.py`、`tests/test_live_startup.py`、`tests/test_ibkr_live_execution_adapter.py`、`tests/test_live_canary_execution.py`、`tests/test_live_recovery.py` 和 `tests/test_execution_environment.py`。

每笔订单至少保留：strategy version、order id、broker order id、masked account/fingerprint、authorization id、session id、Risk decision、canary decision、expected/submitted price、submit/ACK/fill 时间、filled quantity、average fill、commission/fees、slippage、latency、realized P&L 和 reconciliation outcome。禁止把 raw account id、密码或 API secret 写入日志。

## 完成状态

Runbook 被编写不代表 canary 已运行，也不等于 Stage 4 implementation 已完成。Stage 4 operational completion 还必须由真实 supervised canary 证明 broker/local truth 一致、无未解释订单/成交/持仓、可靠的 zero-state shutdown 与 disconnect/restart/kill recovery，并完整记录费用、滑点、延迟及 partial-fill 结果。任何差异未解释前保持 HALT。
