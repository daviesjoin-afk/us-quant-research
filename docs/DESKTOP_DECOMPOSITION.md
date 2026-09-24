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

> v2O-C4 后第一行改读 `research_scenario_capital.decimal_value`，时机不变。

`research_capital = self._research_scenario_capital()` 发生在 `_start_task()`
之前；其余三项在 worker 内部读取。**不能**在 `_run_scan()` 前提前写
`universe = self.universe` 再让 worker 用那个快照，也**不能**把
`_research_scenario_capital()` 挪进 task——那会改变 UI 线程与 worker 线程的求值时机。

> **v2O-C4 更新**：读取方已从 `_research_scenario_capital()` 改为
> `self.research_scenario_capital.decimal_value`（`ResearchScenarioCapitalState`，
> 见 §21）。**求值时机完全未变**——仍在 UI 线程、仍在 `_start_task()` 之前冻结；
> 只是 truth 换成了 canonical owner。上表其余三项同样未变。

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

> **v2O-C4 更新**：这里的 harness 命中的是「第二次调用返回不同值」。该手法跟着
> `_research_scenario_capital` 一起退休了——现在改成「捕获 task 后**改写 canonical
> state**，再执行 task，断言 worker 用的是改写前的值」，验证的是同一件事：capital 在
> request 时冻结。见 `tests/test_desktop_market_scan_service.py::test_the_evaluation_timing_is_preserved`。

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

## 17. 第十六步：`desktop_v2/orchestration/account/`（Account capability 边界）

第十五步之后，account route 的桌面 truth 仍在 `MainWindow`：最后一份
`BrokerAccountPortfolio`、refresh task、ledger append、page render 与 shell
badge 分散在若干个 handler 里。本步把它们收进
`desktop_v2/orchestration/account/`，与 v2O-A 的 market capability 并列。

### 17.1 被移出的知识

- **refresh 请求。** `_refresh_account_snapshot` 的 task 构造（进度文案、
  `timeout_seconds=20`、`resource_group="broker"`、`start_message`）搬进
  `AccountOrchestrator.request_refresh()`；窗口只负责把
  `AccountPage.refresh_requested` 连到它。
- **成功路径。** `_account_snapshot_finished` 的 Account-specific 部分
  （ledger append、page render、runtime event 请求、log 请求、shell 事实）
  搬进 `_refresh_succeeded`。
- **页面渲染。** `_refresh_account_surfaces` 的 ledger 读取与
  `AccountPage.render(...)` 调用搬进 `render_current()`。
- **fresh-Paper 规则。** `_paper_simulation_capital` 变成 Qt-free 纯函数
  `fresh_paper_net_liquidation(portfolio, *, now=None, max_age_seconds=300)`，
  规则逐字义保持（含「恰好 300 秒仍有效」与「naive timestamp 按 UTC 处理」）。
- **state。** `self.account_portfolio` 删除，且**不提供** compatibility
  property；`AccountOrchestrator.portfolio` 是
  `return self._application.portfolio` 的只读委托，因此
  `orchestrator.portfolio is application.portfolio`——**不存在第二份
  account truth**。

### 17.2 为什么 `BrokerAccountApplication` 仍是唯一 truth

`BrokerAccountApplication` 已经拥有 config、portfolio、`last_error` 与
refresh lifecycle，是正确 application-layer owner。若 orchestrator 再存一份
`self._portfolio`，两份副本会在「某条路径忘了更新另一份」时首次分叉——在账户
界面上这是安全缺陷而不是外观问题。所以 orchestrator 只委托，守卫里有一条
AST 检查：`orchestrator.py` 任何位置都不得 assign `self._portfolio` /
`self.portfolio`，且必须存在 `portfolio` property。

### 17.3 边界形状

```text
AccountOrchestrator(
    application=broker_account,     # BrokerAccountApplication
    ledger=account_ledger,          # AccountLedger
    page=account_page,              # 已构造好的 AccountPage
    submit_task=self._start_task,   # 窗口的通用后台任务准入边界
)
```

禁止 `window=self`、禁止 `context=desktop_context`、禁止 `services` 依赖袋。
orchestrator 的 public surface 精确为 `portfolio` /
`fresh_paper_net_liquidation` / `request_refresh` / `render_current` /
`set_presentation_inputs`，加四个 signal：`portfolio_changed` /
`shell_health_changed` / `runtime_event_requested` / `log_requested`。

### 17.4 通用任务生命周期仍属于窗口

`TaskThread`、`DesktopTaskController`、worker 列表、关闭准入闸门、
`_worker_finished`、`_task_cancelled`、busy dialog 一律留在窗口；orchestrator
只知道一个窄 callable `submit_task`。本步**不**提炼全局 `TaskFramework`：
等 v2O-C Research 成为第二个真实消费者时再决定。

### 17.5 exposure multiplier：pushed presentation input

账户页显示的「风险敞口」需要 configured exposure multipliers，而它们来自
app config 的 substitution 规则（Risk 的事实）。若 orchestrator 自己去取，
就会 import Risk / Strategy / config，破坏 capability 边界。所以由窗口
（composition root）推入冻结事实：

```text
AccountPresentationInputs.of({"AAPL": Decimal("2")})
  -> tuple[tuple[str, Decimal], ...]   # 不可变，避免外部 dict 被就地改动
```

orchestrator 只知道 `symbol -> presentation multiplier`，不知道它从哪来。

### 17.6 跨 workflow fan-out 暂留窗口

`_on_account_portfolio_changed()` 只执行既有 downstream side effect，顺序与
retired `_account_snapshot_finished` 一致：`_publish_dashboard_view()` →
（有 snapshot 时）`_render_auto_quant_snapshot()` → `_refresh_target_preflight()`
→ `_refresh_auto_quant_preflight()`。它禁止 `account_page.render`、
`account_ledger.*`、`broker_account.refresh`——只 fan-out finished
`portfolio` fact。本步不新增业务行为；发现潜在旧 bug 只单独记录。

> 记录在案、本步不修：base commit（`99049f0`）的
> `_refresh_account_surfaces` 调用的是 `_populate_auto_quant_snapshot(...)`，
> 而该方法在 Desktop Execution v2 已改名为 `_render_auto_quant_snapshot()`，
> 因此 base 上「账户刷新 → 且当时存在 auto_quant_snapshot」这条路径会
> `AttributeError`。本步迁移时按现状语义保留为 `_render_auto_quant_snapshot()`
> （即修掉该潜在崩溃），未新增任何行为。

### 17.7 research capital 与 strategy notice

`account_page.research_capital_card.set_value(...)` 这类 widget reach-through
收成 `AccountPage.set_research_capital(value, note)` 命名方法；scalar 仍由窗口
持有，最终 owner 留给 v2O-C。`set_notice(...)` 已是正确的 page-level API，
保留。窗口对 `AccountPage` 的直接调用白名单只有
`set_research_capital` / `set_notice` / `refresh_requested`。

### 17.8 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 5450 行 | 5436 行（净减 14） |
| `orchestration/account/orchestrator.py` | — | 252 行 |
| `orchestration/account/models.py` | — | 104 行 |
| `orchestration/account/queries.py` | — | 83 行 |
| 全套测试 | 2904 passed | 2926 passed |

净减少远小于 v2O-A 的 415 行是预期的：本步删掉 4 个窗口方法（约 128 行），
但按规格必须**保留**在窗口的跨 workflow bridge 与 presentation push
（约 117 行）计入新增。ownership 正确优先于行数。

### 17.9 覆盖强度与突变结果

`tests/test_desktop_account_orchestrator.py`（orchestrator 行为 + fresh-Paper
纯规则全分支）、`tests/test_desktop_account_orchestration_wiring.py`（真实
`MainWindow` 端到端：badge、Dashboard card、ledger table、execution equity
card、preflight fan-out、terminal export truth）、
`tests/test_desktop_account_orchestration_architecture.py`（结构守卫：已删
state / 无 compatibility property / 无 reach-through / render 唯一调用者 /
无 ledger 调用 / orchestrator 禁止依赖与禁止调用 / public surface 精确相等 /
Qt-free / line budgets）。

**突变结果：15/15 全杀。** 见 PR 描述。

## 18. 第十七步：`desktop_v2/orchestration/research/`（Research 基础 capability 边界）

v2O-B 之后，Research 两条基础 route 的桌面 truth 仍散在 `MainWindow`：
`self.universe` 快照、refresh task 的 cancel `Event`、refresh worker 句柄、
`_history_progress_percent`，以及围绕它们的 12 个 handler。本轮把它们搬进
`desktop_v2/orchestration/research/`：

```text
desktop_v2/orchestration/research/
  __init__.py                       故意没有 aggregate（见 18.1）
  universe/__init__.py              导出 UniverseOrchestrator
  universe/orchestrator.py          275 行：官方标的 refresh 请求、cancel、
                                    snapshot 持有、page render
  history/__init__.py               导出 HistoryOrchestrator
  history/orchestrator.py           227 行：history 队列的 task intent 与
                                    progress 展示
```

### 18.1 为什么没有 `ResearchOrchestrator`

Research 在导航上是一个 route aggregate，在运行时**不是**一个 owner。把
Universe/History/Scanner/Backtest/Cross-Section/Targeted 收进一个对象，等于
在单个文件里把 `MainWindow` 重新长出来一遍——只是换了个名字。所以本步：

- 每个真正持有 runtime truth 的 workspace 一个 subpackage；
- `research/orchestrator.py` 不得存在；
- `ResearchOrchestrator` / `ResearchManager` / `ResearchContext` /
  `ResearchServices` / `ResearchController` / `DesktopContext` 一律禁止声明。

这三条都由 `tests/test_desktop_research_foundations_architecture.py` 断言
（含「该文件不存在」这一条），是本阶段最重要的长期维护 guard。

### 18.2 单一真相：`UniverseSnapshot` 的 canonical Desktop owner

`DesktopUniverseService` 是无状态的：它执行刷新过程，不持有结果。所以
snapshot 的 canonical owner 只能是 desktop 层，即
`UniverseOrchestrator.snapshot`（只读 property，内部 `self._snapshot`）。

`MainWindow.self.universe` 已删除，且**没有** compatibility property——这是
刻意的：转发 property 会让所有未迁移的 caller 继续静默工作，"谁在读 universe
真相" 就不再是一条 grep 能回答的问题，下一次抽取也就找不到剩余消费者。
窗口所有读取改成显式 `self.universe_orchestrator.snapshot`，且**在任务执行时
读取**而不是在排程时捕获：刷新在任务排队期间落地时，被扫描的必须是新快照。

**startup 恢复只画一次。** `restore_snapshot()` 的语义是「adopt + render once」
——采纳快照改变了页面必须显示的内容，所以它必须画。`_load_local_state()` 因此
在恢复成功时**不再**补一次 `render_current()`：

```text
有 snapshot    → restore + render，恰好 1 次
无 snapshot    → 空视图 render，恰好 1 次
restore 失败   → 空视图 render，恰好 1 次
```

这一点由 `tests/test_desktop_research_foundations_wiring.py` 的三条
render-count 回归钉住（真实 `MainWindow` + spy `UniversePage.render`）。修复前
正常启动会画两次：`restore_snapshot` 一次、`_load_local_state` 再一次，等于每次
启动都重建整张表。capability 是 page render 的唯一 owner，重复调用不只是性能
问题——它让「一个 intent 对应一条 render path」不再成立。

### 18.3 History 为什么仍然不持有队列

`DesktopHistoryService` / `HistoryJobStore` 已经是队列的 canonical truth：
job 行、优先顺序、公共源回退、"把失败放回队列" 的规则都在那里。
`HistoryOrchestrator` 因此**不存** jobs、不存 snapshot、不缓存
`HistoryQueueSnapshot`；`render_current()` 每次都向 service 要一份新的。
它只拥有真正属于桌面的那一半：四个 page intent、进度百分比、以及唯一的
render 调用点。把队列复制进 orchestrator 会立刻产生第二份真相，且第一次
后台下载完成时两份就会不一致。

### 18.4 History 如何拿到 universe，而不依赖 `UniverseOrchestrator`

通过 `universe_provider: Callable[[], UniverseSnapshot | None]`。History 不
import Universe 的实现：universe 的实现还会继续变，依赖它的 History 就得跟着
变。同理 IBKR 配置走 `ibkr_config_provider`，且**每次运行都重新读**——构造时
捕获一份会让「Settings 里改了连接参数」在本次会话剩余时间里继续打旧端点。

### 18.5 `on_finished` 为什么现在才加

`TaskThread` 没有通用 cancel hook，每个 task 自带 `Event`；此前窗口靠
`workers[-1]` 与 worker 身份判断「这次完成属于哪个 capability」，这正是
Universe 必须持有 worker 句柄的原因。第二个真实消费者（History）出现后，
这个耦合不再是可接受的：`_start_task` 因此增加一个可选
`on_finished: Callable[[], None] | None = None`，由 caller 传入，**不接收
worker 对象**——拿到 worker 的 capability 可以拿它跟 worker 列表比身份，
而那正是 `on_finished` 要消除的耦合。

