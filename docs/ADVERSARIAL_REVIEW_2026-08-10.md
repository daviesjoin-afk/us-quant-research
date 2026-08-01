# 对抗性审查报告：USQuant 流程闭环与下单稳定性

- 日期：2026-08-10 · 对象：`USQuant` v0.19.0（IBKR Paper 模拟交易研究台）
- 方法：攻击者/故障注入视角逐行阅读执行链路（4 个子代理并行深审 + 本人通读流程骨架）+ 完整测试基线 + 真实代码复现脚本。
- 基线：`pytest -q` → **281 passed, 9 subtests passed (34.53s)**（全绿 ≠ 无缺陷，见 H-12/H-13/M-系列测试质量问题）。
- 标记说明：`[复现]`=已用真实代码脚本复现；`[A]`/`[B]`/`[C]`/`[D]`=子代理来源；无标记=本人一手确认。

## 结论摘要

安全边界（fail-closed 纵深）是**教科书级**的：账户指纹绑定、幂等键双检、成交数量对账、两段式最终化证明、互斥执行租约、HALTED 人工对账——这些设计经对抗性审查**未被绕过**（无法构造非整股/非 LMT/非 DAY/非 SMART/非 USD/非 4002/非 DU/非本会话订单）。

但"流程闭环的自动运转"存在**系统性断点**，按影响排序：

1. **[复现] 停止会话时重复提交 SELL 订单风暴**（CR-1）：一次停止 + 10 个行情 tick → 10 张相同 SELL 进入券商。
2. **订单看门狗完全依赖行情流存活**（H-1）：设计中的独立轮询 `_poll_auto_quant_orders` 是死代码；行情流崩溃 → 超时撤单/SELL 干预/健康评估全部停摆，会话悬挂 RUNNING。
3. **引擎自发停机不传导 workflow**（H-2）：引擎 `active=False` 时会话仍是 RUNNING 的"僵尸"，风控退出全部失效。
4. **数据完整性：所有哈希只写不验**（CR-5）：历史数据、回测结果、策略参数、晋级状态均可被直接篡改而不被发现；部分测试把缺口合法化。
5. **关闭路径死锁**（CR-2/CR-3）：UI 线程同步 `wait(3000)` + 无限拒绝退出，用户被逼向强杀。

发现统计：**Critical×5 / High×14 / Medium×10 / Low·观察×22**（去重后）。

---

# Critical

## CR-1 停止路径重复提交 SELL 订单风暴（每 500ms 一张新卖单）【复现】【A】【B】
- 位置：`auto_quant.py:441-456`（`_stop_requested` 分支对每个持仓无条件 `_emit_exit`，**无 `not self.pending` 守卫**）对比强平分支 457-468 与 `_check_exit` 469-473（均有守卫）。
- 链路：停止 → `engine.request_stop()`（`auto_quant.py:673-679`）→ 每个行情 tick（500ms，`desktop.py:7856-7867` → `paper_session.py:370-373`）重新执行停止分支 → `_emit` 生成**全新 intent_id/idempotency_key**（`ibkr_paper_orders.py:2051-2072`）→ `submit` 的幂等键匹配不到（1628-1635）→ `sellable_quantities` 只在**成交时**扣减（1954-1963），提交不扣 → 每张都合法通过校验并 `placeOrder`。
- 复现：`repro_stop_duplicate_sell.py`（真实引擎代码）——1 次 stop + 10 tick → **10 张 (AAPL,4) SELL** 全部提交。
- 后果：a) 券商侧 N 张重复卖单；b) 两张以上成交 → 超卖 → 引擎停机（`auto_quant.py:622-629`）+ 对账失败 → HALTED；c) 第二张被 `sellable_quantities` 拒绝 → 引擎停止中途 `active=False`（1072-1090）→ 会话卡 RUNNING（见 H-2）。
- 修复：停止分支加 `not self.pending`（或按 symbol 检查在途 SELL）守卫；`_emit_exit` 按 symbol 幂等；补"stop 后多 tick 只出一张 SELL"行为测试。

## CR-2 关闭路径：UI 线程同步 `wait(3000)` + 无限拒绝退出，用户被逼向强杀【C】
- 位置：`desktop.py:8499`（closeEvent）→ `desktop.py:7765`（`worker.wait(3000)`）→ 8500-8510 `event.ignore()` + "请稍后再次关闭"；循环往复。
- 场景：IBKR 行情线程无法在 3s 内退出（网关半死）→ 每次关闭阻塞 3s 后被拒；无强制退出出口（HALTED 未对账也拒绝退出，8477-8491）。
- 影响：强杀时恰逢 Paper 对账/断开中间态 → 内存态执行租约丢失；"关不上"的护栏诱发不安全退出。
- 修复：关闭改"后台排空 + 异步确认"（复用 `_paper_finalization_inflight` 模式）；提供显式"放弃会话并退出"二次确认（先落盘 journal/事件）。

