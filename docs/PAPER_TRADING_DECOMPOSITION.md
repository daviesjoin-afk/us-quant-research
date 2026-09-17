# Paper 交易应用边界（第三步：PaperTradingFacade）

审计对象：`src/us_quant/desktop.py`（本阶段开始时 9,626 行）。

本阶段**不重写** `IBKRPaperOrderService`，不拆 `ibkr_paper_orders.py`，只做一件事：
把 `MainWindow` → Paper execution 的**直接调用边界收窄**，为下一阶段提取
`PaperTradingService` 做准备。`src/us_quant/paper_trading_facade.py` 是**过渡边界**，
不是最终架构。

审计方式：AST 遍历 `desktop.py`，把每一处 `self.paper_order_service` /
`self.paper_workflow` 触点映射到其所属 `MainWindow` 方法（`_audit_touchpoints.py`
可复现）。本阶段开始时共 **61 处**触点：`paper_order_service` 17 处、
`paper_workflow` 44 处。

## 1. Desktop 中的 Paper 入口（真实扫描结果）

风险等级只用于**技术重构风险**，与交易风险大小无关。

| Desktop 方法 | 调用对象 | 动作 | 是否改变订单 | 是否影响状态机 | 风险等级 |
| --- | --- | --- | --- | --- | --- |
| `__init__` | `paper_order_service` / `paper_workflow` | 声明槽位 + 绑定 controller | 否 | 否 | LOW |
| `_check_auto_order_channel` | `paper_order_service` | 判断订单通道是否被占用 | 否 | 否 | LOW |
| `_reset_auto_launch_controls` | `paper_order_service` | 决定是否恢复启动按钮 | 否 | 否 | LOW |
| `_worker_finished` / `_task_failed` | `paper_order_service` | 同上（按钮态） | 否 | 否 | LOW |
| `_populate_auto_quant_snapshot` | `paper_order_service` | `broker_state()` 只读渲染 | 否 | 否 | LOW |
| `_populate_auto_latency_table` | `paper_order_service` | `reconciliation_rows_with_latency()` 只读 | 否 | 否 | LOW |
| `_apply_paper_workflow_result` | `paper_workflow` | 读 `phase` 渲染按钮态 | 否 | 否 | LOW |
| `_apply_paper_workflow_button_state` | `paper_workflow` | 读 `phase` + `reconciliation_evidence` | 否 | 否 | LOW |
| `_paper_needs_manual_recovery` | `paper_workflow` | 读 `phase` 判断是否只能人工离开 | 否 | 否 | LOW |
| `_auto_candidate_preparation_failed` | `paper_workflow` | 读 `phase` → `cancel_preparing()` | 否 | 是（PREPARING→IDLE） | MEDIUM |
| `_select_auto_quant_candidates` | `paper_workflow` | 读 `phase` → `cancel_preparing()` / `mark_ready()` | 否 | 是 | MEDIUM |
| `_poll_auto_quant_orders` | `paper_workflow` | 读 `phase` 门控 → `poll()` | 否 | 是（health→HALTED） | MEDIUM |
| `_stream_snapshot_received` | `paper_workflow` | 读 `phase` 门控 → `on_stream()` | 否 | 是（health→HALTED） | MEDIUM |
| `_reconnect_auto_order_service` | `paper_order_service` + `paper_workflow` | `connection_snapshot().connected` + `begin_manual_reconciliation()` | 否 | 是（HALTED→RECONCILING） | **HIGH** |
| `_auto_order_reconciliation_failed` | `paper_workflow` | `fail_manual_reconciliation()` | 否 | 是（→HALTED） | **HIGH** |
| `_resume_auto_quant_from_reconciliation` | `paper_workflow` | 读 evidence → `confirm_manual_resume()` | 否 | 是（RECONCILING_READY→RUNNING） | **HIGH** |
| `_start_paper_finalization_refresh` | `paper_order_service` + `paper_workflow` | `capture_finalization_evidence()` → `service.disconnect()` → `confirm_finalization_after_disconnect()` | 否 | 是（STOPPING→FINALIZED/HALTED） | **HIGH** |
| `_finish_auto_quant_session_if_safe` | `paper_order_service` + `paper_workflow` | `broker_state()` + 对账行 → `disconnect()` → `finalize_if_safe()` | 否 | 是（释放 PAPER lease） | **HIGH** |
| `_prepare_auto_quant_candidates` | `paper_workflow` | `begin_preparing()` / `cancel_preparing()` | 否 | 是 | MEDIUM |
| `_start_auto_quant` | `paper_workflow` | `begin_connecting(plan)` / `reject_connecting(plan)` | 否 | 是（取 PAPER lease） | **CRITICAL** |
| `_auto_order_service_connected` | `paper_order_service` + `paper_workflow` | `publish_armed()` + `self.paper_order_service = service` | **否**（提交发生在引擎侧） | 是（CONNECTING→RUNNING） | **CRITICAL** |
| `_reject_unpublished_auto_service` / `_reject_auto_launch_without_service` | `paper_workflow` | `reject_connecting(plan)` | 否 | 是（释放未武装 lease） | **CRITICAL** |
| `_pause_auto_quant_entries` / `_resume_auto_quant_entries` / `_stop_auto_quant` | `paper_workflow` | `set_entries_paused()` / `request_stop()` | 否 | 是（RUNNING↔PAUSED→STOPPING） | **CRITICAL** |
| `closeEvent` | `paper_order_service` + `paper_workflow` | 读 finalized/phase 门控 → `disconnect()` | 否 | 否 | **HIGH** |
| `_paper_finalization_failed` | `paper_workflow` | `fail_finalization_refresh()` | 否 | 是（STOPPING→HALTED） | **HIGH** |