顺序是契约：`_finish_task` 先跑通用清理（释放资源组、重发 execution
controls），再跑 capability 的 hook，因此 hook 看到的是已释放的 worker。
成功、失败、取消三条路径都经由同一个 hook，capability 的完成不再依赖
worker 身份。

### 18.6 shutdown 如何取消 universe refresh

窗口不再读那个 `Event`，也不再 reach into capability 取它：
`_request_worker_stops()` 调 `universe_orchestrator.cancel_for_shutdown()`。
它与 `request_cancel()` 分开是刻意的——关闭不是操作员在请求取消，所以不写
状态行、也不在窗口拆自己时重绘页面。

### 18.7 本轮不动的东西

Scanner、Backtest / Cross-Section、Targeted research orchestration 一律不碰：
它们是后续的独立小刀，本轮只为 Research 的两条基础 route 建立边界。Market
scope bridge（`_on_universe_changed`）与 `_run_scan` 的读取迁移是这一轮的
**必要**连带改动，不是顺手重构。

### 18.8 体积与测试

| | before | after |
|---|---|---|
| `desktop.py` | 5436 行 | 5385 行（净减 51） |
| `orchestration/research/universe/orchestrator.py` | — | 275 行 |
| `orchestration/research/history/orchestrator.py` | — | 227 行 |
| `research/__init__.py` | — | 33 行 |
| `research/universe/__init__.py` | — | 29 行 |
| `research/history/__init__.py` | — | 31 行 |
| 全套测试 | 2926 passed | 见 PR 描述 |

### 18.9 覆盖强度与突变结果

`tests/test_desktop_universe_orchestrator.py`（26 项：refresh 成功/失败/取消
全分支、被拒任务不得进入 refreshing、snapshot 是 stored fact 而非委托）、
`tests/test_desktop_history_orchestrator.py`（16 项：四个 intent、进度百分比
计算与除零边界、失败重置、每次 render 读新快照、provider 每次重读）、
`tests/test_desktop_research_foundations_wiring.py`（14 项：真实按钮点击到
capability、真实 refresh 后下游读到的是新 snapshot、shutdown 取消 live
refresh 且不写状态行、`_start_task` 三条完成路径 + 不依赖 worker 句柄）、
`tests/test_desktop_research_foundations_architecture.py`（51 项：无
aggregate、无已删 state、无 compatibility property、无 reach-through、render
唯一调用点、page class 只在 composition root 构造、依赖白名单双向相等、
禁止 symbol、两个 capability 互不知晓、line budget）。

**突变结果：见 PR 描述（15/15）。**

## 19. 第十八步：`desktop_v2/orchestration/research/scanner/`（Scanner capability 边界）

v2O-C1 之后，Scanner route 的桌面 truth 仍散在 `MainWindow`。`self.scan` 到这一
步已经是一个**共享 Desktop fact**：手动扫描写它、startup restoration 手工解析
`market_scan.json` 重建它、AutoQuant preparation 覆盖它，然后五个互不相关的
消费者读它（Scanner page、intraday watchlist、market scope summary、execution
context 文案、AutoQuant candidate selection）。本轮给它一个唯一 owner：

```text
desktop_v2/orchestration/research/scanner/
  __init__.py              导出 ScannerOrchestrator / ScannerRunInputs
  models.py                74 行：ScannerRunInputs（frozen / slots / Qt-free）
  orchestrator.py          296 行：scan truth 的三种到达路径、chart 读取、
                           page render
```

`desktop.py`：**5392 → 5351 行（净减 41）**。

### 19.1 `MarketScan` canonical Desktop owner = `ScannerOrchestrator`

窗口不再有 `self.scan`，也**没有** compatibility property：

```python
# 禁止
@property
def scan(self):
    return self.scanner_orchestrator.scan
```

转发属性是缩小 diff 的诱人做法，也是陷阱：它让所有未迁移的消费者继续静默工作，
于是"谁读 scan truth"不再是一个 grep，下一轮抽取也找不到剩余消费者。所有消费者
必须显式写 `self.scanner_orchestrator.scan`。

### 19.2 三种到达路径必须分义

刻意做成三个入口，而不是一个带布尔 flag 的 setter：

| | render | `scan_changed` | 「扫描完成」日志 |
| --- | --- | --- | --- |
| `request_scan()` | ✅ | ✅ | ✅ |
| `restore_saved()` | ✅ | ❌ | ❌ |
| `adopt_external_scan(scan)` | ✅ | ✅ | ❌ |

拒绝 `set_scan(scan, emit=True, log=False, render=True)`：startup restoration
≠ cross-workflow new scan ≠ manual scan success，以后改其中一个时不能靠布尔 flag
猜 side effects。

### 19.3 两个方向相反的时间语义

```text
research capital / risk pct / substitutions
  → request_scan() 的 UI thread 冻结（ScannerRunInputs）
UniverseSnapshot
  → task 真正执行时重新读取
```

这是刻意设计，不是巧合：点「扫描」后、worker 启动前改研究资金不应改变即将跑的
扫描；而扫描排队期间落地的官方标的刷新**应该**是被扫描的那一份。

task 执行时 universe 若意外变成 `None`：**fail closed**（raise），不扫描 request
时捕获的旧副本。`ScannerRunInputs` 用 frozen tuple 存 substitutions，所以"输入在
request 时冻结"是数据的性质而不是约定。

### 19.4 AutoQuant 仍然直接 scan（最重要的边界）

`_prepare_auto_quant_candidates` 仍然直接：

```text
scan_market(...) + save_market_scan(...)
```

它连着 Paper `PREPARING`、候选准备失败清理与 `_auto_market_scan_finished`，属于
高风险启动链；本轮的 frozen guard 继续钉住这一点。结果进入 truth 的方向是：

```text
AutoQuant workflow
  ↓ finished MarketScan fact
MainWindow composition bridge（_auto_market_scan_finished）
  ↓
ScannerOrchestrator.adopt_external_scan(scan)
```

**不是** `ScannerOrchestrator → PaperWorkflow → Execution`。Scanner 从不知道 Paper
存在，所以边界正确。

### 19.5 Scanner artifact 与 chart 归 Scanner data boundary

`DesktopMarketScanService` 仍是扫描/I/O owner，本轮从"manual scan only"扩成更
完整、仍然内聚的 Scanner data boundary：

```text
scan(universe, ...)   scan_market + save_market_scan
load_saved()          scan_path → MarketScan | None
load_chart(symbol)    load_close_series(symbol, data_root=..., fallback=...)
```

`MainWindow._load_scan_file()` 因此退休：窗口不再手写 `json.loads`、`ScanResult`
reconstruction 与 `MarketScan` 的 datetime/date parsing，**不再知道 Scanner
artifact schema**。`load_saved()` 三种结果刻意不同：文件不存在 → `None`；合法 →
`MarketScan`；坏文件 → 异常向上。`restore_saved()` 捕获异常、truth 置空、**仍然
渲染一次**，所以坏 artifact 不阻止桌面启动。

chart loading 同样迁出（`_scanner_symbol_selected` 退休），失败语义保持：只 log、
不弹 dialog、不清旧 chart。

### 19.6 render 只有一个 caller

`ScannerOrchestrator` 是 Desktop 层唯一调用 `scanner_page.render(...)` 与
`scanner_page.render_chart(...)` 的对象。窗口只能 construct page、connect
signals、set_palette。`render_current()` 的 research count 规则原样保留（有
Universe 用 `research_eligible`，无 Universe 用 `len(results) + len(skipped)`，
都没有则 0）。

### 19.7 留在窗口的 cross-workflow consumers

`_apply_intraday_watchlist`（Scanner + Market + Account capital）、
`_select_auto_quant_candidates`（Paper workflow + account truth + strategy + risk
multipliers + market references + Execution page）与
`_refresh_market_scope_summary`（scan + universe + local history count）都留在
窗口，只把 truth source 换成 `self.scanner_orchestrator.scan`。

`scan_changed` 接到 `_refresh_market_scope_summary`，所以手动扫描与 AutoQuant
adoption 都自然更新 scope line；startup restore 不 emit change，因为
`_load_local_state()` 最后本来就统一 refresh scope。

窗口新增只有两个薄 bridge：`_scanner_run_inputs()`（composition：冻
`ScannerRunInputs`）与 `_report_scanner_refusal()`（`QMessageBox.information`，
severity 不变）。`_connect_scanner_page()` 只 connect。

### 19.8 依赖与 public surface

`ScannerOrchestrator` 禁止 import：`MainWindow`、其他 orchestrator、`Paper*` /
`Shadow*` / `Execution*` / `Market` / `Account` / `RiskApplication` /
`StrategyApplication` / `TaskThread` / `DesktopTaskController` / `HistoryJobStore`
/ `RuntimeSupervisor`，也不持有 `QMessageBox`。History 与 Scanner 不互相 import
（都通过 callable provider 拿 universe）。

```text
scan
restore_saved()
request_scan()
request_chart(symbol)
adopt_external_scan(scan)
render_current()
signals: scan_changed / refused / log_requested
```

### 19.9 体积与测试

| 文件 | base | 现在 |
| --- | --- | --- |
| `desktop.py` | 5392 行 | 5351 行（净减 41） |
| `scanner/orchestrator.py` | — | 296 行 |
| `scanner/models.py` | — | 74 行 |
| `scanner/__init__.py` | — | 39 行 |
| `desktop_market_scan_service.py` | 72 行 | 150 行 |
| 全套测试 | 3049 collected | 3182 collected |

### 19.10 覆盖强度与突变结果

`tests/test_desktop_scanner_orchestrator.py`（30 项：三条到达路径的
render/publish/log 矩阵、request-time 冻结 vs execution-time 重读、universe 消失
时 fail closed、被拒任务不进入状态、chart 成功/失败/不清旧图、research count 三条
分支、`render_current` 从不 fetch、`ScannerRunInputs` 不可变性与投影是新建
mapping）、`tests/test_desktop_research_scanner_orchestration.py`（60 项：无
Scanner god object、无已删 state/method、无 scan compatibility property、
Scanner 方法白名单、无 reach-through、render/render_chart 唯一调用点、page class
只在 composition root 构造、connect 方法只 connect、AutoQuant 仍直接 scan、
AutoQuant 完成只 publish、依赖白名单双向相等、禁止 symbol、不 import 其他
orchestrator、不持有 worker/dialog、public surface 相等、line budget）、
`tests/test_desktop_research_scanner_wiring.py`（18 项真实 `MainWindow`：按钮点击
→ capability、capability 三个信号都路由、单 truth identity 三条路径、AutoQuant
bridge 全链路 + 不写手动日志、page filter 不改 truth、scope summary 读
capability、startup exactly-once render 三态）。

**突变结果：见 PR 描述（15/15）。**

## 20. 第十九步：`desktop_v2/orchestration/research/backtest/`（Backtest capability 边界）

v2O-C2 之后，Backtest route 的桌面 truth 仍散在 `MainWindow`：
`self.backtest_runs` / `self._selected_backtest_run_id` / `self._backtest_busy`
三个属性被八个 handler 反复读写。本轮把这三项与它们的行为迁入：

```text
desktop_v2/orchestration/research/backtest/
  __init__.py              导出 BacktestOrchestrator 与 queries
  queries.py          162 行：纯规则（Qt-free）
  orchestrator.py     298 行：capability 本体
```

外加本轮唯一允许的 shared simplification：`orchestration/tasking.py`（86 行），
只放 `TaskSubmitter` protocol 与两个 callable alias，不放运行逻辑。

### 20.1 Backtest canonical Desktop owner = `BacktestOrchestrator`

`_runs` 是当前 session 的 Backtest 桌面 truth。窗口不再持有
`self.backtest_runs`，也没有 compatibility property，所以"谁拥有回测 runs?"
只需一次 grep。**本轮不公开 runs**：目前没有其他 workflow 读取
`BacktestRun` 列表，为"可能以后需要"加 accessor 会立刻被下一个 capability 使用，
Backtest workspace 就不再自洽。

### 20.2 纯规则归 `queries.py`

三条规则逐字义迁出，语义不变：

| 规则 | 保持的行为 |
| --- | --- |
| `strategy_options(versions)` | `STRATEGY_SPECS` family order（未知 family = 999），同 family 按 `semver` |
| `select_backtest_versions(compare_all=False)` | 只匹配 `selected_version_id`；不命中返回空，**不回退最新** |
| `select_backtest_versions(compare_all=True)` | 每个 `strategy_id` 取 provider 列表第一个（= 最新），最终按 `STRATEGY_SPECS` 顺序 |
| `build_backtest_requests(versions, draft)` | 所有 `Decimal` 转换与 `target_weight_percent / 100` 精度不变 |

