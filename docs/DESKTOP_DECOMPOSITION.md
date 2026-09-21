# Desktop 运行时职责分解（第一步：RuntimeSupervisor；第二步：MarketDataService）

> **历史记录。** 本文件描述 0.20.0 之前的十五步桌面分解过程，保留为历史过程
> 记录，不再往里追加未来总架构。其中第二步建立的
> `src/us_quant/market_data_service.py` 已在 **Market Data v2** 轮次被删除，
> 其职责迁至 `trading/application/market_data.py` +
> `trading/composition/market_data.py` + `trading/adapters/*/market_data.py`。
> 当前架构见 `docs/TRADING_ARCHITECTURE_V2.md`。下文提到
> `MarketDataService` / `StreamSnapshot` 之处均指当时的实现，不再对应当前代码。

审计对象：`src/us_quant/desktop.py`（审计前 9,624 行 / 371,474 字节）。

第一步：把**通用 runtime 生命周期**从 `MainWindow` 里抽出来交给
`src/us_quant/runtime_supervisor.py`。第二步：把**行情 provider 的选择与构造**
抽出来交给 `src/us_quant/market_data_service.py`（见 §7）。策略、IBKR Paper 下单
核心、reconciliation、工作流状态机一律不动。下面每一行都对应当前代码里的真实位置。

## 1. 责任矩阵

| Current responsibility | Current owner | Target owner | Risk | Related tests |
| --- | --- | --- | --- | --- |
| `paper_order_timer`（1s 心跳，`_build_ui` 内 `start()`） | `MainWindow` | `RuntimeSupervisor`（`paper_order_heartbeat`, order=10） | **高**：审计前全文件无 `.stop()`，只在 `QTimer(self)` 上依赖父对象销毁 | `tests/test_runtime_supervisor.py` |
| `extended_session_timer`（15s 心跳，`_build_ui` 内 `start()`） | `MainWindow` | `RuntimeSupervisor`（`extended_session_heartbeat`, order=20） | **高**：同上，且回调 `_maybe_rotate_extended_ibkr_session` 会发起行情源切换 | `tests/test_runtime_supervisor.py` |
| `stream_timer`（500ms 轮询，`_start_stream` 内 `start()`） | `MainWindow` | `RuntimeSupervisor`（`stream_snapshot_timer`, order=30） | 中：有 `.stop()`，但分散在 3 处（`_stop_stream`、`_stream_finished`、停止失败分支） | `tests/test_runtime_supervisor.py` |
| `StreamWorker`（QThread，行情网络线程） | `MainWindow`（`self.stream_worker`） | `RuntimeSupervisor` 只负责**释放**，创建/重连仍在 `MainWindow`；**provider 构造**已移出，见 §7 | 中：`_stop_stream` 内含交易安全门（Paper 有持仓时拒绝停止） | `tests/test_runtime_supervisor.py`、`tests/test_desktop_market_data_wiring.py`、`tests/test_ibkr_stream.py`、`tests/test_alpaca_stream.py` |
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
8. 行情线程仍存活（`worker_running`，非 `is_live`）→ `event.ignore()` + 弹窗
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

### 6.2 被拒绝的关闭必须能撤销：`cancel_shutdown()`

阶段一有一个后果必须显式处理：**一次被 `event.ignore()` 拒绝的关闭，不是中止的
关闭**。用户被要求先完成手工对账，然后继续使用客户端，但闸门已经抬起 —— 而
HALTED/RECONCILING* 的唯一出口（对账、重新确认、最终收尾）全都是走
`_start_task()` 的任务。闸门留在原处，就会锁死用户被要求执行的那一步：
停在那里、无法对账、无法 finalize、无法退出。

因此 supervisor 提供第二阶段入口的**逆操作**：

- `RuntimeSupervisor.cancel_shutdown()` —— 仅恢复准入。它不启动任何东西、不重建
  线程、不碰券商、不做 I/O。drain 组件的 `stop` 是取消请求，撤销它纯粹是记账。
- 一旦**释放**阶段已经开始（`shutdown()` 或 `stop()` 被调用过），`cancel_shutdown()`
  抛出 `RuntimeError`，闸门保持关闭并记录日志：一个已半释放的运行时绝不能对外
  表现成「可用」。这就是 `_release_entered` 标志存在的唯一原因。

`desktop.py` 侧对应 `_cancel_close_drain()`，只在 `_paper_needs_manual_recovery()`
为真时由 `_release_close_drain_if_recovery_required()` 触发。

**挂钩点是单一汇聚处。** 所有进入 HALTED/RECONCILING* 的路径（共 7 处调用）都
经过 `_apply_paper_workflow_button_state()`，所以恢复钩子放在那里，而不是在每个
调用点重复。这不是风格问题：**自动**路径 `RUNNING → STOPPING → 收尾证明失败 →
HALTED` 在关闭那一刻的相位上是**看不出来的**（当时还是 RUNNING/PAUSED），只在
证明失败之后才出现。只在 `closeEvent` 里检查相位，会漏掉这条路径，于是用户看到
「请手工对账」的提示，而准入闸门正卡着对账任务本身。

**范围界定。** `RUNNING`/`PAUSED` 被 `_paper_needs_manual_recovery()` 明确排除：
关闭这两个相位仍有自动出路（`request_stop` → `STOPPING` → 零状态证明），所以
证明运行期间闸门应当保持关闭。只有那三个「没有自动出口」的相位才触发撤销。

## 7. 第二步：`MarketDataService`（provider 边界）

### 7.1 被移出的知识

`StreamWorker.__init__` 原来按 `provider` 分支构造三种 adapter，于是 UI 层知道：
每种 provider 的构造签名、凭据参数名、stale 超时、IBKR 的 extended-hours venue、
coverage 文案、provider label。这些都不是展示决策，现已全部移入
`src/us_quant/market_data_service.py`（零 PySide6/QThread/QWidget/MainWindow 依赖）。

分工：

```text
StreamWorker        = Qt 线程适配器（只负责线程 + 信号）
MarketDataService   = provider 选择 / 构造 / 生命周期（应用服务）
ibkr|alpaca|finnhub = adapter（WebSocket 实现一行未改）
```

`MainWindow` 只负责读 combobox、symbol 输入与凭据存储，然后构造
`MarketDataRequest`；它不再 import 或实例化任何 provider 类。仍需要
`StreamSnapshot` / `MARKET_DATA_TYPE_NAMES`（UI 展示用）的 import 保留不动。

### 7.2 fail closed

provider 采用显式白名单 `ibkr` / `ibkr_extended` / `alpaca_iex` / `finnhub_trades`；
其他值一律 `raise ValueError`，**没有 catch-all 分支**。原来的 `else:
IBKRReadOnlyStream(...)` 会把 typo 静默解释成券商连接。构造失败时不保存任何
半初始化 stream，`snapshot()` 也不会报告 `running`。

### 7.3 行为逐字保持，不「顺便优化」

Alpaca `stale_after_seconds=8` + listener、Finnhub `20` + listener、IBKR
`requested_market_data_type=1` / `stale=8` / `market_exchange`、
extended 的 `provider_label="IBKR 5×24"` 与 coverage 文案，全部与原值逐字一致，
并由 `tests/test_market_data_service.py` 的突变测试锁定。

**IBKR 刻意不给 listener**：它一直是靠 desktop 的 snapshot timer 轮询的，加
listener 会让每笔行情发布两次。

### 7.4 `MarketDataServiceSnapshot` 只描述生命周期

`provider` / `symbols` / `running` / `last_error`。quote 真值仍由
`StreamSnapshot` 独占，service snapshot 不复制任何报价字段。

生命周期只有一条路径，且状态不可造假：

```text
build_stream  →  run  →  stop / 自然结束
```

四个状态分别是 `built`、`running`、`stop_requested`、`finished`，由
`_run_started` / `_stop_requested` / `_finished` 三个标志表示（不额外维护
「当前是否在跑」，它恒等于 `_run_started and not _finished`）。

* `run()` 由 service 提供，`finally` 清除 running，正常返回与异常退出都不会留下
  假 running；异常同时写入 `last_error` 后重新抛出，由 `StreamWorker` 带到 GUI
  线程。`StreamWorker` 是纯 Qt 线程外壳（`self.market_data.run()`），不再直接
  `self.service.run()`。
* **`stop()` 不等于 stream 已结束**。`stop()` 只是请求 adapter 收摊，`run()` 可能
  仍在其内部执行——IBKR socket 循环退出要数秒，阻塞中的网络读则可能更久。因此
  `stop_requested=True` 而 `run()` 尚未返回时，`build_stream()` 与
  `update_config()` 都必须继续拒绝；否则会在旧流仍在跑的情况下建起第二条流、或
  换掉它正在使用的连接配置。放行条件只有两个：`run()` 已返回，或 stream 被 build
  但从未 run 且已被 stop（aborted-before-run，否则该 stream 永远无法结束，service
  会被永久卡死）。
* `stop()` 与 `snapshot()` 都由 `StreamWorker` 真实调用（`request_stop()` 走
  service），不是假接口。
* 已有未结束的 stream 时再次 `build_stream()` 抛 `MarketDataStreamActive`，
  **不自动 stop、不偷偷替换**：覆盖 `self._stream` 会让旧流脱离生命周期管理，旧流
  继续跑而 service 报告新流。旧流 finished（或 aborted-before-run）之后才能被替换。
* `update_config()` 只在没有活动 stream 时接受，且只影响**未来**的
  `build_stream()`：不做网络操作、不自动 reconnect、不创建 stream。活动 stream
  期间拒绝（fail closed），因为已建立的连接就是按构造时那份配置连的，改了会让
  service 描述的连接与实际打开的连接不一致。

### 7.4.1 配置检查与配置应用分开

`ensure_config_update_allowed(config)` 只检查、不改状态、不做 I/O：配置相同直接放
行，否则在没有活动 stream 时才放行，`update_config()` 是它的薄封装。存在的理由是
设置保存必须在写盘**之前**知道这次变更会不会被接受，同时**不能**在写盘之前就把
runtime 改掉——否则写盘失败会出现「提示未保存、runtime 却已用新 client id」。