`submit` / `cancel` / `ExecutionLease` 在本阶段审计中**没有**出现在 `desktop.py`
的直接调用里：下单走 `AutoQuantEngine` → `PaperOrderPort`，撤单走引擎
`cancel_intent`，`ExecutionLease` 由 `PaperWorkflowController` 内部持有。桌面侧
只读状态与触发控制器方法，因此这些 CRITICAL 路径在本阶段既未迁移、也未新增包装。

## 2. 现有 Paper 状态机（以代码为准）

真身是 `src/us_quant/workflow_state.py` 的 `PaperWorkflowPhase`，共 **11 个相位**：
`IDLE`、`PREPARING`、`READY`、`CONNECTING`、`RUNNING`、`PAUSED`、`STOPPING`、
`HALTED`、`RECONCILING`、`RECONCILING_READY`、`FINALIZED`。
（本阶段要求里列的 8 个是示例；实际多出 `PREPARING`/`READY`/`CONNECTING`。）

合法迁移由 `_PAPER_TRANSITIONS` 白名单 + `validate_paper_transition()` 钉死：

```text
IDLE              → PREPARING
PREPARING         → READY | IDLE | HALTED
READY             → PREPARING | CONNECTING | IDLE | HALTED
CONNECTING        → READY | RUNNING | HALTED
RUNNING           → PAUSED | STOPPING | HALTED
PAUSED            → RUNNING | STOPPING | HALTED
STOPPING          → FINALIZED | HALTED
HALTED            → （白名单为空，只能走显式人工对账）
RECONCILING       → RECONCILING_READY | FINALIZED | HALTED
RECONCILING_READY → FINALIZED | HALTED
FINALIZED         → IDLE
```

两条**只能由人工确认**打开的边（`explicit_reconciliation=True` 才放行）：

```text
HALTED            → RECONCILING        （begin_manual_reconciliation）
RECONCILING_READY → RUNNING            （confirm_manual_resume）
```