`queries.py` 禁止 PySide6 / `QMessageBox` / `QObject` / Page / MainWindow /
`DesktopBacktestService` / `TaskThread`，因此策略选择与 draft → request 都可以
不启动 Qt 就单元测试。

### 20.3 busy ownership 与 last-good runs

`_busy` 属于 orchestrator，不从 worker 列表推导：

```text
request → validate → _busy = True → render → submit_task
                                                 ↓ False（准入被拒）
                                           _busy = False → render
```

failure：`_busy = False` + render + **保留上一次完整 runs**（不清空，与
Account / Universe 一致）；success：`_busy = False` + 替换 runs + 选第一条 +
render + 完成日志。`_worker_finished` 现在完全不认识 Backtest。

### 20.4 拒绝 severity 与 dialog bridge

三种拒绝保持原 severity：busy = `information` / 任务忙，无版本 = `warning` /
没有可运行版本，日期非法 = `warning` / 日期无效。capability 不 import
`QMessageBox`，只发 `refused = Signal(str, str, str)`；窗口
`_report_backtest_refusal(level, title, message)` 只按 level 选 dialog，不含
business logic。本轮**没有**创建 `DesktopNoticeBus` / `NotificationService` /
`DialogManager`——severity 模型尚未统一，等 Targeted / System 出现相同结构再定。

busy 预检（`task_available`）在其他校验**之前**，与迁移前顺序一致；
`_start_task` 仍做 authoritative admission，orchestrator 不读 `workers`。

### 20.5 页面唯一 caller

`BacktestPage.render` 与 `BacktestPage.set_strategy_options` 的唯一 caller 是
`BacktestOrchestrator`。窗口只允许 construct page、connect signals、
`set_palette`。以后改 Backtest display 只需看 `page/` + `presenter/` +
`orchestrator/`。

### 20.6 依赖与 public surface

禁止 import：`MainWindow`、其他任何 orchestrator、`Paper*` / `Shadow*` /
`Execution*` / `TradingRuntime` / `RiskApplication`、
`StrategySelectionService` / `StrategyApplication`、`TaskThread` /
`DesktopTaskController`。strategy catalogue 走
`Callable[[], tuple[StrategyVersion, ...]]` provider，不走 service。

public surface 精确为 `log_requested` / `refused` /
`refresh_strategy_options` / `request_selected` / `request_compare_all` /
`select_run` / `render_current`。

### 20.7 体积与测试

| 文件 | 迁移前 | 迁移后 |
| --- | --- | --- |
| `desktop.py` | 5351 行 | 5224 行（净减 127） |
| `backtest/orchestrator.py` | — | 298 行 |
| `backtest/queries.py` | — | 162 行 |
| `tasking.py` | — | 86 行 |

新增 `tests/test_desktop_backtest_orchestrator.py`（27 项行为，无窗口）、
`tests/test_desktop_research_backtest_orchestration.py`（79 项结构）、重写
`tests/test_desktop_v2_backtest_wiring.py`（17 项真实 `MainWindow`）。
architecture guards keep their rules local; shared AST support is deferred until
at least three real consumers exist. 两个 consumer 不足以支撑多一层跳转，
所以每个 guard 自带它实际需要的机械查询函数。

### 20.8 本轮明确不做的事

没有 startup restore saved runs（产品行为变化）；没有并发化 / asyncio /
rollback / cancellation（`DesktopBacktestService` 边界保持稳定）；没有改任何
`Decimal` / commission / slippage / `BacktestEngine` / position sizing；没有碰
Cross Section（含 `research capital`）、Targeted、Shadow、Paper。

**突变结果：见 PR 描述（15/15，全部由对应具名测试捕获）。**

维护导航见 `docs/DESKTOP_CAPABILITY_MAP.md`。

## 21. 第二十步：`desktop_v2/orchestration/research/cross_section/`（Cross Section capability 边界）+ Research Scenario Capital

### 21.1 本轮真正的问题有两个

```text
1. Cross Section 的 report / task / render 仍在 MainWindow

2. research capital 仍是 MainWindow 的全局 scalar
```

第二个是这一刀的关键。`self._research_capital_value` 当时已被 **7 个**独立工作流消费：

```text
Cross Section research
Scanner 手动扫描
AutoQuant candidate preparation
Market watchlist fallback
Targeted replay
Targeted robustness
Account presentation
```

所以它既不能定义成「Cross Section 的 widget value」，也不能继续让 MainWindow
做它的 truth owner。

### 21.2 Research Scenario Capital（研究情景资金）

本轮正式命名并单独立 owner：

```text
orchestration/research/scenario_capital.py  ->  ResearchScenarioCapitalState
```

语义（同时写进代码 docstring 与 capability map）：

```text
它表示：历史研究 / replay / scan affordability / cross-sectional research
        使用的情景初始资金

它不是：IBKR Paper NetLiquidation / Live 账户资金 / 可用购买力 /
        Risk capital / 真实仓位资金 / Capital allocation authority
```

**为什么现在才抽象**：此前规定「没有真实重复消费者 → 不抽象」。这里已经有 7 个
被代码证实的消费者，所以这是 shared boundary，不是 premature abstraction；与上一轮
删除 premature AST helper 并不矛盾。

**为什么不能叫 `CapitalAllocator`**：终极架构里 allocator 建立在 broker truth +
portfolio risk + strategy allocation 之上。研究情景 scalar 没有任何真实账户资金权限，
一旦叫这个名字就很容易被误带进 Live sizing。名字本身就是安全边界。

这个模块 **78 行**，只允许 `decimal` + 普通 class；禁止 PySide6 / QObject /
Signal / MainWindow / Account / Broker / Risk / Execution / Paper / Shadow /
CrossSection executor / Config / repository。它只是一个 scalar 的 canonical owner，
不是 event bus，也不是 `ResearchState` bag（`self._value` 是唯一实例属性，
有 guard）。

### 21.3 新增 service 与 artifact pair

```text
src/us_quant/desktop_cross_section_service.py   （120 行）
```

与 `DesktopMarketScanService` / `DesktopBacktestService` 同构：Cross Section research
procedure + artifact load/save boundary。public surface 仅：

```text
run(universe, *, research_capital: int) -> dict
load_saved() -> dict | None
```

**两条时间语义刻意相反，且都保持原样**：

* `config` 在 **task 真正执行时** 通过 `config_provider` 读取 —— provider 在 `run()`
  内调用，不在 constructor 调用，否则会捕获 stale startup config；
* `research_capital` 在 **request 时冻结** —— 排队期间再改页面 capital 不得改变已提交
  的本次 run。

`load_executable_research(path)` 放在 `save_executable_research` **旁边**
（`executable_research.py`），只做 read UTF-8 / `json.loads` / top-level 必须 dict。
理由是降低维护检索成本：以后 artifact format 改动只需读一个模块，不必到 desktop
service 猜 JSON。它不复制整个 report schema validator —— 那是 presenter 的事。

### 21.4 CrossSectionOrchestrator

```text
desktop_v2/orchestration/research/cross_section/orchestrator.py   （267 行）
desktop_v2/orchestration/research/cross_section/__init__.py
```

不为了对称增加 `models.py` / `queries.py`：当前没有真实需求。

**owner**：`_report`（private，无 getter —— 没有其他 workflow 消费 raw report，
与 Backtest runs 同理）。

**public surface 精确为**：

```text
request_capital_change(value)
request_run(draft)
restore_saved()
render_current()
signals: capital_changed / report_changed / refused / log_requested
```

**report truth 的唯一 owner 是它**；`CrossSectionResearchPage.render` 在 production
中只有它一个调用者。

**success 顺序是契约**：

```text
validate（必须 dict）
  ↓
project（build_cross_section_view，此时 last-good report 还在）
  ↓
build completion message（含数值格式化 —— 这也是会失败的一步）
  ↓
commit（_report = result）        ← 过了这条线就不得再因 result 失败
  ↓
render
  ↓
report_changed + completion log
```

禁止「先 commit 再 render」：presenter 随后失败会把 last-good 污染成画不出来的对象。

**并且：commit 之后不得再有任何可能因 result 失败的操作。** 这条比「先 project」更强，
它是由 review 发现的真实缺陷换来的。presenter 用 `float(...)` 强转，所以带**数字字符串**
的报告（`"total_return": "0.2"`）能正常投影；但完成日志用裸 `{:+.1%}` 格式化同一个值会抛
`ValueError`。原先日志在 commit 之后构建，于是出现：

```text
_report 已被替换 → 页面已 render → report_changed 已发出
（Dashboard artifact bridge 已跑）→ 然后 exception，且没有完成日志
```

即「UI 已接受、logger 才失败」的中间状态：truth 已移动、bridge 已触发、日志缺失。
现在把**完整成功输出所需的一切**（含日志文案）都在 commit 前准备好；失败统一在边界上
normalize 成 `TypeError`（`__cause__` 保留原始 `KeyError` / `ValueError`），因此

```text
能投影 ⇒ 整条 success path 一定能跑完
```

回归在 `test_desktop_cross_section_orchestrator.py`
（`test_numeric_string_metrics_complete_the_whole_success_path`、
`test_every_unusable_shape_fails_before_any_side_effect`）与
`test_desktop_v2_cross_section_wiring.py`
（`test_numeric_string_metrics_still_refresh_the_artifact_bridge`，走真实窗口验证 bridge）；
顺序本身由 `test_desktop_research_cross_section_orchestration.py` 的
`test_the_success_path_prepares_everything_before_it_commits` 结构钉死。

task failure 不清 `_report` —— 一次失败的 run 不能作为上一份报告错误的证据，与
Account / Universe / Backtest 的 last-good 原则一致。

**startup restore 三条路径，各自 exactly-once render**：

| 路径 | 行为 |
| --- | --- |
| 无产物 | `_report = None`，render 一次，不 emit `report_changed`，无完成日志 |
| 有效产物 | load + project + commit，render 一次，不 emit `report_changed` |
| 产物损坏 | catch，`_report = None`，render 一次，log `风险一致研究产物读取失败：…`，不 emit |

build 阶段**不再** paint（删除了 `_publish_cross_section_view()` 的构造期调用），
由 `restore_saved()` 统一第一次 render —— 这正是早期 Universe double-render 的同类
问题。

### 21.5 MainWindow 最终 C4 形状

删除的 state（无 compatibility alias）：

```text
self.cross_section_report
self.cross_section_path
self._research_capital_value
```

删除的 method（无 forwarding wrapper）：

```text
_publish_cross_section_view
_run_cross_section_research
_cross_section_finished
_load_cross_section_report
_research_scenario_capital
_research_capital_changed
```

保留的只有 construct + connect + 三个 cross-workflow bridge：

```text
_on_research_scenario_capital_changed  （capital fact → Account presentation）
_on_cross_section_report_changed       （success → ArtifactCatalog + Dashboard）
_report_cross_section_refusal          （只做 QMessageBox.information）
```

**为什么不建 ResearchCapitalChangedEventBus**：capital 改变时 Scanner / AutoQuant /
Targeted / watchlist 都不需要立即运行，它们在下一次 request 时 pull 最新值即可。
只有 Account presentation 需要立刻跟随，所以只有这一条 bridge。

**Account 收正**：`AccountPage.set_research_capital` 退休，card 改由 `AccountPage.render`
的 `research_capital: int | None` 参数绘制，值通过已有的
`AccountPresentationInputs.research_capital` 传进来。于是恢复了真正的
「`AccountOrchestrator` = AccountPage 唯一 render owner」，Account 说明卡也从「第二条
paint path」变成 presentation projection。

### 21.6 明确不接进来的东西

**Backtest 不绑进这个 shared state**：它有自己的 draft / initial-equity 输入，不是
research scenario capital 的直接消费者。为了「统一研究资金」强行接上会改变产品行为。

**研究精度完全冻结**：`run_executable_cross_sectional_research`、simulation math、
candidate ranking、walk-forward folds、training Sharpe selection、whole-share sizing、
risk multiplier、gross/position risk percentage、commission、minimum commission、
slippage、cost 2x、substitution holding limit、unaffordable backfill、promotion gate ——
全部未改。`Decimal(draft.research_capital)` 迁移后仍等价于
`Decimal(research_capital_int)`，没有 float round-trip。artifact 的 key /
`research_version` / `promotion_gate` / `scope` schema / `chart_data` 全部保持兼容，
旧 JSON 仍能 startup restore。

**没有创建 `ResearchOrchestrator` / `CapitalAllocator`**；也没有碰 Targeted 算法、
Shadow engine、Paper workflow、IBKR adapter、Risk limits、ExecutionApplication、
strategy promotion、AI、Live mode。

### 21.7 体积与测试

