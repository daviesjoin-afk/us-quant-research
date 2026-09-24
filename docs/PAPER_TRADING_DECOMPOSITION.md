# Paper 交易模块拆分（第四步 ownership + 第五步 domain/journal 分层 + 第五步补遗）

审计对象：`src/us_quant/desktop.py`（第四步开始时 9,911 行）与
`src/us_quant/ibkr_paper_orders.py`（第五步开始时 2,208 行）。

三条线互不重叠：

- **第四步**：`MainWindow` 不再构造、不再持有、不再清空 `IBKRPaperOrderService`，
  ownership 交给 `PaperTradingService`。见 §1–§9。
- **第五步**：把 `ibkr_paper_orders.py` 里的纯数据模型与 SQLite Journal 拆出去，
  adapter 只剩 broker 语义。见 §10–§18。
- **第五步补遗**：把第五步漏拆的 `sessions()` 持久化查询从 adapter 移回 journal，
  adapter 只留一行代理。见 §19–§23。

第五步与第五步补遗**只做模块搬迁**：交易行为 0 变化、SQLite schema 0 变化、旧 import
路径继续有效、旧数据库继续可读。本阶段**不**继续拆 IBKR network adapter。

---

# 第一部分：第四步（PaperTradingService 接管 order-service ownership）

本阶段仍然**不重写** `IBKRPaperOrderService`、不碰 submit / cancel /
reconciliation 算法 / `ExecutionLease` / 策略 / 相位迁移。

审计方式：AST + grep 扫描 `desktop.py` 的每一个 `self.paper_order_service` /
`IBKRPaperOrderService` / `self.paper_workflow` 触点，映射到所属 `MainWindow` 方法
（AST 扫描脚本可复现）。扫描结果见 §6。

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
- **active 槽位**：只有一个入口，而且是**两阶段的**：`reserve_candidate_promotion()` 一边
  把候选装进 active、一边把槽位锁给这次启动，`commit_candidate_promotion()` /
  `cancel_candidate_promotion()` 结束这次启动的占用。两阶段不是整洁癖——启动必须能在
  publication 失败时把自己的晋升收回来，同时又必须在 publication **之前**就已经是 owner，
  否则「已发布但无 owner」的会话会出现（见 `DESKTOP_DECOMPOSITION.md` §27.11）。任一阶段都
  **拒绝覆盖**已存在的 active；新候选到达也**绝不隐式断开**旧会话——「旧会话是否结束」是
  终局化路径的判断，不是新启动的副作用。

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

### 3.2 候选生命周期（第四步新增）

| 方法 | 语义 |
| --- | --- |
| `connect_candidate(id, config=, journal=, extended_hours_enabled=)` | 建并连一个候选；**只有成功才登记**。失败时 best-effort 断开；若连断开都失败，则**保留登记**（丢掉引用=丢掉对一个可能还活着的连接的控制） |
| `candidate_service(id)` | **借出**已登记的候选，供 promotion 前的 arm/submit/publish 接线使用；调用方不得存为成员变量 |
| `reserve_candidate_promotion(id)` | **装入 active 并锁槽**（两阶段的第一阶段）：候选人必须存在、槽位必须为空且无人占用；成功后该 service 即成为 active，且在 commit/cancel 之前，任何其他晋升、同 id 的 connect 都被拒绝 |
| `commit_candidate_promotion(reservation)` | 结束本次启动的占用（第二阶段）。ownership 已在 reserve 时取得，所以这里**结构上没有可失败的残余**；唯一拒绝是「不是当前那张 reservation」——编程错误，按名字 loud 拒。**不是流水账**：占用不结束，该 service 将永远无法再被 reserve |
| `cancel_candidate_promotion(reservation)` | 回滚：把 service 放回**候选槽位**、释放占用。**刻意不抛异常**——它跑在 rollback 里，抛出会跳过 rejection 并把 `CONNECTING` 永久卡住持有 PAPER；是否释放用返回值报告 |
| `discard_candidate(id)` | 只断开并遗忘**指定的那一个**候选；active 与其他候选一字不动。断开成功后才移除登记，失败则保留登记并记录错误 |
| `has_candidate(id)` | 该 id 是否已登记（含飞行中的占位） |

### 3.3 active 生命周期（第四步新增）