## CR-3 启动路径：UI 线程重 IO + 同步 TCP 探测，冻结数秒至 120 秒【C】
- 位置：`desktop.py:3604-3681`（`_load_local_state`，由 9499 `QTimer.singleShot(0,...)` 首帧后执行）→ `desktop.py:6913-6922`（`_probe_gateway` 同步 `socket.create_connection`，超时按用户配置最长 120s）。
- 场景：universe（~11k 条）+ 扫描 + 日K×3 + 最多 8 个 targeted 目录全部 JSON（每目录 ≤100 文件）解析，然后同步探测 Gateway；防火墙丢包时挂满超时。
- 修复：加载与探测迁入 `TaskThread`；探测独立 3s 硬超时。

## CR-4 重连后 `nextValidId` 无条件覆写订单号 → 订单号复用风险【A】
- 位置：`ibkr_paper_orders.py:840-845`（回调直接 `service._next_order_id = int(orderId)`），connect 全程（805-1230）无"与 journal 已用订单号比对"的防护。
- 场景：Gateway/TWS 重启后服务端订单号计数器回退（已知场景）→ 新会话用回旧 ID → IBKR 以 orderId 为键，可能**修改/撤销旧订单**；或 journal `broker_order_id UNIQUE` 冲突（`record_intent` 抛错 → submit 失败 → 停机）。
- 修复：`_next_order_id = max(int(orderId), journal 最大 broker_order_id + 1)`；connect 后校验已用 ID 集无碰撞。

## CR-5 数据完整性：所有哈希"只写不验"，历史数据/回测/策略/晋级状态可被直接篡改【D】
- **C5a 读路径从不校验 sha256**：`market_data.py:379-417` 加载时直接信任文件内自称的 `source_sha256`（L405），从不重算内容比对；`_write_once`（500-520）只在写入时防覆盖。篡改任何 close 价/删行/注入 bar 加载照常成功，且向下传导 `data_hash` → `run_hash` → research manifest → targeted_* 交叉校验——**整条研究审计链锚定在未验证的元数据上**。
- **C5b `run_hash` 只写不验**：`backtest_workspace.py:397-398` 写入后全代码库无任何读取校验（grep 仅此一处）；结果文件按 `run_id=uuid4()` 命名不按键控，`parameter_hash/code_hash` 是调用方原样写入（304-311），可改写数值后继续充当可信结果。
- **C5c strategy_registry 无篡改检测**：`strategy_registry.py:663-681` 直接 `json.loads(row[7])` 不重算 `parameter_hash`；`gate_passed` 直接信任 DB 列（677）——`UPDATE ... SET gate_passed=1` 即绕过"调用方不能自声明晋级"硬门（85-88）；种子 `code_hash` 是字面标签 `"unverified-local-source-0.7.0"`。
- **C5d PIT 执行候选门是信任标志**：`market_data.py:412-413` 读裸布尔；全 src **无任何保存路径写入该字段**（grep 仅读取/传播）→ `EXECUTION_CANDIDATE_PIT_RAW` 门（138）要么不可达，要么手写 `true` 即零证据通过；`tests/test_market_data.py:182-204` 甚至把"手写标志通过"确立为预期行为（测试放水）。
- 修复：读路径重算 canonical 哈希比对（失败即拒/置 legacy_invalidated）；`run_hash` 实现读取校验；registry 读时重算 parameter_hash + gate_passed 审计链；PIT 必须引用可校验成员证据；删除/改造放水测试。

---

# High

## H-1 订单看门狗依赖行情流存活：`_poll_auto_quant_orders` 是死代码【复现级代码证据】【A】【B】【C】
- 位置：`desktop.py:5339-5351` 定义，**全工程无调用者**（grep 确认）；唯一事件入口 `desktop.py:7856-7867`（stream tick → `on_stream`）；`paper_session.py:340-374` 的 `poll()` 与 `on_stream()` 看门狗序列相同但无入口。
- 场景：行情 worker 崩溃（`_stream_failed` 8426 只记录事件，不重置/不暂停会话）→ `_poll_stream_snapshot`（7916）提前返回 → `on_stream` 不再调用 → **超时 BUY 不再撤销（README 承诺失效）、SELL 90 秒干预失效、健康评估/券商断线检测停摆**；会话界面停留 RUNNING 且无任何提示。测试 `test_desktop_paper_controller.py:19,101` 只断言方法源码文本存在——**死代码被测试固化**。
- 修复：接入独立 1-2s 心跳定时器调用 `_poll_auto_quant_orders`；或流失败时自动暂停 Paper 会话并提示。