| 文件 | 行数 | 预算 |
| --- | --- | --- |
| `desktop.py` | 5243 行（迁移前 5224，**净增 19**） | 只减不增不是成功标准 |
| `scenario_capital.py` | 78 | 80 |
| `cross_section/orchestrator.py` | 267 | 300 |
| `desktop_cross_section_service.py` | 120 | 180 |

`desktop.py` 本轮略增，原因是新增了 3 个 bridge 与 2 处 composition 注释，同时删掉了
6 个 handler。**没有**为了少几十行引入 `ResearchController` / `CapitalContext` —— 那才是
失败。

新增测试：`tests/test_desktop_research_scenario_capital.py`（21 项）、
`tests/test_desktop_cross_section_service.py`（16 项）、
`tests/test_desktop_cross_section_orchestrator.py`（20 项行为，无窗口）、
`tests/test_desktop_research_cross_section_orchestration.py`（44 项结构）；
重写 `tests/test_desktop_v2_cross_section_wiring.py`（25 项真实 `MainWindow`）。
四个旧 guard 文件各自追加本轮的 delta 声明（removed / added / net-zero / changed），
并把 `_research_capital_changed` 从上一轮的 changed 列表移到本轮 removed 列表。

**没有新建 shared AST helper**。上一轮已经明确「测试工具抽象也要有真实重复证据」，
本轮仍先在各 guard 内写少量机械查询；不为了抽取主动改旧 tests 制造消费者。

**突变结果：见 PR 描述。** 维护导航见 `docs/DESKTOP_CAPABILITY_MAP.md`。

## 22. Maintenance：preview tooling 修复（不计作 capability 阶段）

C1–C3 的三次迁移各自退休了一批 `MainWindow` widget 属性与 private handler，
`scripts/render_desktop_preview.py` 仍引用它们，因此已无法完整运行（`window.scan`
早于 Scanner orchestration 完成即消失，`auto_summary_label` / `auto_detail_tabs`
早于 Backtest PR）。本轮不恢复任何 compatibility alias，而是让 tooling 改用它本该
使用的 public/semantic boundary：

| preview 原先引用 | 现在引用 |
| --- | --- |
| `window.scan` | `window.scanner_orchestrator.scan`（只读一次进局部变量） |
| `window.auto_summary_label.setText(...)` | `window.execution_page.render_context(summary=...)` |
| `window.auto_detail_tabs.setCurrentIndex(2)` | `window.execution_page.set_active_detail(ExecutionDetailWorkspace.ORDERS)` |
| `window.target_symbol_input.setText(...)` + `window._apply_target_symbol()` | `window.targeted_validation_page.target_apply_requested.emit("AAPL")` |
| `window._run_targeted_replay()` / `_run_targeted_robustness()` | 同名 page signal 的 `emit()` |
| `window.targeted_*_tabs.setCurrentIndex(N)` | `set_active_workspace` / `set_active_evidence_workspace` / `set_active_robustness_detail` / `set_active_review_detail` |
| `window.workers` | `window.task_controller.active_count`（超时改为 fail loudly） |

为此新增的全部 production 代码是 **presentation navigation**，没有一条携带业务语义：

* `execution/models.py`：`ExecutionDetailWorkspace`（5 个 `IntEnum` key）；
* `execution/page.py`：`set_active_detail(...)`；
* `targeted/models.py`：`TargetedWorkspace`、`TargetedEvidenceWorkspace`、
  `TargetedRobustnessDetail`、`TargetedReviewDetail`；
* `targeted/page.py`：四个 `set_active_*` 转发方法；
* `targeted/evidence_panel.py`：robustness/review 的 detail `QTabWidget` 由局部变量
  改为 panel 自持字段，并暴露同名语义方法（panel 仍是 page 的 implementation detail）。

这些 enum 只决定**哪个 tab 显示**，不能成为 workflow phase，也不进入任何 business
code。`desktop.py` 本轮 **0 行改动**：目标正是让 tooling 适配已经存在的正确边界。

留作 bridge（对应 capability 尚未轮到，不提前拆）：`_populate_auto_quant_candidates`
（Execution/Paper → v2O-E）。`_refresh_minute_data_status` 已在 v2O-C5B 退休，见 §25。

ownership、roadmap 与 capability map 均未改变；preview 的 fixture 数据、Scanner 候选
选择算法、Targeted 研究算法与 Execution/Paper 状态机全部冻结。下一刀当时仍是
**v2O-C4 Cross Section**（已完成，见 §21）。

## 23. 路线状态（v2O-C 收口后）

```text
v2O-A Market        ✅
v2O-B Account       ✅

v2O-C Research      COMPLETE
  C1 Universe + History   ✅
  C2 Scanner              ✅
  C3 Backtest             ✅
  C4 Cross Section        ✅   （含 Research Scenario Capital 单 owner 化）
  C5A Targeted Evidence   ✅
  C5B Targeted Session + Preflight   ✅

NEXT:
v2O-D Shadow
v2O-E Paper
v2O-F System

MainWindow composition closure
Final Architecture Closure
```

C5 是 Research 中最复杂的一刀，因此 C4 先把 `research scenario capital` 这个共享
事实收干净：C5A 的 Targeted replay / robustness 已经是它的消费者，它们读的是唯一
owner，拼接期间不存在两套资金真值。

C5 本身又拆成两刀，因为 Targeted 实际混着三类互不相关的东西：

```text
A. Evidence research      → C5A  TargetedEvidenceOrchestrator
B. Target session/preflight → C5B  TargetedSessionOrchestrator
C. Shadow runtime         → v2O-D（仍未迁）
```

全部塞进一个 `TargetedOrchestrator` 会直接制造一个新的 MainWindow，所以两轮都明确
禁止创建 `targeted/orchestrator.py`、`TargetedOrchestrator`、`TargetedContext`、
`TargetedState`、`TargetedServices`（见 §24、§25）。

**Research 收口条件**（§25.8）：下表的每一行都有自己的 owner，且 `MainWindow` 不再
持有任何 Research capability state。Shadow state 不算 Research state。

```text
Universe truth        → UniverseOrchestrator
History truth         → HistoryOrchestrator / DesktopHistoryService
Scanner truth         → ScannerOrchestrator
Backtest truth        → BacktestOrchestrator
Cross Section truth   → CrossSectionOrchestrator
Research Capital      → ResearchScenarioCapitalState
Targeted Evidence     → TargetedEvidenceOrchestrator
Targeted Session      → TargetedSessionOrchestrator
```

没有 `ResearchOrchestrator`、`ResearchManager`、`ResearchContext`。

## 24. v2O-C5A：Targeted Evidence 提取

### 24.1 为什么 Evidence 与 Session 必须分开

最终架构里这是四个不同的权威问题：

```text
Targeted Evidence → 这套策略有什么研究证据？
Shadow            → 这套策略在实时市场模拟运行得怎样？
Paper             → 是否允许发送真实 IBKR Paper orders？
Live              → 是否经过 Risk Kernel 后允许真实资金执行？
```

研究证据不能因为页面叫「Targeted」就获得 Shadow/Paper/Live 权威。B 组的
依赖结构（Universe + Market + Account + Strategy + Minute summary + Shadow
session state）与 A 组（研究产物）完全不同，所以 B 留在 C5B。

### 24.2 状态收口

MainWindow 退出九个 evidence state：

```text
targeted_replay_results            targeted_robustness_results
targeted_walk_forward_results      targeted_overfit_results
targeted_data_quality_results      targeted_execution_stress_results
targeted_review_results

_selected_robustness_run_id        _selected_review_run_id
```

外加两个一刀即用的导航状态 `_targeted_active_workspace` /
`_targeted_active_evidence_tab`——它们原本只是为了「robustness 完成 → 下一次
combined render → 切 tab」，现在直接使用 PR #42 的 semantic navigation
（`set_active_workspace(TargetedWorkspace.EVIDENCE)` /
`set_active_evidence_workspace(TargetedEvidenceWorkspace.REVIEW)`）。

canonical truth 是 `TargetedEvidenceOrchestrator._snapshot`，类型为 immutable
`TargetedEvidenceSnapshot`（七类 result tuple + 两个 selection）。它**公开**
`:attr:`snapshot``，因为有一个真实的跨能力消费者：`export_terminal_bundle`
需要全部七类。Backtest runs 不公开是因为没有消费者；这里的判据是消费者，不是
对私有性的偏好。没有增加七个独立 getter。

### 24.3 依赖与文件

```text
TargetedEvidenceOrchestrator
        ↓
DesktopTargetedEvidenceService
        ↓
targeted_replay / targeted_robustness / targeted_validation /
targeted_overfit / targeted_data_quality / targeted_execution_stress /
targeted_review / MinuteQuoteStore
```

反向依赖被禁止，所以 contract 放在中性模块
`src/us_quant/desktop_targeted_evidence_models.py`（Qt-free）：
`TargetedEvidenceRunInputs` / `TargetedEvidenceSnapshot` /
`TargetedRobustnessBundle` / `TargetedEvidenceRuntimeEvent`。service 与
capability 都依赖它，而不是 service 反向 import orchestration。

capability 包内按真实职责分为四个文件：

```text
targeted/evidence/models.py      纯规则：拒绝、commit、类型校验
targeted/evidence/messages.py    面向操作员的文本（含结果相关文案）
targeted/evidence/projector.py   snapshot → page view 的字段接线
targeted/evidence/orchestrator.py 时序、请求、render owner
```

`models.py` 与 `messages.py` 分开是因为「结果相关格式化必须在 commit 之前完成」
这条规则需要一个显式的家：C4 曾出现 completion message 用 `{:+.2%}` 格式化
numeric string 而在 truth 已经移动、页面已经重绘之后抛 `ValueError` 的缺陷。

### 24.4 时序语义（与 C4 相反）

```text
request-time freeze（UI 线程，每个请求各读一次）：
  strategy version    ← strategy_provider
  target symbol       ← target_symbol_provider（page 自己的 editor）
  research capital    ← capital_state.decimal_value

request-time gate：
  Universe            ← 仅用于 eligibility；None ≠ 拒绝（产品行为，未改）

execution-time（service 内部）：
  minute evidence     ← MinuteQuoteStore
```

worker 不重新读取任何一项。Universe 不做 execution-time re-read，因为
Targeted 从来不用它做研究——为了「和 C4 统一」而加一次读取会改变请求语义。

### 24.5 冻结的研究语义

provider selection 逐字义等价（行数最多优先，同数时取 lexical 最大 provider
名——`max` over `(row_count, provider)` 的实际语义），data quality 使用**同一个**
provider 的 raw rows；replay session 是 `group_regular_sessions(selected)` 的
`sessions[-1]`；pipeline 顺序与每个 save 时机不变（robustness → overfit →
quality → walk-forward when `usable_sessions >= 20` → stress → review）。
`Decimal initial_equity` 不做 float round-trip。

匿名 `tuple[6]` 改为具名 immutable `TargetedRobustnessBundle`：`bundle.review`
比 `result[5]` 稳定得多，而且六项必须**原子** commit——旧代码逐条
`insert(0, ...)` 可能产生「robustness 列表已更新但 review 仍是旧值」的中间态。

### 24.6 render 边界拆分

```text
TargetedValidationPage.render_session(view)   ← MainWindow（C5B 再迁）
TargetedValidationPage.render_evidence(view)  ← TargetedEvidenceOrchestrator
```

combined `render(TargetedValidationView)` 与其 view model 已**删除**，没有留
compatibility wrapper——否则 MainWindow 以后仍可拿回 evidence render。

这同时消掉一个隐性性能问题：此前任何 market tick / preflight refresh /
minute status 更新都会走 `_publish_targeted_view()`，从而重新 build
evidence view 并重绘七张研究证据表，即使 evidence 完全没变。现在
session 变化只 `render_session`，evidence 变化只 `render_evidence`。

### 24.7 跨 workflow 信号

```text
refused                          → 窗口显示 dialog
runtime_event_requested          → 窗口写入 runtime event store
minute_status_refresh_requested  → 窗口刷新 session 的分钟证据状态
focus_requested                  → 窗口 shell.navigate_to("research") +
                                   research_page.set_active_workspace(TARGETED)
```

前三条让 capability 不持有 dialog / event store / minute-status ownership。
第四条是刻意的分层：完成一次 robustness suite 后，capability 自己把**页面的**
evidence workspace 切到 REVIEW（这是该 capability 的 presentation behavior），
而「整个桌面路由是否跳转」属于 shell composition，所以发信号让窗口决定。
capability 不 import `DesktopShellV2` / `ResearchPage` / `ResearchWorkspace`。

`restore_saved()` 与普通 session refresh 都不发这些信号。

### 24.8 启动

`_load_local_state()` 里手写的七个 loader 全部退休，改为：

