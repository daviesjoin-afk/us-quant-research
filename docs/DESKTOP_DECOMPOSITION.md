# Desktop 运行时职责分解（第一步：RuntimeSupervisor）

审计对象：`src/us_quant/desktop.py`（审计前 9,624 行 / 371,474 字节）。

本次只做第一步：把**通用 runtime 生命周期**从 `MainWindow` 里抽出来交给
`src/us_quant/runtime_supervisor.py`。策略、IBKR Paper 下单核心、MarketDataService、
工作流状态机一律不动。下面每一行都对应当前代码里的真实位置。

## 1. 责任矩阵

| Current responsibility | Current owner | Target owner | Risk | Related tests |
| --- | --- | --- | --- | --- |
| `paper_order_timer`（1s 心跳，`_build_ui` 内 `start()`） | `MainWindow` | `RuntimeSupervisor`（`paper_order_heartbeat`, order=10） | **高**：审计前全文件无 `.stop()`，只在 `QTimer(self)` 上依赖父对象销毁 | `tests/test_runtime_supervisor.py` |
| `extended_session_timer`（15s 心跳，`_build_ui` 内 `start()`） | `MainWindow` | `RuntimeSupervisor`（`extended_session_heartbeat`, order=20） | **高**：同上，且回调 `_maybe_rotate_extended_ibkr_session` 会发起行情源切换 | `tests/test_runtime_supervisor.py` |
| `stream_timer`（500ms 轮询，`_start_stream` 内 `start()`） | `MainWindow` | `RuntimeSupervisor`（`stream_snapshot_timer`, order=30） | 中：有 `.stop()`，但分散在 3 处（`_stop_stream`、`_stream_finished`、停止失败分支） | `tests/test_runtime_supervisor.py` |
| `StreamWorker`（QThread，行情网络线程） | `MainWindow`（`self.stream_worker`） | `RuntimeSupervisor` 只负责**释放**，创建/重连仍在 `MainWindow` | 中：`_stop_stream` 内含交易安全门（Paper 有持仓时拒绝停止） | `tests/test_runtime_supervisor.py`、`tests/test_ibkr_stream.py`、`tests/test_alpaca_stream.py` |
| `TaskThread` 集合（`self.workers`） | `DesktopTaskController` | `RuntimeSupervisor` 只负责 stop/join；准入仍在 `DesktopTaskController` | 中：`TaskThread` 无通用取消钩子，每个 task 自带 `Event` | `tests/test_desktop_tasks.py` |
| `universe_refresh_cancel_event`（`Event`） | `MainWindow` | `MainWindow`（唯一可取消的长网络任务） | 低：已有 `_cancel_universe_refresh` 与 `_reset_universe_refresh_controls` 成对管理 | `tests/test_desktop_tasks.py` |
| 关闭准入闸门 `_closing` | 无（本次新增） | `RuntimeSupervisor`（`closing_gate`, order=5） | 低：`_start_task` 新任务入口 | `tests/test_runtime_supervisor.py` |
| `IBKRPaperOrderService` 连接与 `disconnect()` | `MainWindow` | **不迁移** | **高**：`disconnect()` 涉及 DU 账户绑定语义（`disarm` 才能解绑） | `tests/test_ibkr_paper_orders.py` |
| Paper 下单 / 撤单 / 对账 | `WorkflowController` + `MainWindow` | **不迁移** | **高**：交易语义 | `tests/test_paper_workflow.py`、`tests/test_workflow_controller.py` |
| `ExecutionLease` | `WorkflowController` | **不迁移** | **高**：交易语义 | `tests/test_workflow_controller.py` |
| HALTED / RECONCILING 状态机 | `WorkflowController` | **不迁移** | **高**：交易语义 | `tests/test_workflow_state.py` |
| 分层风控 `LayeredRiskLimits` | `MainWindow` + 引擎 | **不迁移** | **高**：交易语义 | `tests/test_risk.py` |
| Shadow 引擎 / 影子工作流 | `MainWindow` | **不迁移** | 中：`closeEvent` 里必须先于行情停止 | `tests/test_shadow_paper.py` |

## 2. 谁创建、谁启动、谁停止

**MainWindow 创建**：全部 `QTimer`（3 个）、`StreamWorker`、`TaskThread`、
`DesktopTaskController`、`WorkflowController`（内含 paper/shadow 两个工作流）、
`IBKRPaperOrderService`（延迟到启动会话时）、`ShadowPaperEngine`、`AutoQuantEngine`。

**MainWindow 启动**：
- `_build_ui()` 内启动 `paper_order_timer`、`extended_session_timer`（**无对应 stop**）
- `_start_stream()` 内 `worker.start()` + `stream_timer.start()`
- `_start_task()` 内 `worker.start()`（所有研究/数据后台任务）
- `_start_auto_quant()` 链路里启动引擎与 `IBKRPaperOrderService.connect()`

