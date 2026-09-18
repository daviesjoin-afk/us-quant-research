# Desktop 运行时职责分解（第一步：RuntimeSupervisor；第二步：MarketDataService）

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