```python
self.targeted_evidence_orchestrator.restore_saved()   # 一次读全部七类
self.targeted_session_orchestrator.render_current()   # session 自己的首绘
```

evidence 恰好绘制一次（constructor 不绘制）；startup **不**自动选 latest
robustness / review——与既有行为一致，本轮不改变。session 的首次 render 独立
计算，不计入 evidence render。

### 24.9 冻结范围

研究算法（`run_targeted_*` 的计算、阈值、数据过滤、成本、统计方法）、
artifact schema（`run_id` / `strategy_version_id` / `parameter_hash` /
`data_hash` / `provider` / `evidence_grade` / `status` / review gates）与
precision（Decimal、returns、commissions、slippage、execution stress、
PBO/DSR、Newey-West、walk-forward partitions）全部未改。

Shadow（engine / start-stop / stream ingestion）完全冻结，本轮不 import、
不迁移，属 v2O-D（**后来已在 §26 完成**）。Target symbol、minute-status、preflight、strategy-selection
的 ownership 属 C5B（见 §25）。

## 25. v2O-C5B：Targeted Session + Preflight 提取

### 25.1 为什么这一刀不能顺手把 Shadow 一起迁

C5B 的页面确实显示 Shadow 的 cards / positions / fills，但**显示**不是**拥有**。
三类问题在这一刀之后必须保持分离：

```text
TargetedEvidenceOrchestrator  研究证据：这套策略有什么证据？
TargetedSessionOrchestrator   会话/准入：当前 target 能不能启动内部仿真？
MainWindow（未来 ShadowOrchestrator）  Shadow runtime：仿真实际在跑什么？
```

所以 `TargetedSessionOrchestrator` 的构造参数里有
`shadow_snapshot_provider`，但没有 `shadow_engine`；它在 `render_current()` 里
**每次绘制时读取**快照，从不保存。`_start_shadow` / `_stop_shadow` /
`shadow_engine` / `shadow_snapshot` / `shadow_store` / `ShadowWorkflow` 全部留在
`MainWindow`，属 v2O-D。（**该轮后来已完成：见 §26。** 前四项已迁入
`ShadowOrchestrator`，`shadow_store` 与 `shadow_workflow` 作为 composition fact 留在
窗口。）architecture test 反向锁定：
`TargetedSessionSnapshot` 的字段集恰好是
`{target_draft, target_status, minute_status, preflight}`，且 capability 的源码里
不出现 `shadow_engine` / `set_shadow_snapshot`。

名字也据此选定：**不是** `ShadowSessionOrchestrator`，因为它不拥有 Shadow
session；这里的 session 指 Targeted 工作区的**呈现会话**。

### 25.2 canonical truth 与为什么 snapshot 公开

```python
@dataclass(frozen=True, slots=True)
class TargetedSessionSnapshot:
    target_draft: str = ""
    target_status: str = "未指定"
    minute_status: str = DEFAULT_MINUTE_STATUS
    preflight: TargetPreflightResult | None = None
```

canonical Desktop truth 是 `TargetedSessionOrchestrator._snapshot`，公开为
`snapshot` property。本刀有两个**真实**外部消费者：

```text
Targeted Evidence  → 需要当前 target draft（作为 Replay/Robustness 的 symbol）
Shadow start       → 需要当前 target
```

因此允许一个 immutable snapshot。**禁止**四个独立 accessor
（`target_draft()` / `target_status()` / `minute_status()` / `preflight()`）：
四次读取就是四次机会读在不同时刻并互相矛盾。architecture test 断言这四个名字
都不作为方法存在，且 `snapshot` 是 property。

`broker_orders_available` 在 service 里硬编码为 `False` 且不是参数：
Research Targeted 没有券商下单通路。`TargetPreflightResult.shadow_ready` 只表示
**内部影子仿真就绪**，不等于任何券商订单授权（见 `TRADING_ARCHITECTURE_V2.md`）。

### 25.3 target_draft 的语义

不叫 `applied_target` / `active_target` / `trading_target`。当前 UI 行为是：

```text
用户输入 AAPL（还没点“应用标的”）
→ Replay / Robustness 已经会读到 AAPL
```

这是 **target draft / 当前归一化输入**，不是已通过 preflight 的 target。C5A 的
Evidence target provider 因此从 `page.target_symbol` 改为：

```python
lambda: self.targeted_session_orchestrator.snapshot.target_draft
```

后半句同样重要：只输入代码、没点 Apply，Replay / Robustness 仍然能读到它 ——
这是既有行为，本轮保持。

### 25.4 draft signal 的边界

`TargetedControls` / `TargetedValidationPage` 新增 presentation intent：

```python
target_draft_changed = Signal(str)     # QLineEdit.textChanged → strip + upper
```

它**只能**接 `adopt_target_draft`，做一次赋值。禁止触发 preflight / 读 minute
store / 改 Market subscription / 启动 Market / 写 runtime event / 跑 Evidence /
重绘整页 —— 打一个字母不能触发业务 fan-out。wiring guard 断言这个 signal 的
receiver 列表恰好是 `["session.adopt_target_draft"]`。

`set_target_symbol(...)` 必须是 silent：`blockSignals(True)` + `try/finally`，
否则形成 orchestrator → page → orchestrator 回环，并且会让 widget 反向覆盖它刚
被告知要显示的 canonical draft。

### 25.5 Apply 与 Subscribe 的顺序（冻结）

`request_target_apply(symbol)`：

```text
1. normalize（strip + upper）
2. regex 校验，anchor 两端：r"[A-Z][A-Z0-9.-]{0,9}"
   不合法 → REFUSAL_WARNING，不改任何状态
3. 读当前 ShadowSnapshot
4. Shadow active 且要换标的：
     REFUSAL_INFORMATION + 「请先停止当前影子会话」
     page 恢复为 shadow.target_symbol
     canonical draft 同步恢复为同一 symbol
     return（不写 subscription，不跑 preflight，不 log）
5. commit draft
6. page 静默反映 normalized symbol
7. 由当前 Universe 计算 target status
8. Market 未 live 时预置 subscription=(symbol,)；**不启动** Market
9. refresh minute status
10. refresh preflight
11. log「当前指定做 T 标的已切换为 ...」
```

`request_target_subscribe(symbol)`：

```text
1. normalize + regex，不合法 → REFUSAL_WARNING
2. Market 已 live → REFUSAL_INFORMATION，return
3. canonical draft = symbol；page 静默反映
4. Market subscription = (symbol,)，note="针对性日内 T：{symbol}"
5. log 既有文案
6. refresh minute status
7. refresh preflight          ← 必须在 start 之前
8. request Market start
```

四条 safety 语义逐字保持：

```text
active Shadow → 拒绝切换标的（page 与 canonical draft 都回到引擎真正在做的）
Market live   → 拒绝改 subscription
apply         → 只预置 subscription，不启动行情
subscribe     → preflight 先于 market start
```

Subscribe 不重算 target status：订阅一个 symbol 从来没有改变 Universe 对它的
判断，只有「应用标的」会。step 7 之后第一份 market snapshot 会再次 refresh
preflight，所以面板会自己追上。

### 25.6 三类外部变化如何进入 Session

```text
Market snapshot    → refresh_preflight
Account portfolio  → refresh_preflight
Evidence replay 完成（minute_status_refresh_requested）
                   → refresh_minute_status
Shadow snapshot 变化 → render_current（只重绘）
Market subscription / start → 两条极窄的注入命令
```

`refresh_minute_status()` **不**自动重算 preflight，`refresh_preflight()` 也**不**
跑 Evidence。每个变化只刷新它真正影响的东西；架构守卫禁止
`refresh_all` / `refresh_session` 这类聚合方法。

Universe `snapshot_changed` 当前**并不**直接 refresh Targeted preflight，本轮不因为
「看起来应该」而新增 —— extraction ≠ product behavior redesign，记作未来产品决策。

### 25.7 Preflight 与 render 的边界

`refresh_preflight()` 每次读取**当前**的：target draft、Universe snapshot、
Market snapshot、Broker Account truth、MinuteQuote summary、选中的
StrategyVersion、exposure multiplier，然后交给
`DesktopTargetedSessionService.evaluate_preflight()`。quote lookup **没有 fallback**
（不换 symbol、不用 Scanner 价、不用 broker mark）：没有 quote 就让 gate 失败。

资金链路保持 Decimal → Decimal：

```text
account.net_liquidation    → Decimal，不 float()
exposure_multiplier        → Decimal，不 float()
```

Paper account gate 用 `BrokerAccountApplication.portfolio.account`（券商真值），
**不**使用 `ResearchScenarioCapital`。两者语义严格分离：

```text
Targeted Evidence Replay/Robustness → ResearchScenarioCapital（研究情景资金）
Targeted Preflight whole-share 容量 → fresh Paper account truth（券商真值）
```

Preflight 是 **derived fact**：只有 evaluator 返回了才 commit。失败时**异常继续传播**
（fail loudly），`snapshot.preflight` 保持不动且**不 repaint** —— 面板继续显示上一次
verdict，同时 caller / log / 任何 runtime-event 边界都能看到这次刷新失败了。

这一条是**继承**而非新选择：base 的 `_refresh_target_preflight()` 没有 try/except，
任何 Universe / Market / Account / provider / store / evaluator 异常都会显式暴露。
在 capability 里写 `except Exception: return` 不是 extraction，而是错误语义变化，而且
是更危险的那种：操作员会把**过期的 verdict 继续当成当前的**读，而它携带的 gate
（Paper account freshness、行情 freshness、whole-share 容量）恰恰是刷新失败后绝不能
看起来仍然有效的那几个。吸收异常属于 composition / error boundary 的职责，不是
capability 的。

`render_current()` 每次绘制时通过 provider 读 Shadow snapshot，然后
`session_view(...)` + `page.render_session(view)`。它**只**画 session 一半；
evidence 七张表有自己的 owner 和入口，所以一次 market tick 不会重建它们。
`render_session` 的 production caller 现在恰好只有一个（capability），
`render_evidence` 的也恰好只有一个（evidence capability）。

### 25.8 新增文件与预算

```text
desktop_v2/orchestration/research/targeted/session/
    __init__.py      包文档：命名理由与完整规则集
    models.py        immutable snapshot + 默认文本 + refusal severity + 操作员文案
    queries.py       纯规则：normalize / regex 门 / Universe 与 quote 查找 /
                     两条 status 文本 / controls 投影
    orchestrator.py  命令、顺序、refresh、render owner
desktop_targeted_session_service.py   Qt-free：MinuteQuoteStore + preflight evaluator
```

预算：`orchestrator.py` ≤ 320、`models.py` ≤ 140、`queries.py` ≤ 200、
`__init__.py` ≤ 60、service ≤ 180。

`queries.py` 里只放 Qt-free 纯规则，不放 store 读、Market 命令、preflight 执行、
page 调用或 Shadow runtime。symbol regex 完全冻结为
`r"[A-Z][A-Z0-9.-]{0,9}"`，不替换成另一个 validator。

`models.py` 只放 snapshot 与文本；**不允许**出现 `shadow_snapshot` /
`shadow_active` / `positions` / `fills` —— 那些不是 Targeted Session truth。

### 25.9 MainWindow 退休清单

删除的状态（禁止 compatibility property / alias）：

```text
_target_status
_minute_status
target_preflight_result
```

删除的方法：

```text
_current_target_symbol
_target_symbol_requested
_target_subscribe_requested
_apply_target_symbol
_sync_targeted_symbol_to_stream
_refresh_minute_data_status
_refresh_target_preflight
_targeted_controls
_publish_targeted_session_view
_shadow_strategy_selection_changed
```

保留：

```text
_start_shadow / _stop_shadow / _selected_shadow_strategy_record   （v2O-D）
_on_market_snapshot_changed / _on_account_portfolio_changed
_configured_exposure_multipliers / _record_runtime_strategy_selection
_report_targeted_evidence_refusal / _record_targeted_evidence_runtime_event
_focus_targeted_evidence
所有 Paper / execution bridge
```

新增的四个 **composition helper**（只回答「这个窗口现在能看到的某个事实当前值」，
不做任何决定）：

```text
_targeted_account_snapshot       → account_orchestrator.portfolio.account
_targeted_displayed_strategy     → strategy_selection.restore_or_default(...)
_targeted_strategy_options       → strategy_selection.options(...) + label 投影
_targeted_strategy_selected      → strategy_selection.select(...)
```

以及一个 refusal bridge：

```python
def _report_targeted_session_refusal(self, level, title, message) -> None:
    if level == REFUSAL_INFORMATION:
        QMessageBox.information(self, title, message)
    else:
        QMessageBox.warning(self, title, message)
```

severity 必须保留：invalid symbol = warning，active Shadow target switch =
information，subscribe while Market live = information。