## H-2 引擎自发停机不传导 workflow → "僵尸 RUNNING"【B】【A】
- 位置：引擎所有 `active=False` 路径（`auto_quant.py:508-532` BUY 被撤、552-553 非整股、558-560 方向不符、567-570 超买、622-629 超卖、1075-1090 提交异常、1175-1178 跨交易日）都不会触发 coordinator `_halt()`（`paper_session.py:483-487` 是唯一停机通道）；且 coordinator 在引擎 inactive 后不再调 `on_stream`（370-373）。
- 场景：BUY 超时被正常取消（416-445 是设计内流程）→ broker 回报 Cancelled → 引擎停机 → 券商侧干净（取消成功、无持仓）→ 健康评估永远 HEALTHY → 会话以"RUNNING + 引擎已死"无限悬挂；**已持仓时止损/移动止损/时段平仓全部不再运行**。
- 修复：引擎停机通过 snapshot 显式字段（如 `halted_reason`）传导给 coordinator `_halt()`；或 `_result()` 检测"engine inactive 且 phase ∈ RUNNING/PAUSED"→ 自动停机。

## H-3 STOPPING 期反复触发全量券商对账刷新 + 误 HALTED 路径【B】
- 位置：`desktop.py:5221-5226`（每次 `_apply_paper_workflow_result` 且 STOPPING 未 finalized 且非 inflight → 启动最终化任务）→ 5239-5247（任务内 `refresh_reconciliation_snapshot` = 5 段阻塞刷新，`ibkr_paper_orders.py:1350-1363`，每段等超时）→ 5256-5259（`_start_task` 返回 False → `fail_finalization_refresh()` → **STOPPING→HALTED**）。
- 场景：平仓期间引擎仍 active → 证据取不到 → 任务结束 → 下个 tick 又启动新任务 → **每隔几秒一次全量券商刷新**直到持仓清空；若期间任一其他 broker 组任务在跑（`desktop_tasks.py:38-51` 按组互斥），`_start_task` 失败 → **误 HALTED**，强制人工对账。
- 修复：最终化刷新加最小间隔/退避 + "引擎仍 active 时短路不刷新"；`_start_task` 失败不立即停机，改重试队列。

## H-4 对账/恢复路径跨线程变更控制器与协调器，无锁【A】【B】
- 位置：`desktop.py:5239-5247`/`5368-5372`（TaskThread 后台线程直接调 `paper_workflow.capture_finalization_evidence/confirm_finalization_after_disconnect/complete_manual_reconciliation`）与 UI 线程 `on_stream`/`set_entries_paused`/`request_stop`（7856-7867 等）并发改写同一 `PaperWorkflowController` 及 coordinator 的 `_engine_snapshot`；`auto_quant.py:543-545` 的 `_seen_executions` 是 check-then-act。
- 现状缓解：`_paper_finalization_inflight` + phase 守卫 + 资源组串行使窗口很窄；但**无任何锁/内存屏障**，正确性依赖事件循环次序与 GIL；两线程同时 `on_execution` 可重复记账或丢更新（最终多半被 digest 变化拉回 HALTED，但账本已污染）。
- 修复：coordinator 加锁或状态变更收敛到 UI 线程（worker 只做 I/O 回传证据对象）；引擎 `on_execution` 的 check-then-act 加锁。

## H-5 closeEvent 在 UI 线程执行 Paper 停机与券商断开【C】
- 位置：`desktop.py:8483`（同步生成平仓意图并 submit）、8493（`disconnect()` → `ibkr_paper_orders.py:1788-1806` 含 `thread.join(timeout=3)`）；4839（拒收路径同样 join）。
- 修复：断开收敛到后台任务；closeEvent 只请求 + 异步确认（与 CR-2 一并处理）。

## H-6 恢复窗口可提交订单：submit 不检查对账快照完整性【A】
- 位置：`ibkr_paper_orders.py:1604-1655`（submit 校验连接/武装/路由，但不校验 `_snapshot_complete`/`_reconciliation_snapshot_complete`）；`desktop.py:5368-5372` 重连任务只收集证据不恢复交易（UI 已防），但**协调器恢复后（`confirm_reconciliation`）引擎立即可再次入场**。
- 影响：对账刷新未完成期间理论上可提交新单（当前 UI 流程下窗口很小，属纵深缺口）。
- 修复：submit 增加 `_snapshot_complete` 门（fail-closed）。

## H-7 持仓不一致被降级 WAITING 期间可继续交易（有界 fail-open）【A】
- 位置：`paper_execution_health.py:121-141`——`position_mismatch` 在"有在途单/未对账/15 秒内有订单事件"时 severity=waiting。
- 说明：这是为异步成交回报设计的宽容窗口，方向上有意为之；但与 CR-1 叠加时（重复 SELL 制造持续 mismatch + 持续 pending），"继续交易"窗口被拉长。建议将 waiting 窗口绑定到"仅本会话在途单"并加总时长上限（如 60s）。

## H-8 成交早于意图注册被 `_seen_executions` 静默丢弃【A】
- 位置：`auto_quant.py:543-548`——先 `_seen_executions.add(execution_id)` 再查 intent；若 intent 未注册（时序窗口），成交被永久丢弃且无告警 → 本地无持仓而券商有 → 后续 mismatch 停机。
- 当前单线程流程下窗口极小（submit 与 intent 注册同事件循环轮次），但 H-4 的多线程 drain 会放大；修复：intent 未知时不移除 seen 并记录告警事件。