`MainWindow._save_user_preferences()` 的顺序因此是：

```text
构造并 validated preferences
  → 生成新的 IBKRConnectionConfig
  → service.ensure_config_update_allowed(new_config)   # 只检查，失败即弹窗返回
  → preferences_store.save(preferences)                 # 提交点：失败则什么都没变
  → _apply_preferences_to_config(saved)                 # 落盘成功后才动 runtime
  → 更新 self.preferences / 主题 / UI
```

`_apply_preferences_to_config()` 在更新 `self.config` 之前先问 service，顺序保证
两者不会互相矛盾。初始化顺序用 `getattr(self, "market_data_service", None)`
兜底。这一段全部在同步 GUI 调用里，因此刻意不引入锁或事务框架。

### 7.5 与 supervisor 的关系

行情停止仍走既有的 `runtime_supervisor`（`market_data_stream`, order=100），
没有第二套 shutdown 管理。第二步只改变「谁来构造 adapter」，关闭顺序、Paper
安全顺序、`begin_shutdown`/`cancel_shutdown` 一律未动。

该组件的存活探针读的是 `market_orchestrator.worker_running`（底层线程是否真的
还在跑），**不是** `is_live`。`is_live` 表达的是「行情是否可用」，stop 一旦
pending 就为 False；若用它做探针，超时未退出的线程会被判成已释放，shutdown
会误报 clean release。`closeEvent` 第 8 步的最终检查同理。

## 8. 第七步：`desktop_workers.py`（Qt worker 边界）

第七步把 `TaskThread` 与 `StreamWorker` 从 `desktop.py` 移到
`src/us_quant/desktop_workers.py`，**逐字搬迁**（搬迁前后文本逐字节相同，用
脚本对 `git show HEAD:src/us_quant/desktop.py` 的原始块做过比对），只新增模块
docstring 与 import 头。两个 class 的真正定义现在只有一份。

### 8.1 为什么这两个类值得单独成模块

它们不是「小工具」，而是**唯一一处后台作业变成 `QThread` 的地方**。把它留在
`desktop.py` 里的代价是：任何想读线程语义的人必须先 import 一个近万行的 GUI
模块；而任何测试想验证线程语义，都会连带拉起整个窗口栈。移出后
`tests/test_desktop_workers.py` 只 import 两个 worker，不再需要 `MainWindow`。

### 8.2 边界形状

```text
MainWindow module (desktop.py)
        ↓
Qt worker adapters (desktop_workers.py)
        ↓
application services (MarketDataService, universe, ...)
```

`desktop.py` 改为 `from us_quant.desktop_workers import (StreamWorker, TaskThread)`，
因此旧路径 `from us_quant.desktop import TaskThread` 仍然有效，且
**`is` 同一对象**（不是 wrapper、不是 subclass）。这一条有专门测试
（`test_old_and_new_import_paths_are_the_same_object`），因为 wrapper 能通过
import 却会破坏 desktop 里所有 `isinstance` 判断。

### 8.3 允许依赖只有 5 个

`typing.Callable`、`PySide6.QtCore.{QThread, Signal}`、
`us_quant.market_data_service.{MarketDataService, MarketDataRequest}`、
`us_quant.universe.UniverseRefreshCancelled`。测试对**允许清单**做了 pin
（集合相等，不只是「不含禁用项」），所以依赖面既不能悄悄扩大也不能意外缩小。

禁止 import 的层：`PySide6.QtWidgets`/`QtGui`、`us_quant.desktop`、
`paper_*`、`workflow_*`、`risk`、`auto_quant`、`ibkr_paper_orders`、
`strategy`。禁止出现的名字：`MainWindow`/`QMainWindow`/`QWidget`/
`QMessageBox`，以及任何具体 provider adapter 名（`IBKRReadOnlyStream`、
`AlpacaIEXStream`、`FinnhubStream`）。

### 8.4 逐字保持的语义

* `TaskThread`：4 个 signal（`succeeded`/`failed`/`cancelled`/`progress`）、
  构造参数 `(task, resource_group="research")`、`self.task`/`self.resource_group`
  字段、`run()` 顺序 `task(self.progress.emit)` →
  `UniverseRefreshCancelled` → `Exception` → `else`。**cancellation 不是 failure**：
  `cancelled` 恰好一次、`succeeded`/`failed` 均为 0。
* 失败消息继续 `" ".join(str(error).split())`，`RuntimeError("hello\n   world")`
  必须得到 `hello world`（测试固化）。
* `StreamWorker.run()` 继续 `try: self.market_data.run() except Exception as error:
  self.failed.emit(f"{type(error).__name__}: {error}")`，即 `RuntimeError: boom`，
  不允许退化成 `boom`、`<class ...>` 或 traceback。lifecycle truth 仍属
  `MarketDataService.run()`；worker 不自管 `finished`、不碰 service 生命周期。
* `request_stop()` 继续只调 `self.market_data.stop()`：不调 `self.service.stop()`、
  不调 `self.quit()`/`self.terminate()`，也没有新增 QThread interruption 或
  asyncio cancellation。
* listener 继续是 `snapshot_ready.emit`（不是 `window._stream_snapshot_received`）：
  stream 线程 emit → Qt queued delivery → GUI 线程。直接调用 window 会跨线程操作 UI。

### 8.5 本步不碰的东西

`RuntimeSupervisor` 完全未动，仍独占 task registration / worker lifetime /
shutdown admission / close drain，两个 worker 没有第二套 lifecycle manager。
`MarketDataService` 的 `build_stream`/`run`/`stop`/`record_failure`/
`update_config`/`ensure_config_update_allowed` 全部冻结（尤其没有顺手去修之前
讨论过的 pre-run stale worker identity race —— 那是独立的生命周期问题）。
Paper 全路径冻结：`_start_auto_quant`、`_auto_order_service_connected`、
`_reconnect_auto_order_service`、`_start_paper_finalization_refresh`、
`_finish_auto_quant_session_if_safe`、`_paper_needs_manual_recovery` 以及
`PaperTradingService`/`PaperWorkflowController`/`IBKRPaperOrderService`/
`ExecutionLease` 一律未改。`MainWindow` 的 `_start_task`/`_worker_finished`/
`_task_failed`/`_start_stream`/`_stop_stream`/snapshot handling/`closeEvent`/
supervisor 接线 0 行为变化，diff 只来自 import 路径。

### 8.6 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9913 行 / 387,438 字节 | 9837 行 / 384,684 字节 |
| `desktop_workers.py` | — | 97 行 / 3,506 字节 |
| `tests/test_desktop_workers.py` | — | 518 行 / 15,026 字节 |
| 全套测试 | 601 passed | 623 passed |

（字节数是 **blob 对 blob**：before 取 `git cat-file -s HEAD:...`，after 把工作树
按 `core.autocrlf` 归一化后 `git hash-object` 再取大小——直接量工作树会因 CRLF
多出约 0.8% 的假增量。附件写的 before 是 9914 行，实测三种口径——`wc -l`、
`splitlines()`、CRLF 计数——都是 9913，按实测记录。）

`desktop.py` 的减少幅度不大（约 0.8%），这不是本步的价值所在：本步建立的是
上面 §8.2 那条边界。widgets（`MetricCard`/`QuoteTableModel`/`PriceChart`/
`EquityComparisonChart`）按计划留到下一步单独做，避免 UI 回归时无法定位。

### 8.7 覆盖强度

`tests/test_desktop_workers.py` 共 **22 tests**，其中 7 条是结构守卫（AST 读源码，
不经 import），15 条是行为断言。

**突变测试 24/24 全杀、0 skip、0 未归因**（harness 在本步结束后删除）。每个突变体
声明「预期失败的测试名集合」，只有失败集合与声明**相交**才算杀死——「被别的守卫
杀掉」不算数，因为那证明不了目标断言真的有效。

两个突变体在第一轮**存活**，暴露的是真实测试缺陷，不是覆盖不足：

* `self.provider = "ibkr"` 硬编码：原断言写成 `worker.provider == request.provider`，
  而 `request.provider` 恰好就是 `"ibkr"`（列表里第一个 provider），硬编码也能过。
  修法：改用 `PROVIDER_FINNHUB_TRADES` 构造请求，两个值无法混淆。
* `request_stop()` 里加 `self.terminate()`：原断言 `worker.isRunning() is False` 在
  从未 `start()` 过的线程上恒真，`terminate()` 是否被调用完全看不出来。修法：
  monkeypatch `QThread.terminate` 记录调用，断言调用列表为空。

**守卫必须不依赖被测模块可导入**：结构守卫按路径读源码（`ast.parse(path.read_text())`），
行为测试用惰性 import（函数内 `import us_quant.desktop_workers`）。原因是——本步的
守卫要防的头号违规就是「`desktop_workers` 反过来 import `desktop`」，一旦发生，顶层
import 会让**整个测试文件 collection 失败**，而 collection error 不报任何测试名，
守卫恰好在它该起作用的那一刻变成不可归因。harness 因此把 collection error 单列为
`unattributed`，既不算杀死也不算存活。

---

## 9. 第八步：`desktop_widgets.py`（presentation 边界）

### 9.1 被移出的知识

`desktop.py` 里混着两种完全不同的东西：窗口的**业务编排**（MainWindow 的
task/stream/paper 处理），和纯粹的**展示层**——把数据变成像素的四个 widget，
以及两个只做文本格式化的 helper。后者与 MainWindow 的生命周期毫无关系，却因为
「一开始就写在一起」而必须跟着 9.9k 行的窗口模块一起被导入、一起被测试。

本步把展示层单独成模块，边界是：**`desktop_widgets.py` 不知道任何应用服务，
只接受数据、产出像素。**

### 9.2 边界形状

