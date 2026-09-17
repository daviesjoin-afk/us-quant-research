# Paper 交易应用边界（第四步：PaperTradingService 接管 order-service ownership）

审计对象：`src/us_quant/desktop.py`（本阶段开始时 9,911 行）。

第三步建立了只读 + 生命周期的过渡边界 `PaperTradingFacade`，并把 **order service 的
ownership 留在 `MainWindow`**。本阶段做第四步：把 ownership 也交出去，`MainWindow`
**不再构造、不再持有、不再清空** `IBKRPaperOrderService`。过渡类 `PaperTradingFacade`
被 `PaperTradingService` **取代**（同一文件重命名，非新增第二套）。

本阶段仍然**不重写** `IBKRPaperOrderService`、不拆 `ibkr_paper_orders.py`、不碰
submit / cancel / reconciliation 算法 / `ExecutionLease` / 策略 / 相位迁移。

审计方式：AST + grep 扫描 `desktop.py` 的每一个 `self.paper_order_service` /
`IBKRPaperOrderService` / `self.paper_workflow` 触点，映射到所属 `MainWindow` 方法
（`_count4.py` 可复现）。扫描结果见 §6。

## 1. 本阶段要解决的三个真实缺陷

审计发现的不是「代码不好看」，而是三处**可触发的所有权漏洞**：

| 缺陷 | 触发条件 | 后果 |
| --- | --- | --- |
| 候选 service 与已发布 service 共用一个槽位 | 异步 connect 回调晚到（用户已停止/改了策略） | 过期回调把**正在运行的会话**的连接断开或覆盖 |
| `_reject_unpublished_auto_service` 无法区分「哪个候选」 | 同一次启动期间有多个 attempt | 晚到的失败回调断掉新 attempt 的连接 |
| `closeEvent` / 终局化路径直接读写窗口成员 | 任何一处忘记清空 | 已释放的 socket 仍被引用，或反向：新会话被旧引用干扰 |

对应设计决定：**两个槽位，永远不是一个**。

## 2. 两个所有权槽位

```text
_candidates: dict[str, service | None]     ← 已连接但「尚未被信任」的候选
_order_service: service | None             ← 已被验证并武装的那一个
```

- **候选槽位**：`connect()` 成功**不等于**可以成为会话。启动还必须通过 preflight、
  策略与资金复核，因此过期的异步回调必须能**只处置自己的候选**，而绝不触碰 active。
  `dict` 值先写 `None` 是**占位（reservation）**：表示该 id 的 connect 正在飞行中，
  这才让 id 冲突真正 fail-closed，而不是 check-then-insert 的竞态。
- **active 槽位**：`promote_candidate()` 是唯一入口，且**拒绝覆盖**已存在的 active；
  新候选到达也**绝不隐式断开**旧会话——「旧会话是否结束」是终局化路径的判断，不是
  新启动的副作用。

candidate id 是调用方给的不透明字符串；本模块**从不 import** launch-plan 或 workflow
类型，边界不需要知道一次策略启动长什么样。

## 3. `PaperTradingService`（533 行，非空非注释 416 行）

`src/us_quant/paper_trading_service.py`，零 PySide6 / 零 desktop / 零 QThread 依赖，
可被纯 pytest 直接测试。构造只接受一个 getter 与一个可注入工厂：

```python
self.paper_trading = PaperTradingService(
    workflow_getter=lambda: self.paper_workflow,
    # order_service_factory 默认就是 IBKRPaperOrderService
)
```

**getter 是必须的，不是风格选择**：`MainWindow` 仍然会在安全测试里被替换
`paper_workflow`（驱动 HALTED / 拒绝关闭路径），getter 保证边界永远作用在**当前**
那个 controller 上。

### 3.1 只读 API（沿用第三步，语义未变）

| 方法 | 真值来源 |
| --- | --- |
| `phase()` | controller 的相位，**没有第二份 phase** |
| `is_finalized()` | `result is None or result.state.finalized`（未开始会话也算已终局） |
| `has_order_service()` | active 槽位非空（**不含候选**） |
| `is_connected()` | **active** service 的 `connection_snapshot().connected`（候选被排除） |
| `reconciliation_status()` | `reconciliation_evidence is not None` |
| `snapshot()` | 四个字段的 frozen 快照，不复制订单真值 |
| `broker_state()` | active service 的只读快照，无 owner 时 `None` |
| `reconciliation_rows_with_latency()` | active service 的只读行，无 owner 时 `()` |