## H-9 撤单×成交竞态：error 202 覆盖真实 Filled【A】
- 位置：`ibkr_paper_orders.py:1808-1827`——已知订单的 error 202 一律记为 `Cancelled`（filled=0）经 `_record_order_status` 入账；若订单实际已成交、撤单确认迟到，引擎看到"Cancelled + filled=0 ≠ 已执行数量"→ 误判停机（`auto_quant.py:1099-1120`）。
- 影响：方向安全（停机）但**误停机率高**，增加人工对账次数；修复：202 处理前查 journal 已执行数量，若 >0 则按实际数量记为 cancelled-partial 而非覆盖。

## H-10 账户级日亏/回撤熔断配置未生效【B】
- 位置：`desktop.py:5106-5108` 传入 `LayeredRiskLimits(account=...)`，但 `auto_quant.py:921-957` 只消费 `max_position_exposure_pct`/`max_gross_exposure_pct`；`risk.py:138-146` 的 `daily_loss_halt_pct`/`drawdown_halt_pct` 与 `PreTradeRiskEngine`（risk.py:82-235）**无处调用**；引擎日亏检查（`auto_quant.py:813-826`）只比较**已实现**盈亏、用本地估值而非券商净值。
- 影响：配置的账户级熔断形同虚设；大浮亏日仍可开新仓（已实现未破线时）。
- 修复：`_evaluate_entry` 增加账户级日亏/回撤门（基于 estimated_equity vs initial_cash），或 submit 前接 `PreTradeRiskEngine`。

## H-11 风控退出单用 mid 定价，可能高于 bid 永不成交 → 90 秒干预停机【B】
- 位置：`auto_quant.py:1009-1034`（`_check_exit` 用 `mark`=mid 触发）→ 1063-1066（`_limit_price(mid, slippage, buy=False)` 只从 mid 减滑点，结果**高于 bid**）；对比停止/强平路径用 `quote.bid`（448-455、461-468）；`paper_session.py:447-461` 对 90s 未成交 SELL 直接停机。
- 场景：宽点差下止损/止盈/移动止损挂出高于盘口的限价 → 不成交 → 90s 后整会话停机人工介入；**止损保护形同虚设**。
- 修复：风控退出统一用 `min(mid, bid)` 定价。

## H-12 测试质量：桌面控制器测试全部是源码字符串 meta-test【C】【D】
- 位置：`tests/test_desktop_paper_controller.py`（10 个测试全部 `inspect.getsource` + 子串/顺序断言，控制器从不实例化）；`test_desktop_unified_workflow.py:64/94` 同样断言源码文本。
- 后果：H-1 类"方法存在但未接线"缺陷全部通过；行为级回归漏网。修复：桩依赖 + 真实状态转换断言（offscreen Qt 基础设施已具备）。

## H-13 测试质量：OMS 部分成交路径零覆盖【D】
- `tests/test_oms.py` 仅 2 测；`PARTIALLY_FILLED` 全套件缺席；无部分成交→终态、成交回调迟到、journal 重开/损坏测试。`record_intent→placeOrder` 崩溃窗口（CR-4 关联）与"幽灵意图"（见下）无测试。

## H-14 幽灵意图：`record_intent` 先落库、`placeOrder` 后发，崩溃产生永久非终态意图【A】
- 位置：`ibkr_paper_orders.py:1695-1707`（同一锁内先 journal 后 placeOrder）；崩溃于两者之间 → journal 有意图、券商无单、无终态更新 → 对账行"等待券商首次订单状态"永远 unreconciled → 恢复证据要求 HEALTHY（`paper_session.py:616-617`）永远不满足 → RECONCILING_READY 不可达 → **会话永久卡死（含 closeEvent）**，只能手工改库。
- 修复：崩溃恢复路径增加"意图在 journal 但券商无此单"的显式对账分支（重挂或作废并留审计）。

## H-15 测试质量：`test_market_data.py` 的不可变测试是墙钟时间炸弹【D】
- `tests/test_market_data.py:91-122` fixture 用 2026-01 固定日期，而加载按真实墙钟过滤（`market_data.py:399-402`）；美东日期 ≤2026-01-20 时全量 bar 被滤 → 测试挂。且未来 bar 排除逻辑本身无测试。修复：注入 clock 或相对日期。

---

# Medium