`_populate_strategy_selection_combos()` 不再自己画 Targeted combo，改为调用
`targeted_session_orchestrator.refresh_strategy_options()`；capability 成为
`TargetedValidationPage.set_strategy_options` 的唯一 production caller。
Targeted 不 import StrategyPage：label 投影留在 window 的
`_targeted_strategy_options` helper 里。

strategy selection 的 canonical owner 仍是 `StrategySelectionService`：signal 已经
携带 `version_id`，capability 直接用 signal argument，禁止回头读
`page.selected_strategy_version_id()`；也**不**缓存 StrategyVersion。

### 25.10 页面 wiring 的最终形状

```text
Page
 ├─ target_draft_changed       → Session.adopt_target_draft
 ├─ strategy_selected          → Session.request_strategy_selection
 ├─ target_apply_requested     → Session.request_target_apply
 ├─ target_subscribe_requested → Session.request_target_subscribe
 ├─ replay_requested           → Evidence.request_replay
 ├─ robustness_requested       → Evidence.request_robustness
 ├─ robustness_run_selected    → Evidence.select_robustness_run
 ├─ review_run_selected        → Evidence.select_review_run
 ├─ shadow_start_requested     → ShadowOrchestrator.start     （v2O-D）
 └─ shadow_stop_requested      → ShadowOrchestrator.stop      （v2O-D）
```

C5B 当时把 Shadow 两个 intent 留在 MainWindow，因为把它们接到 Session 就是把引擎
交给一个不该拥有它的对象。v2O-D 落成后它们改接 `ShadowOrchestrator`——仍然**不**接
Session，理由不变。Session 与 Evidence 互不 import，唯一共享事实是
`Session.snapshot.target_draft`，通过 provider 传递；Shadow 也通过 provider 读它，
同样不 import。

### 25.11 启动

```python
self.targeted_evidence_orchestrator.restore_saved()   # 一次读全部七类
self.targeted_session_orchestrator.render_current()   # session 恰好首绘一次
```

startup 时：`preflight is None`、draft 为空、target status 为「未指定」、minute
status 为默认文案；**不**自动跑一次空 symbol 的 preflight。

### 25.12 冻结范围与 mutation 要求

本轮**不**修改：Shadow 引擎 / store / 算法 / fill math / 手续费与滑点、Paper
workflow、ExecutionApplication、RiskApplication、TradingRuntime、Targeted 研究
算法（Replay / Robustness / Walk-forward / PBO / DSR / 数据质量 / 执行压力 /
Review）、`evaluate_target_preflight` 算法（symbol format gate、Universe identity、
STK/ETF、`eligible_for_research`、StrategyStatus 规则、fresh realtime quote、
Paper account truth、300 秒账龄、whole-share sizing、commission、slippage、
exposure multiplier、minute evidence、broker route disabled、decision strings）。

mutation 必须 RED 的关键项（不建框架，手工验证）：恢复 `_target_status` /
`_minute_status` / `target_preflight_result`；MainWindow 重新调用
`page.render_session`；MainWindow 重新把 `page.target_symbol` 当 canonical truth；
Evidence 重新读 `page.target_symbol`；Session import `MarketOrchestrator` /
`AccountOrchestrator` / `UniverseOrchestrator` / `ShadowPaperEngine`；snapshot
增加 `shadow_snapshot`；typing 每个字符触发 preflight；invalid target 被 Apply；
active Shadow 时允许切 target；Market live 时 Subscribe 仍改 subscription；
`broker_orders_available` 改 True；strategy selection 忽略 signal version_id 改读
combo；minute refresh 自动重算 preflight；Market snapshot 不再 refresh preflight；
Account change 不再 refresh preflight；Shadow snapshot change 不再 render session；
MainWindow 继续 `set_strategy_options`；programmatic `set_target_symbol` 产生 signal
loop；Shadow start/stop 被迁入 Session；Evidence render 被 Session refresh 触发。




## 26. v2O-D：Shadow orchestration 提取

### 26.1 这一刀移走什么

Shadow runtime 原本是 MainWindow 上的一组散落属性，被六个地方读写：

```text
_start_shadow / _stop_shadow       两个页面 intent
_on_market_snapshot_changed        stream ingress + session repaint
_stop_market_data                  Paper/Shadow 停止 interlock
_start_auto_quant                  Paper 启动前的互斥门
closeEvent                         关闭时停止
_export_terminal_state             shadow_store.recent_fills(500)
```

全部收口到 `desktop_v2/orchestration/shadow/`：

```text
models.py        冻结事实、拒绝文案、ShadowLease 协议。Qt-free
queries.py       纯规则：十道启动门、symbol 校验、capital source 文案。Qt-free、无 I/O
orchestrator.py  sequencing：读一次事实 → 过门 → 建引擎 → 启动 → 喂快照 → 发布
```

### 26.2 单一 truth

`ShadowPaperEngine` 仍然是 session id / active / cash / PnL / position / fills /
marks 的唯一 owner。`ShadowOrchestrator` 只持有**引擎引用**和**引擎最后产出的
snapshot**，`snapshot` property 读它，`is_active` 问引擎。它不算 PnL、不模拟 fill、
不写 store。`_store` 只通过 `recent_fills()` 委托给终端导出。

**`_holds_lease` 不违反这条规则。** orchestrator 另有一个 capability-local 布尔
`_holds_lease`，回答的是另一个问题：**"本 capability 是否取得了共享执行租约"**。
共享 `ExecutionLeaseManager` 是 Shadow 与 Paper 共用的，`lease.active` 在任何一方持有
时都为真，无法表达 ownership —— 需要判断"该不该释放"时只能由本地记录回答。它**不**
镜像 `engine.active`、**不**镜像任何交易或 session 状态，所以不是第二份交易真相。

这条边界是安全要求而非风格，而且是踩过两次坑才定下来的：

```text
重复 start()         → 失败路径释放了"正在运行"会话的租约
                       （第二个 engine 被 lease 拒绝后，旧代码释放了第一个的租约）
stop() / shutdown()  → Shadow 已停、Paper 之后取得 lease 时，释放了 Paper 的租约
```

两处都会让 "Shadow XOR Paper" 静默失效。现在 `self._lease.stop()` 全文件**只有一处**
（`_release_lease()` 内），由 `_holds_lease` 把关。**禁止改回 `self._lease.active`。**

**重复启动是 no-op（安全不变量）。** `start()` 最前面有 own-active gate：已 active 时
不读任何 provider、不构造第二个 engine、不碰 lease、不改 snapshot、不丢 `_engine`。
这是本 capability 自己的生命周期完整性，不是 Paper 逻辑，也不需要改 Shadow core。

### 26.3 依赖注入

12 个显式依赖，全部是 callable 而非对象句柄：

```python
ShadowOrchestrator(
    store=..., lease=...,                        # 引擎的东西
    strategy_provider=..., target_provider=...,  # 输入事实
    capital_provider=..., account_alias_provider=...,
    market_stream_provider=..., market_is_live=...,
    universe_provider=..., runtime_is_active=...,
    exposure_multipliers_provider=...,
    render_session=...,                          # 唯一输出动作
)
```

所以这个 package **不** import Market / Account / Research 的 orchestrator，也不
import 它们所在的页面。`lease` 是 `WorkflowController` 组合出的**共享**
`ExecutionLeaseManager` 句柄，Paper 拿到的是同一个，所以"Shadow 与 Paper 不能同时
持有执行权"仍是结构性的；orchestrator 通过 `ShadowLease` Protocol 拿它，从不
import `desktop_v2/workflows.py`（那个模块里坐着 `PaperWorkflowController`）。

### 26.4 一个容易改错的行为细节

`_start_shadow` 里资金**金额**和资金**来源**是在不同时刻读的：金额在资金门读，
来源（account alias）只在真正构建 engine 时才读。因此一个被拒绝的启动**从不**
触碰 portfolio。提取时把两者合并成一个 eager read 会改变这个行为——我第一版就是
这么写的，`tests/test_desktop_v2_targeted_wiring.py` 里三条 `test_shadow_start_rejects_*`
当场变红。现在由 `test_a_refused_start_never_reads_the_account_alias` 正向锁定（断言
拒绝路径下 alias provider 的调用列表为空）。所以分成
`_shadow_capital_fact()`（门）与 `_shadow_account_alias()`（构建）两个 provider。

### 26.5 三层 shutdown 语义

```text
stop()      操作员停止：停引擎 + 释放 lease + 重绘 + 记 SHADOW_STOP 事件
shutdown()  关闭时：只停引擎 + 释放 lease。不重绘、不记录、不改 snapshot
start()     已 active 时 no-op（不读 provider、不建 engine、不碰 lease）
on_market_snapshot()  无运行时时是 no-op
```

三条路径的释放**都**经由 `_release_lease()`，且只在 Shadow 自己持有租约时生效
（见 §26.2）。这不是统一风格的整理：`stop()` 会保留 `_engine` 以便发布引擎最后一个
snapshot，因此"Shadow 已停 + Paper 之后取得 lease + 再次 stop()"是可达状态，内联
`lease.active` 判断在这里会误释放 Paper 的租约。

`shutdown()` 比 `stop()` 安静是刻意的：关闭时没有窗口可画，且 runtime teardown
自己会报告；如果它写一条"已停止"事件，就会把操作员从未停止的会话记录成停止。
snapshot 也不重新赋值——退休的 `closeEvent` 就是 `engine.stop()` 后丢弃返回值。

### 26.6 MainWindow 剩下什么

```python
self.shadow_orchestrator = ShadowOrchestrator(...)   # 构造
self.shadow_orchestrator.refused.connect(...)        # 接线
self.shadow_orchestrator.log_requested.connect(...)
self.shadow_orchestrator.runtime_event_requested.connect(...)
page.shadow_start_requested.connect(self.shadow_orchestrator.start)
page.shadow_stop_requested.connect(self.shadow_orchestrator.stop)
```

加上五个 composition helper（`_shadow_capital_fact`、`_shadow_account_alias`、
`_report_shadow_refusal`、`_record_shadow_runtime_event`、
`_paper_runtime_is_active`）和两处 cross-capability interlock 读取
（`_stop_market_data` 的停止、`_start_auto_quant` 的互斥门）。

仍留在窗口的两个 Shadow 属性都不是 runtime truth：`shadow_store` 是终端导出经
capability 读取的持久化，`shadow_workflow` 是与 Paper 共享的执行租约句柄。
`_selected_shadow_strategy_record` 是 strategy selection 的 composition 读取，
不是 Shadow runtime。

### 26.7 冻结范围

Shadow 引擎 / trade_logic / store / models / 算法 / fill math / 手续费与滑点**完全未改**；
`_money` 的语义（含 `不可用` 与无 `+` 号）在 `queries.format_money` 里逐字保留；
十道门的**顺序**与每道门的**文案**逐字保留；Paper workflow 生命周期一行未动。

**唯一一处刻意不"逐字保留"的行为：** 退休 `_start_shadow` 的重复启动后果没有迁移。
旧行为下第二次启动会构造第二个 engine、被 lease 拒绝，然后在失败路径释放**第一个**
run 的租约，使一个仍在运行的会话失去共享租约。这是安全漏洞而非产品语义，原样迁移
（再补一个保护它的测试）会把漏洞固化进新的 canonical owner。现在的契约是
`active → no-op`，见 §26.2。

mutation 必须 RED 的关键项（手工验证）：把 `shadow_engine` / `shadow_snapshot`
加回 MainWindow；恢复 `_start_shadow` / `_stop_shadow`；让 Shadow package import
MarketOrchestrator / AccountOrchestrator / ResearchOrchestrator / PaperWorkflowController；
让 `orchestrator.py` 摸 widget（`QtWidgets`）；把 `shutdown()` 换成会记录事件的
`stop()`；把金额与来源合并成一次 eager read；让 `on_market_snapshot` 在无运行时
仍然重绘。

两条**最重要**的安全 invariant（本轮由 review 发现，必须有 guard）：

```text
duplicate start      → 只能有一个 engine、一个 lease；不改 snapshot、不丢 _engine
                       删掉 start() 的 own-active gate 必须 RED
Shadow never releases a lease it did not acquire
                       把 _release_lease 改回 if self._lease.active 必须 RED
                       （同时覆盖 stop() / shutdown() 在 Paper 持租约时的误释放）
```

## 27. v2O-E1：Paper launch orchestration 提取

### 27.1 这一刀移走什么

v2O-D 之后，Paper 的**启动链**还是 `MainWindow` 上的一对 handler 加四个只服务它们的
helper：`_start_auto_quant`（读 preflight、冻结 plan、`begin_connecting`、提交 broker
task）、`_auto_order_service_connected`（过期判定、二次 preflight、identity 复验、校验
券商读数、建 runtime、arm、ensure、publish、promote），以及
`_reject_unpublished_auto_candidate` / `_reject_auto_launch_without_service` /
`_current_auto_launch_matches` / `_reset_auto_launch_controls`。它们整体迁入
`desktop_v2/orchestration/paper/`。