`is_connected()` 会向 order service 询问连接事实，**不是廉价属性读**，因此
`snapshot()` 是显式状态查询，不放在逐 tick 渲染路径上。

### 3.2 候选生命周期（本阶段新增）

| 方法 | 语义 |
| --- | --- |
| `connect_candidate(id, config=, journal=, extended_hours_enabled=)` | 建并连一个候选；**只有成功才登记**。失败时 best-effort 断开；若连断开都失败，则**保留登记**（丢掉引用=丢掉对一个可能还活着的连接的控制） |
| `candidate_service(id)` | **借出**已登记的候选，供 promotion 前的 arm/submit/publish 接线使用；调用方不得存为成员变量 |
| `ensure_candidate_can_promote(id)` | **纯检查，零变更**：在不可逆的 `publish_armed` 之前调用，使随后的 promotion 不会因「本可提前知道」的原因失败 |
| `promote_candidate(id)` | 移入 active 槽位；**拒绝覆盖**已存在的 active，且从不隐式断开它 |
| `discard_candidate(id)` | 只断开并遗忘**指定的那一个**候选；active 与其他候选一字不动。断开成功后才移除登记，失败则保留登记并记录错误 |
| `has_candidate(id)` | 该 id 是否已登记（含飞行中的占位） |

### 3.3 active 生命周期（本阶段新增）

| 方法 | 语义 |
| --- | --- |
| `connect_active()` | 只**重连**已有 active；无 owner 是编程错误（人工对账针对的是已存在的会话，不是新建一个） |
| `disconnect()` | 现有 disconnect 语义原样执行一次；**不清空所有权**——断开成功只证明 socket 没了，不证明会话可以释放。失败记录到 `last_error` 并**原样重抛** |
| `clear_active(*, expected_service=None)` | 释放所有权。**双向 fail-closed**：仍报告 connected 的 active 被拒绝（丢引用=抛弃一个没人能再够到的 socket）；`expected_service` 让晚到的调用方证明自己清的是**它真正想清的那个**，而不是期间替换上来的新 service |

### 3.4 一次性探针

`probe_order_channel(...)` 是操作员的「订单通道通不通」检查：连接、读、断开，
**既不登记候选，也不打扰 active 槽位**。

### 3.5 锁的纪律

`RLock` **只保护所有权状态的读与提交**，绝不包裹网络调用：
`connect` / `disconnect` 一律在锁外执行。`closeEvent` 与心跳路径因此不会因
一次 broker 往返而阻塞 UI 线程。

## 4. 与第三步的差异（一句话）

第三步：窗口仍拥有 service，只是**读**改走边界。
第四步：窗口**不再拥有** service——`self.paper_order_service` 这个属性已从
`MainWindow` 上彻底消失。

## 5. 失败路径的 fail-closed 矩阵

| 场景 | 行为 |
| --- | --- |
| 候选 connect 抛异常，且断开成功 | 释放占位，异常原样上抛 |
| 候选 connect 抛异常，且断开也失败 | **保留登记** + 记录错误 + 异常上抛（绝不静默丢连接） |
| 同一 id 二次 connect | `PaperTradingLifecycleError`，零变更 |
| 候选 id 非字符串 / 空白 | `PaperTradingLifecycleError`，零变更 |
| 对未知 id 做 discard / promote | `PaperTradingLifecycleError`，零变更 |
| 对仍在 connecting 的候选做 discard / promote | `PaperTradingLifecycleError`（"still connecting"），零变更 |
| 已有 active 时再 promote | `PaperTradingLifecycleError`，**不覆盖、不断开** |
| 无 active 时 `connect_active()` | `PaperTradingLifecycleError` |
| 无 active 时 `clear_active()` | `PaperTradingLifecycleError` |
| active 仍 connected 时 `clear_active()` | `PaperTradingLifecycleError`，引用保留 |
| `expected_service` 与当前 active 不符 | `PaperTradingLifecycleError`，零变更 |
| discard 期间该候选被替换 | `PaperTradingLifecycleError`，拒绝移除「另一个」service |

## 6. 验收数字（全部由脚本导出，见 `_count4.py`）

| 指标 | 迁移前 | 迁移后 |
| --- | --- | --- |
| `self.paper_order_service` 直接触点 | 8 | **0** |
| `IBKRPaperOrderService` 在 desktop 中的引用 | 9 | **0** |
| `self.paper_order_service =`（窗口自己赋值） | 3 | **0** |
| `self.paper_workflow` 触点（**有意保留**） | 31 | 31 |
| `self.paper_trading.` 触点 | 22 | 39 |
| `desktop.py` 行数 | 9,911 | 9,913 |
| `paper_trading_service.py` 行数 | 0（旧 facade 199） | 533（非空非注释 416） |