| 迁移 | 触发者 | 允许者 | 要求人工确认 | 允许自动 |
| --- | --- | --- | --- | --- |
| `IDLE→PREPARING` | `_prepare_auto_quant_candidates` | `_PAPER_TRANSITIONS` | 否 | 是 |
| `PREPARING→READY` | `_select_auto_quant_candidates`（`mark_ready`） | 同上 | 否 | 是 |
| `PREPARING→IDLE` | `cancel_preparing` | 同上 | 否 | 是 |
| `READY→CONNECTING` | `_start_auto_quant`（`begin_connecting`） | 同上 + 先取 PAPER lease | 操作员已勾选武装确认 | 否 |
| `CONNECTING→RUNNING` | `_auto_order_service_connected`（`publish_armed`） | 同一 `plan` 才放行（stale 回调返回 False） | 否 | 是 |
| `CONNECTING→READY` | `reject_connecting` | 同上 | 否 | 是 |
| `RUNNING↔PAUSED` | `_pause/_resume_auto_quant_entries` | `_PAPER_TRANSITIONS` | 否 | 是 |
| `RUNNING/PAUSED→STOPPING` | `_stop_auto_quant` / `closeEvent` | 同上 | 否 | 是 |
| `STOPPING→FINALIZED` | `finalize_if_safe`（需 `result.state.finalized`） | 同上 + 释放 PAPER lease | 否 | 是 |
| `任意→HALTED` | `_apply_runtime_result`（health 不安全）/ `fail_finalization_refresh` / `fail_manual_reconciliation` | 同上 | 否 | 是（粘性，不可自动离开） |
| `HALTED→RECONCILING` | `_reconnect_auto_order_service`（`begin_manual_reconciliation`） | **仅显式人工对账** | **是** | 否 |
| `RECONCILING→RECONCILING_READY` | `complete_manual_reconciliation`（一次性 evidence） | 同上 | 否（但需同一 attempt_id） | 是 |
| `RECONCILING_READY→RUNNING` | `_resume_auto_quant_from_reconciliation`（`confirm_manual_resume`） | **仅显式人工确认** | **是** | 否 |
| `FINALIZED→IDLE` | `begin_preparing` | 同上 | 否 | 是 |

关键链**未被本阶段改动**，且不可自动跨越：

```text
HALTED
  ↓  begin_manual_reconciliation（人工点击，attempt_id 一次性）
RECONCILING
  ↓  complete_manual_reconciliation（一次性 evidence，过期即失效）
RECONCILING_READY
  ↓  confirm_manual_resume（人工确认，evidence_id 必须匹配）
RUNNING
```

`reconciliation_evidence` 在 `confirm_manual_resume` 里**先原子消费再调用**第二次
broker 刷新，因此重复点击不可能恢复引擎两次。

## 3. 本阶段新增：`PaperTradingFacade`

`src/us_quant/paper_trading_facade.py`，**199 行**（含 docstring 与 Protocol 声明；非空非注释 140 行），
零 PySide6 / 零 desktop / 零 QThread / 零 QWidget 依赖，不自己实现任何订单逻辑，
只代理现有安全组件。

构造用 **getter**，不持有对象：

```python
self.paper_trading = PaperTradingFacade(
    workflow_getter=lambda: self.paper_workflow,
    order_service_getter=lambda: self.paper_order_service,
)
```

getter 是必须的，不是风格选择：`MainWindow` 仍然创建、武装、清空
`IBKRPaperOrderService`，而既有安全测试通过替换 `window.paper_workflow` 来驱动
HALTED / 拒绝关闭路径。getter 保证 Facade 永远作用在**当前**那个对象上，绝不会
对一个已被替换或已丢弃的 service 下手，Facade 也不会成为第二个 owner。

暴露的 API（第一版只有只读 + 生命周期）：

```text
phase()                       → PaperWorkflowPhase（真值来自 controller）
is_finalized()                → bool（真值来自 controller.result.state.finalized）
has_order_service()           → bool
is_connected()                → bool（service.connection_snapshot().connected）
reconciliation_status()       → PaperReconciliationStatus(awaiting_confirmation)
snapshot()                    → PaperTradingSnapshot（frozen, slots）
broker_state()                → service.broker_state() | None
reconciliation_rows_with_latency() → service 的只读行 | ()
disconnect()                  → 现有 disconnect 语义，失败记录 + 原样重抛
```