```text
src/us_quant/desktop_widgets.py
    _sortable_number(...)      # 文本 → 可排序数值，供 QTableWidgetItem 使用
    _price(...)                # Decimal → 展示文本
    QuoteTableModel            # 实时报价表（StreamSnapshot → 14 列）
    MetricCard                 # 指标卡
    PriceChart                 # 收盘价曲线
    EquityComparisonChart      # 权益对比曲线
```

依赖方向单向：`desktop.py` → `desktop_widgets.py`，反向禁止。旧 import 路径
`from us_quant.desktop import QuoteTableModel` 继续有效，且必须是**同一个对象**
（`is`），因为 `desktop.py` 内部对这几个类有 `isinstance` 判断，wrapper 或
subclass 能通过 import 却会破坏它们。

### 9.3 允许依赖

`desktop_widgets.py` 的 import 面被钉死为：

| 来源 | 名字 |
|---|---|
| `us_quant.ibkr_stream` | `MARKET_DATA_TYPE_NAMES`, `StreamSnapshot` |
| `us_quant.ui_theme` | `theme_palette` |
| stdlib | `datetime.date`, `decimal.Decimal`, `re` |
| Qt | `QtCore` / `QtGui` / `QtWidgets` 的 16 个名字 |

`ibkr_stream` 是允许的，因为 `QuoteTableModel` 的输入就是 `StreamSnapshot`——
它是**数据形状**，不是业务服务。其余业务侧依赖（`paper_*`、`workflow_*`、
`auto_quant`、`risk`、`strategy`、`market_data_service`、`runtime_supervisor`、
`desktop_workers`）全部禁止，`desktop` 自身更是禁止反向 import。

### 9.4 为什么 `QTableWidgetItem` 和 `_money` 留在 `desktop.py`

`QTableWidgetItem` 是 `_QTableWidgetItem` 的子类，被 MainWindow 的各表格广泛使用；
它需要 `_sortable_number`，所以 `desktop.py` 从新模块 import 该 helper。这样
`QTableWidgetItem` 的行为一字未改，也不需要为它设计新的边界。

`_money` 同理留在原地：MainWindow 大量使用它。**本步不做格式化框架**——只搬
已经在别处被独立使用、且搬走后能自洽的两个 helper。

`configure_chinese_font` 也不动：它牵涉 `QFontDatabase` 与 Windows 字体路径，
属于应用启动而不是展示逻辑。

### 9.5 逐字保持的语义

搬迁用 AST span 从原始字节切片，脚本先断言锚点再写盘，因此**不可能**顺手重排
格式或改字。六个块与 `main` 原文逐一比对，**字节完全相同**（LF 归一化后），
比对由一次性脚本完成（附件 §52 要求的正是这种一次性等价检查）：

| 块 | 字节 |
|---|---|
| `_sortable_number` | 418 |
| `_price` | 143 |
| `QuoteTableModel` | 5,846 |
| `MetricCard` | 1,441 |
| `PriceChart` | 3,815 |
| `EquityComparisonChart` | 3,480 |

冻结的行为要点：

* `update_snapshot` 的 reset/incremental 二态：symbol 集合变了才 `beginResetModel`
  全量重建（`reset_count += 1`），集合没变则**保持现有行序**、只对变化的行
  `dataChanged.emit`（`changed_row_count` 累加实际变化数）。不「优化」为每次 reset。
* 排序 key 仍是 `(numeric is None, numeric if numeric is not None else value.casefold())`，
  `Qt.DescendingOrder` 决定 `reverse`——`_sortable_number` 搬走后排序**不得**退化成
  字符串比较。
* `ForegroundRole` 的列集合不变：STALE → 列 0/6/10/12/13 用 `theme.error`，
  READY → 列 0/6/10/12 用 `theme.success`，其余列返回 `None`。
* `MetricCard.set_value(value)` 的 `note=None` 表示**保留旧 note**，不是清空。
* `PriceChart.set_series` 只做 `points[-180:]`，不排序、不归一化日期、不转 dataframe。
* 两个 `paintEvent` 的几何 magic number 与 `QPainter` 原语原样保留，不引入
  matplotlib / pyqtgraph，也不抽象出 chart engine。

### 9.6 本步不碰的东西

`MainWindow` 的任何方法、`desktop_workers.py`、Paper 全栈、`MarketDataService`、
`RuntimeSupervisor` 全部未改。`desktop.py` 的 diff 精确等于「删 6 个定义 + 删 8 个
不再使用的 Qt import + 加 1 条 import」，无其他改动。

### 9.7 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9837 行 / 384,684 字节 | 9384 行 / 369,555 字节 |
| `desktop_widgets.py` | — | 507 行 / 16,392 字节 |
| `tests/test_desktop_widgets.py` | — | 909 行 / 30,774 字节 |
| 全套测试 | 623 passed | 685 passed |

（同样是 blob 对 blob；`desktop.py` 的 `main` 基线取 `git rev-parse main:...`。
`desktop.py` 净减 **453 行**。）

### 9.8 覆盖强度

`tests/test_desktop_widgets.py` 共 **51 个测试函数**（参数化展开后 **62 个用例**），
其中 **12 条是结构守卫**（AST 读源码，不经 import），39 条是行为断言。结构守卫覆盖：
迁移符号各只有一份定义、`desktop.py` 零重复定义、六个符号全仓唯一、
依赖白名单（集合相等）、禁止反向 import `desktop`、禁止线程与业务服务、
禁止白名单外的 Qt 名字、旧 import identity、以及 `desktop.py` 里不得残留
因搬迁而死的 Qt import。

逐字搬迁由**一次性比对脚本**证明（附件 §52 的写法），**不常驻测试**：把原文哈希
冻进测试会让此后任何一次合法改动都被判死，而且它会抢先杀死所有触及已搬迁块的
突变体，把真正的覆盖缺口全部掩盖掉——第八步第一轮突变就是这样丢了 5 个信号。

### 9.9 本步的代价与下一步

`desktop.py` 从 9837 行降到 9384 行（约 4.6%），绝对值仍很大——`MainWindow` 本身
才是主体，而本步刻意没有拆它（附件明确要求「不要继续拆 MainWindow methods」）。
下一步若继续，对象是 `MainWindow` 的方法族（`_build*` / `_apply_theme` /
stream 与 paper 的 handler），那才是真正需要逐段行为冻结的部分。

---

## 10. 第九步：`desktop_settings.py`（设置事务边界）

```text
MainWindow
    ↓ 从控件收集值（只做这一件事）
DesktopSettingsService.commit(...)
    ↓
UserPreferencesStore          MarketData config port
（磁盘）                        （runtime）
```

事务顺序：

```text
validate  →  preflight  →  persist  →  runtime apply
```

### 10.1 被移出的知识

`MainWindow._save_user_preferences()` 原本在一个方法里做了四件互不相干的事：读控件、
校验取值、写偏好文件、把运行中的行情服务搬到新连接参数上。后三件都不是表现层，而且
**它们的前后顺序是安全属性而不是风格选择**，因此全部移入本模块。

同时被删除的重复实现：`MainWindow._ibkr_config_from_preferences()` 与
`MainWindow._apply_preferences_to_config()`。前者与 `__init__` 里手写的一份
`IBKRConnectionConfig(...)` 是同一条映射的两个副本——现在 `__init__` 也改走
`ibkr_config_from_preferences()`，startup config 与 saved config 不会再漂成两套规则。

### 10.2 为什么 preflight 必须在 persist 之前

stream 存活时 `MarketDataService` 拒绝更换连接参数。若先写文件，磁盘上是新 client ID
而 runtime 还是旧值——操作者被告知「已保存」，下一次 stream 却仍用旧参数。

`ensure_config_update_allowed()` 只**检查**：不改状态、不做 I/O。这正是两种失败模式
得以分离的原因——被拒绝的变更什么都没留下。

### 10.3 为什么 persist 必须在 runtime apply 之前

写盘是提交点。若先 apply 再写，写失败时 runtime 已经切换，下一次 stream 会用设置文件
里并不存在的值连接。

### 10.4 本步不做的事

**不新增任何事务框架**：没有回滚、不备份 `preferences.json`、不加文件锁、不做两阶段
提交、不引入 transaction manager。现有顺序已经让两种失败模式各自惰性，为「检查与
应用之间的理论竞态」发明一套机制是另一个 PR 的事。

### 10.5 两个安全层不得互相蕴含

`preferences.paper_order_capability_enabled` 说的是**操作者可以被提供** Paper 下单
控件；它绝不能让 `IBKRConnectionConfig.paper_order_submission_enabled` 变成 True。
desktop 的基础 IBKR config 永远保持 `api_read_only=True` /
`paper_order_submission_enabled=False`，真正的 Paper order service 仍由它自己的
专用路径从自己的 config 创建。

### 10.6 边界形状

`desktop_settings.py` 依赖面用**集合相等**钉死：

```text
dataclasses, typing
us_quant.config, us_quant.ibkr, us_quant.user_settings
```

不 import `MarketDataService` 本身，而是通过 `MarketDataConfigPort(Protocol)` 描述所需
的三个成员（`config` / `ensure_config_update_allowed` / `update_config`）——服务可以
新增方法而不惊动本模块，测试也可以用一个计数器替身替代它。

无 Qt、无 `desktop`、无 credential store、无 Paper 栈、无 provider adapter。
`DesktopSettingsCommit` 是 `frozen=True, slots=True`。

### 10.7 本步不碰的东西

`MarketDataService`（`ensure_config_update_allowed` / `update_config` /
`build_stream` / `run` / `stop` 全部未改）、`user_settings.py`（schema、allowed
providers、host 白名单、端口 4002、原子写全部未改）、credential methods、Settings UI
布局、provider switch、Paper 全栈、`desktop_workers.py`、`desktop_widgets.py`、
`RuntimeSupervisor` 全部未改。`MainWindow` 除设置保存接线外未改，尤其
`_start_stream` / `_stop_stream` / `_stream_finished` / `closeEvent` /
`_start_auto_quant` / `_paper_execution_health_adapter` /
`_finish_auto_quant_session_if_safe` 未触碰。