| 方法 | 语义 |
| --- | --- |
| `connect_active()` | 只**重连**已有 active；无 owner 是编程错误（人工对账针对的是已存在的会话，不是新建一个） |
| `disconnect()` | 现有 disconnect 语义原样执行一次；**不清空所有权**——断开成功只证明 socket 没了，不证明会话可以释放。失败记录到 `last_error` 并**原样重抛** |
| `clear_active(*, expected_service=None)` | 释放所有权。**三向 fail-closed**：① 仍有未结束的 promotion 占用时**直接拒绝**——这是「reservation 锁住槽位」这句话的落实，否则 finalization / recovery 调用方可以在 reserve 与 commit 之间把 owner 清掉，启动就会发布一个 owner 已被丢弃的会话（见 `DESKTOP_DECOMPOSITION.md` §27.11(6)）；② 仍报告 connected 的 active 被拒绝（丢引用=抛弃一个没人能再够到的 socket）；③ `expected_service` 让晚到的调用方证明自己清的是**它真正想清的那个**，而不是期间替换上来的新 service |

### 3.4 一次性探针

`probe_order_channel(...)` 是操作员的「订单通道通不通」检查：连接、读、断开，
**既不登记候选，也不打扰 active 槽位**。

### 3.5 临时的 raw-service bridge

`candidate_service(candidate_id)` 借出候选的原始 order service，只为了让现有的
arm / publish 接线把 order port 交给 workflow。它不是第二份 ownership：

- `MainWindow` **不保存**它、不赋给成员变量，只在
  `_auto_order_service_connected()` 的当前调用栈里临时借用；
- 有**结构测试**钉住调用点白名单——`candidate_service(` 只允许出现在
  `_auto_order_service_connected` 里，且该行必须是 `service = ...` 赋值形式。
  新增一个调用点会让测试失败（已用注入验证过它真的会红）。

### 3.6 锁的纪律

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

## 6. 第四步验收数字（全部由脚本导出，不是目测）

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

第四步测试：**466 → 512 passed**（新增 46）。

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

第四步**未**新增 `submit_order()` / `cancel_order()` / `replace_order()` /
`place_order()` / `reqGlobalCancel()`，也**未**包装 `IBKRPaperOrderService.submit`。

## 8. 时序保证（迁移后逐字保留）

1. **先证明零状态，再断开，再确认**：`capture_finalization_evidence()` →
   `paper_trading.disconnect()` → `confirm_finalization_after_disconnect()`。
   顺序由源码字面量断言钉住。
2. **先取晋升，再出版，最后结束占用**：`reserve_candidate_promotion()` →
   `publish_armed()` → `commit_candidate_promotion()`。owner 在出版**之前**就已存在，
   因此 `RUNNING` 必然有 owner——不是「窗口很短」，而是不存在这样一个顺序。出版失败则
   `cancel_candidate_promotion()` 收回槽位，走既有 rollback。
   （早期版本是 `ensure_candidate_can_promote()` 纯检查 + 出版后 `promote_candidate()`；
   纯检查让槽位在出版期间是空的，出版后一旦被拒就留下 `RUNNING` 且无 owner 的会话。
   变动理由与实测见 `DESKTOP_DECOMPOSITION.md` §27.11。）
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

---

# 第二部分：第五步（Paper order models 与 journal 拆出 adapter）

第五步开始时 `ibkr_paper_orders.py` 一个文件同时承担：Paper order dataclasses、
SQLite journal、IBKR 连接、reconciliation snapshot、submit、cancel、fill
callbacks、order id、arming、broker state。

目标分层：

```text
paper_order_models.py     纯 DTO
        ↓
paper_order_journal.py    SQLite 持久化
        ↓
ibkr_paper_orders.py      IBKR broker adapter
```

箭头单向向下：journal 依赖 models，adapter 依赖两者，**反向依赖会形成循环**。

## 10. `paper_order_models.py`（148 行，非空非注释 122 行）

搬迁的 10 个纯 DTO，全部逐字复制、无重写：

```text
PaperOrderIntent          PaperBrokerState
PaperOrderUpdate          PaperOrderReconciliation
PaperExecution            ReconciliationSummary
PaperOrderConnection      PaperBrokerOrder
PaperBrokerPosition       PaperReconciliationSnapshot
```

字段名 / 类型 / 默认值 / `frozen` / `slots` 全部保持：`@dataclass(frozen=True,
slots=True)`，`Decimal` 仍是 `Decimal`，`tuple` 仍是 `tuple`。

**唯一一并搬走的非 DTO 是 `TERMINAL_ORDER_STATUSES`**。理由不是「顺手」而是依赖
方向的硬约束：Journal 有 3 处用它、adapter 有 2 处用它，而 journal 不允许 import
adapter，所以它必须位于两者共同的**叶子模块**；定义两份等于允许两份终态词汇表各自
漂移——那会直接改变 reconciliation 的判定结果。附件 §4 允许在真实依赖分析证明必须
移动时这么做，且明确「不要顺手重新设计规则」：本阶段只是搬迁该 frozenset 的字面量，
成员一字未改。

