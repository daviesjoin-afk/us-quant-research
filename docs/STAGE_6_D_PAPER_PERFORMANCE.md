# Stage 6-D：持久化 Paper 绩效证据

D1 评估应用只读取、评估和保存事实，不修改策略状态。
D1 已通过 PR #84 合并；`STAGE_6_D_EVIDENCE_BASE` 为
`e33d4d9fb8f3183ab1e18414b158afa3bcd4b5f1`。D2 基于该实际合并提交，
由现有唯一的 StrategyLifecycleController 消费绩效事实。

## 唯一核算来源与旧实现退役

`trading/domain/portfolio_reconciliation.py` 的
`replay_portfolio_execution_truth` 是唯一部分成交回放入口。
`_scale_contributions`、`_apply_fill` 和费用尾差分配沿用 Stage 5。
原 `reconcile_portfolio_truth` 内联回放循环已移到这个入口；对账仅消费回放结果。
新增的绩效投影同样消费这个入口，不再核算成本或分配策略成交。
抽取前的固定结果保存在 `tests/fixtures/portfolio_replay_stage5.json`。

## 指标口径

- 回放全部窗口前历史，截止 `window_end`；只累计闭区间
  `[window_start, window_end]` 内的成交指标。
- 成交数按不同 execution ID 计数；同一策略多个 proposal 不重复计数。
- 平仓回合按策略及股票持仓从正数归零计数；部分卖出不计数。
- 净已实现盈亏 = 实际成交已实现盈亏 − 已分配费用，滑点不重复扣除。
- 滑点为正表示不利执行；不利滑点单独累计，不能用有利滑点抵消。
- 敞口为所有股票的成本余额之和。平均值按时间积分，不按成交次数平均。
- 回撤只来自窗口内累计净已实现盈亏，包括成交费用；不含未实现市值回撤。
- 证据时长为窗口内最后与最早真实归属成交的时间差；空窗口或单时点为零。
- 会话只计入窗口内存在真实归属成交的 session ID。

## 使用入口

1. 构造 `StrategyPaperPerformancePolicy`，显式提供所有阈值和布尔要求。
   当前 `policy_version` 为 `paper-performance-policy-v1`。没有默认政策。
2. 调用 `build_strategy_paper_performance_components`，传入数据库路径、策略仓库、
   portfolio 仓库、持久化订单事实源、只读 broker open-order 事实源。
3. 使用 `components.repository.append_policy_revision(policy,
   expected_current_revision=None)` 保存第一个版本。后续版本必须连续递增，并传入
   预期当前版本；竞争写入只能有一个成功。
4. 调用 `components.application.evaluate(strategy_version_id=...,
   policy_id=..., window_start=..., window_end=..., broker=...,
   evaluated_at=...)`。全部时间须带时区，窗口结束不能晚于评估时间。
   策略版本从仓库加载，必须已经进入 Paper；broker 事实必须来自 Paper。
5. 使用 `get_evaluation`、`evaluations_for_version` 或 `latest_for_version`
   在重启后读取不可变评估。

应用每次重新加载持久化事实，从同一组事实构建当前对账和窗口绩效。
对账时间记录实际 broker 观察时间，而非用评估调用时间伪造新鲜度。
source_digest 绑定决策及其修订、归属、订单意图/事件/成交、会话、策略身份、
窗口和对账事实。评估 ID 还绑定政策修订、指标、结论、阻断原因与评估器版本。
`evaluated_at` 不进入语义 ID；新鲜度结论不变时，只变调用时间的重试保留第一次存储记录。
评估还保存并绑定 `requested_policy_id`；即使政策不存在，不同失败查询也不会被
去重成同一记录。实际政策身份保持空值，并明确记录 POLICY_MISSING。

## 结论优先级与检查

完整性和硬风险失败 → FAIL；可信但样本不足 → INSUFFICIENT；
样本足够但正收益要求失败 → FAIL；全部满足 → PASS。
SQLite 交叉校验索引身份、规范 JSON、摘要和语义 ID；损坏抛出专用异常。

`tests/test_strategy_paper_performance_architecture.py` 锁定 P01–P12 边界。
`scripts/mutation_strategy_paper_performance_d.ps1` 先检查正常测试全绿，再运行
40 个有效变异，分别报告 survivor 与 harness error；代码在 finally 中恢复。

模拟测试中的成交不是实际 Paper 观察。只有完成受监督的多策略真实 Paper 观察、
保存评估并在重启后从真实持久化事实重建结果，才能声明 operational complete。

## D2 生命周期使用

继续使用 `build_strategy_lifecycle_components`；它将同一数据库中的持久化绩效
仓库接入原有生命周期服务。先保存 D1 评估，再调用原有 `service.apply`，
`action=StrategyLifecycleAction.PAUSE`。服务读取该策略最新的持久化评估，
控制器检查策略身份和原生命周期政策的证据时效要求。

- 当前 FAIL 可以通过 `PAPER_PERFORMANCE_FAILED` 授权 Paper → Paused。
- PASS、INSUFFICIENT 或尚无评估，不能单独授权暂停；其它治理失败仍按原规则处理。
- 首次 Research → Paper 保持原研究证据链要求，不要求已有 Paper 绩效。

过期、未来或版本不符的绩效不能授权绩效暂停，也不会否决其它有效治理原因。

决策保存不可变 `paper_performance_evaluation_id`，并将其纳入决策 ID；
现有 PREPARED → APPLIED 流程仍负责中断恢复。旧决策不增加空字段、不重写摘要，
原策略表和生命周期表结构不变。不增加绩效布尔接口、第二个状态控制器、Live 或 AI。