### 10.8 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9384 行 / 369,555 字节 | 9338 行 / 366,941 字节 |
| `desktop_settings.py` | — | 176 行 / 6,924 字节 |
| `tests/test_desktop_settings.py` | — | 1025 行 / 32,437 字节 |
| 全套测试 | 685 passed | 757 passed |

（同为 LF 归一化后的 blob 口径——与第八步同口径；`desktop.py` 的 before 取
`git show main:src/us_quant/desktop.py`。`desktop.py` 净减 **46 行**。）

### 10.9 覆盖强度

`tests/test_desktop_settings.py` 共 **40 个测试函数**，参数化展开后 **72 个用例**：
服务本身用 fake store + fake market data 直接测，不启动 `QApplication`；末尾的接线
测试才构造 `MainWindow`。其中 **5 个函数是结构守卫**（参数化后 33 个用例），35 个是
行为断言（39 个用例）。

断言的是**调用日志**而不是只断言返回值——先保存后检查的服务也会返回一个看起来正确的
commit。被钉住的顺序为 `["ensure", "save", "update"]`。

结构守卫按 AST 读源码：依赖面集合相等、禁止 import 与禁止提到的符号各自参数化
（`MarketDataService` 也在禁提名单里，因为本模块只应通过 Protocol 与它对话）、
`commit` 的 `current_config` / `market_data` 为 keyword-only 且无默认值、
`_save_user_preferences` 里不得出现三处事务调用、`desktop.py` 全文不得出现
`preferences_store.save(` / `ensure_config_update_allowed(` /
`market_data_service.update_config(`。

### 10.10 突变结果

**28/28 全杀、0 存活、0 skip、0 错误归因、0 未归因。** 第一轮有 1 个存活：
`port=preferences.ibkr_port` → `port=4002`。原因是 `UserPreferences.validated()`
本身就把端口钉成 4002，测试数据里字面量恰好等价——这是**映射函数的真实测试缺口**，
不是覆盖不足。补 `test_the_mapping_copies_every_connection_field_verbatim`（用未校验
的 4003 / client 123456 / timeout 99 直接调映射）后杀死。

harness 另有两处必须处理的坑：① 锚点用 `\n` 写而文件是 CRLF，多行锚点会全部匹配失败
→ 匹配前按文件自身行尾归一化；② 某个突变体会让 `_save_user_preferences` 弹出模态
`QMessageBox`，offscreen 下永久阻塞，导致整个 sweep 被 `TimeoutExpired` 打断 →
逐突变体超时记 `HUNG` 并继续。

## 11. 第十步：`desktop_credentials.py`（凭据边界）

```text
MainWindow
    ↓ 读凭据输入框 / 弹 QMessageBox / 决定控件可见性
DesktopCredentialService
    ↓
WindowsCredentialStore（DPAPI）
```

流启动时的凭据解析：

```text
Stream start
    ↓
DesktopCredentialService.resolve_stream_credentials()
    ↓
environment override
    ↓ fallback
DPAPI
```

### 11.1 被移出的知识

`MainWindow` 原本同时知道三件事：provider → secret 名的映射、`.dpapi` 文件名的拼法、
以及「环境变量优先于 store」这条规则。前两件是持久化细节，第三件是**哪一份凭据被使用**
的规则——三者都不是表现层，因此移入本模块。

`STREAM_CREDENTIAL_SOURCES` 是唯一的一份映射表；`ENVIRONMENT_NAMES` 由它反向推导，
不另写一份，否则两张表会漂移，凭据会从错误的变量里读出来。

### 11.2 为什么 `has_secret()` 必须加在 store 上

`_refresh_credential_status()` 过去直接拼 `root / "finnhub_api_key.dpapi"` 再
`Path.exists()`——UI 知道了磁盘布局。`has_secret()` 把这件事收回 store，并且**复用
`_clean_name()`**（不复制文件名净化逻辑，否则非法名会绕过一个入口）。

它**只表示加密文件是否存在，绝不解密**。这是有意的分歧：损坏的 blob、或属于另一个
Windows 用户的 blob 会让 `load_secret()` 抛错，但「是否已保存」仍然只是「是否存在」——
若让状态显示依赖解密，一行状态就会变成错误。

### 11.3 为什么 active-stream guard 留在 UI

```python
if (
    self.stream_worker is not None
    and self.stream_worker.isRunning()
    and self.stream_worker.provider == provider
):
    # 拒绝清除
```

`QThread.isRunning`、active provider、stream ownership 属于 **UI/runtime 协调**，不属于
凭据存储。时序是「MainWindow 先判断，允许后才调用 `clear_provider()`」。这条规则很容易
被写成过宽的形式（「只要有 stream 在跑就禁止清除任何凭据」），因此两条回归测试分别钉住
「拒绝 active」与「放行 inactive」。

### 11.4 环境变量优先，且逐项独立

每个凭据**各自**解析：环境变量有非空值就不再读对应的 DPAPI blob。只含空白的变量视为
未设置（`FINNHUB_API_KEY="   "` 会回落到 DPAPI），值本身会被 `.strip()`。

`CredentialStoreError` 继续转换成 `ValueError`，并保留 `__cause__`：`_start_stream()`
捕获的是 `ValueError` 而不是 `CredentialStoreError`，异常类型是外部契约的一部分。

### 11.5 Alpaca 半套必须拒绝

UI 已经检查过「Key 与 Secret 必须同时填写」，但 service **自己也必须防御**，否则非 UI
调用者可以写出半套凭据——那会看起来「已配置」，却在 stream 启动时失败。半套输入抛
`ValueError` 且**写入 0 次**，绝不静默只保存一半。

### 11.6 边界形状

依赖面用**集合相等**钉死：

```text
__future__, collections.abc, dataclasses, os, typing
us_quant.credential_store
```

无 Qt、无 `desktop`、无 `market_data_service`、无 provider adapter、无 Paper 栈、无
`MainWindow`。provider id 以字面量固定在本模块内——它需要三个字符串，不需要整个
market-data adapter 依赖图。`CredentialStatus` / `StreamCredentials` 均为
`frozen=True, slots=True`。

`CredentialStatus` 只允许 bool 与 identifier，**不得出现 `api_key` / `api_secret`
这类字段**。`StreamCredentials` 是内部运行时 DTO，必须携带真实 key 才能建立行情请求；
它的安全边界是「不写日志、不写 export、不进 `CredentialStatus`」，本阶段不新造 secret
wrapper 或脱敏框架。

### 11.7 本步不碰的东西

`MarketDataService`（`build_stream` / `run` / `stop` /
`ensure_config_update_allowed` / `update_config` / `listener_for` /
`market_exchange_for` 逐字节未变）、`desktop_settings.py`、`desktop_workers.py`、
`desktop_widgets.py`、`user_settings.py`、`config.py`、`risk.py` 与 Paper 全栈
（`paper_trading_service.py` / `paper_session.py` / `paper_workflow.py` /
`ibkr_paper_orders.py` / `ibkr_paper_gateway.py` / `workflow_state.py`）全部逐字节未变。
`_api_provider_changed` 与 `_set_connection_settings_enabled` 未搬——前者控制
`QLineEdit` 可见性与按钮使能，后者操纵 `QWidget.setEnabled`，都是表现层。

`_clear_saved_finnhub_key` 按规格保留为私有兼容方法（AST 引用扫描确认全仓零调用，但
「顺手大扫除」不在本阶段范围），仅把内部调用改走 `clear_provider("finnhub_trades")`。

### 11.8 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9338 行 / 366,941 字节 | 9321 行 / 366,375 字节 |
| `desktop_credentials.py` | — | 212 行 / 7,436 字节 |
| `tests/test_desktop_credentials.py` | — | 700 行 / 21,063 字节 |
| 全套测试 | 757 passed | 845 passed |

（同为 LF 归一化后的 blob 口径——与第八、九步同口径；`desktop.py` 的 before 取
`git show main:src/us_quant/desktop.py`。`desktop.py` 净减 **17 行**。）

### 11.9 覆盖强度

`tests/test_desktop_credentials.py` 共 **36 个测试函数**，参数化展开后 **72 个用例**：
service 用 fake store 直接测，不启动 `QApplication`、不启动线程、不碰真实 DPAPI；末尾的
接线测试才构造 `MainWindow`。其中 **14 个函数是结构守卫**（参数化后 42 个用例），
22 个是行为断言（30 个用例）。

结构守卫按 AST 读源码：依赖面集合相等、禁止 import 与禁止提到的符号各自参数化
（`MarketDataService` 与三个 provider adapter 名也在禁提名单里，因为本模块只需要
provider *id*）、`CredentialStatus` 字段集、`save_provider` 的 `api_key` /
`api_secret` 为 keyword-only、`desktop.py` 全文不得出现
`credential_store.save_secret(` / `load_secret(` / `delete_secret(` / `has_secret(` /
`credential_store.root` / `.dpapi`（构造 `WindowsCredentialStore` 本身允许，因为
`MainWindow` 仍拥有 persistence object）。

`status()` 的「不解密」用两个方向钉住：行为侧让 fake store 的 `load_secret` 直接
`raise AssertionError`，结构侧把 `has_secret` 的正文按 AST 钉成两条语句
（净化名 + 存在性判断）。

### 11.10 突变结果

**38/38 全杀、0 存活、0 skip、0 错误归因、0 未归因。** 第一轮暴露三个 harness 缺陷：

① **一个 SKIP**——锚点凭印象写成了 `"alpaca_api_key": "APCA_API_KEY_ID"`（dict 字面量
形态），而真实正文是元组 `("alpaca_api_key", "APCA_API_KEY_ID")`。锚点 0 命中即零覆盖，
绝不能当通过。

② **一个错误归因**——`_clean_name` 突变实际死在
`test_has_secret_normalises_like_the_other_operations`，harness 的失败解析器只认
`FAILED` / `ERROR` 前缀，漏掉了 unittest subtest 的 `SUBFAILED(name=...)` 行，于是判成
「死在别的测试上」。修法：解析器同时接受 `SUBFAILED`，并剥掉参数化前缀。