- **M-1 执行租约纯内存、无持久化、无陈旧租约检测**【B】：崩溃后重启只能靠 arm 时券商空仓/无开放单检查兜底；旧会话 journal 残留行永久无人对账（审计空洞）。建议租约/phase 持久化 + 启动残留检测。
- **M-2 AutoLaunchPlan 不含账户指纹**【B】：arm 绑定的是连接时账户而非用户确认时账户（`auto_launch.py:10-18`）；只读连接与订单连接看到不同账户时，预检与武装对象可能不一致。建议确认时指纹入 plan，arm 强制比对。
- **M-3 `resubmit_pending_intent` 死代码且不撤原单**【B】【我】：`auto_quant.py:726-747` 只替换本地 pending，从不调用 `cancel_intent`、从不提交新单；一旦接线 UI 即双成交/幽灵挂单。删除或实现完整路径。
- **M-4 对账证据 30 秒过期 vs 人工确认对话框**【我】：`paper_session.py:591`；用户确认停留 >30s → 证据过期 → 必须重做整轮对账。建议对话框显示有效期/自动重取证。
- **M-5 500ms 定时器内 SQLite 写入 + 多表全量重建**【C】：`desktop.py:7874-7914`/`5500-5509`/`7854`；节流 + `ImmutableRowsTableModel.set_rows` 一次性 reset。
- **M-6 导出/设置保存在 UI 线程同步写盘**【C】：`desktop.py:8954-9007`/`9058-9103`；移入 `_start_task`。
- **M-7 `_refresh_runtime_events` 每次事件全量重建 500 行**【C】：节流合并刷新。
- **M-8 停止/重连窗口 UI 状态漂移**【C】：超时分支按钮状态未收敛；统一 STOPPING 态 + `_stream_finished` 解锁。
- **M-9 滚动保护只覆盖滑块拖拽**【C】：滚轮 + 排序态行序跳动；`valueChanged` 防抖。
- **M-10 测试质量：review 手工盖章 / cross_sectional 同义反复 / paper_workflow 假端口+私有注入**【D】：`test_targeted_review.py:67-135`（replace 全过证据）、`test_cross_sectional_fixed.py:57`（常量与自己比较，核心模拟零覆盖）、`test_paper_workflow.py:65-120`（调用日志+`_health_evaluator` 私有注入）。

---

# Low / 观察项

- **L-1 假期表缺半日**【B】：`extended_hours.py:180-196` 无感恩节次日 13:00 提前收盘；黑五 13:00–16:00 被当 REGULAR 允许下单（docstring 已声明以 IBKR 为准，本地门偏宽）。
- **L-2 券商快照 30s 陈旧即停机**【我】：`paper_execution_health.py:72-79`；安静时段（隔夜无推送）可能误 HALT；建议无在途活动时提示性降级。
- **L-3 `_stream_failed` 透传上游异常原文**【C】：`desktop.py:586-588`；建议过 redaction 脱敏。
- **L-4 内存线性增长**【B】：`_intents/_seen_executions/_terminal_updates`（`auto_quant.py:286/315/317`）只增不减，超长会话内存增长。
- **L-5 分钟门先置位后求值**【B】：`auto_quant.py:474-486` 求值异常会静默跳过该分钟。
- **L-6 `exit_order_intervention_seconds` 靠 getattr 默认值**【B】：`paper_session.py:452-453`；ShadowConfig 无该字段，建议显式声明。
- **L-7 行情中断即丢预热**【B】：缺口 >60s 清空历史（`auto_quant.py:1131-1136`）；恢复后需重新预热。
- **L-8 订单客户端 ID 上限碰撞**【我】：`desktop.py:4974-4976` `min(999_999, prefs+100)`。
- **L-9 connect 共享单一截止时间**【我】：`ibkr_paper_orders.py:1156-1227` 8 个顺序等待同一 deadline；慢机虚假超时（fail-closed，可重试）。
- **L-10 关闭无强制退出出口（设计取舍）**【C】：HALTED 未对账无法退出；建议显式放弃路径。
- **L-11 死迁移/死代码**【B】：`workflow_state.py:102/108/116/132` 与 `paper_workflow.py:222-227` 快速 FINALIZED 分支当前不可达（无害但增审查面）。
- **L-12 凭据明文驻留内存**【C】：DPAPI 解密后普通字符串无法抹除（Python 普遍限制）；UI 侧已扎实。
- **L-13 原子写耐久性不一致**【D】：`market_data.py:510-520`/`credential_store.py:39-49`/`scanner.py:312-322` 只 flush 无 fsync（断电耐久缺口，非原子性缺口）。
- **L-14 CSV 公式注入未防前导空白**【D】：`export_service.py:463-468` `_csv_safe` 未防 `" =cmd"`；导出测试跑空数据。
- **L-15 `placeOrder`/`record_intent` 持 `_correlation_lock` 做网络写与 journal IO**【A】：`ibkr_paper_orders.py:1689-1712`；回调线程的 `error()`/`cancel_intent` 同样取该锁（881-882、1751），高频下单时可能短暂饿死回调处理（placeOrder 本身快，风险低）。
- **L-16 恢复期 `orderStatus` 先于 `openOrder` 到达被丢弃**【A】：`ibkr_paper_orders.py:1893-1896` intent 未知直接 return；映射恢复依赖 openOrder/completedOrder 先到（IBKR 通常如此但无顺序保证）。
- **L-17 同 intent_id 重复 submit 不重验参数**【A】：`ibkr_paper_orders.py:1622-1627` 命中缓存即返回旧 order_id；若调用方以同 intent_id 不同参数重提，静默沿用旧单。建议重提时比对参数一致。
- **L-18 `_account`/`_connected` 等基础字段部分读写无锁**【A】：`ibkr_paper_orders.py:862/1140/1248` 区域，靠 GIL 保证原子性。
- **L-19 无 schema 迁移机制**【A】：`ibkr_paper_orders.py:681-737` 仅 CREATE TABLE IF NOT EXISTS；未来表结构变更无平滑升级路径。
- **L-20 HALTED 后事件队列无界增长**【A】：HALT 后 UI 不再调 `on_stream`/`poll`（`desktop.py:7856-7867` phase 守卫），`_updates`/`_executions` deque 持续累积直到会话清理——内存泄漏窗口。
- **L-21 `audit_rows(limit=1000)` 截断审计**【A】：超千条审计仅导出前 1000。
- **L-22 隐形开放单永久阻塞 arm（fail-closed）**【A】：非本服务创建的开放单（如 TWS 手工单）导致 arm 永远拒绝（`ibkr_paper_orders.py:1556-1563`），需人工清场；方向安全但无豁免/提示入口。