`PaperTradingSnapshot` 只有四个字段，描述 **Paper application lifecycle**：
`phase: str`、`connected: bool`、`finalized: bool`、`last_error: str | None`。
**不复制** positions / orders / fills / reconciliation details —— 订单真值仍由原有
service 与 journal 负责。

`is_finalized()` 对「尚未开始任何会话」返回 `True`（没有未完成的会话），这正是
close 门控的语义：只在存在未 finalized 会话时拒绝关闭。

`disconnect()` 只做现有语义：不自动 submit、不自动 cancel、不自动 reconciliation、
不自动 resume、不自动 acquire `ExecutionLease`。失败时写入 `last_error` 并**原样
重抛**，不静默吞掉；`closeEvent` 原有的异常处理保持原样。

### 3.1 `snapshot()` 的成本

`snapshot()` 里的 `is_connected()` 会向 order service 请求连接事实
（`connection_snapshot()`），不是廉价属性读取，因此**不放在逐 tick 的渲染路径**上。
本阶段迁移的渲染路径读的是 `phase()`（纯属性）与 service 的只读查询，
`snapshot()` 供状态查询与测试使用。

## 4. 已迁移到 Facade 的调用（24 处）

`desktop.py` 中 `self.paper_order_service` / `self.paper_workflow` 的直接触点由
**61 → 39**（`paper_order_service` 17 → 8，`paper_workflow` 44 → 31）。

| 方法 | 迁移前 | 迁移后 |
| --- | --- | --- |
| `_check_auto_order_channel` | `self.paper_order_service is not None` | `self.paper_trading.has_order_service()` |
| `_reset_auto_launch_controls` | `... is None and ...` | `not self.paper_trading.has_order_service() and ...` |
| `_worker_finished` | `... is None` | `not self.paper_trading.has_order_service()` |
| `_task_failed` | `... is None` | `not self.paper_trading.has_order_service()` |
| `_populate_auto_quant_snapshot` | `self.paper_order_service.broker_state() if ... is not None else None` | `self.paper_trading.broker_state()` |
| `_populate_auto_latency_table` | `if ... is not None: reconciliations = (...)` | `reconciliations = self.paper_trading.reconciliation_rows_with_latency(...)` |
| `_auto_candidate_preparation_failed` | `self.paper_workflow.phase is ...` | `self.paper_trading.phase() is ...` |
| `_select_auto_quant_candidates`（4 处 phase 读） | `self.paper_workflow.phase` | `self.paper_trading.phase()` |
| `_apply_paper_workflow_result` | `phase = self.paper_workflow.phase` | `phase = self.paper_trading.phase()` |
| `_apply_paper_workflow_button_state` | `phase` + `reconciliation_evidence` | `phase()` + `reconciliation_status().awaiting_confirmation` |
| `_resume_auto_quant_from_reconciliation`（门控读） | `self.paper_workflow.phase` | `self.paper_trading.phase()` |
| `_poll_auto_quant_orders`（门控读） | `self.paper_workflow.phase` | `self.paper_trading.phase()` |
| `_stream_snapshot_received`（门控读） | `self.paper_workflow.phase` | `self.paper_trading.phase()` |
| `_paper_needs_manual_recovery` | `self.paper_workflow.phase` | `self.paper_trading.phase()` |
| `closeEvent`（门控） | `paper_result = ...result; if ... is not None and not ...finalized` | `if not self.paper_trading.is_finalized()` |
| `closeEvent`（disconnect） | `if ... is not None: ...disconnect(); ... = None` | `if self.paper_trading.has_order_service(): self.paper_trading.disconnect(); ... = None` |

**行为逐字保持**：`has_order_service()` 与原来的 `is not None` 同义；
`phase()` 直接返回 controller 的相位，没有第二份 phase；`is_finalized()` 复用的
是 `controller.result.state.finalized`，不是自己推测；`broker_state()` 与
`reconciliation_rows_with_latency()` 的 `None` / 空元组语义与原内联判断一致。