③ **一个存活**——`target.exists()` → `target.is_file()`：本 store 能产生的每一种状态
下两者等价（`save_secret` 写普通文件、`delete_secret` 只 unlink），任何行为断言都区分
不了。这是**等价突变体**，不是覆盖不足。但 `has_secret` 的正文是规格写死的，因此补一条
AST 结构守卫把它钉住（净化名 + 存在性判断、共两条语句），既让改动必须有意为之，也让该
突变体有了真实的杀手。

harness 本轮另外两处经验：锚点必须**从真实文件取**（凭记忆写的全落空）；度量与审计脚本
**不得与突变 sweep 并发**——突变期间源文件被临时改写，同时读到的字节数会漂（本轮实测
同一文件在两次读取间从 7,437 变成 7,395 字节）。

## 12. 第十一步：`desktop_settings_panel.py`（Settings 页表现层边界）

第十一步把 `MainWindow._settings_tab()` 里 364 行的 Qt 控件构造搬进
`src/us_quant/desktop_settings_panel.py`。这一 PR 是 **extraction，不是 API
redesign**：不重排布局、不改 copy、不改尺寸、不改 provider 顺序、不改 signal
wiring、不重命名任何控件。

```text
MainWindow
    ↓ callbacks + field_label + configure_combo_width
DesktopSettingsPanel
    ↓ Qt widgets only
```

设置事务仍在 `DesktopSettingsService`；凭据管理仍在 `DesktopCredentialService`；
`MainWindow` 的 13 个 handler 逐字节未变；Paper 全栈未变。

### 12.1 边界形状

```python
@dataclass(frozen=True, slots=True)
class DesktopSettingsCallbacks:
    preview_theme_changed
    settings_provider_selected
    switch_to_settings_provider
    api_provider_changed
    save_api_credentials
    clear_api_credentials
    paper_order_capability_toggled
    extended_hours_paper_toggled
    save_user_preferences


class DesktopSettingsPanel(QScrollArea):
    def __init__(
        self,
        *,
        preferences: UserPreferences,
        paths: ApplicationPaths,
        callbacks: DesktopSettingsCallbacks,
        field_label: Callable[[str], QLabel],
        configure_combo_width: Callable[..., None],
        parent: QWidget | None = None,
    ) -> None:
```

Panel 就是 `_settings_tab()` 的返回值本身——仍然是
`setFrameShape(QFrame.NoFrame)` / `setWidgetResizable(True)` /
`Qt.ScrollBarAlwaysOff` 的 `QScrollArea`，内容页 `minimumHeight` 仍是 820。

### 12.2 十七个 alias 必须是 identity

`MainWindow` 仍有几十处按名字读 `self.settings_*`。本步**不**一次性改光，而是让
`_settings_tab()` 在构造完 panel 之后，把 17 个控件逐个显式 alias 过去：

```python
self.settings_theme_combo = panel.settings_theme_combo
```

显式赋值而非 `for name in ...: setattr(...)`——后者无法审计、也无法被测试逐项钉住。
alias 必须是 identity：一个 Qt widget 只能有一个真实实例，禁止 wrapper、禁止复制。

### 12.3 初始 refresh 留在窗口，且必须在 alias 之后

`_api_provider_changed()` 与 `_refresh_credential_status()` 都读 `self.settings_*`。
若放进 panel 构造函数，窗口还没有那些属性。因此顺序固定为：

```text
construct panel → alias 17 controls → _api_provider_changed() → _refresh_credential_status() → return panel
```

Panel 构造函数本身**不调用任何 application callback**：两个 combo 都是
`addItem → setCurrentIndex → connect`，connect 落在定位之后，所以构造页面不会
顺带触发 theme preview / provider sync / credential refresh。

### 12.4 不搬通用 UI helper

`_field_label` 与 `_configure_combo_width` 仍留在 `MainWindow`，通过注入的
callable 使用——避免同一套 UI rule 定义两份。panel 不复制它们的实现，调用参数
逐字保持（provider combo `300 / 22`，api provider combo `180 / 14`）。

### 12.5 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9321 行 / 366,375 字节 | 9025 行 / 356,403 字节 |
| `_settings_tab()` | 364 行 | 64 行 |
| `desktop_settings_panel.py` | — | 452 行 / 16,492 字节 |
| `tests/test_desktop_settings_panel.py` | — | 1407 行 / 42,054 字节 |
| 全套测试 | 850 passed | 924 passed |

（同为 LF 归一化后的 blob 口径；before 取 `git show 847fd2dc:src/us_quant/desktop.py`。
`desktop.py` 净减 **296 行**。）

### 12.6 覆盖强度

`tests/test_desktop_settings_panel.py` 共 **62 个测试函数**，参数化展开后 **74 个用例**：
**12 个函数是结构守卫**（参数化后 24 个用例），50 个是行为断言（50 个用例）。

结构守卫按 AST 读源码：`_settings_tab` 不再直接构造
`QFrame` / `QGridLayout` / `QComboBox` / `QLineEdit` / `QSpinBox` /
`QCheckBox` / `QTextEdit`；三段 layout copy 只存在于 panel；panel 的依赖面集合
相等（只允许 `dataclasses` / `typing` / `PySide6` / `paths` /
`user_settings`）；panel 不得 import 窗口、不得持有任何 service、不得出现
`WindowsCredentialStore` / `DesktopCredentialService` / `CredentialStatus`。

**§41 的 handler byte-equivalence 是最重要的 scope guard**，它从 base commit
`847fd2dc` 取 13 个 handler 的源码与 PR head 逐字节比较。CI 的
`actions/checkout@v4` 默认 `fetch-depth: 1`，base commit 不在本地对象库里——
因此该 helper 先尝试 `git show`，失败则 `git fetch --depth 1 origin <base>` 后重试，
且**取不到就 fail，绝不 skip**：一条被跳过的守卫等于零覆盖。

### 12.7 突变结果

**46/46 全杀、0 存活、0 问题。** 第一轮暴露两处 harness 缺陷：

① **一个错误归因**——`findData(...) - 1` 突变我按印象声明期望测试是
`test_an_unknown_provider_falls_back_to_the_first_item`，但该测试传的正是未知
provider（`findData` 返回 `-1`），减一后仍被 `max(0, ...)` 夹回 0，行为等价；
真正被杀的是 `test_initial_values_come_from_the_preferences` 与
`test_extended_hours_maps_onto_the_ibkr_api_provider`（已知 provider 会错位）。

② **一个存活**——`_settings_tab` 末尾删掉 `self._refresh_credential_status()`。
原测试用 `_api_provider_changed` 的**真身**做 recorder，而真身内部自己就会刷一次
status，于是掩盖了「tab 自己有没有刷」。修法：把 `_api_provider_changed` 完全打桩成
纯记录器，断言 `seen == ["api_provider_changed", "refresh_credential_status"]`。

harness 本轮另外两处经验：**前台超时会把 sweep 杀在中途并留下未恢复的突变体**
（本轮实测 `appearance-title-changed` 的改写留在盘上）→ sweep 必须后台跑，且恢复后
用「生成器重建 + 逐字节比对」自证工作树干净；**突变体的期望测试名必须从真实失败输出取**，
不能凭模块语义推断。

## 13. 第十二步：`desktop_history_service.py`（历史队列边界）

第十二步把 `MainWindow` 里**日 K 队列的编排逻辑**搬进
`src/us_quant/desktop_history_service.py`。这是 extraction：不重排队列语义、
不改日志文案、不改 IBKR / 公开数据源的参数、不改 2500 行 UI 上限、
不改 `_auto_market_scan_finished` 的自动扫描路径。

```text
MainWindow
    ↓ dialogs / Qt task wrapper / rendering

DesktopHistoryService
    ↓
HistoryJobStore
IBKR history runner
Public history runner
Universe priority
```

`MainWindow` 仍是唯一持有 Qt 的地方：对话框、`_start_task` 包装、表格渲染全部留在窗口。
service 是纯应用层代码——**不 import Qt、不开线程、无取消机制**。

### 13.1 边界形状

```python
@dataclass(frozen=True, slots=True)
class HistoryQueueSnapshot:
    jobs: tuple[HistoryJob, ...]
    pending: int
    running: int
    completed: int
    failed: int


class DesktopHistoryService:
    def __init__(self, *, queue_path: Path, data_root: Path) -> None: ...

    def schedule_universe(self, universe) -> HistoryScheduleResult
    def run_ibkr(self, config, *, maximum_jobs: int, progress=None) -> dict[str, int]
    def run_public(self, *, maximum_jobs: int, progress=None) -> dict[str, int]
    def reset_failed(self) -> int
    def snapshot(self) -> HistoryQueueSnapshot
```

构造函数**必须 lazy 且不做 SQLite I/O**：它只记住 `queue_path` 与 `data_root`，
每次调用时才构造 `HistoryJobStore`。窗口构造期间因此不会碰数据库文件，
也不会把测试拖进真实的 `runtime_root`。

`queue_path` 与 `data_root` 直接取自窗口（`self.queue_path` / `self.data_root`），
不是从 scan path 反推。

### 13.2 六个允许变化的方法

| 方法 | before | after |
|---|---|---|
| `__init__` | — | 构造 service（+2 行） |
| `_schedule_history` | 21 行 | 16 行 |
| `_run_history` | 22 行 | 19 行 |
| `_run_public_history` | 25 行 | 21 行 |
| `_retry_failed` | 5 行 | 4 行 |
| `_refresh_queue_table` | 41 行 | 40 行 |

`MainWindow` 方法总数 **191 → 191**（不变）。其余 185 个方法与 Paper / AutoQuant /
RuntimeSupervisor 全栈逐字节未变。

`MainWindow` 每次 refresh 只调用 `service.snapshot()` **一次**；service 内部仍执行
两个 store read，并**刻意保持旧版顺序：`list_jobs()` → `counts()`**。顺序是语义的一部分——
runner 可能在这两次读取之间改动队列，反过来读会悄悄改掉旧 UI 的观察语义。

### 13.3 语义必须保持不变的四点