---

# 确认做得扎实的部分（对抗性验证未突破）

1. **提交路径三重幂等**（`ibkr_paper_orders.py:1622-1635`）：内存 `_order_by_intent` + journal `idempotency_key` + 锁内双检；`record_intent` 先落盘再 placeOrder（代价见 H-14）。
2. **账户绑定不可变**：武装指纹（`_account_fingerprint`）+ 每次 submit/对账前校验；disconnect 不解除绑定（只有 disarm）。
3. **可卖数量由本会话成交构建**（1944-1963，锁内）：初始为空 → 无法卖出会话外持仓；SELL 校验硬性防超卖。
4. **对账判定严谨**：终态+数量一致才算 reconciled（483-564）；Filled 数量≠订单数量永不 reconciled → 阻断最终化；`execution_id` UNIQUE 去重。
5. **两段式最终化证明**：零态取证（本地+券商+对账全零且快照完整且 HEALTHY）→ 断开 join → 排空重算摘要比对（`paper_session.py:304-338`）；证据绑定 runtime/service/session/digest 且一次性消费。
6. **互斥执行租约**（`workflow_state.py:60-96`）：释放必须 finalized；桌面双重守卫（4918-4928、8236-8244）。
7. **状态机迁移白名单 + 人工对账显式门**（`workflow_state.py:99-174`）：HALTED→RECONCILING 与 RECONCILING_READY→RUNNING 必须 `explicit_reconciliation=True`；对账证据"再次刷新并比对"是真实的两遍取证（`paper_session.py:201-254`，逐字段比对指纹/代次/双摘要 + 30s 过期 + 单次消费）。
8. **连接边界硬锁**：4002/非只读/能力开关/回环 + 唯一 DU 账户校验（847-864）+ 代次丢弃过期回调。
9. **引擎成交防护**：非整股/方向不符/超量/无持仓卖出 → 立即停机（549-570、622-629）；跨交易日残留 → 停机（1168-1187）。
10. **扩展时段路由**：节假日+周末+维护窗+OVERNIGHT 直连（`extended_hours.py:39-153`）；SPY/QQQ 状态门 entries fail-closed、exits 不受门控。
11. **数据库一致性**：每操作独立连接 + WAL + 30s busy_timeout；journal append-only。
12. **关闭约束**：closeEvent 拒绝未 finalized（8466-8491）；"取证→断开→确认"顺序有测试固化。
13. **行情快照线程安全**：三个流服务 RLock 不可变快照 + generation 丢弃过期回调。
14. **凭据与脱敏**：DPAPI 当前用户绑定 + 原子替换写盘；导出过 redaction；运行时事件入库即脱敏。
15. **研究证据链**：targeted_review 22 项硬门含跨 run 身份交叉绑定、PBO≤0.50、DSR≥0.95、HAC≥0.95、未触碰测试集（`targeted_review.py:88-130`）；反 look-ahead 有真实测试（T 日收盘不影响排名、测试集收益不改选择）。
16. **测试纪律**：全套件仅 1 处 skip、无 xfail、无 mock 被测模块自身、写路径 TemporaryDirectory 隔离；scanner/shadow_paper/targeted_*/workflow_state 等测试期望值可独立推导（STRONG 名单，见 D 报告）。

---

# 修复优先级路线图