`desktop.py` 只净增 **2 行**：窗口丢掉了一整类职责（构造、连接、持有、清空
order service），代价是若干行 `self.paper_trading.*` 调用点。

测试：**466 → 510 passed**（新增 44：service 单元 56 − 旧 24 + 接线 21 − 旧 9，
净 +44）。`check_publish_safety.py` → **scanned 193 publishable text files / OK**
（193 = 191 tracked + 3 未跟踪新文件 − 1 个已从索引移除的旧 facade）。

## 7. 仍然 NOT MIGRATED（有意留在 desktop）

| 方法 | 保留的直接调用 | 风险 | 原因 |
| --- | --- | --- | --- |
| `_start_auto_quant` | `begin_connecting(plan)`、`reject_connecting(plan)` | CRITICAL | 取 PAPER `ExecutionLease` 的唯一入口 |
| `_auto_order_service_connected` | `publish_armed(...)` | CRITICAL | 武装发布；交易语义 |
| `_reject_unpublished_auto_candidate` / `_reject_auto_launch_without_service` | `reject_connecting(plan)` | CRITICAL | 释放未武装 lease |
| `_pause/_resume_auto_quant_entries` | `set_entries_paused(...)` | CRITICAL | 交易闸门 |
| `_stop_auto_quant` | `request_stop(...)` | CRITICAL | 有序停机 |
| `_reconnect_auto_order_service` | `begin_manual_reconciliation()` / `complete_manual_reconciliation()` / `fail_manual_reconciliation()` | HIGH | 人工对账执行 |
| `_auto_order_reconciliation_failed` | `fail_manual_reconciliation(attempt_id)` | HIGH | HALTED 恢复 |
| `_resume_auto_quant_from_reconciliation` | `confirm_manual_resume(evidence_id)` | HIGH | 人工确认恢复 |
| `_start_paper_finalization_refresh` | `capture_finalization_evidence()` / `confirm_finalization_after_disconnect()` | HIGH | 零状态证明与释放时序 |
| `_paper_finalization_failed` | `fail_finalization_refresh()` | HIGH | 自动失败路由 |
| `_finish_auto_quant_session_if_safe` | `finalize_if_safe()` | HIGH | 零状态终局化判断 |

这些是**交易语义**，不是所有权语义；把它们搬进 service 会让 service 变成第二个
策略引擎。`PaperTradingService` 只决定**谁此刻持有 broker 连接**。

本阶段**未**新增 `submit_order()` / `cancel_order()` / `replace_order()` /
`place_order()` / `reqGlobalCancel()`，也**未**包装 `IBKRPaperOrderService.submit`。

## 8. 时序保证（迁移后逐字保留）

1. **先证明零状态，再断开，再确认**：`capture_finalization_evidence()` →
   `paper_trading.disconnect()` → `confirm_finalization_after_disconnect()`。
   顺序由源码字面量断言钉住。
2. **先纯检查，再武装，再晋升**：`ensure_candidate_can_promote()` →
   `publish_armed()` → `promote_candidate()`。检查在武装之前，因此已发布的会话
   不会出现「没有 owner」的中间态。
3. **只有 workflow 自己报告 finalized，才释放所有权**：
   `finalize_if_safe()` 返回真之后才 `clear_active()`。断开成功本身不作数。
4. **过期回调只处置自己的候选**：`has_candidate()` → `discard_candidate(id)`，
   放在 `try/finally` 里，保证 broker 断开失败也不会让会话卡在 `CONNECTING`。

## 9. 与既有安全回归的关系

以下既有测试全部继续通过，数量未减少：HALTED recovery、RECONCILING、
RECONCILING_READY、manual confirmation、closeEvent drain、`ExecutionLease` 互斥、
Paper/Shadow 互斥、whole shares、no margin borrowing、Live disabled。

钉住源码字面量的断言随迁移同步更新（`_reject_unpublished_auto_service` →
`_reject_unpublished_auto_candidate`；`service.disconnect()` →
`self.paper_trading.disconnect()`），**保证不变**：未 finalized 时不得断开、
零状态证明必须早于断开、断开必须早于确认。

`doctor` 保持：`environment = paper`、`live_trading_enabled = false`、
`whole_shares_only = true`、`allow_margin_borrowing = false`、`ibkr.port = 4002`。