① `schedule_universe` 仍以 **`limit=None`** 调用 `prioritized_research_symbols`：
全量研究池，不是前 N 个。

② IBKR 与公开两条路径的 `maximum_jobs` 都来自 `self.batch_size.value()` 的透传，
不是硬编码 25。

③ 公开数据源路径**仍先 `reset_failed()`**：service 内部先重置失败任务再跑队列，
窗口不再自己重置。

④ `HistoryQueueSnapshot` **不做 2500 上限**：上限只作用于 UI 表格与「还有 N 条未显示」提示，
快照本身携带完整 `jobs`。

### 13.4 `_auto_market_scan_finished` 为什么故意不迁

自动扫描结束后那段代码直接构造 `HistoryJobStore` 并调用
`prioritized_research_symbols`，看起来与 `_schedule_history` 重复。但它属于
**AutoQuant 扫描收尾**这条路径，本轮的重构面是「用户手动触发的历史队列编排」。
把它一起迁进来会同时改动 AutoQuant 的收尾顺序，超出本步范围——因此
`_auto_market_scan_finished`（19 行）**逐字节冻结**，并由 byte-equivalence 守卫钉住。

### 13.5 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9025 行 / 356,403 字节 | 9016 行 / 356,158 字节 |
| `desktop_history_service.py` | — | 152 行 / 4,919 字节 |
| `tests/test_desktop_history_service.py` | — | 1364 行 / 40,865 字节 |
| 全套测试 | 924 passed | 979 passed |

（同为 LF 归一化后的 blob 口径；before 取 `git show 2b3e3f53:src/us_quant/desktop.py`。
行数不是本步的核心指标——本步的目的是把编排逻辑与 Qt 分离，见 §63。）

### 13.6 覆盖强度

`tests/test_desktop_history_service.py` 共 **40 个测试函数**，参数化展开后 **53 个用例**：
**11 个函数是结构守卫**（参数化后 24 个用例），29 个是行为断言（29 个用例）。

结构守卫按 AST 读源码：service 不 import Qt、不 import Paper / workflow 模块、
不开线程、无取消 API、依赖面集合相等；`_refresh_queue_table` 不再构造 `HistoryJobStore`；
`_auto_market_scan_finished` 与 base commit 逐字节相同。

### 13.7 突变结果

**54/54 全杀、0 存活、0 问题、0 挂起、0 SKIP。** 第一轮暴露 7 个存活，全部是
**真实覆盖缺口**而非等价突变：

① `summary-completed-is-failed` / `summary-failed-is-pending`——测试里 completed 与
failed 都是 0，互换后不可见 → 补一个五个计数互不相同的用例。

② `summary-cap-condition-widened`——只测了 2600 条一种场景，`> 2500` 放宽成 `> 0`
不可见 → 补恰好 2500 条与 2501 条的边界用例。

③ `public-start-message-changed`——断言只查子串 → 改为整段文案全等。

④ `public-resets-failed-itself`——没有断言窗口**不再**自己 reset → 把 service 的
`reset_failed` 打桩成抛 `AssertionError` 的哨兵。

⑤ `table-snapshot-called-twice`——没有数 `snapshot()` 调用次数 → 加计数器。

⑥ `ibkr-config-read-from-the-stream`——`market_data_service.config` 在构造后
**恰好是同一个对象**，`is window.config.ibkr` 分辨不出两者。真缺口在于设置提交会替换
`self.config` → 补一个「替换 `self.config` 后仍读到新对象」的用例。

第二轮暴露 3 个存活，根因是 **harness 的期望测试名没跟着新测试改名**（`-k` 没选中），
修正后全杀。这条与第十一步同款：**期望测试名必须实测确认，不能凭印象写**。

## 14. 第十三步：`desktop_universe_service.py`（官方标的刷新边界）

第十三步把 `MainWindow._refresh_universe()` 里「准备可写目录 → 下载官方标的 →
SEC profile 增量核验」这段非 Qt 编排搬进
`src/us_quant/desktop_universe_service.py`。这是 extraction：不改刷新语义、
不改参数、不改中文文案、不改取消链路。

```text
MainWindow
    ↓ Event / TaskThread / 中文 progress / buttons

DesktopUniverseService
    ↓
ApplicationPaths.ensure_user_reference_catalog
refresh_official_universe
enrich_us_profiles
```

`MainWindow` 继续拥有 `Event`、`TaskThread` 与全部中文 presentation；
service 是纯应用层代码——**不 import Qt、不开线程、无 `cancel()` / `stop()` /
`request_stop()`**。

### 14.1 边界形状

```python
@dataclass(frozen=True, slots=True)
class UniverseRefreshProgress:
    stage: str
    done: int = 0
    total: int = 0
    detail: str = ""


UniverseProgressCallback = Callable[[UniverseRefreshProgress], None]


class DesktopUniverseService:
    def __init__(self, *, paths: ApplicationPaths) -> None: ...

    def refresh(
        self,
        *,
        should_stop: Callable[[], bool] | None,
        progress: UniverseProgressCallback | None = None,
    ) -> UniverseSnapshot: ...
```

构造函数**只保存 `paths`**，不做任何 I/O：不 `ensure_user_reference_catalog()`、
不 `mkdir`、不 `copytree`、不发网络请求、不读 `universe.json`、不碰 SEC。
唯一被赋值的属性就是 `self.paths`（由 AST 守卫钉住：`self.<attr>` 的 Store
集合必须恰好等于 `{"paths"}`）。

`refresh()` 返回的仍是 `enrich_us_profiles(...)` 的原值——`UniverseSnapshot`，
不再包一层业务结果，`TaskThread.succeeded → _universe_refreshed` 收到的还是原来那个对象。

### 14.2 顺序是契约

```text
emit prepare_reference
↓
ensure_user_reference_catalog()
↓
emit download_official
↓
refresh_official_universe(...)
↓
emit enrich_sec_start
↓
enrich_us_profiles(...)
```

**先发 progress 再建目录**，不能反过来：旧代码就是这个顺序，且测试同时从两侧钉住
（事件序列，以及「事件发生时目录尚不存在」）。

四个 stage 名是模块常量，不散落 magic string：

```python
STAGE_PREPARE_REFERENCE = "prepare_reference"
STAGE_DOWNLOAD_OFFICIAL = "download_official"
STAGE_ENRICH_SEC_START = "enrich_sec_start"
STAGE_ENRICH_SEC = "enrich_sec"
```

### 14.3 逐项保持的 domain 参数

`refresh_official_universe`：`cache_root=reference_root`（来自
`ensure_user_reference_catalog()`，**不是**自己拼 `state_root / data / reference`）、
`leader_seed_path=resource_root/configs/sector_leaders.csv`、
`china_denylist_path=resource_root/configs/china_concept_denylist.csv`、
`should_stop=should_stop`、**`save_snapshot=False`**。

`save_snapshot=False` 是安全语义，不是性能开关：第一阶段只下载完名单、尚未完成
enrichment 时若写 `universe.json`，取消会把上一份完整可用的 snapshot 覆盖成半成品。
只有 `enrich_us_profiles(...)` 完整结束后才由 domain 层保存最终快照。

`enrich_us_profiles`：`snapshot` 原样传入、`cache_root=reference_root / "sec_profiles"`、
`max_new_profiles=500`、`should_stop=should_stop`。**不传**
`request_interval_seconds` / `user_agent` / `timeout`，继续走 `universe.py` 默认值。
500 是产品常量（`SEC_PROFILE_BUDGET`），不是配置项——UI 文案引用了这个数字。

### 14.4 取消与异常原样传播

`should_stop` 是**同一个 callable**，原样透传给两次 domain 调用（测试断言
`seen[0] is stop` 且 `seen[1] is stop`）。service 不新建 `Event`、不缓存 callback。

`UniverseRefreshCancelled` 原样向上：不 catch、不返回 `False`/`None`、不转成
`RuntimeError`。链路保持
`should_stop() → UniverseRefreshCancelled → refresh() → TaskThread.run() →
cancelled.emit() → _task_cancelled()`。

其他异常同样不包装：没有 `except OSError` / `except Exception`，也不新建
`DesktopUniverseError` 之类错误体系。`universe.py` 自己已有 fallback 语义，
service 只做编排。

### 14.5 中文 presentation 留在窗口

service 不含任何中文文案。窗口在 task 内部把 domain event 翻成文本：

| stage | 窗口显示 |
|---|---|
| `prepare_reference` | 正在准备可写的用户参考数据目录… |
| `download_official` | 正在下载 Nasdaq Trader 与 SEC 官方标的清单… |
| `enrich_sec_start` | 正在增量核验 500 家 SEC 注册地与行业… |
| `enrich_sec` | SEC 核验 {done}/{total}：{detail} |

未知 stage **fail closed**：`raise ValueError(f"unknown universe refresh stage: ...")`，
让 service/window 协议漂移在测试里暴露，而不是静默丢一条进度。

`detail` 原样透传：失败时 `universe.py` 可能传 `"XYZ 暂时失败: timeout"`，
不 parse、不截断、不翻译。

### 14.6 允许变化的两个方法

| 方法 | before | after |
|---|---|---|
| `__init__` | 183 行 | 184 行 |
| `_refresh_universe` | 46 行 | 38 行 |

`MainWindow` 方法总数 **191 → 191**。其余 189 个方法逐字节未变，包括
`_cancel_universe_refresh` / `_reset_universe_refresh_controls` /
`_universe_refreshed` / `_request_worker_stops` / `_worker_finished` /
`_task_cancelled` / `_run_scan` / `_scan_finished` /
`_prepare_auto_quant_candidates` / `_auto_candidate_preparation_failed` /
`_auto_market_scan_finished` / `_select_auto_quant_candidates` / `_start_task` /
`closeEvent`（14 个 byte-equivalence 用例）。

`_start_task` 返回 `False` 时语义不变：不保存 `cancel_event`、不设
`universe_refresh_worker`、不动 refresh / cancel 按钮，直接 return。