该模块**不 import** 任何 `us_quant` 模块、`ibapi`、`sqlite3`、PySide6、desktop。

## 11. `paper_order_journal.py`（588 行，非空非注释 558 行）

`PaperOrderJournal` 整体迁出，方法原样保留：`record_intent` / `record_update` /
`record_execution` / `intent_for_broker_order` / `intent_for_idempotency_key` /
`executed_quantity` / `max_broker_order_id` / `execution_rows` /
`reconciliation_rows` / `reconciliation_summary` / `pending_orders_for_session`
（含 `_dicts` 变体）/ `audit_rows` / `_initialize`；**第五步补遗**又把漏拆的
`sessions()` 归位到这里（见 §19）。

**helper 归属按 §8 的真实用法判定，不是按名字猜**：

| helper | 原状 | 结论 |
| --- | --- | --- |
| `_now_iso()` | journal 2 处 + adapter 14 处 | 两模块共用 → 移到 journal 并定义一次，adapter 从 journal import（**不复制**） |
| `_decimal_text()` | journal 3 处、adapter 0 处 | journal 专用 → 随 journal 迁移 |
| `_optional_decimal()` | adapter 专用 | 留在 adapter |

时间格式与 Decimal 文本格式因此仍是**一份**实现：`str(value)` 与
`datetime.now(timezone.utc).isoformat()`。

该模块**不 import** `IBKRPaperOrderService`、`ibapi`、`PaperTradingService`、
`desktop`、`PySide6`、`QThread`、`MainWindow`、`ExecutionLease`。

**IBKR adapter 不直接访问 SQLite**（第五步补遗之后）：`closing` 与 `connect_sqlite`
两个 import 已删除，adapter 里不再出现 `connect_sqlite(` / `self.path` /
任何 `FROM paper_order_intent`；旧 `service.sessions()` 仅保留 compatibility
delegate，把调用原样转给 journal（见 §20）。

## 12. `ibkr_paper_orders.py`（2,208 → 1,541 行；81,537 → 57,519 字节）

adapter 保留 broker 语义，且**只有 import 来源发生变化**：`connect()` /
`disconnect()` / `arm()` / `disarm()` / `submit()` / `cancel_intent()` /
`poll_updates()` / `poll_executions()` / `refresh_reconciliation_snapshot()` /
`broker_state()` / `connection_snapshot()` / IBKR callback 类 / order id 分配
全部零改动。

搬迁方式：脚本按**行区间做字节级复制**（一次性迁移脚本，未入库），并在动手前断言 15 处锚点
（`class IBKRPaperOrderService:` 的行号、`TERMINAL_ORDER_STATUSES` 的字面量、
Journal 的起止、两个 helper 的行号）。锚点断言在本轮**真的拦住过两次边界算错**
（Journal 尾部多算一行、tail 区间误把 Journal 再圈进去）——这正是它存在的意义。

## 13. 旧 import 路径兼容（§10/§11 硬要求）

`ibkr_paper_orders.py` 继续 **re-export** 全部 12 个名称（10 个 DTO +
`PaperOrderJournal` + `TERMINAL_ORDER_STATUSES`），因此下面仍然有效：

```python
from us_quant.ibkr_paper_orders import (
    PaperOrderJournal,
    PaperOrderIntent,
    PaperOrderUpdate,
    PaperExecution,
    PaperOrderReconciliation,
)
```

**类型 identity 不变**（re-export，不是 wrapper / subclass）：

```python
from us_quant.ibkr_paper_orders import PaperOrderIntent as Old
from us_quant.paper_order_models import PaperOrderIntent as New
assert Old is New
```

全仓仍有 **8 个文件**从旧路径 import（AST 统计；其中 7 个 import 了至少一个
迁移名，`tests/test_paper_trading_service.py` 只从该模块取非迁移名）：
`auto_quant.py`、`desktop.py`、`paper_execution_health.py`、
`scripts/check_paper_order_channel.py` 以及 4 个测试文件。§14 要求 desktop 尽量不动
—— 因此 desktop 的 import 块**一字未改**。

（另有一个文件 `tests/test_paper_order_journal_module.py` 只是把旧路径当作
**路径字符串**断言，不构成依赖。）

## 14. SQLite 兼容（§7/§20）

表名、列名、主键、`UNIQUE`、`FOREIGN KEY`、查询顺序、reconciliation 定义、
order id floor、idempotency key 行为、时间格式、Decimal 字符串格式**全部未变**；
无 schema migration、无 ORM、无 SQL「顺手优化」。