| 优先级 | 条目 | 工作量 |
|---|---|---|
| **P0（下个迭代，先堵下单与停机闭环）** | CR-1 停止分支 SELL 幂等守卫；H-1 `_poll_auto_quant_orders` 接入 1-2s 心跳；H-2 引擎停机传导 workflow；H-3 STOPPING 刷新退避 | 小 |
| **P1（稳定性）** | CR-2/CR-3/H-5 关闭与启动/断开移出 UI 线程；H-4 控制器加锁；H-11 风控退出 bid 定价；H-10 账户级熔断生效 | 中 |
| **P2（数据可信）** | CR-5 读路径哈希校验 + run_hash 读取校验 + registry 篡改检测 + PIT 真实证据；H-14 幽灵意图对账分支；CR-4 订单号不回退 | 中 |
| **P3（体验与告警）** | H-6/H-7/H-8/H-9 收紧窗口与告警；M-4 证据过期自动重取证；M-5/M-6/M-7 UI 节流；M-1 租约持久化；M-2 计划含账户指纹；L-1 半日假期 | 中 |
| **P4（测试补强）** | H-12/H-13/H-15/M-10：行为测试替代源码断言；OMS 部分成交全链路；横截面端到端；review 管线联通；注入 clock 的日期测试 | 大 |

---

# 修复记录（2026-08-10 实施）

对抗性审查后实施的修复与优化，全部带行为/回归测试，`pytest -q` 全量回归通过。

## P0 流程闭环修复

| 审查条目 | 修复 | 文件 |
|---|---|---|
| CR-1 停止路径重复 SELL 风暴 | 停止分支按 symbol 检查在途 SELL，每个持仓只挂一张平仓单；部分成交/终态后会按剩余量重新挂单 | `auto_quant.py` on_stream 停止分支；测试 `test_stop_emits_one_sell_per_position_across_many_ticks` |
| H-2 引擎停机不传导 workflow（僵尸 RUNNING） | coordinator 新增 `_require_engine_active`：引擎 inactive 且未 stop_requested → `PAPER_ENGINE_STOPPED` 停机（HALTED 人工对账）；显式停止/协调器 halt（stop_requested=True）不触发 | `paper_session.py` poll/on_stream；测试 `test_poll_halts_when_engine_stopped_itself_without_stop_request` / `test_poll_does_not_halt_when_stop_was_requested` |
| H-1 订单看门狗死代码 | `paper_order_timer` 1s 心跳接入 `_poll_auto_quant_orders`；流 tick 驱动看门狗后 1.2s 内心跳跳过（避免重复），行情流崩溃后心跳独立接管（超时撤单/SELL 干预/健康评估不再依赖流存活） | `desktop.py` MainWindow 初始化 + `_poll_auto_quant_orders`；测试 `test_paper_order_watchdog_heartbeat_is_wired` |
| H-3 STOPPING 刷新风暴 + 误 HALTED | 新增 `_schedule_paper_finalization_refresh`：引擎仍在平仓时 5s 退避；`_start_task` 因 broker 资源组忙失败时**静默延后重试**（不再 `fail_finalization_refresh` → 误 HALTED）；`_start_task` 新增 `suppress_busy_message` | `desktop.py`；测试 `test_finalization_deferral_replaces_halt_on_busy_resource` |

## P1 下单安全修复

| 审查条目 | 修复 | 文件 |
|---|---|---|
| CR-4 nextValidId 订单号回退 | journal 新增 `max_broker_order_id()`；connect 时计算 floor，`nextValidId` 取 `max(服务端ID, floor)` 单调推进，Gateway 重启永不复用旧 ID | `ibkr_paper_orders.py`；测试 `test_journal_max_broker_order_id_floor` |
| H-6 恢复窗口可下单 | submit 增加 `_snapshot_complete` 门：连接快照未完成一律拒绝提交 | `ibkr_paper_orders.py`；测试 `test_submit_rejects_incomplete_connection_snapshot` |
| H-9 error202 覆盖真实成交 | 202（撤单确认）竞态成交时按 journal 实际执行数量记录 filled/remaining，不再以 0 覆盖 | `ibkr_paper_orders.py` `_record_error`；测试 `test_error_202_preserves_executed_quantity` |
| H-10 账户级熔断未生效 | `_evaluate_entry` 增加账户级日亏（相对会话起点估计权益）与回撤（相对峰值权益）熔断门；引擎跟踪 `_peak_equity` | `auto_quant.py`；测试 `test_account_daily_loss_halts_entries` / `test_account_drawdown_halts_entries_after_peak` |
| H-11 风控退出 mid 定价永不成交 | 止损/止盈/移动止损/持有时间退出统一以 `quote.bid` 定价（限价 ≤ bid 可成交），不再从 mid 推导 | `auto_quant.py` `_check_exit`；测试 `test_risk_exit_limit_prices_off_the_bid` |
| L-8 订单连接 client_id 碰撞 | 订单通道 client_id 改为 `(只读id + 100) % 1_000_000`，任意配置值下都与只读连接不同且不越界 | `desktop.py`（通道检查 + 启动两处） |

## 行情/UI 与数据可信优化