### 14.7 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9015 行 / 356,078 字节 | 9014 行 / 356,115 字节 |
| `desktop_universe_service.py` | — | 114 行 / 3,987 字节 |
| `tests/test_desktop_universe_service.py` | — | 1261 行 / 37,905 字节 |
| 全套测试 | 979 passed | 1032 passed |

（同为 LF 归一化后的 blob 口径；before 取 `git show 5109a180:src/us_quant/desktop.py`。
行数不是本步的重点——本步的价值是把 Qt 生命周期与 universe 应用编排分开。）

### 14.8 覆盖强度

`tests/test_desktop_universe_service.py` 共 **40 个测试函数**，参数化展开后 **53 个用例**：
**10 个函数是结构守卫**（参数化后 23 个用例），30 个是行为断言（30 个用例）。

结构守卫按 AST 读源码：service 的 import 集合**相等**（只允许 `__future__` /
`collections.abc` / `dataclasses` / `us_quant.paths` / `us_quant.universe`）、
不 import Qt、不 import 窗口或任何兄弟 service、不开线程、无取消 API、
`self.<attr>` 的 Store 集合恰好是 `{"paths"}`、`desktop.py` 不再出现
`refresh_official_universe(` / `enrich_us_profiles(` 且不再 import 这两个符号。

一个值得记的细节：§28 要求 `prioritized_research_symbols` 继续保留，而它在
`_auto_market_scan_finished` 正文里也出现——所以那条守卫查的是 **import 符号集合**，
不是源码子串；子串断言在 import 被删掉后仍然通过（首轮 sweep 就抓到了这一点）。

### 14.9 突变结果

**49/49 全杀、0 存活、0 问题、0 挂起、0 SKIP。** 首轮 1 个存活
（`prioritized-symbols-dropped`）暴露的正是上面那条弱断言：期望测试用的是
`assert name in source`，删掉 import 后正文里的同名调用仍让断言成立。
改成查 `_imported_names()` 集合后全杀。

本轮同时修正了第十二步文档的四点漂移（§61）：`HistoryQueueSnapshot` 没有 `total`
字段、`schedule_universe` 不接 `batch_size`、`snapshot()` 内部仍是**两次** store read
且顺序必须是 `list_jobs() → counts()`、以及测试数改为 979。第三点不是文字问题——
它是合并 PR #12 前最后修掉的行为漂移，写错就等于把错误语义固化成文档。

## 15. 第十四步：`desktop_market_scan_service.py`（手动市场扫描边界）

第十四步把 `MainWindow._run_scan()` 里「读本地日 K → `scan_market` →
`save_market_scan`」这段非 Qt 编排搬进
`src/us_quant/desktop_market_scan_service.py`。

```text
MainWindow
    ↓ Qt task / progress / dialogs

DesktopMarketScanService
    ↓
scan_market
save_market_scan
```

**只迁移用户手动扫描这条路径。** AutoQuant 的候选准备
（`_prepare_auto_quant_candidates`）内部还有一份同样的
`scan_market` + `save_market_scan`，本步**故意保留**：它连着 Paper `PREPARING`、
候选准备失败清理与 `_auto_market_scan_finished`，属于高风险启动链。

### 15.1 边界形状

```python
class DesktopMarketScanService:
    def __init__(
        self,
        *,
        data_root: Path,
        fallback_data_root: Path | None,
        scan_path: Path,
    ) -> None: ...

    def scan(
        self,
        universe: UniverseSnapshot,
        *,
        capital: Decimal,
        max_position_risk_pct: Decimal,
        substitutions: dict[str, SubstitutionRule],
    ) -> MarketScan: ...
```

构造函数**只保存三个路径**，零 I/O：不 `mkdir`、不 `exists`、不 `open`、
不 `read`、不 `scan_market`、不 `save_market_scan`。AST 守卫钉住
`self.<attr>` 的 Store 集合恰好是
`{data_root, fallback_data_root, scan_path}`。

三个路径全部取自窗口已有对象，不重新 `ApplicationPaths.discover()`、
不自行拼 `runtime/` / `research/results/` / `data/`。

### 15.2 scan → save → return

```text
scan_market(...)  →  save_market_scan(result, scan_path)  →  return result
```

顺序是契约：不 save-before-scan、不在 save 之前 return、不做后台保存。
`save_market_scan` 返回的 `Path` **不得**替代 domain result——`service.scan(...)`
的返回值必须与 `scan_market` 的返回值**同一对象**（identity，不是相等）。

`scan_market` 失败（`ValueError` / `OSError`）时 `save_market_scan` 调用 **0 次**，
异常原样向上；`save_market_scan` 失败时同样原样抛出，**不返回 scan result
假装成功**。两者都不 catch、不包装成 `DesktopMarketScanError`。service 里
没有任何 `except` 子句（AST 守卫）。

### 15.3 scanner 参数逐项保持

```python
scan_market(
    universe,
    data_root=self.data_root,
    fallback_data_root=self.fallback_data_root,
    capital=capital,
    max_position_risk_pct=max_position_risk_pct,
    substitutions=substitutions,
)
```

`allow_quality_second_tier` **不传**：旧代码也没传，因此继续使用 scanner 自己的
默认值 `True`。extraction 不得偷偷改第二层质量池规则。

### 15.4 求值时机（本步最容易错的地方）

| 值 | 求值时机 |
|---|---|
| `research_capital` | **task 启动前**（UI 线程） |
| `self.universe` | task 执行时读取 |
| `self.config.risk_limits.max_position_exposure_pct` | task 执行时读取 |
| `self.config.substitutions` | task 执行时读取 |

`research_capital = self._research_scenario_capital()` 发生在 `_start_task()`
之前；其余三项在 worker 内部读取。**不能**在 `_run_scan()` 前提前写
`universe = self.universe` 再让 worker 用那个快照，也**不能**把
`_research_scenario_capital()` 挪进 task——那会改变 UI 线程与 worker 线程的求值时机。

测试用「捕获 task 但不执行 → 替换 `universe` / `config` → 再执行 task」的方式钉住：
`capital` 必须是替换前的 `CAPITAL_A`，而 `universe` / `risk_pct` / `substitutions`
必须是替换后的新对象。

### 15.5 UI 留在窗口

- 缺少标的池的对话框（标题「缺少标的池」、正文「请先刷新官方标的。」）留在
  `_run_scan()`：这是 UI prerequisite，不是 application 逻辑。
- 进度文案「正在读取已通过质量门的本地日 K…」留在窗口；service **不需要**
  progress callback。本步**不**引入 `MarketScanProgress` / `STAGE_*` /
  callback protocol——只有一条进度，为形式统一而抽象没有价值。
- `_start_task(task, on_success=self._scan_finished, start_message="市场扫描中…",
  resource_group="scan")` 逐项不变。
- `_scan_finished` 完全冻结：`self.scan = result` → `_populate_scan_table()` →
  `_refresh_cards()` → `_refresh_market_scope_summary()` → `summary()` → `_log(...)`。

### 15.6 允许变化的两个方法

| 方法 | 说明 |
|---|---|
| `__init__` | 构造 service（+5 行） |
| `_run_scan` | 40 行 → 22 行 |

`MainWindow` 方法总数 **191 → 191**。其余方法逐字节未变，包括
`_scan_finished`、`_prepare_auto_quant_candidates`、
`_auto_candidate_preparation_failed`、`_auto_market_scan_finished`、
`_select_auto_quant_candidates`、`_stop_auto_market_data`、
`_confirm_and_start_auto_quant`、`_start_auto_quant`（8 个 byte-equivalence 用例）。

`scanner.py` 本身冻结——本步只是给它加一层 application orchestration wrapper。
`desktop.py` 仍 import `scan_market` / `save_market_scan`，因为 AutoQuant 路径
仍然直接调用它们。

### 15.7 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9014 行 / 356,115 字节 | 9016 行 / 356,227 字节 |
| `desktop_market_scan_service.py` | — | 72 行 / 2,440 字节 |
| `tests/test_desktop_market_scan_service.py` | — | 1084 行 / 31,495 字节 |
| 全套测试 | 1032 passed | 1077 passed |

（字节口径为 LF 归一化后的 blob；before 取 `git show d497388:src/us_quant/desktop.py`。
`MainWindow` 方法总数 **191 → 191**；`__init__` 184 → 189 行、`_run_scan` 32 → 28 行、
`_scan_finished` 10 → 10 行不变。）

### 15.8 覆盖强度与突变结果

`tests/test_desktop_market_scan_service.py` 共 **39 个测试函数**，参数化展开后
**46 个用例**：**16 个函数是结构守卫**（参数化后 23 个用例），23 个是行为断言
（23 个用例）。

结构守卫按 AST 读源码：service 的 import 集合**相等**（只允许 `__future__` /
`decimal` / `pathlib` / `us_quant.portfolio` / `us_quant.scanner` /
`us_quant.universe`）、不 import Qt、不 import Paper / workflow / risk / auto_quant、
无 `except` 子句、无 `cancel`/`stop`/`request_stop`、无中文文案、
`self.<attr>` 的 Store 集合恰好是三个路径、`__init__` 正文不含任何 I/O 调用；
`_run_scan` 正文不再出现 `scan_market(` / `save_market_scan(`，而
`_prepare_auto_quant_candidates` **必须**仍然直接调用它们。

突变结果：**44/44 全杀、0 存活、0 问题、0 挂起、0 SKIP。** 三轮才收敛：

① 首轮一个存活：`data_root.exists()` 加在构造函数里。这是**语义等价**突变——
纯读取调用无副作用、外部不可观测，行为断言永远杀不掉它。处理方式是加一条
AST 结构守卫（`__init__` 正文的调用集合不得含 `mkdir`/`exists`/`open`/…），
让「零 I/O」这条规则有真实杀手。

② 次轮两个存活：`capital-moved-into-the-task`（我的假
`_research_scenario_capital` 恒返回同一值，在 UI 线程算还是在 worker 里算看起来
一样 → 改成第二次调用返回不同值）；`on-success-callback-changed`（突变体是**多加了
一个 keyword**，而断言只查键存在 → 补 `set(kwargs)` 键集合断言）。