**MainWindow 停止**（审计前）：
- `_stop_stream()` → `request_stop()` + `wait(3000)` + `stream_timer.stop()`
- `_stream_finished()` → `stream_timer.stop()`（信号回调路径）
- `_stop_auto_market_data()`、`_stop_auto_quant()`、`_stop_shadow()` → 交易相关
- `closeEvent` → `paper_order_service.disconnect()`、`_stop_stream()`
- **`paper_order_timer` / `extended_session_timer` 全文件无任何 `.stop()`**

## 3. 审计发现：退出时可能没有 join / close 的资源

1. **`paper_order_timer`（1 秒周期）** — 审计前从未被停止。`closeEvent` 会
   `event.ignore()` 并要求用户「稍后再次关闭」，期间该计时器仍在跑
   `_poll_auto_quant_orders`。它内部有 phase 守卫，因此不会提交订单，但会在
   关闭流程中继续驱动 Paper 工作流轮询。已纳入 supervisor（order=10，在
   准入闸门之后、行情释放之前停止）。
2. **`extended_session_timer`（15 秒周期）** — 同样从未停止。其回调
   `_maybe_rotate_extended_ibkr_session` 在美东时段切换时会调用
   `_request_stream_switch`，即**在关闭过程中仍可能发起一次行情源切换**。
   已纳入 supervisor（order=20）。
3. **`StreamWorker` 停止失败时无重试** — `_stop_stream()` 在 `wait(3000)` 超时后
   返回 `False`，`closeEvent` 随后 `event.ignore()`。原来没有任何机制保证下一次
   关闭会再次尝试；现已由 supervisor 的「失败即重试」语义覆盖。
4. **`TaskThread` 无通用 join** — `closeEvent` 在有任何 worker 运行时直接
   `event.ignore()` 并弹窗，不做 join。这是**有意的安全设计**（避免半写入产物），
   本次保留该行为，只在 supervisor 里补一条 `wait(3000)` 兜底路径。

### 3.1 已逐项核查、确认 desktop.py 中不存在的类别

需求清单里的下面这些类别在 `desktop.py` 中**没有**对应实现。记录在此是为了把
「已检查且不存在」与「漏检」区分开——否则后来者无法判断审计是否覆盖过：

| 类别 | `desktop.py` 中的情况 |
| --- | --- |
| `Lock` / `RLock` | 无。锁只在更底层：`ibkr_paper_orders.py`（`_id_lock`/`_state_lock`/`_event_lock`/`_correlation_lock`/`_refresh_lock`）、`ibkr_stream.py`（`_lock`）。这些属于交易/连接层，本次不迁移。 |
| executor | 无。`ThreadPoolExecutor` 只出现在 `finnhub_stream.py` 与 `public_history.py` 的 `with` 块内（作用域受限、自带 join），不是常驻资源。 |
| daemon thread | `desktop.py` 中无。仅存在于 `ibkr.py`、`ibkr_history.py`、`ibkr_paper_orders.py`、`ibkr_readonly.py`、`finnhub_stream.py`。 |
| `asyncio` / `async def` | 全 `src/` 无。项目是纯线程模型。 |
| websocket 客户端 | `desktop.py` 中无；行情走 `ibkr_stream.py` / `alpaca_stream.py` / `finnhub_stream.py` 的线程封装。 |
| 原始 `threading.Thread` | `desktop.py` 中无。后台任务统一经 `TaskThread`（`QThread` 子类）。 |
| `reconnect` | 存在但**已由任务系统承载**：`_reconnect_auto_order_service` 走 `_start_task`，属于交易相关重连，本次不迁移。 |

## 4. 记录在案、本次不修的高风险问题

以下问题已定位但不属于第一步范围，按需求要求只记录：

1. **`closeEvent` 的双重会话检查与 `_stop_stream` 的守卫重复** —
   `closeEvent` 先查 `paper_workflow.result` 是否 finalized，`_stop_stream` 内又查
   `auto_quant_snapshot.active/positions/pending_orders`。两处判断依据不同
   （工作流结果 vs 快照状态），存在短暂窗口两者不一致。属交易安全逻辑，不动。
2. **`_stop_stream` 混用返回值与弹窗表达失败** — 返回 `False` 表示「拒绝停止」，
   同时内部又弹 `QMessageBox`。作为 supervisor 的 `stop` 可调用对象时，返回
   `False` 不会被误判为失败（只有 `join` 的返回值才是裁决），语义已对齐，但该
   函数同时承担「用户交互 + 状态迁移 + 返回值协议」三种职责，是后续拆分的候选。
3. **`_stop_shadow` 在 `_stop_stream` 内部被调用** — 行情停止会级联停止影子盘，
   而影子盘又依赖行情数据。级联方向正确，但调用关系是隐式的（读 `_stop_stream`
   才能发现）。建议后续显式化为 supervisor 的注册顺序，本次未改。