| 审查条目 | 修复 | 文件 |
|---|---|---|
| M-5 候选表每 tick 全量重建 | 静态列（代码/名称/行业/层级/评分/信号）仅在候选集变化时重建，realtime 列单独更新 | `desktop.py` `_populate_auto_quant_candidates` |
| M-7 事件表每次事件全量重建 | `_record_runtime_event` 1s 合并刷新（`_schedule_runtime_events_refresh` + singleShot 兜底） | `desktop.py` |
| CR-5 数据完整性（部分） | normalized 文件写入端新增自引用 `normalized_sha256`（对 canonical 序列化）；读取端存在该字段时强制校验，篡改即拒绝加载；旧文件无字段按 legacy 兼容读取 | `market_data.py` save/load；测试 `test_normalized_tampering_fails_integrity_check` / `test_normalized_without_self_hash_loads_as_legacy` |

## 未实施（需更大工程，列入后续）

- CR-5 其余部分：`run_hash` 读取校验、strategy_registry 读时重算 parameter_hash + gate_passed 审计链、PIT 真实成员证据（替换信任标志及其放水测试）
- H-14 幽灵意图（record_intent 与 placeOrder 之间进程崩溃）的对账分类入口
- H-4 跨线程控制器加锁（当前靠 `_paper_finalization_inflight` + phase 守卫，窗口已收窄）
- CR-2/CR-3/CR-5 closeEvent 异步化、启动加载移出 UI 线程、`_probe_gateway` 短超时
- M-1 执行租约持久化 / M-2 计划含账户指纹 / M-4 证据过期自动重取证

## 行情获取修复（2026-08-10，Finnhub）

实测确认根因：本机到 `ws.finnhub.io` 的 TLS 握手被网络中间设备中断（`SSLEOFError: UNEXPECTED_EOF_WHILE_READING`，~5s），属网络层干扰而非代码逻辑；但旧代码的错误信息只有异常类名（`"Finnhub: SSLEOFError"`）、token 无效时无限重连、事件日志按代次记录导致重连刷屏。修复（`finnhub_stream.py` + `desktop.py`）：

| 问题 | 修复 |
|---|---|
| 错误信息不可诊断 | `classify_connect_error` 分类：HTTP 401/403（Key 无效/未授权，fatal）、超时（提示检查网络/代理）、TLS/传输中断（提示 HTTPS_PROXY 或换源）、generic（保留异常详情，截断 200 字符） |
| token 无效无限重连 | 新增 `FinnhubRejectedError`：401/403 或 WS 返回 token/unauthorized 类 error 消息 → 停止重连，经 `failed` 信号在 UI 明确报错 |
| open_timeout 硬编码 10s | 新增 `open_timeout_seconds` 参数（默认 15s） |
| 无代理支持 | `connect(..., proxy=proxy_from_environment())`：读取 `HTTPS_PROXY`/`https_proxy` 环境变量（websockets>=15 原生支持） |
| WS error 消息无详情 | `process_message` 的 error 分支带出 `msg` 详情；含 token/unauthorized/api key 关键字 → fatal |
| 重连错误事件刷屏 | `desktop.py` 事件 key 由 `(generation, code)` 改为 `(provider, code)`，恢复后重置，同类错误每次故障只记一条 |

测试：`tests/test_finnhub_stream.py` +5（401 fatal、超时分类、TLS 中断、generic、EOF、proxy 读取、auth 消息 raise、9102 记录）。

**用户侧行动建议**（代码无法替代）：设置 `HTTPS_PROXY` 环境变量（如 Clash `http://127.0.0.1:7890`）后重启客户端；或改用 Alpaca IEX / IBKR 行情源。

## Clash 代理接入（2026-08-10）

外部行情源默认走本机 Clash 代理 **`http://127.0.0.1:7897`**（新增 `proxy_support.py`）：

- `resolve_proxy()`：`HTTPS_PROXY`/`https_proxy` 环境变量优先，否则内置默认 7897；
- `proxy_unreachable()`：代理本身不可达（ConnectionRefused/ECONNREFUSED 等）→ 本次流生命周期自动回退直连并提示，避免对死代理无限重试；
- `finnhub_stream.py` / `alpaca_stream.py` 的 `connect(..., proxy=self._active_proxy)`；
- Alpaca 同时补齐错误详情（原只有异常类名）。
- IBKR 不经过代理（本机 Gateway 直连）。

实测验证（本机 7897 Clash 运行中）：直连 `ws.finnhub.io` 被 TLS 中断；经 7897 代理 8.5s 收到服务器 HTTP 401 响应，`classify_connect_error` 正确报"API Key 无效"。测试：`tests/test_proxy_support.py`（6 例）+ `test_finnhub_stream.py` 代理用例更新。

# 附录

- 复现脚本（本会话，未入项目）：`repro_stop_duplicate_sell.py` —— 真实 `AutoQuantEngine`：1 次 stop + 10 tick → 10 张重复 SELL（CR-1 证据）。
- 基线命令：`<项目根>\.\.venv313\Scripts\python.exe -m pytest -q` → 281 passed + 9 subtests (34.53s)。
- 审查为只读：未修改任何生产代码；新增文件仅本报告与复现脚本。
- 版本说明：子代理 A/B/C/D 的完整报告均已整合；A 的 M2/M5 与 Low/观察补充项（L-15~L-22）于最终版本并入。