有测试直接用**迁移前的 DDL 字面量**建库、插入迁移前格式的行，再用新的
`PaperOrderJournal` 打开并断言：读回 intent / update / execution、算出
reconciliation、`max_broker_order_id` 正确、**并且能继续追加新订单**。另有一个
测试比对两套 DDL 的 `sqlite_master.sql`，防止「兼容测试」因为 DDL 漂移而变成空断言。

## 15. 第五步验收数字（脚本导出）

| 指标 | 迁移前 | 迁移后 |
| --- | --- | --- |
| `ibkr_paper_orders.py` | 2,208 行 / 81,537 字节 | **1,541 行 / 57,519 字节**（含第五步补遗） |
| `paper_order_models.py` | — | 148 行 / 3,532 字节 |
| `paper_order_journal.py` | — | 588 行 / 22,670 字节（含第五步补遗） |
| adapter re-export 名称 | — | 12 / 12 |
| 每个迁移类的定义份数 | 1（在 adapter 内） | **1**（在新模块内） |
| 从旧路径 import 的文件 | 8 | 8（**有意不迁移**） |

adapter 净减 **667 行 / 24,018 字节**（其中第五步补遗再减 29 行 / 1,141 字节），
且没有任何一份代码被复制两份。

测试：**512 → 540 passed**（新增 28：models 契约 10 + journal 模块 16 + adapter delegate 2；
其中第五步补遗新增 8 —— journal 行为 3、结构守卫 3、adapter delegate 2）。
`check_publish_safety.py` → **scanned 195 publishable text files / OK**
（195 = 191 已跟踪 + 4 个新文件；一次性脚本清理后复测）。

一个既有结构断言随之更新：`test_the_wrapped_service_is_still_far_larger_than_
this_boundary` 原本比较单个文件大小，拆分后被包裹的实现变成三个文件，故按原意改为
比较三者之和（阈值 3x → 2.5x，因为实测比值约 3.0x，贴着阈值的守卫会在无关改动上
误报），测试名同步改为 `..._the_wrapped_implementation_...`。

