# Stage 5 多策略 Paper 操作手册

本手册用于一次有人监督的 Paper 验证。Stage 5 architecture completion 不代表 Live authorization，也不会把 Paper
runtime 切换到 Live。不要用本流程提交真实账户订单。

## 启动前检查

1. 确认运行的是已审查的 Stage 5 build，Live feature 与 Live authorization 保持关闭。
2. 检查 `python -m us_quant doctor`：环境必须为 Paper，Live trading disabled，IBKR 账户明确为 Paper account，
   连接事实、API 权限和 Paper order submission 开关符合本次批准的操作范围。
3. 打开 Portfolio Operating Plan，复核每个选中的 governed strategy version、PAPER_SHADOW 状态、gate、capital weight、
   每策略资本/敞口上限、组合总资本、gross/net、symbol concentration、position count 和 open-order count。
4. 保存 plan 时使用当前 revision；stale revision 必须重新载入、复核后再保存，不得绕过 CAS。
5. 在确认窗口记录预期 masked account alias；完成连接后核对实际 alias 与之完全一致。
6. 连接后读取 fresh broker account values、positions、open orders 和 local durable order/fill/attribution truth。
   account/equity/cash 不可缺失；positions 与 open orders 必须完整且足够新。
7. 要求 clean reconciliation、`can_open_exposure = true`、无 unknown order/position、无 pending execution、无本地未对账订单。
   启动时必须 zero positions / zero open orders；若不是 zero-state，走人工 recovery/安全减仓流程，不开启 autonomous entries。
8. 复核 autonomy state 为允许本次 Paper session 的显式状态；PAUSED、DISABLED、kill latch、缺失或不可读控制面都必须停止新 exposure。

## 监督运行

1. 启动单一 Portfolio Paper session，确认状态页显示同一 account alias、冻结 plan revision、选中策略和有效配置。
2. 等待一次完整 portfolio cycle。记录 `portfolio_cycle_id`、`observed_at`、proposal cutoff、snapshot identity 和 plan revision。
3. 检查各 strategy proposal 来自同一个 market observation window；记录 BUY、SELL、HOLD proposal 及其 strategy version/proposal ID。
4. 检查 PortfolioDecision 的 symbol、signed attribution 和 net quantity。相反方向 proposal 必须先聚合，再形成最多一个 net action。
5. 批准的 action 必须经过 RiskDecision、唯一 OrderDispatch、ExecutionApplication 和 BrokerExecutionPort。保留 order ID、broker order ID、
   reserve/durable/submit 证据；不确定 submit 时停止 session 并 reconciliation，绝不重试。
6. 订单在途时检查 broker open-order facts 是否与 durable intent/account/symbol/side/quantity/remaining quantity 一致，
   strategy-side reservation 方向与 contribution 符号一致。
7. 收到 fill 后保存 execution ID、数量、价格、费用和时间；核对 partial fill 的 strategy attribution 与 runtime/reconciliation
   使用相同的 deterministic scaling helper。若本次没有 fill，记录“未发生”，不要补造证据。
8. 每个可能产生新 exposure 的 cycle 都必须 fresh reconcile。unknown broker order、position、fill、stale fact、missing decision、
   duplicate link 或 ledger corruption 都应阻止新 exposure 并进入 halt/recovery。

## Pause、Stop 与结束

1. Pause 只关闭新 entry；观察并验证已有持仓的策略 exit 与必要 reductions 仍经 Portfolio/Risk/Dispatch/Execution。
2. operator stop 或 end-of-day force-flat 应为每个 durable strategy owner 生成 reduction proposal。核对 attribution 后，
   通过单一 PortfolioRuntime netting 和共享 execution path 发出物理订单；禁止 direct broker flatten。
3. 等待所有订单到达 terminal status，并让 event filled quantity 与 durable fills 一致。无法解释的在途订单必须人工恢复。
4. 确认 broker positions 和 open orders 都为零；再次执行 fresh final reconciliation，确认 `can_open_exposure = true`、
   `open_order_ids` 为空、broker quantity 等于 strategy ownership 总和且各策略数量均为零。
5. 停止 session，确认进程内 arm/worker state 已结束，下一次启动必须重新通过全部 startup proof。

## 留存证据

归档 masked account alias、plan ID/revision、策略 version IDs、cycle/snapshot identity、proposal、PortfolioDecision、RiskDecision、
OrderIntent、broker order ID、broker event/fill、fees/slippage、execution attribution、reconciliation blockers/result、stop reason 和
zero-state final snapshot。不要归档账户凭据或原始 account identity。

Stage 5 实施完成与 supervised Paper canary 是两个独立状态。每次报告都明确标记 canary 为 `RUN` 或 `NOT RUN`，不得用自动化测试
替代真实有人监督的 Paper 操作证据。