## 5. NOT MIGRATED IN STEP 3（37 处，全部留在原位）

以下调用**有意**不迁移，且全部属于文档明确标记的高风险路径：

| 方法 | 保留的直接调用 | 风险 | 原因 |
| --- | --- | --- | --- |
| `_start_auto_quant` | `begin_connecting(plan)`、`reject_connecting(plan)` | CRITICAL | 取 PAPER `ExecutionLease` 的唯一入口 |
| `_auto_order_service_connected` | `publish_armed(...)`、`self.paper_order_service = service` | CRITICAL | 武装发布 + service ownership 变更 |
| `_reject_unpublished_auto_service` / `_reject_auto_launch_without_service` | `reject_connecting(plan)` | CRITICAL | 释放未武装 lease |
| `_pause_auto_quant_entries` / `_resume_auto_quant_entries` | `set_entries_paused(...)` | CRITICAL | 交易闸门 |
| `_stop_auto_quant` | `request_stop(...)` | CRITICAL | 有序停机 |
| `_reconnect_auto_order_service` | `begin_manual_reconciliation()`、`complete_manual_reconciliation()`、`fail_manual_reconciliation()` | HIGH | 人工对账执行 |
| `_auto_order_reconciliation_failed` | `fail_manual_reconciliation(attempt_id)` | HIGH | HALTED 恢复 |
| `_resume_auto_quant_from_reconciliation` | `confirm_manual_resume(evidence_id)` | HIGH | 人工确认恢复 |
| `_start_paper_finalization_refresh` | `capture_finalization_evidence()`、`confirm_finalization_after_disconnect()` | HIGH | 零状态证明与释放时序 |
| `_paper_finalization_failed` | `fail_finalization_refresh()` | HIGH | 自动失败路由 |
| `_finish_auto_quant_session_if_safe` | `finalize_if_safe()`、`self.paper_order_service = None` | HIGH | 零状态终局化 |
| `closeEvent`（phase 集合判断） | `self.paper_trading.phase() in {RUNNING, PAUSED}` | HIGH | 与停机顺序耦合 |
| `__init__` | 两个槽位声明 | — | ownership 仍属 `MainWindow` |

本阶段**未**新增 `submit_order()` / `cancel_order()` / `replace_order()` /
`place_order()`，也**未**包装 `IBKRPaperOrderService.submit...`。

## 6. 验收标准对照

| 验收项 | 结果 |
| --- | --- |
| `desktop.py` 中 Paper 直接调用数量 | **61 → 39**（service 17 → 8，workflow 44 → 31） |
| 已迁移方法列表 | 见 §4（24 处调用点，全部 LOW/MEDIUM） |
| 未迁移 HIGH/CRITICAL 列表 | 见 §5（37 处，全部标记 `NOT MIGRATED IN STEP 3`） |
| `PaperTradingFacade` 行数 | **199 行**（非空非注释 140 行） |
| pytest 数量 | 433 → **466 passed**（新增 33：facade 24 + desktop 接线 9） |
| CI Python 3.12 / 3.13 | 见 PR 检查结果 |

`doctor` 保持：`environment = paper`、`live_trading_enabled = false`、
`whole_shares_only = true`、`allow_margin_borrowing = false`、`ibkr.port = 4002`。

## 7. 与既有安全回归的关系

以下既有测试全部继续通过，数量未减少：HALTED recovery、RECONCILING、
RECONCILING_READY、manual confirmation、closeEvent drain、`ExecutionLease`
互斥、Paper/Shadow 互斥、whole shares、no margin borrowing、Live disabled。

`tests/test_desktop_paper_controller.py` 中钉住源码字面量的断言（例如
`closeEvent` 里 disconnect 必须在 finalized 判断之后）随迁移同步更新为
`self.paper_trading.disconnect()`，断言**仍然**保证「未 finalized 时不得断开」，
并新增结构测试禁止 Facade 出现任何真实交易入口。