**本轮不迁**（属 v2O-E2/E3/E4）：RUNNING 之后的 pause / resume / stop、HALT recovery、
manual reconciliation、finalization、`closeEvent` teardown、execution page 的 session
渲染。

### 27.2 单一 truth：phase 取代 plan 镜像

窗口曾同时持有 `_active_auto_launch_plan`，而 `PaperWorkflowController` 也有
`active_plan`——两份 launch identity truth。本轮删掉窗口那一份，且**不留 compatibility
property**：留 property 会让每个未迁移的调用点继续工作，于是"谁拥有 launch"就不再是
一次 grep 能回答的问题。

判据改为 workflow 自己的 phase。两者在启动期完全等价：

```text
_active_auto_launch_plan is not None
    ==  phase is PaperWorkflowPhase.CONNECTING
```

因为 `begin_connecting` 是进入 `CONNECTING` 的唯一入口，两条退出路径（reject / publish）
都会清掉 plan。差别在**publish 之后**：那时 plan 合法地跨越 attempt 存续进 `RUNNING`，
而 attempt 已经结束——只有 phase 仍能正确回答"还有 attempt 在飞吗"。窗口的
`_launch_locked` 与确认门因此都读 `paper_trading.phase()`。

`_next_auto_launch_attempt` 是另一回事：attempt 序号属 desktop orchestration
bookkeeping，不是交易或 session truth，所以它迁进 `PaperOrchestrator`，并**保持整数递增
语义**，不改 UUID——那只会改变 candidate id 在线上的形状而没有任何收益。

### 27.3 顺序即安全

`start()` 的门序是退休 handler 的，且**重复门必须第一**：等 candidate 连上才发现重复，
就会已经建了第二个 candidate、起了第二个 broker task、并用第二次 `begin_connecting`
覆盖在飞的 plan。

```text
duplicate gate   →  phase is CONNECTING ⇒ 拒绝（且**不**清 arm_confirmed，attempt 仍在飞）
shadow active    →  清 arm_confirmed + 拒绝
first preflight  →  清 arm_confirmed + 拒绝（保留逐条 bullet 文案）
freeze plan      →  读一次：attempt_id / strategy / candidates / capital limit / order channel
begin_connecting →  workflow 取得 PAPER lease
render + submit  →  resource_group="broker"；未被接纳则 reject_connecting + 还控件
```

`freeze plan` 必须在 `begin_connecting` **之前**、`begin_connecting` 必须在 broker
connect **之前**：Shadow / Paper 共享一个执行租约，所以这个顺序**就是**结构性互斥，
不是 UI gate。

### 27.4 arm / reserve / publish / commit 的硬顺序

```text
candidate_service(candidate_id)     借用；只存在于本 callback 调用栈
→ validate_broker_state             净值非空且 > 0；positions 为空；cash 非空（Decimal）
→ build_session                     组合根建 config / risk / execution / runtime，start()，返回 sizing
→ service.arm(...)                  本模块自己的调用，不藏在 seam 里（见 27.9(3)）
→ reserve_candidate_promotion       装入 active **并锁槽**：ownership 在此取得
→ publish_armed
→ commit_candidate_promotion        结束本次启动的占用；它不再移动任何东西
```

`reserve` 早于 `publish_armed` 是刻意的，而且是**结构性**的：owner 在发布之前就已经存在，
所以「已发布的 session 没有 owner」不是一个窗口有多短的问题，而是**不存在这样一个顺序**。
早期版本在发布前只做 `ensure_candidate_can_promote`（纯检查，零变更），槽位在发布期间仍是
空的；发布后 promotion 一旦被拒，留下的是 `RUNNING` + coordinator 持着已武装通道 + 无人
接管，而所有 recovery 路径都从 `has_order_service()` 起步、于是全部直接 return。详见 27.11。

promote 不再最后，是因为它被 `commit` 取代：**broker connect 成功 ≠ session 已获信任**
这条约束没有变，只是「取得信任」的时点必须早于 `publish_armed`——否则一个正在运行的会话
可能在没有任何 owner 的情况下存在。publication 失败时由 `cancel_candidate_promotion` 把
槽位收回，走 27.9(2) 描述的原有 rollback，因此提前取得 ownership 不会留下残留。

### 27.5 stale callback 的完整保护

异步 callback 到达后依次判断：结构合法性（非法则 **raise**，不吞）、connect error（只
结束本方 attempt）、stale plan（只 discard 自己的 candidate、只 reject 自己的 plan）、
二次 preflight、identity 复验。`reject_connecting` 返回 `False` 即"这是过期
callback"，此时连 presentation 都不动：不清更新的 attempt 的 arm_confirmed、不重绘它
的控件、不为它弹窗。log 行不 gate——操作员仍应看到"过期结果已忽略"。

### 27.6 新增文件与职责

```text
__init__.py      只导出 PaperOrchestrator
models.py        冻结 shape + 两个协作方 Protocol + 逐字 operator 文案
queries.py       启动门 / 冻结 plan / identity 比对 / 券商门（纯规则，Qt-free，无 I/O）
orchestrator.py  只做 sequencing
```

**本轮的架构要求不是行数。** 早先版本在这里写了逐文件行数上限（180 / 197 / 450），
并在测试里做成阻断性的 Guard G；随后又退成一条"导航阈值"（800 行）。**两者都已彻底删除**，
因为换名字不改变性质——那仍然是一个会让 CI 失败的强制行数上限。把上限钉在文件当时恰好多长，
只会在一次良性改动上失败、在一次恶性改动上通过。

现在断言的是**职责单一、ownership 明确、依赖方向稳定、无重复 truth / context bag /
god object、关键安全顺序有测试锁住**——每一条都由一个直接命名该性质的 guard 断言。
`tests/test_desktop_paper_orchestration_architecture.py` 里不存在任何 LOC 断言。

### 27.7 MainWindow 退休清单

```text
删除（无 shim、无 forwarding property）
    _start_auto_quant
    _auto_order_service_connected
    _reject_unpublished_auto_candidate
    _reject_auto_launch_without_service
    _current_auto_launch_matches
    _reset_auto_launch_controls
    self._active_auto_launch_plan
    self._next_auto_launch_attempt

保留（composition + presentation）
    _confirm_and_start_auto_quant     操作员确认（QMessageBox 不能进 capability）
    _auto_quant_order_channel         构造 IBKRConnectionConfig（组合根的事）
    _build_paper_session              窄 session-build seam
    _on_paper_session_published       接 session_published，沿用既有渲染
    _report_paper_launch_refusal      弹窗
    _record_paper_launch_event        事件落库
```

### 27.8 冻结范围

`trading/runtime/*`、broker adapter、IBKR callback/gateway、Paper journal/schema、
Shadow core **一行未改**。

`trading/application/paper/{models,service,__init__}.py` 在第四轮 review 后**有改动**，
而且是有意为之：本轮发现的安全缺陷（27.11）恰恰是「candidate 与 active 之间缺少原子
ownership 语义」，而这两个槽位本来就是 `PaperTradingService` 的 canonical ownership。
把 reservation 放到 orchestrator 里会再造第二份 lifecycle truth 并让 owner 变成两个，
所以缺口补在拥有者身上：新增 `reserve/commit/cancel_candidate_promotion` 与
`PaperPromotionReservation`，替换 `ensure_candidate_can_promote` / `promote_candidate`。
没有新增任何交易语义——不下单、不算风险、不改对账与终局化。

### 27.9 review 后补的三项修复

首轮评审发现三个问题，都在本分支修掉，并各配一条 regression。

**（1）冻结的 request 并没有真正冻结 strategy parameters。** `freeze_launch()` 原本把整个
`strategy` 原对象塞进 request。`StrategyVersion` 虽然是 `@dataclass(frozen=True)`，但它的
`__post_init__` 明确执行 `object.__setattr__(self, "parameters", dict(self.parameters))`
——`parameters` 仍是**可变 dict**；而 `parameter_hash` 只是 `identity.parameter_hash` 的
投影，**从不重算**。于是异步连接期间一次原地修改（`strategy.parameters["x"] = ...`）不会
改变 hash，第二次 `current_inputs_match()` 仍返回 true，随后 `_build_paper_session()` 却
用上了已改变的参数：**plan 记 hash A，实际运行参数 B**，immutable launch identity 被静默
破坏。

修法是新增 `PaperStrategyLaunchFact`：只带 identity / version_id / hash 与一份
**deepcopy** 后的 parameters，不再保存 live `StrategyVersion`。deepcopy 而非结构冻结，是因为
`validate_strategy_parameters` 要求整数列表是真正的 `list`，改成 tuple 会拒掉合法目录项。
同时用 domain 自己的 `parameter_hash_for()` 校验快照参数确实等于 governed hash；不一致时
抛 `PaperLaunchIntegrityError` 并**拒绝启动**（不是以旧 hash 跑新参数）。callback 上的
identity 门把该错误转成 mismatch 而非异常——在 Qt slot 里抛会逃逸出槽、把 attempt 卡在
`CONNECTING` 持有租约，而 mismatch 走正常 rejection 释放 PAPER。两条路径都 fail closed。