4. **`IBKRPaperOrderService.disconnect()` 的账户绑定语义** — 注释明确写「只有
   `disarm` 才清除绑定」。`closeEvent` 里直接 `disconnect()` 并置
   `self.paper_order_service = None`，丢失了后续对账所需的引用。属交易安全逻辑。
5. **`QTimer.singleShot` 未跟踪** — `_schedule_runtime_events_refresh` 使用
   `QTimer.singleShot(1000, ...)`，关闭瞬间若有 pending 回调会尝试刷新已销毁的
   表格。Qt 的父子对象机制通常能兜住，但这是审计中唯一未纳入 supervisor 的
   定时器（单次、无句柄、无法 stop）。记录待后续处理。

## 5. RuntimeSupervisor 边界

`src/us_quant/runtime_supervisor.py` 不导入 PySide6、不导入任何 broker 客户端、
不做 I/O。它接收**可调用对象**而不是接口实现，因为 desktop 手上已经是
`QTimer` / `QThread` / service 对象，为它们套适配器只增加代码不增加安全性。

语义要点：

- 注册顺序即关闭顺序，可用 `order=` 解耦；关闭始终按 `order` 升序。
- **没有 `stop`/`join` 的资源也能注册**，使「这个资源没有 owner」这件事
  *可观察*，而不是静默消失。
- `shutdown()` 可重复调用：**干净释放过的跳过，失败过的重试**。后者是关键——
  `closeEvent` 在行情线程仍存活时会 `event.ignore()` 并依赖下一次关闭取得进展，
  若把「已关闭」缓存下来，程序将永远无法退出。
- 单个组件的 `stop`/`join` 抛异常不会跳过其余组件，错误记录在
  `snapshot().components[].last_error` 与 `errors()` 中，不吞异常。
- 探针（`is_running`）存在时以探针为准；探针抛异常视为 failed，并且
  **fail closed**（仍然尝试释放），因为「问不出来」不等于「不需要释放」。

## 6. 迁移后 desktop.py 的关闭顺序

`closeEvent` 分两个阶段。第一阶段在**任何其他检查之前**执行，这是修复的核心：
原先「先查后台任务 → 有则 ignore 并 return」，导致任务运行期间 `_closing` 不被
置位、新任务仍可被准入、可取消任务收不到停止请求，`background_workers` 实际上
从未参与「存在运行任务时」的关闭流程。

### 阶段一：`runtime_supervisor.begin_shutdown()`（每次关闭请求都执行）

1. `closing_gate`（order=5，`drain=True`）→ `_closing = True`，此后 `_start_task()`
   一律拒绝新任务。
2. `background_workers`（order=200，`drain=True`）→ `_request_worker_stops()`：
   设置 `universe_refresh_cancel_event`。**只发请求，不 join、不 terminate。**
3. 有后台任务运行 → `event.ignore()` + 弹窗，等待任务自行安全结束。
4. Paper 会话未 finalized → `event.ignore()` + 弹窗；若是 RUNNING/PAUSED 会先
   `_stop_auto_quant()`（原有行为，未改）。

阶段一**不释放任何资源**：定时器、行情线程、Paper/broker 连接全部保持原状，
正在写入的数据不受干扰。

### 阶段二：仅当上述检查全部通过后

5. `paper_order_service.disconnect()`（原有行为，未改）
6. Shadow 引擎/工作流停止（原有行为，未改）
7. `runtime_supervisor.shutdown()` —— 内部顺序：
   `closing_gate`(5) → `paper_order_heartbeat`(10) → `extended_session_heartbeat`(20)
   → `stream_snapshot_timer`(30) → `market_data_stream`(100)
   → `background_workers`(200)
8. 行情线程仍存活 → `event.ignore()` + 弹窗（原有行为，未改）
9. `event.accept()`

交易安全判断全部在第 5–6 步之前完成，supervisor 的完整 `shutdown()` 只在会话已
finalized 之后运行，**不改变任何交易语义**。

### 6.1 闸门的唯一例外：`shutdown_essential`

`_start_task(..., shutdown_essential=True)` 是关闭期间唯一能被准入的任务。
原因是一个真实的死锁：Paper 的零状态收尾证明
（`_start_paper_finalization_refresh`）本身就是通过
`_start_task(resource_group="broker")` 启动的，而它正是让会话进入 `finalized`
的那一步。如果闸门无条件关闭，证明永远无法启动 → 会话永远无法 finalized →
窗口永远关不掉。该参数只在这一处使用，并由
`tests/test_desktop_runtime_teardown.py::test_a_non_essential_task_is_still_refused_once_closing`
锁定其窄度（其余任何资源组仍被拒绝）。