③ 一个 harness 缺陷：`scan-exceptions-swallowed` 的替换文本产生了不成对的 `try`，
`ast.parse` 拦下了它并**中止整个 sweep**。`ast.parse` 前置校验是对的（没有写坏文件），
但中止应该是**逐体跳过并记录**，不是让整轮失败。

### 15.9 三条守卫的维护方式

本步合法改动了 `_run_scan`，而前几轮的 scope guard 各自相对自己的 base commit 断言
「只有我声明的方法变了」。按 §49 的要求，处理方式是**把 `_run_scan` 加进它们的
显式 allowlist**，而不是删除、跳过或放宽守卫：

- `test_desktop_history_service.py`：`LATER_ROUND_METHODS += "_run_scan"`
- `test_desktop_settings_panel.py`：`DECLARED_REFACTOR_SURFACE += "_run_scan"`
- `test_desktop_universe_service.py`：从 `FROZEN_METHODS` 移出 `_run_scan`
  （它在第十三步的 base 里存在，但第十四步合法重写了它），并在本步自己的
  byte-equivalence 清单里改为冻结 `_scan_finished` 与 AutoQuant 七方法。

**每轮都要为自己那一刀配一个相对自己 base commit 的守卫**——否则「允许面」会
逐轮膨胀成什么都允许。

## 16. 第十五步：`desktop_backtest_service.py`（回测批量执行边界）

第十五步把 `MainWindow._run_backtest_workspace()` 里「逐个
`run_backtest` → `save_backtest_run` → 收集结果」的非 Qt 编排搬进
`src/us_quant/desktop_backtest_service.py`。

```text
MainWindow
    ↓ UI validation
    ↓ BacktestRequest construction
    ↓ Qt progress / task lifecycle

DesktopBacktestService
    ↓
run_backtest
save_backtest_run
```

**只迁移批量执行循环。** Backtest 页面、`BacktestRequest` 的 UI 构造、结果表格渲染、
Strategy Research、AutoQuant、Paper 全部未迁移。

### 16.1 边界形状

```python
BacktestProgress = Callable[[int, int, BacktestRequest], None]


class DesktopBacktestService:
    def __init__(
        self,
        *,
        data_root: Path,
        fallback_data_root: Path | None,
        output_root: Path,
    ) -> None: ...

    def run(
        self,
        requests: Sequence[BacktestRequest],
        *,
        on_progress: BacktestProgress | None = None,
    ) -> tuple[BacktestRun, ...]: ...
```

构造函数**只保存三个根**，零 I/O：不 `mkdir`、不 `exists`、不 `open`、
不 `resolve`、不 `glob`、不 `run_backtest`、不 `save_backtest_run`。
AST 守卫钉住 `self.<attr>` 的 Store 集合恰好是
`{data_root, fallback_data_root, output_root}`，并逐条检查 `__init__` 正文的
调用集合不含任何 I/O 名字（纯读取调用外部不可观测，行为断言杀不掉）。

三个根取自窗口已有对象：`data_root` / `bundled_data_root` 与
`paths.research_results_root / "backtests"`，不重新 `ApplicationPaths.discover()`。

### 16.2 每个 request 的顺序是契约

```text
progress(index, total, request)
↓
run_backtest(request, data_root=..., fallback_data_root=...)
↓
save_backtest_run(run, output_root=...)
↓
runs.append(run)
↓
next request
```

最后 `return tuple(runs)`。**progress 在 run 之前发出**——旧 closure 就是这个顺序，
测试同时从两侧钉住（事件序列，以及「run 计数器在该 progress 之后才增加」）。

严格串行：没有 `ThreadPoolExecutor`、没有 `asyncio`、没有 `multiprocessing`、
没有 `concurrent.futures`，也没有 `cancel()` / `stop()` / `request_stop()`。
现有行为是**一个 TaskThread 内串行跑多个版本**，保持如此。

### 16.3 返回值与 save 返回值

`run()` 返回 `tuple[BacktestRun, ...]`，且每个元素就是 `run_backtest` 返回的
**原对象**（identity，不是相等）。`save_backtest_run()` 返回的 `Path`
**必须被忽略**——这是和 market scan service 同样的 identity invariant，
突变体把 `runs.append(run)` 换成 `runs.append(save_backtest_run(...))` 会被杀死。

### 16.4 failure 与部分提交语义

- 第一个 request 的 `run_backtest` 抛错 → 该 request **不 save**、后续 request
  **不运行**、异常原样向上。
- 某个 request 的 `save_backtest_run` 抛错 → 该 run **不加入返回元组**、
  后续 request 不运行、异常原样向上。
- **中途失败不回滚**：已经保存的 A 留在磁盘上。这不是事务，是**部分提交批处理**；
  service 不删文件、不做 rollback、不做 batch transaction。
- service 里**没有任何 `except` 子句**（AST 守卫），也不新增错误体系
  （没有 `DesktopBacktestError` / `BacktestBatchError` / `PartialBacktestFailure`）。
- 空请求不发明规则：`run(()) == ()`，且 `run_backtest` / `save_backtest_run` /
  `on_progress` 均零调用。UI 层的「没有可运行版本」仍由窗口拦。

### 16.5 UI 与 presentation 留在窗口

- 三段前置校验全留窗口：已有 backtest worker → 「任务忙」；`records` 为空 →
  「没有可运行版本」；`start_date > end_date` → 「日期无效」。
  service 不知道 `QMessageBox`，也不知道 `_backtest_records(compare_all)`。
- `BacktestRequest` 的构造继续留窗口，包括 `target_weight / 100` 与
  各字段的 `Decimal(...)` 转换。本步**不**引入 `BacktestRequestFactory` /
  `BacktestFormModel` / `BacktestConfig`。
- 进度文案由窗口生成：`f"回测 {index}/{total}：{request.strategy_id} {request.symbol}"`。
  service 不含任何中文（「回测」「正在运行」「任务忙」「日期无效」「没有可运行版本」
  均不得出现）。
- `_start_task(task, on_success=self._backtest_workspace_finished,
  start_message=f"正在运行 {len(requests)} 个版本绑定回测…", resource_group="backtest")`
  逐项不变，且**不增加** `on_failure` / `on_cancel` / `shutdown_essential`。
  两个按钮在 `_start_task` 之前禁用。
- `_backtest_workspace_finished` 完全冻结；`_run_backtest_workspace` 里
  **不加 try/except**，service 异常继续走 `TaskThread.run() → failed.emit()`。

### 16.6 允许变化的两个方法

| 方法 | before | after |
|---|---|---|
| `__init__` | 189 行 | 194 行 |
| `_run_backtest_workspace` | 89 行 | 77 行 |

`MainWindow` 方法总数 **191 → 191**。其余方法逐字节未变，包括
`_backtest_workspace_finished`（40 行不变）、`_backtest_result_selection_changed`、
`_show_backtest_run`、`_backtest_records`、`_run_strategy_research`、
`_strategy_finished`、`_start_task`、`_task_failed`、`closeEvent`（9 个
byte-equivalence 用例）。

`desktop.py` 不再 import `run_backtest` / `save_backtest_run`——本步之后全文件
只剩 import 一处引用，按 §47 删除；`BacktestRequest` / `BacktestRun` /
`STRATEGY_SPECS` 保留（窗口仍构造 requests 并标注 task 返回类型）。

### 16.7 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 9016 行 / 356,227 字节 | 9008 行 / 356,059 字节 |
| `desktop_backtest_service.py` | — | 83 行 / 2,766 字节 |
| `tests/test_desktop_backtest_service.py` | — | 1414 行 / 41,920 字节 |
| 全套测试 | 1077 passed | 1134 passed |

（字节口径为 LF 归一化后的 blob；before 取 `git show b2fd5ee:src/us_quant/desktop.py`。）

### 16.8 覆盖强度与突变结果

`tests/test_desktop_backtest_service.py` 共 **49 个测试函数**，参数化展开后
**57 个用例**：**20 个函数是结构守卫**（参数化后 28 个用例），29 个是行为断言
（29 个用例）。

结构守卫按 AST 读源码：service 的 import 集合**相等**（只允许 `__future__` /
`collections.abc` / `pathlib` / `us_quant.backtest_workspace`）、不 import Qt、
不 import 兄弟 service / Paper / AutoQuant / risk、无 `except` 子句、
无 `cancel`/`stop`/`request_stop`、无中文文案、不知任何 widget 名、
`self.<attr>` 的 Store 集合恰好是三个根、`__init__` 正文无 I/O 调用、
`run()` 循环体内 `append` 必须在 `save` 之后；
`_run_backtest_workspace` 正文不再出现 `run_backtest(` / `save_backtest_run(`
且不含 `except`。

突变结果：**55/55 全杀、0 存活、0 问题、0 挂起、0 未解析。** 首轮 4 个存活，
两类原因：

① **两个真实覆盖缺口**。`initial-equity-not-decimal` 存活是因为
`Decimal(2500) == 2500` 为真——只断言值不断言类型的测试杀不掉「去掉 `Decimal(...)`」
的突变体（这正是「测试必须断言类型而非只断言值」那条老教训）。已补
`isinstance(..., Decimal)`。

② **两个是我的 harness 或测试写法问题**。`run-request-not-passed`（把
`request` 换成 `requests[0]`）在**单请求**的测试里完全等价——测试只传了一个
request，`requests[0] is request` 恒真；改成三个 request 后即被杀。
`append-before-save` 与 `failure-rolls-back-the-earlier-runs` 同理：
返回的元组在两种顺序下完全相同，行为断言**在原理上**分辨不出，必须补
AST 语句顺序守卫，并把「不回滚」写成**观察真实文件是否仍在**（假 save 写真实
文件），而不是数调用次数。

harness 本轮另有一处改进：替换文本若 `ast.parse` 失败，记录为
`UNPARSEABLE` 并**继续**，不再让整轮中止（上一轮踩过这个坑）。