**（2）`publish_armed()` 成功但 `promote_candidate()` 失败时的回滚是错的。** 原本
build/arm/ensure/publish/**promote** 全在同一个 `try`，任何异常都进
`_discard_candidate()`。但 `publish_armed()` 一返回，workflow 已经是 `RUNNING` 且
coordinator 已发布：`reject_connecting()` 此时是 no-op，丢弃 candidate 会让一个**正在运行**
的会话没有 owner，释放 PAPER 则会让 Shadow/Paper 互斥失效。结果可能留下
`RUNNING` + 持有租约 + 已发布 coordinator + candidate 已丢弃 + 无人接管 的 split-brain。

修法是把 **publication 前的 rollback** 与 **publication 后的不变量失败**分开，边界正好是
"`publish_armed` 是否返回"：它抛异常发生在 `RUNNING` 转换**之前**，所以到它抛为止都仍是
rollback（丢弃 candidate、reject plan、释放 PAPER）；它返回之后 promotion 再抛，走
`_fail_after_publication()`——**不做任何回滚**，candidate / PAPER 租约 / 已发布 workflow
原状保留，并以独立 code `PAPER_PROMOTION_INVARIANT` 在 error 级别报出来。静默清理只会把
真实缺陷伪装成一次看起来合理的启动失败。

**这一版修法在第四轮 review 被判定仍然不足**：它只保证了「不会错误回滚」，没有保证
「不存在无 owner 的已发布会话」。当时写在这里的长期方案——在 order-service owner 上做
promotion 的 reserve/commit 两阶段提交——已经落地，见 27.11。

**（3）`service.arm()` 原本还留在 MainWindow。** `_build_paper_session()` 不只做
composition，它还执行了 `runtime.start()` **和** `service.arm(...)`，所以 orchestrator 只
看到 `_build_session → ensure → publish → promote`；名义上拥有 "build → arm → ensure →
publish → promote" 的它，实际看不到 arm 那一步，architecture test 也只能断言
`_build_session < ensure < publish`，真正的 `arm < ensure < publish < promote` 锁不住。
既然本轮的标题就是 **Launch / Arm orchestration**，这里已收口：build seam 只做 composition
加 `runtime.start()`，返回 `session_id / runtime / max_order_notional`；`arm` 由
`PaperOrchestrator._arm_and_publish()` 明确执行。现在完整顺序可以被 AST guard 与 mutation
test 直接锁死，MainWindow 也真正退出了 Paper launch 的 mutation。

**（4）`freeze_launch()` 的完整性错误从 Qt slot 逃逸。** 加上 (1) 的
`PaperLaunchIntegrityError` 之后，`freeze_launch()` 在 `start()` 里仍是**无保护调用**。
交易上是 fail closed（不 bind plan、不取 lease、不建 candidate、不下单），但 UI 不是：
操作员确认步骤已经把 `arm_confirmed` 置为 True，异常却直接从一个 Qt slot 抛出，于是既没有
refused signal、也没有 log、也没有 runtime event，用户看到的是"已武装但什么都没发生"的
假空闲状态，而 traceback 打在 stderr 上。

修法：**按名字**捕获 `PaperLaunchIntegrityError`（不是 broad `except Exception`），清掉
`arm_confirmed`、写一条 `severity="error"`、`code="PAPER_STRATEGY_INTEGRITY_FAILED"` 的
runtime event、并向操作员发一条 `refused`。既不掩盖真实目录缺陷，也不把它变成一次静默的
无效启动。对应 regression 从"期望异常逃出去"改成断言 phase 仍为 `READY`、未取得 lease、
无 candidate、无 task、`arm_confirmed` 已清、有 error event、有 operator refusal。

### 27.10 mutation 必须 RED

第一至第三轮手工验证。下面前四行的目标代码本轮未改动，因此数字仍然成立；promotion 相关的
四行引用了已被替换的 `promote_candidate`，已在 27.11 以新形式重测：

```text
删掉 duplicate gate                                → RED（4 failed）
把 stale plan 检查改成 if False                    → RED（2 failed）
删掉二次 preflight                                 → RED（2 failed）
把 arm 移到 reserve 之后                           → RED（4 failed）  ← 锚点变更，本轮重测
freeze 不做 deepcopy（参数不脱钩）                 → RED（2 failed）
不校验 governed hash                               → RED（2 failed）
```

promotion 升级为 reserve/commit 之后的完整重测（27.11）：

```text
去掉 reserve + commit（启动根本不晋升）            → RED（15 failed）
把 commit 提到 publish 之前                        → RED（14 failed）
publication 失败时不 cancel reservation            → RED（6 failed）
publish 后 commit 失败仍去 discard candidate       → RED（2 failed）
publish 后 commit 失败去 reject_connecting         → RED（2 failed）
publish 后 commit 失败去 clear_active              → RED（3 failed）
```

**reservation 收口之后（第五轮，27.11(6)）：**

```text
clear_active 不再理会 reservation                → RED（5 failed + 1 error）
commit 不再确认槽位仍被持有                       → RED（1 failed）
orchestrator 忽略 cancel 的返回值                 → RED（5 failed）
cancel 未证明已归还时仍完成回滚                    → RED（4 failed）
closeEvent 任由被拒的 clear 抛出                  → RED（1 failed + 2 errors）
```

完整性错误的报告路径同样有 mutation 覆盖：

```text
不按名字捕获（改成永不匹配的 except）      → RED（2 failed）
捕获但不清 arm_confirmed                   → RED（2 failed）
捕获但不写 runtime event                   → RED（2 failed）
捕获但不发 refused                         → RED（2 failed）
报告后仍继续往下 launch                    → RED（1 failed）
```

第一至第三轮的十五条已手工验证为 RED（单元 + wiring + 架构 guard 三层合计）。三项修复的
regression 分别是：参数脱钩与 hash 校验
`test_freeze_launch_detaches_parameters_from_the_live_version` /
`test_editing_the_live_parameters_during_the_connect_is_refused` /
`test_a_version_whose_hash_contradicts_its_parameters_is_refused`；arm 归属
`test_the_orchestrator_arms_the_channel_itself` /
`test_the_window_build_seam_does_not_arm_the_channel`；完整性报告
`test_an_inconsistent_catalogue_version_does_not_escape_the_qt_slot`。
publication 拆分的那两条 regression（`test_a_promotion_failure_after_publication_is_not_rolled_back`
/ `..._is_reported_as_an_invariant`）随 promotion 的换代被 27.11 的四条取代，语义更弱的那
一半（「不要错误回滚」）保留在 `test_a_commit_failure_after_publication_still_leaves_the_session_owned`。

### 27.11 第四轮 review：publication 与 promotion 之间的事务边界

**（1）`publish_armed()` 成功、`promote_candidate()` 失败时，会话仍在被驱动。**
27.9(2) 把这条路径判成「不回滚、保留原状」，看起来 fail closed，**实际不是**：
`publish_armed()` 已经把 workflow 推到 `RUNNING` 并建好 `PaperSessionCoordinator`，而
coordinator 手里的 `_orders` 就是那个已经 `arm()` 过的 candidate service。`MainWindow`
的两条驱动路径**只读 phase**：

```text
_on_market_snapshot_changed   phase ∈ {RUNNING, PAUSED, STOPPING} ⇒ paper_workflow.on_stream(snapshot)
_poll_auto_quant_orders       同理                      ⇒ paper_workflow.poll()
```

于是 promotion 失败之后，下一个行情 tick 仍会进入已发布的 coordinator，而它握着一条武装过的
通道——「Desktop 没正式接管，但已发布通道仍可被 workflow 驱动」。恢复链同样救不回来：
`has_order_service() == False`，于是 `_reconnect_auto_order_service()` 第一行就 return，
终局化也依赖 active order service，`clear_active()` 面对的是一个空槽。

在真实 desktop 路径上实测（offscreen `MainWindow` + 真 `PaperTradingService`，仅把 promotion
换成抛错）：`phase RUNNING`、`has_order_service False`、`lease PAPER` 仍持有、
`arm_confirmed True`、coordinator 已发布且 `coordinator._orders is <已 arm 的 fake>`、
`_poll_auto_quant_orders()` 确实调到了 `paper_workflow.poll()`，而
`begin_manual_reconciliation` 一次都没被进入。

**（2）修法：promotion 升级成 reservation / commit，由 `PaperTradingService` 拥有。**

```text
reserve_candidate_promotion(candidate_id)
    候选必须存在；槽位必须为空且无人占用
    ⇒ 把候选**装入 active**，并把槽位锁给这次启动，返回一张 reservation
publish_armed()
commit_candidate_promotion(reservation)      结束占用（结构上不会失败）
cancel_candidate_promotion(reservation)      publication 失败时把槽位收回
```

要点是 **ownership 在 `reserve` 时取得，而不是在 `commit`**。只做「纯检查 + 稍后提交」的话，
`RUNNING` 与「有 owner」之间仍存在一个瞬间；把它前移之后，`RUNNING ⇒ has_order_service()`
是顺序本身的推论，而不是一条需要靠 review 保证的纪律。`commit` 仍然不是流水账：占用不结束，
该 service 就永远无法再被 reserve——fail closed，但代价落在**下一个**启动被拒，而不是当前
会话无人接管。

**（3）为什么 `reserve` 之后不存在「已接管但未发布」的漏洞。** 两条理由都写进了代码注释，
也各有一条 guard 钉住：一是这段时间只存在于一次 Qt slot 调用栈内，而
`PaperWorkflowController`（含基类 `ManualReconciliation`）不是 `QObject`、phase 转换不发
任何信号，本模块在 `publish_armed` 与 `commit` 之间也不 emit 任何东西——由
`test_nothing_between_publication_and_the_commit_can_yield_control` 断言，而不是靠论证；
二是 publication 一旦失败，`cancel` **就是逆操作本身**，把 service 放回候选槽位，于是既有的
`discard_candidate` + `reject_connecting` 原样可用。`cancel` 因此刻意**不抛异常**：它跑在
rollback 里，抛出会跳过 rejection 并把 `CONNECTING` 永久卡在持有 PAPER 的状态——这条由 AST
guard（`test_the_rollback_gives_the_reservation_back_before_disposing`）断言。

**（4）本轮 regression。**

```text
真实路径（真 MainWindow + 真 PaperTradingService，只 fake broker 与 worker）
    test_every_pre_publication_failure_rolls_back_and_leaves_no_owner   5 个注入点
    test_a_commit_failure_after_publication_cannot_orphan_the_session
    test_a_publication_failure_releases_the_promotion_for_the_next_launch
能力层（fake）
    test_a_publish_failure_gives_the_reservation_back_and_rolls_back
    test_a_commit_failure_after_publication_still_leaves_the_session_owned
    test_a_commit_failure_after_publication_is_reported_as_an_invariant
架构 guard
    test_the_rollback_gives_the_reservation_back_before_disposing
    test_nothing_between_publication_and_the_commit_can_yield_control
    test_the_commit_refusal_is_caught_by_name
service 层
    test_reserving_installs_the_candidate_and_takes_the_slot
    test_a_second_reservation_cannot_overlap_the_first
    test_commit_refuses_an_equal_but_foreign_reservation
    test_cancel_returns_the_service_to_the_candidate_slot
    test_a_committed_launch_frees_the_slot_for_the_next_one
```

**（5）一处刻意的语义收窄，供 review 复核。** 提议的写法是 `reserve` 只锁槽、`commit` 才把
candidate 移入 active。本分支把「移入 active」放在 `reserve`，因为按前一种写法
`RUNNING` 与「有 owner」之间仍然存在一个瞬间（只是很短、且不可观测），而 27.11(1) 要消除的
恰恰是这个状态本身。收益是缺陷从「不安全」降级为「fail closed 的活性问题」：commit 若因 bug
没有执行，会话仍然是**有 owner 且可恢复**的，代价只是下一次启动被拒。代价是 `reserve` 在
语义上不只是「预定」，文档与 docstring 都按这个语义写。

**（6）第五轮 review：reservation 并没有真正锁住槽位，且回滚忽略了 cancel 的结果。**

**(6a) `clear_active()` 完全不检查 `_promotion_reservation`。** 只要 service 已显示
disconnected，它就能把 `_order_service` 清成 `None`，而占用仍在；`commit` 当时也只看
reservation 是否匹配、不检查 active 是否还在。于是这条**合法 public API 序列**成立：

```text
reserve → active = service, reservation = R
service disconnect
clear_active()      → active = None
publish_armed()     → workflow RUNNING
commit(R)           → 匹配，成功
最终：RUNNING + active order service == None
```

也就是 27.11(1) 刚修掉的 ownerless `RUNNING` 又可以通过另一个 public lifecycle API 造出来。
在真实 desktop 路径上实测（reserve 之后、publish 之前插入一次 `clear_active()`）：`phase
RUNNING`、`has_order_service False`、`lease PAPER`、coordinator 已发布，而唯一的事件是一条
`PAPER_SESSION_ARMED`（severity warning）——即这次启动还在宣称自己成功。

修法两半：`clear_active()` 在占用存在时**直接拒绝**（这是「reservation 锁住槽位」从
docstring 变成性质的另一半），`commit_candidate_promotion()` 再独立确认 `_order_service` 仍
持有该 service；两者都保持占用不释放——owner 无法交代时，把槽位交回复用是唯一绝不能做的事。

**(6b) 回滚调用 `cancel_candidate_promotion()` 却完全忽略它的 `bool`。** 该 API 明确用返回值
报告「是否真的把 active 还回了候选槽位」，而 orchestrator 无条件继续 discard + reject +
release PAPER。真实 desktop 路径实测（publication 抛错 + cancel 返回 `False`）：`phase READY`、
`lease NONE`（PAPER **已释放**）、owner 仍被持有、armed 通道仍活着，而操作员只看到一条普通的
「Paper 会话未启动」。PAPER 与 Shadow 共享一个租约，所以这是共享租约最不该允许的状态。

修法：cancel 返回 `False` 时走新的 `_fail_to_release_promotion()` —— **不回滚**、保持
`CONNECTING` 与 PAPER 租约，用独立 code `PAPER_LAUNCH_ROLLBACK_FAILED` 在 error 级别报出。
与 `_fail_after_publication` 对称：那边是"已发布且已接管、无可回滚"，这边是"尚未发布、于是
留在飞行中"。两者都不拆自己交代不清的东西。卡住的启动是操作员看得见、可处置的；一个悄悄空出
来的租约什么也保护不了。

**(6c) 随之而来的 `closeEvent` 收口。** `clear_active()` 的新拒绝暴露了一处**本轮自己引入
的**异常路径：`closeEvent` 里那次 `clear_active()` 是无保护的，而
`runtime_supervisor.begin_shutdown()` 在它之前、`shadow_orchestrator.shutdown()` /
心跳 / 行情 / worker join 在它之后。实测该状态下 `window.close()`：异常从该 Qt override 抛出，
窗口最终不可见，而 owner 仍被持有——即后半段收尾全被跳过。这与第三轮的 integrity 逃逸是同一类
缺陷，因此按同一方式收口：**按名字**捕获 `PaperTradingLifecycleError`、弹窗、
`event.ignore()` 把客户端交还给操作员。占用只在内存里，所以重启是当前诚实的处置；E3 的应用内
恢复才是将来替代那句话的东西。

本轮 mutation（均为 RED，实测）：

```text
clear_active 不再理会 reservation                → RED（5 failed + 1 error）
commit 不再确认槽位仍被持有                       → RED（1 failed）
orchestrator 忽略 cancel 的返回值                 → RED（5 failed）
cancel 未证明已归还时仍完成回滚                    → RED（4 failed）
closeEvent 任由被拒的 clear 抛出                  → RED（1 failed + 2 errors）
```

新增 regression：service 层 `test_clearing_is_refused_while_a_promotion_is_reserved` /
`test_clearing_is_refused_before_it_even_reads_the_connection` /
`test_commit_refuses_when_the_reserved_slot_was_lost`；能力层
`test_a_rollback_that_cannot_give_the_slot_back_stays_in_flight`；真实路径
`test_a_rollback_that_cannot_give_the_slot_back_keeps_the_lease` /
`test_a_refused_clear_cannot_be_reached_between_the_reserve_and_the_commit` /
`test_a_stuck_launch_is_not_closed_over_silently`；guard
`test_the_rollback_stops_when_the_promotion_cannot_be_released` /
`test_the_reservation_gates_the_clear_of_the_active_slot`。