## 16. 新增测试

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_paper_order_models.py` | DTO 契约（字段名/类型/默认值/frozen/slots）、默认值取值（不止「有默认值」）、import isolation（无 ibapi/sqlite3/PySide6/desktop/adapter）、无 `us_quant` 包内依赖、old↔new identity、`TERMINAL_ORDER_STATUSES` 只定义一次、模块可脱离 adapter 单独 import |
| `tests/test_paper_order_journal_module.py` | 旧路径 re-export identity、journal 不碰 broker/GUI/线程、共享 helper 未被复制第二份（按行为判定，改名也算）、reconciliation 判定规则（非终态不结算、终态但数量不符不结算）、session 作用域真实生效、idempotency key 确实写库、新库完整 round-trip、**迁移前数据库可读且可继续写入**、DDL 与迁移前逐字一致 |

新增测试**不使用** `skip` / `xfail` 绕过失败，也不连接真实 IBKR。

## 16.1 突变验证（§19）

`_mutate5.py`（一次性脚本，未入库）注入 16 个突变体，**16/16 全杀**，且每个死因都落在目标断言上
（不是被无关守卫顺手拦住）：

| 突变体 | 杀它的测试 |
| --- | --- |
| models 引入禁用运行时依赖（`sqlite3`） | `test_models_module_has_no_runtime_dependencies` |
| models 反向依赖 adapter | 收集期 ERROR |
| DTO 丢掉 `frozen` | `test_every_migrated_model_is_a_frozen_slots_dataclass` |
| DTO 字段改名 / 改类型 | `test_migrated_models_keep_their_exact_field_contract` |
| 某个默认值被翻转 | `test_models_keep_their_declared_defaults` |
| 终态集合被就地重新定义 | `test_terminal_statuses_are_defined_exactly_once` |
| adapter 停止 re-export | 收集期 ERROR |
| journal 反向依赖 adapter | 收集期 ERROR |
| journal 复制一份 `_now_iso`（含改名副本） | `test_journal_module_has_no_private_duplicate_of_shared_helpers` |
| adapter 复制 journal 的时间戳 helper | 同上 |
| 旧列改名（旧库读不了） | `test_reconciliation_reason_codes_stay_distinguishable` |
| intent 插入不再持久化 idempotency key | `test_idempotency_keys_survive_a_write_and_are_not_silently_nulled` |
| `max_broker_order_id` 改取 MIN | `test_a_database_written_before_the_split_still_works` |
| reconciliation 无条件判为已对齐 | `test_reconciliation_reason_codes_stay_distinguishable` |
| 行查询丢掉 session 作用域 | `test_reconciliation_rows_are_scoped_to_the_requested_session` |

第一轮突变有 **5 个存活**（默认值翻转、改名副本 helper、idempotency key 落库、
reconciliation 判定、session 作用域），补了 4 个行为测试后才全杀 —— 这正是
「先跑突变再声称测试有效」的意义：不做这一步，这 5 个性质会以「测试全绿」的
假象留在仓库里。

## 17. 本阶段未做的事（§24 禁止清单）

未改 submit / cancel / arm / reconciliation 算法 / IBKR callback / connection
lifecycle / candidate lifecycle / `PaperTradingService` ownership / workflow /
`ExecutionLease` / `AutoQuantEngine` / risk / strategy / UI / SQLite schema；
未加新依赖、未引入 ORM 或 asyncio、未开 Live。

`PaperTradingService` **未扩大**：没有新增 `submit` / `cancel` 方法。

## 18. 安全规则保持（§16）

`DU only`、`localhost only`、`port 4002`、`Paper only`、`whole shares`、
`limit orders`、`no short`、`no fractional`、`no margin borrowing`、`no Live`、
`no global cancel` 全部不变。

`doctor` 保持：`environment = paper`、`live_trading_enabled = false`、
`whole_shares_only = true`、`allow_margin_borrowing = false`、`ibkr.port = 4002`。

---

# 第三部分：第五步补遗（把漏拆的持久化查询归位）

## 19. 被漏掉的东西：一个**从未能运行**的方法

第五步把 `PaperOrderJournal` 整体搬走时，`sessions()` 留在了 adapter 里 —— 但它
的实体依赖已经跟着 journal 走了：

| adapter 里的 `sessions()` 依赖 | 实际情况 |
| --- | --- |
| `self.path` | **`IBKRPaperOrderService` 没有这个属性**（只有 `self.journal`） |
| `connect_sqlite(...)` | 第五步后成为 adapter 里唯一的 SQLite 触点 |
| `closing(...)` | 同上，唯一用途就是那个方法 |

后果不是「分层不干净」，而是**调用即 `AttributeError`**。全仓 `grep` 确认没有任何
调用点（`src/` / `tests/` / `scripts/` 全无 `.sessions(`），所以它是一段**从未被
执行过、因而从未被测试覆盖**的死方法 —— 这也解释了为什么第五步的 532 个测试全绿
却没有发现它：**没有任何测试碰过它**。

这正好是「拆分必须逐符号核对依赖，而不是按行区间搬」的反面教材：按行区间搬迁时，
方法体被完整搬走了，但它的**隐式接收者状态**（`self.path`）不属于它。

## 20. 修法：查询归 journal，adapter 只留一行代理

`PaperOrderJournal.sessions(*, limit: int = 50)` 现在持有原来的 SQL（逐字迁移：
同一条 `GROUP BY i.session_id` 聚合、同一套 `MIN`/`MAX` 时间戳、同样的
`Decimal` 转换、同样的排序与 `limit`）。adapter 侧保留同名方法作为**兼容代理**：

```python
def sessions(self, *, limit: int = 50) -> tuple[dict[str, object], ...]:
    return self.journal.sessions(limit=limit)
```

保留代理的理由与 §13 的 re-export 一致：外部调用方按旧签名调用时行为不变；同时
删除 adapter 里已成死码的 `from contextlib import closing` 与
`from us_quant.sqlite_support import connect_sqlite` —— 迁走后它们各自只剩 import
行本身，留着就是误导读者「这里还会碰数据库」。

## 21. 行为等价性怎么证明

不是「看起来一样」：新增测试直接对比两侧输出。

| 断言 | 覆盖的缺陷 |
| --- | --- |
| 两个 session 的完整 rollup（intent 数、成交股数之和、首末时间戳、最新状态、`filled_quantity` 的 `Decimal` 类型） | 聚合语义被改写 |
| 按 `started_at DESC`（即 `MIN(generated_at)`）倒序、`limit` 截断 | 排序 / 截断被改 |
| adapter `sessions(limit=7)` 只调用 journal 一次且**参数原样透传** | 代理悄悄改默认值或吞掉参数 |
| adapter 不带参数调用时透传 `50` | 默认值漂移 |
| 调用后 `service._connected is False` 且 `service._client is None` | 代理偷偷去连 broker |
| adapter 源码不含 `connect_sqlite` / `closing(` / `self.path` / `FROM paper_order_intent` / `sqlite3` | 持久化查询回流 adapter |
| adapter 的 `sessions` AST 体只有 **1 条**语句且是 `return self.journal.sessions(...)` | 代理被重新实现成一份查询 |
| journal 源码含 `connect_sqlite(self.path)` / `FROM paper_order_intent` / `GROUP BY i.session_id` | 查询被「移走」但没落地 |
| 只有 intent、没有任何 update 的 session 仍出现且以 `generated_at` 计时 | `LEFT JOIN` / `COALESCE` 被改成内连接后静默隐藏最新 session |
| 空库返回 `()` | 聚合在无数据时炸掉或返回伪造行 |

## 22. 第五步补遗验收数字（脚本实测）

| 指标 | 第五步后 | 第五步补遗后 |
| --- | --- | --- |
| `ibkr_paper_orders.py` | 1,570 行 / 58,660 字节 | **1,541 行 / 57,519 字节** |
| `paper_order_journal.py` | 544 行 / 21,032 字节 | **588 行 / 22,670 字节** |
| adapter 里的 SQLite 触点 | 1（`sessions`） | **0** |
| adapter 里的 `self.path` | 1 | **0** |
| 测试总数 | 532 passed | **540 passed** |
| `check_publish_safety.py` | 195 OK | **195 OK** |
| `verify.ps1` | exit 0 | **exit 0** |

adapter 再减 **29 行 / 1,141 字节**，journal 增 **44 行 / 1,638 字节**（多出的部分
是方法本身加 docstring）。两个文件的**行为面 0 变化**：`sessions()` 的签名、返回值
形状、SQL 语义一字未改。

## 23. 第五步补遗未做的事

未改 `sessions()` 的 SQL、排序、`limit` 语义、返回字段；未动
`record_intent` / `record_update` / `record_execution` / reconciliation 系列方法；
未改 schema、未加索引、未改 `pending_orders_for_session` 的「只返非终态」语义；
未动 `PaperTradingService`、`desktop.py`、workflow、`ExecutionLease`、risk、strategy；
未新增依赖。**没有**因为「顺手」把 `sessions()` 加进任何调用方 —— 它仍然没有调用者，
本步只让「有调用者时它会正常工作」成立。

---

# 第四部分：第六步（IBKR Paper callback bridge 拆出）

## 24. 拆之前：一个类同时做两件事

`IBKRPaperOrderService.connect()` 里内嵌着 `class PaperApp(EWrapper, EClient)`。
这 16 个 callback 其实分成两层职责：

```text
IBKR transport          → 收到 callback、判断这条 callback 是否属于当前物理连接
IBKRPaperOrderService   → 决定这条 callback 对 Paper 状态意味着什么
```

前者是纯转发，与交易语义无关；后者要改 `_account`、`_broker_positions`、
`_next_order_id`、写 Journal、填 reconciliation attempt。两者混在一个类里，
transport 的每一次改动都落在 1,542 行的交易 adapter 内。

第六步**只拆 transport shell**。

## 25. `ibkr_paper_gateway.py`（386 行）

新模块只做四件事：创建 `EWrapper`/`EClient` bridge、持有 connection epoch、
接收 callback、把 callback 原样转交 sink。

| 导出 | 作用 |
| --- | --- |
| `PaperGatewaySink`（`Protocol`） | 17 个 `gateway_*` 方法，Gateway 唯一能看到的接口 |
| `PaperGatewayHandshake`（`@dataclass(slots=True)`） | 7 个 `Event` + `errors` 的**定义** |
| `create_paper_gateway_app(*, sink, epoch, handshake)` | 唯一工厂，内部 lazy import ibapi |

**lazy ibapi 保持**：`from ibapi.client import EClient` 在工厂函数体内，
模块顶层只有 `typing` / `threading` / `dataclasses`。`import
us_quant.ibkr_paper_gateway` 在没有官方 API 的机器上不崩。

**依赖方向单向**：Gateway 不 import `ibkr_paper_orders`、不 import models/journal、
不 import `desktop`/workflow/risk。sink 是 `Protocol`，不是具体类 ——
因此不存在循环依赖，也不需要 `TYPE_CHECKING` 绕路。

## 26. callback 的业务 body 归位 Service

每个 callback 的业务 body 机械搬迁为 Service 的私有 handler。以 `orderStatus`
为例：

```text
PaperApp.orderStatus
    ↓  if not self._current(): return
sink.gateway_order_status(app, epoch, ...)
    ↓
service.gateway_order_status(...)
    ↓  原来的 Decimal 转换
_record_order_status(...)
```

**行为等价性是脚本证明的，不是目测的**：把旧 `PaperApp.<callback>` 去掉
epoch 守卫后的 body，与新 `gateway_<callback>` 去掉同名守卫后的 body，
按标识符重命名表（`service.X` → `self.X`，`self` → 规范记号）归一化后逐语句比对
—— **16/16 IDENTICAL**。另有 16/16 的守卫集对照，确认没有 callback 丢掉
`if not self._current()`。

## 27. 第六步验收数字（脚本实测）

| 指标 | main（`408e68d`） | 第六步后 |
| --- | --- | --- |
| `ibkr_paper_orders.py` | 1,542 行 / 57,519 字节 | **1,528 行 / 55,582 字节** |
| `ibkr_paper_gateway.py` | 不存在 | **386 行 / 10,476 字节** |
| adapter 内嵌 `class PaperApp` | 有 | **0** |
| adapter 里的 `EWrapper` / `EClient` | 有 | **0** |
| 测试总数 | 540 passed | **595 passed** |
| `check_publish_safety.py` | 195 OK | **197 OK**（+2 新文件） |
| `verify.ps1` | exit 0 | **exit 0** |

adapter 减 **14 行 / 1,937 字节**；新增的 386 行 transport 从交易 adapter 中
移出，adapter 的 `submit` / `cancel_intent` / `disconnect` / `arm` / `disarm` /
`refresh_reconciliation_snapshot` 六个方法的 diff 均为 **0**。

## 28. 新增测试（55 个）

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `tests/test_ibkr_paper_gateway.py` | **41** | import isolation、lazy ibapi、16 个 callback 的转发与 stale 拦截、无状态、不碰 service 私有字段 |
| `tests/test_ibkr_paper_orders.py` | **+14** | connect wiring、adapter 不再定义 transport、DU-only / 单账户 / `nextValidId` floor / 外账户守卫 / reconciliation execution 不进 Journal |

## 29. 突变验证

**16/16 全杀**，每个突变体的死因都落在指定的那个测试上。清单：
gateway 去掉 stale-epoch 守卫、epoch 写死 99、交换 `avgFillPrice`/`lastFillPrice`、
截断 `error` 变参、stale 的 `connectionClosed` 照转、模块顶层 import ibapi、
`_current` 绕过 sink；service 只比 client 不比 epoch、只比 epoch 不比 client、
去掉非 DU 拒绝、去掉单账户要求、`nextValidId` 允许回退到 floor 以下、
`accountSummary`/`position` 去掉外账户守卫、reconciliation execution 直接进 Journal、
停止记录 order status。

## 30. 第六步未做的事

未搬 `submit` / `cancel_intent` / `arm` / `disarm` / `disconnect` /
`refresh_reconciliation_snapshot` / `_request_reconciliation_snapshot` /
`_publish_reconciliation_snapshot` / `reconciliation_snapshot_is_current`；
未改 request ID（`91_001`/`91_002`/`91_003`/`91_101`/`91_103`）、
未改 network thread（仍是 `Thread(target=app.run, name="ibkr-paper-order-network", daemon=True)`）、
未改 `connect()` 时序、未改 handshake 的 7 个阶段、未把 connection ownership
转给 Gateway、未改任何安全不变式（paper only / DU only / port 4002 /
whole shares / limit only / no short / no margin / no Live / no global cancel）。

---

# 第五部分：第六步补遗（transport 层的参数完整性）

## 31. 缺陷：Gateway 仍在解释 callback 参数

第六步的 Gateway 已经做到了「不判断业务」，但**仍在丢字段**：

```text
orderStatus    丢 permId / parentId / clientId / mktCapPrice（11 → 7）
               whyHeld 被重命名为 message（业务解释提前发生在 transport 层）
accountSummary 丢 currency（5 → 4）
```

其余 13 个 callback 是全量转发。审计脚本按 AST 逐 callback 比对 IBKR 签名与
实际转发实参，只有这两个不通过。

这不是第六步拆分造成的回归 —— 搬迁时**照抄了旧 `PaperApp` 的签名形状**，
而旧 `PaperApp` 里就有 `del permId, parentId, clientId, mktCapPrice` 和
`del currency`。但 `del` 出现在**业务 body 里**是服务的选择；出现在
**transport 层**则是 transport 替服务做了决定。边界要求后者消失。

## 32. 修法：Gateway 全量搬运，Service 决定不用

```text
PaperApp.orderStatus（11 个参数）
    ↓  if not self._current(): return
sink.gateway_order_status(app, epoch, order_id, status, filled, remaining,
                          average_fill_price, perm_id, parent_id,
                          last_fill_price, client_id, why_held,
                          market_cap_price)
    ↓
service.gateway_order_status(...)
    ↓  del perm_id, parent_id, client_id, market_cap_price
    ↓  why_held → message
_record_order_status(...)      # 业务语义与拆分前逐字相同
```

`accountSummary` 同理：Gateway 传 `currency`，Service 首行 `del currency`。

**这是 transport boundary 修复，不是行为变化**：`filled`/`remaining` 仍是
`Decimal`、零价仍是 `None`、`whyHeld` 仍是 message、四个未用参数仍不进记录。

## 33. 新增守卫：结构上禁止任何 callback 丢参数

`test_no_callback_drops_an_ibkr_argument` 遍历 `PaperApp` 的每个 callback，
把形参集合与转发实参集合求差 —— 非空即失败。`self` 与 `*args` 除外
（`error(reqId, *args)` 整包转发是既有契约）。这条守卫覆盖全部 16 个
callback，未来任何一处再丢字段都会在结构层被拦住，而不是等某个 sentinel
测试恰好踩到。

配套的 `test_account_summary_currency_reaches_the_sink` 与加严后的
`test_order_status_arguments_keep_their_positions`（11 个互不相同的 sentinel）
钉住真实转发值；Service 侧三个回归测试钉住「全量入、旧语义出」。

## 34. 补遗验收数字（脚本实测）

| 指标 | 第六步 | 第六步补遗 |
| --- | --- | --- |
| `ibkr_paper_gateway.py` | 385 行 / 10,476 字节 | **395 行 / 10,732 字节** |
| `ibkr_paper_orders.py` | 1,527 行 / 55,582 字节 | **1,534 行 / 55,789 字节** |
| 丢参数的 callback | 2（`orderStatus`、`accountSummary`） | **0** |
| `tests/test_ibkr_paper_gateway.py` | 41 tests | **44 tests** |
| `tests/test_ibkr_paper_orders.py` | 31 tests | **34 tests** |
| 测试总数 | 595 passed | **601 passed** |
| 突变 | 16/16 | **18/18**（新增丢参数与 sentinel 突变体） |
| `verify.ps1` | exit 0 | **exit 0** |

# 第六部分：forward reference —— 启动链的 ownership 演进（v2O-E1）

本文档记录的历史阶段**不重写**。这一节只做前向引用，说明后续一轮把**启动链**的
ownership 移动到了哪里，以免读者按本文档的旧描述去 `MainWindow` 找已经不在那里的代码。

## 35. 启动链迁入 `PaperOrchestrator`（v2O-E1）

本文档此前描述的边界是"`PaperTradingService` 拥有 order service 的候选/激活槽，
`PaperWorkflowController` 拥有 phase / active plan / execution lease，窗口负责启动的
sequencing"。v2O-E1 把最后那一半——**启动 sequencing**——也移出了窗口：

```text
desktop_v2/orchestration/paper/
    models.py        冻结 request / 券商读数 / build 结果 / publication / event
    queries.py       启动门、冻结 plan、identity 比对、券商门（纯规则）
    orchestrator.py  READY → CONNECTING → 校验 → arm → publish → promote → RUNNING
```

所有权**没有变**，只是启动的 driver 换了：

| 事实 | 唯一 owner | E1 之前由谁驱动 | E1 之后由谁驱动 |
| --- | --- | --- | --- |
| phase / active_plan / execution lease | `PaperWorkflowController` | `MainWindow` | `PaperOrchestrator`（仍只调它的公开面） |
| candidate / active broker connection | `PaperTradingService` | `MainWindow` | `PaperOrchestrator`（`candidate_service` 仍是借用） |
| 已武装 session 的 runtime sequencing | `PaperSessionCoordinator` | 未变 | 未变 |
| session / book / positions / risk / execution dispatch | `TradingRuntime` | 未变 | 未变 |
| **启动 sequencing** | — | `MainWindow._start_auto_quant` + `_auto_order_service_connected` | **`PaperOrchestrator`** |

窗口仍是 composition root：它构造 orchestrator、构造 `IBKRConnectionConfig`、提供窄的
session-build seam（`_build_paper_session`）、接 `session_published` 并沿用既有渲染。

**仍未迁移**（属 v2O-E2/E3/E4）：RUNNING 之后的 pause / resume / stop、HALT recovery、
manual reconciliation、finalization、`closeEvent` teardown、execution page 的 session
渲染。本文档 §16–§34 描述的 `PaperTradingService` / gateway / journal 边界在这些阶段
依然成立。

设计依据见 `docs/DESKTOP_DECOMPOSITION.md` §27 与 `docs/TRADING_ARCHITECTURE_V2.md`
§8.18。
