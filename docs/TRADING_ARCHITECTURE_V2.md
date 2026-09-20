# Trading Core v2 架构

本文件定义 0.20.0 之后的**目标架构**。`docs/DESKTOP_DECOMPOSITION.md` 保留为
历史过程记录（0.20.0 之前的十五步桌面分解），不再往里追加未来总架构。

本轮的交付边界是 **Domain + Ports + UI Shell**。真实 MarketData、IBKR、
Account、Strategy、Risk、Execution 的迁移都在后续轮次，本文档只记录它们的
归宿，不声称已完成。

> **进度更新（Desktop Execution v2）**：execution route 已成为原生 v2 page。
> `MainWindow._auto_quant_tab` 已删除，页面位于
> `desktop_v2/pages/execution/`（models / rows / presenter / tables /
> controls / page），presenter 是 Qt-free 的纯投影，`MainWindow` 只保留
> `self.execution_page` 这一个属性，取数与编排仍在窗口。见 §8.2 与 §14。
>
> **进度更新（Runtime v2B）**：`paper_session.py` / `paper_workflow.py` /
> `workflow_state.py` 三个过渡 root module 已删除，Paper session 协调、
> workflow 生命周期与 `ExecutionLease` 语义已迁入
> `trading/runtime/`（`paper_contracts.py` / `paper_models.py` /
> `reconciliation.py` / `recovery.py` / `coordinator.py` /
> `workflow_state.py` / `workflow.py`），没有任何 compatibility re-export。
> 见 §2、§4 与 §10.4。
>
> **进度更新（Runtime v2A）**：`AutoQuantEngine` 已删除。信号与运行时状态
> 已拆成 `trading/runtime/strategy.py`（StrategyRuntime）与
> `trading/runtime/trading.py`（TradingRuntime），
> `src/us_quant/auto_quant.py` **不存在**且没有任何 import 指向它。
> 见 §2、§4 与 §10.3。
>
> **进度更新（Execution v2）**：Execution 链已完成迁移。
> `PaperOrderIntent` / `new_paper_order_intent` / `order_sink` 与
> `paper_order_journal.py` / `ibkr_paper_orders.py` 已删除，
> `TRANSITIONAL_EXECUTION_COUPLED_STRATEGY_FILES` **已归零**。见 §4 Execution
> 与 §10.2。
>
> **进度更新（Risk v2）**：Risk Domain、Risk Application、Desktop RiskPage 与
> AutoQuant 的风险集成已完成迁移。
>
> **进度更新（Strategy v2）**：Strategy Governance、Strategy Repository、
> Strategy Selection 与 Desktop StrategyPage 已完成迁移。
>
> **进度更新（Broker / Account v2）**：Account 链已完成迁移，见 §4 Account。
>
> **进度更新（Market Data v2）**：Market 链已完成迁移，见 §4 Market 与
> §10 roadmap。

## 迁移状态一览

| 链 | 状态 |
| --- | --- |
| Market Data | MIGRATED |
| Broker / Account | MIGRATED |
| Strategy Governance | MIGRATED |
| Strategy Repository | MIGRATED |
| Strategy Selection | MIGRATED |
| Desktop StrategyPage | MIGRATED |
| Risk Domain | MIGRATED |
| Risk Application | MIGRATED |
| Desktop RiskPage | MIGRATED |
| Desktop ExecutionPage | MIGRATED |
| AutoQuant Risk Integration | MIGRATED |
| Execution Domain | MIGRATED |
| Execution Application | MIGRATED |
| SQLite Order Repository | MIGRATED |
| IBKR Paper Execution Adapter | MIGRATED |
| AutoQuant Execution Integration | MIGRATED |
| Strategy Runtime | MIGRATED |
| Trading Runtime Core | MIGRATED |
| Paper Session Coordination | MIGRATED |
| Paper Workflow / Lease | MIGRATED |

真实链路现在是：

```text
Market Data
    ↓
StrategyRuntime
    ↓ TradeProposal（按排名）
TradingRuntime
    ↓
RiskApplication
    ↓ RiskDecision
ExecutionApplication
    ↓
OrderRepositoryPort / BrokerExecutionPort
```

而 **PaperSessionCoordinator 不在发单 pipeline 的"下游之后"**。它是外层的
runtime sequencer：券商事实与行情流先经过它，再由它决定能否让
TradingRuntime 看这一笔行情。

```text
broker facts (fills / events) + market stream
    ↓
PaperSessionCoordinator       会话顺序、超时、健康、halt
    ↓
TradingRuntime                策略 → Risk → Execution（发单仍在这里）
    ↓
PaperWorkflowController       生命周期、人工恢复、finalization、lease
```

**这不是"全部完成"。** 但 `paper_session.py` / `paper_workflow.py` /
`workflow_state.py` 已经不存在：会话协调、阶段机与执行租约都已模块化，
没有任何 compatibility shim 保留第二个入口。剩余的是
`PaperTradingService`、`paper_execution_health.py` 与 `workflow_controller.py`
的定位清理，见 §14。

## 1. 核心依赖方向

```text
Desktop UI / CLI
        ↓
Application Commands / Queries
        ↓
Trading Runtime
        ↓
Domain
        ↑
Ports
        ↑
Adapters
```

依赖只向内。Domain 在最底层，不认识任何人；Ports 认识 Domain，不认识
Adapters；Adapters 认识 Domain 和厂商库，但 Domain 不认识它们。

## 2. 五条主链与汇合点

五条 pipeline 最终都汇合到 **Trading Runtime**：

```text
Market Data
Broker / Account
Strategy
Risk
Execution
        ↓
Trading Runtime
```

UI 不是 orchestrator。UI 渲染 `TradingSnapshot` 并提交 command；它不拥有
券商生命周期、不拥有对账状态机、不拥有数据源策略。

## 3. 禁止的反向依赖

以下依赖一律禁止：

```text
Domain   → IBKR          Domain 不认识任何券商客户端
Domain   → Qt            Domain 不认识任何 UI 工具包
Domain   → SQL           Domain 不认识 sqlite3 或任何 schema

Strategy → Broker        策略只能产出 TradeProposal
Risk     → UI            风控不认识界面

Broker Adapter → Strategy   适配器不认识策略
UI             → concrete IBKR callbacks   UI 不认识券商回调
```

这些规则由 `tests/test_trading_architecture.py` 以 AST 方式强制：它读取
源码并断言 import 集合，任何一条反向边被重新引入都会让测试失败。

## 4. 五条 pipeline 的目标形态

### Market —— **已迁移（Market Data v2）**

```text
IBKR / Alpaca / Finnhub
        ↓
trading/adapters/{ibkr,alpaca,finnhub}/market_data.py
        ↓  （transport StreamQuote/StreamSnapshot → 领域 MarketQuote/MarketSnapshot）
trading/adapters/market_data_state.py
        ↓
MarketDataPort        （run / stop / snapshot / health）
        ↓
trading/application/market_data.py   （provider 策略与生命周期）
        ↓
MarketSnapshot       （上层唯一真相）
```

装配点唯一：`trading/composition/market_data.py` 是唯一同时认识 application
与具体 adapter 的模块；application 本身不 import 任何 adapter（由
`tests/test_trading_architecture.py` 强制）。

厂商行情类型号（IBKR `1/2/3/4`）只在 IBKR adapter 内解释为
`MarketDataMode`；上层只看到 `REALTIME/FROZEN/DELAYED/DELAYED_FROZEN/UNKNOWN`。

`source_id`（`alpaca_iex` / `finnhub_trades` / `ibkr` / `ibkr_extended`）是
逻辑键，用于比较与路由；`source_label`（`Alpaca` / `Finnhub` / `IBKR` /
`IBKR 5×24`）只是展示文本，任何逻辑都不得比较它。

### Account —— **已迁移（Broker / Account v2）**

```text
IBKR Paper Gateway
        ↓
trading/adapters/ibkr/account.py      （IBKR Account Adapter，只读）
        ↓  （raw callbacks → 领域 BrokerAccountSnapshot/BrokerPositionSnapshot）
BrokerAccountPort                     （refresh，只读）
        ↓
trading/application/accounts.py       （BrokerAccountApplication：config owner + refresh lifecycle）
        ↓
BrokerAccountPortfolio                （上层唯一账户真相）
        ↓
AccountPage / Preflight / CLI
```

装配点唯一：`trading/composition/accounts.py` 是唯一同时认识 application 与
具体 adapter 的模块；application 本身不 import 任何 adapter（由
`tests/test_trading_architecture.py` 强制）。

**行情链与账户链正式分家。** `BrokerAccountPort` 只负责账户 / 持仓 / P&L：

```text
Broker Account path does not request market data.
Market readiness is exclusively owned by Market Data v2.
```

账户 adapter 内不存在 `reqMarketDataType` / `reqMktData` / `cancelMktData` /
`marketDataType` / `tickPrice` / `TickTypeEnum`，也不再有 `reqContractDetails`
（账户持仓 callback 已经给出 `conId`/`symbol`/`localSymbol`/`secType`/
`exchange`/`currency`）。持仓市值来自 `reqPnLSingle` 的 `value`，即券商自己的
估值，不再由行情报价推算。`intraday_market_data_reasons` 已删除：账户刷新不再
判断、也不再改写行情状态。

**Execution 分离。** `BrokerAccountPort` 是只读的；`BrokerExecutionPort` 是另一
条链，本轮未迁移。账户刷新不 arm、不 submit、不 cancel，也不复用 Paper
execution socket。

**原始账户号只存在于 adapter 内。** `DU1234567` 只用于 `reqPnL` /
`reqPnLSingle` 与券商回调；转换成 Domain 前必经
`trading/adapters/ibkr/support.py` 的 `mask_account_id`（`DU***67`）。
Domain 类型没有 `account_id` / `account_number` / `raw_account` /
`managed_account` 字段，因此无法携带原始账户号。非 `DU` 账户 fail closed。

**账户类型分工。** `RiskAccountSnapshot`（由 `AccountSnapshot` 机械改名）是
`PreTradeRiskEngine` 的风险计算输入；`BrokerAccountSnapshot` /
`BrokerPositionSnapshot` / `BrokerAccountPortfolio` 才是券商事实。两者不可混用，
`AccountSnapshot` 这个泛化名字已消失。

**数量诚实。** `BrokerPositionSnapshot.quantity` 保持 `Decimal`，绝不取整：
whole-share 是 Execution policy，不是 observation policy。

### Strategy —— 治理/存储/选择/界面已迁移，运行时仍为过渡态

真实主链（§110）：

```text
SQLite（strategy_definition / strategy_version /
        strategy_deployment / strategy_audit —— schema 冻结）
        ↓
trading/adapters/sqlite/strategy_repository.py    SQLiteStrategyRepository
        ↓
trading/ports/strategy_repository.py              StrategyRepositoryPort
        ↓
trading/application/strategies.py                 StrategyApplication
        ↓
trading/application/strategy_selection.py         StrategySelectionService
        ↓
desktop_v2/pages/strategy.py · Research · AutoQuant setup
```

装配点唯一：`trading/composition/strategies.py` 的
`build_strategy_application(path)` 是唯一同时认识 application 与具体
SQLite adapter 的模块；application 本身不 import 任何 adapter、不 import
`sqlite3`（由 `tests/test_trading_architecture.py` 强制）。

Domain 拥有状态机。`StrategyStatus` / `StrategyMode` / `ALLOWED_TRANSITIONS`
住在 `trading/domain/strategy.py`：`STOPPED` 与 `LEGACY_INVALIDATED` 都是
终态，进入 `PAPER_SHADOW` 仍需 `gate_passed`。Repository 不判断任何
policy —— 它只保存 application 已经决定的结果。

`StrategyVersion.risk_budget_pct` 是 `Decimal`，`created_at` / `updated_at`
必须是 timezone-aware；naive 时间戳抛出而不是被补上 UTC。
`created_at` 来自 `strategy_version` 表，`updated_at` 只存在于
`strategy_deployment` 表，因此它照旧从 join 读取。

`parameter_hash` 的算法逐字保持：`json.dumps(parameters,
ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 后取 SHA-256，
并且**在 validate 之后的归一化文档上**计算。已治理版本的哈希、回测
artifact 引用的哈希都依赖这一点；`tests/fixtures/strategy_seed_baseline.json`
保存了旧模块删除前录下的 11 条种子记录，测试用它证明新旧哈希相等。

未来链（§111）—— 尚未实现：

```text
MarketSnapshot
      ↓
StrategyRuntime        ← 待建；今天的 AutoQuantEngine 是它的临时替身
      ↓
TradeProposal
      ↓
RiskApplication
      ↓
OrderIntent
      ↓
Execution
```

### Risk —— **已迁移（Risk v2）**

```text
TradeProposal
        ↓
RiskEvaluationRequest
        ↓
RiskApplication.evaluate()          trading/application/risk.py
        ↓
RiskDecision（approved / requested_quantity / approved_quantity /
              reasons / adjustments）
```

Risk 是**唯一**的盘前风险权威。它输入 `TradeProposal` + `RiskAccountSnapshot`
+ 当前持仓 + 行情，输出一个**整股数量**的裁决；它不产生订单。

```text
Risk 可以决定：
    是否允许这笔提案
    允许多少整股
    为什么拒绝、为什么缩量（adjustments 必须写明是哪个上限在生效）

Risk 不能：
    创建订单 ID
    创建券商订单
    提交 / 撤单 / 对账
```

两条不变量是结构性的，不是约定：

1. **风险缩量，而不是否决。** 策略请求 100 股、上限只允许 20 股时，返回的是
   `approved=True` / `approved_quantity=20`，并把触发缩量的上限写进
   `adjustments`。这保持了迁移前 runtime 本来就在做的"先把数量裁到安全值"，
   同时把这段算术收进一个地方。
2. **熔断永不阻止减仓。** 单日亏损熔断、回撤熔断、symbol 被禁止、账户已经
   超限、缺无关标的行情——这些全部只阻止**买入**。SELL 只对着持仓校验：
   有仓位、数量为正、不超过持有量。把"账户已经危险"变成"不允许平仓"，
   等于让风控成为无法离场的原因。

`MainWindow` 现在只通过 `build_risk_application()`（`trading/composition/risk.py`）
构造一个 `RiskApplication`，并以构造参数注入 `AutoQuantEngine`。旧的双入口
缺陷见 §10.4。

### Execution —— **已迁移（Execution v2）**

```text
Approved Proposal + RiskDecision
        ↓
ExecutionApplication.submit_approved()
        ↓
OrderIntent                        （唯一订单身份，只有它能创建）
        ↓
OrderRepositoryPort + BrokerExecutionPort
        ↓
SQLiteOrderRepository + IBKRExecutionAdapter
        ↓
IBKR Paper
```

提交被刻意拆成两段：`reserve()` 只分配券商订单号、**什么都不发**，
`ExecutionApplication` 随后把 intent ↔ broker id 的对应关系**先写进库**，
最后才 `submit()`。这个顺序由 `test_trading_execution_application` 用调用序列
钉死，并且做过反向验证：把 `record_intent` 挪到 `submit` 之后，测试立刻变红。

`OrderIntent` / `OrderEvent` / `ExecutionFill` 是上层唯一认的订单词汇；
券商原始状态文本仍随事件传递（对账与审计视图要读），但策略读的是映射后的
`OrderStatus`，且未识别文本一律落 `UNKNOWN`、永不落 `FILLED`。

## 5. 当前唯一正确的交易路径

Execution v2 之后，真实运行链是：

```text
AutoQuant signal logic
      ↓
TradeProposal                        （策略想要什么）
      ↓
RiskApplication.evaluate()
      ↓
RiskDecision                         （能不能做、做多少）
      ↓
ExecutionApplication.submit_approved()
      ↓
OrderIntent → OrderRepositoryPort + BrokerExecutionPort
```

`PaperOrderIntent` / `new_paper_order_intent` / `order_sink` 已删除，
`auto_quant.py` 不再认识任何执行侧类型。

### 5.1 目标形态

```text
MarketSnapshot
      ↓
Strategy Runtime
      ↓
TradeProposal
      ↓
Risk Application
      ↓
OrderIntent
      ↓
Execution Service
      ↓
BrokerExecutionPort
```

关键约束：**策略不能拥有 `order_sink`、`submit()` 或任何 IBKR service。**

这正是 `TradeProposal` 与 `OrderIntent` 必须是两个类型的原因。
`OrderIntent` 带 `order_id` 和 `client_order_id`，因此它是**可提交的**；
`TradeProposal` 两者都没有，所以策略在类型层面就够不到券商。中间必须经过
Risk Application 批准、再由 Execution Service 构造 `OrderIntent`。

Risk v2 已经把这句"必须经过"变成事实：`RiskEvaluationRequest` 同样没有
`order_id` / `client_order_id` / `broker_order_id` / `tif` / `transmit`，
由 `tests/test_trading_risk_domain.py` 与
`tests/test_trading_architecture.py` 强制。

## 6. 本轮建立的 Domain

`us_quant.domain` 已删除，全部类型迁入 `us_quant.trading.domain`，且**不保留
compatibility re-export**。同一个类型不允许有两个 import 路径。

| 模块 | 内容 |
| --- | --- |
| `common.py` | `ZERO`、`ONE`、`decimal()`、`Environment` |
| `market.py` | `Bar`、`MarketSlice`（迁移）；`MarketQuote`、`MarketSnapshot`、`MarketSubscription`、`MarketDataHealth`、`MarketDataMode`（Market Data v2 起为完整领域类型） |
| `account.py` | `Position`、`RiskAccountSnapshot`（由 `AccountSnapshot` 机械改名）；`BrokerAccountSnapshot` / `BrokerPositionSnapshot` / `BrokerAccountPortfolio` / `BrokerDiagnostic`（Broker / Account v2 新增）；`BrokerConnectionState` |
| `orders.py` | `Side`、`OrderStatus`、`OrderIntent`、`OrderEvent`（迁移）；`ExecutionFill`（新增） |
| `risk.py` | `RiskDecision`（迁移，Risk v2 升级）；`RiskEvaluationRequest`、`RiskLimits`、`SymbolRiskOverrides`、`SessionRiskOverrides`、`LayeredRiskLimits`、`resolve_symbol_risk_overrides`、`resolve_session_risk_overrides`（Risk v2 由 `us_quant/risk.py` 迁入并补校验） |
| `strategy.py` | `TradeAction`、`StrategyIdentity`、`TradeProposal`（全部新增）；`StrategyStatus`、`StrategyMode`、`ALLOWED_TRANSITIONS`、`StrategyDefinition`、`StrategyVersion`、`canonical_parameters_json`、`parameter_hash_for`（Strategy v2 新增） |
| `strategy_parameters.py` | `StrategyParameterError`、`validate_strategy_parameters`、`strategy_schema_summary`（Strategy v2 由 `strategy_schema.py` 逐字迁入） |
| `session.py` | `TradingSessionPhase`、`TradingSnapshot`（全部新增） |

迁移是**逐字搬家**：`quantity` 仍是 `int`（显式拒绝 `bool`，因为
`isinstance(True, int)` 为真）、`estimated_price > 0`、整股约束、
`Position` 不可做空、`RiskAccountSnapshot` 不可为负——全部语义不变，由
`tests/test_trading_domain.py` 钉住。

Domain 不得出现 IBKR `marketDataType`、Alpaca websocket 对象、Finnhub trade
id、`QThread` 或 `Signal`。这些属于 adapter。

### 6.1 并存清单：现已为空

**Broker / Account v2 之后不再有任何记录在案的重复核心类型。**
`MarketQuote` 曾经重复：领域类型 + `ibkr_readonly.MarketQuote`（带
`request_id` / `market_data_type`）。`ibkr_readonly.py` 已删除，该 vendor quote
类型随之消失，重叠清零。

唯一剩下的刻意并存是 `TradingSessionPhase` 与 `PaperWorkflowPhase`（见下），
它不属于"同一类型的两个定义"，因此不在重叠清单里。

1. **`TradingSessionPhase` 与 `PaperWorkflowPhase` 并存。**
   两者取值故意对齐。本轮不删除 `PaperWorkflowPhase`，因为真实 Paper
   runtime 还没迁——删掉它等于在没有替代品的情况下改写实盘交易代码。
   等 `paper_workflow.py` 迁入 `trading/runtime/session.py` 时，Paper 的枚举
   退役进这一个。

`KNOWN_TRANSITIONAL_OVERLAPS` 现为空字典，并由
`test_transitional_overlaps_are_exactly_the_documented_ones` 强制：新增任何重复
核心类型都会让测试失败，直到它被显式写进文档。

## 7. 本轮建立的 Ports

Ports 只允许 import 标准库、`typing` / `collections.abc` 和
`us_quant.trading.domain.*`。禁止 Qt、`ibapi`、`sqlite3`、任何具体 adapter。

| Port | 边界 |
| --- | --- |
| `MarketDataPort` | `run` / `stop` / `snapshot` / `health`（Market Data v2 起为 `run`，不再是 `start(subscription)`） |
| `BrokerAccountPort` | `refresh(timeout_seconds)`（**只读**，无 submit；Broker / Account v2 起改为反映真实的一次性 connect/read/disconnect） |
| `BrokerExecutionPort` | `connect` / `disconnect` / `submit` / `cancel` / `fills` |
| `StrategyRepositoryPort` | `list_versions` / `get_version` / `insert_version` / `update_deployment`，配 `StrategyAuditEvent`（Strategy v2 起重写，返回 `StrategyVersion` 而非行视图） |
| `OrderRepositoryPort` | `record_intent` / `record_event` / `record_fill` / `status` / `intent` / `fills` |

`MarketDataPort` 另有 `SnapshotListener`（`Callable[[MarketSnapshot], None]`）
与 provider 中立的 `MarketDataCredentialsError`。IBKR 不给 listener：它由桌面
的 snapshot 定时器轮询，再挂 listener 会让每条行情发布两次。

`BrokerAccountPort` 与 `BrokerExecutionPort` 刻意分开：只读账户链路绝不能被
误认为可交易通道。这与现有代码把只读连接和 Paper 订单连接分开是同一条原则。
`BrokerConnectionState.execution_ready` 对 Account adapter 永远为 `False`。

账户 Port 的错误是 provider 中立的（`BrokerAccountError` /
`BrokerAccountUnavailable` / `BrokerAccountValidationError` /
`BrokerAccountActiveError`）；UI / CLI 不再 catch `IBKRReadOnlyError` 或
`IBKRAPIUnavailable`，adapter 在边界上完成转换。

`StrategyRecordView` 已随 Strategy v2 消失：Port 现在直接以
`StrategyVersion` 交换数据，行形状只存在于 adapter 内部。

Port 也不判断 policy。它不认识 `ALLOWED_TRANSITIONS`，不读 `gate_passed`，
不决定 transition 是否合法、能否 clone；这些都是 `StrategyApplication` 的
职责。由 `test_the_strategy_repository_port_is_the_only_governance_surface`
强制：port 的四个方法之外不得出现任何 policy 符号。

## 8. 本轮建立的 Desktop UI v2

`src/us_quant/desktop_v2/`：

```text
navigation.py   唯一 NAVIGATION_ITEMS 定义（route + label）
shell.py        DesktopShellV2：导航轨 + QStackedWidget
```

八个一级入口，顺序固定：

| # | 标签 | route |
| --- | --- | --- |
| 1 | 总览 | `dashboard` |
| 2 | 行情 | `market` |
| 3 | 账户 | `account` |
| 4 | 策略 | `strategy` |
| 5 | 风控 | `risk` |
| 6 | 订单与成交 | `execution` |
| 7 | 研究 | `research` |
| 8 | 系统 | `system` |

Shell 只负责：route 注册、导航点击、当前 route、页面切换。仅此而已。

Shell **不得** import `MarketDataService`、`PaperTradingService`、
`PaperWorkflowController`、`AutoQuantEngine`、`RiskApplication`、
`StrategyApplication` 或任何 IBKR 模块，也不得 connect broker、start stream 或
submit order。`tests/test_desktop_v2_shell.py` 以结构守卫强制这一点。

页面身份被保留：传入的 widget 对象就是放进 stack 的那个对象，不复制、不重建。
未知 route **fail closed**（抛 `KeyError`）且不半途生效，不会静默回退到别的页面。

`navigation.py` 是纯数据，不 import Qt，也不 import 任何业务模块。

### 8.1 页面归属

`MainWindow` 现阶段仍是 composition root：它构造 page widgets，交给新 shell。

```text
dashboard  → _dashboard_tab()
market     → _quotes_tab()
account    → desktop_v2/pages/account.py            ✅ native v2
strategy   → desktop_v2/pages/strategy.py           ✅ native v2
risk       → desktop_v2/pages/risk.py               ✅ native v2
execution  → desktop_v2/pages/execution/            ✅ native v2
research   → QTabWidget（针对性验证 / 广域标的池 / 历史数据 / 市场扫描 / 回测 / 横截面研究）
system     → QTabWidget（运行事件 / 系统设置）
```

### 8.2 execution 页已完成（Desktop Execution v2）

`MainWindow._auto_quant_tab()`（416 行 builder）与它自己拥有的两个表格填充器
`_populate_auto_shadow_table` / `_populate_auto_latency_table` 已**删除**，
execution route 现在是一个原生 v2 page。`_auto_quant_tab` 不存在，也没有
compatibility shim 返回 page。

```text
desktop_v2/pages/execution/
  __init__.py   19 行   只导出 ExecutionPage
  models.py    188 行   不可变展示模型（含 Tone 语义色）
  rows.py      332 行   表格行投影 + 显示格式化（money / price）
  presenter.py 193 行   cards、control state、view 组装
  tables.py    279 行   ExecutionDetailTabs：五个 detail tab
  controls.py  354 行   ExecutionControls：输入与按钮
  page.py      214 行   ExecutionPage：组合、render、signal 转发
```

职责边界：

```text
MainWindow            取数 + 编排（workflow / service / lease 仍在这里）
presenter / rows      纯投影：facts → strings + tone（Qt-free）
ExecutionPage         只 render + emit intent
```

- **Page 只渲染、只报告意图。** 它不持有 `PaperTradingService`、
  `PaperWorkflowController`、`TradingRuntime`、repository 或 broker；每个按钮只
  emit 一个 signal。`set_arm_confirmed` 存在的意义是让窗口的确认弹窗把结果写进
  页面，而不是让页面自己去问。
- **Page 不能改 workflow phase。** 它收到的是
  `ExecutionControlState`（一组布尔），不是 `PaperWorkflowPhase`，因此它没有
  生命周期词汇可以据以行动。`HALTED` 无法自动恢复、finalization 两阶段、lease
  语义全部仍由窗口与 workflow 拥有。
- **Presenter Qt-free。** `models.py` / `presenter.py` / `rows.py` 不 import
  PySide6，所以投影可以不用 widget 测试（`tests/test_desktop_v2_execution_presenter.py`
  就是纯 Python）。tone 只表达 success / warning / error / neutral，具体颜色由
  page 与 table 决定。
- **MainWindow 只知道 `self.execution_page`。** 它不再持有
  `auto_pause_button` / `auto_order_table` / `auto_status_card` 一类 widget；由
  AST 守卫 `test_the_window_no_longer_names_an_execution_widget` 钉住。业务状态
  `auto_quant_candidates` / `auto_quant_snapshot` 仍然保留在窗口。
- **strategy combo 仍然只是 selection service 的 view。** 页面 emit
  `strategy_selected(version_id)`，窗口调用
  `StrategySelectionService.select(...)`；`_selected_auto_strategy_record()`
  继续从 service 读取。combo 没有重新变成 runtime truth。
- **preflight / 启动确认 / finalization 编排仍在窗口。**
  `calculate_quote_readiness_breakdown`、`evaluate_auto_quant_preflight`、
  启动弹窗、`_schedule_paper_finalization_refresh` 等一律不迁 UI。

同一轮新增的守卫（`tests/test_trading_architecture.py` 的 Desktop Execution v2 段）：

- execution route 只有一个入口：`_auto_quant_tab` 不存在，
  `pages["execution"] is self.execution_page`；
- 页面包不得 import application / composition / adapters / runtime / ports /
  `PaperTradingService` / `workflow_controller` / IBKR / sqlite / desktop，也不得
  出现 `PaperWorkflowPhase`、`ExecutionLease`、`begin_connecting`、
  `publish_armed`、`finalize_if_safe`、`placeOrder`、`cancelOrder` 等名字；
- presenter / rows / models 不得 import PySide6；
- 每个页面文件有自己的行数上限（`page.py <= 400`、`controls.py <= 360`、
  `rows.py <= 350`、`tables.py` / `presenter.py <= 320`、
  `models.py <= 220`、`__init__.py <= 40`），不共用 runtime 的 500 行上限。

两处刻意的合并（行为一致化，不是行为变更）：

1. `stop_stream` 的可用性现在由「行情在跑 **且** 没有会话占用」推导。旧代码在
   会话武装时禁用、在行情重启时又启用，是同一个按钮的两个 writer；新规则取两者
   中更安全的一个，并且只在一个地方计算。
2. 暂停 / 恢复 / 停止按钮过去有两个 writer（snapshot 标志与 workflow phase）。
   snapshot 那一份永远被紧接着的 phase 那一份覆盖，所以现在是单一来源：phase。


研究功能不是交易 runtime 主导航，因此收在 `research` 的二级页签里。
每个 v2 页面重写完成后，直接删除对应旧 builder。

**`account` 是第一个真正 native 的 v2 页面。** 它不再复用任何 legacy builder：
`MainWindow._account_tab` 与 `MainWindow._populate_account_view` 已删除，页面由
`desktop_v2/pages/account.py` 的 `AccountPage` 承担。页面只渲染，不取数：没有
application、没有 adapter、没有 `ibapi`，刷新由窗口执行后把
`BrokerAccountPortfolio` 交给 `AccountPage.render()`。

**`strategy` 是第二个。** `MainWindow._strategy_manager_tab`、
`_populate_strategy_registry`、`_selected_strategy_record`、
`_strategy_registry_selection_changed`、`_clone_strategy_version`、
`_transition_selected_strategy` 全部删除，页面由
`desktop_v2/pages/strategy.py` 的 `StrategyPage` 承担。它同样只渲染：没有
application、没有 repository、没有 `sqlite3`，只 import domain 类型、Qt 与
可复用展示控件；它通过 `version_selected` / `clone_requested` /
`transition_requested` 三个信号把意图交回窗口，由窗口调用
`StrategyApplication`。

**治理选中 ≠ 运行时选择。** 在策略页点一行只代表"当前查看的版本"，不得
改写 `AUTO_ROTATION` / `TARGETED_SHADOW` 的运行时选择。后者由
`StrategySelectionService` 拥有，只有那两个页面上的组合框会与它对话，且
组合框只是该服务的**视图**：填充来自 `options(purpose)`，当前项来自
`restore_or_default(purpose)`，用户改选则写回服务。这样"自动轮动跑哪个
版本"在任何地方都只有一个答案，不再取决于当时显示的是哪个标签页。

页面自身不做参数校验：编辑框里的 JSON 原样交给窗口，由 application 决定
是否合法；页面只会拒绝"空版本号"这种无法构成请求的输入。

**`risk` 是第三个。** `MainWindow._safety_tab` 已删除，页面由
`desktop_v2/pages/risk.py` 的 `RiskPage` 承担。它把原来那段静态安全说明
**逐字搬进** `SAFETY_ITEMS`，并新增展示风险层真正在执行的四个上限
（总敞口 / 单标的 / 单日亏损 / 回撤）与保证金借款状态。

它是**只读**的，而且是结构性只读：页面里没有任何 `QPushButton` /
`QLineEdit` / `QComboBox` / `QAbstractSpinBox`，由
`test_the_page_has_no_control_that_could_change_a_limit` 强制。一个能在
运行时放宽上限的界面，会让所有上限变成建议值。

页面还印出风控层"能做什么 / 不能做什么"（`RISK_BOUNDARY`）：是否允许、
允许多少整股、为什么；以及不能创建订单 ID、不能提交、不能撤单、不能对账。

`AccountPage` 也不显示任何行情状态：没有 quote type、没有 mark source、没有
STALE/FRESH 列。唯一的类价格数字 `Broker Mark` 来自
`market_value / quantity`，即券商自己的估值。缺失值显示 `—`，不是 `$0`。

## 9. 已删除的旧架构

```text
src/us_quant/domain.py
src/us_quant/unified_workflow_ui.py
scripts/render_unified_workflow_preview.py

MainWindow._build_legacy_workspace
MainWindow._build_unified_workflow
MainWindow._workflow_tabs
MainWindow._workflow_scroll_page
MainWindow._workspace_tabs

US_QUANT_LEGACY_UI
_unified_workflow_enabled
unified_workflow_page
```

### 9.1 Market Data v1（Market Data v2 删除）

```text
src/us_quant/market_data_service.py
src/us_quant/ibkr_stream.py
src/us_quant/alpaca_stream.py
src/us_quant/finnhub_stream.py

MarketDataService / MarketDataRequest / MarketDataServiceSnapshot
MarketDataStreamActive          → trading.ports.market_data.MarketDataActiveError
AlpacaCredentialsMissing        → MarketDataCredentialsError 的子类
FinnhubCredentialsMissing       → MarketDataCredentialsError 的子类
StreamQuote / StreamSnapshot    → 降级为 adapter 内部类型，仅存于
                                  trading/adapters/market_data_state.py
```

`StreamQuote` / `StreamSnapshot` 没有被删除，而是**降级**：它们是
provider 形状（ISO 字符串、厂商类型号），因此只允许存在于 adapter 层，
且只在一个模块里定义。上层唯一的行情真相是领域 `MarketQuote` /
`MarketSnapshot`。

以后**没有新旧 UI 切换**，只有 Desktop UI v2。旧 internal import 全部迁走，
本轮明确允许 breaking internal imports——这是架构重构，不是 SDK compatibility
project。

### 9.2 Broker / Account v1（Broker / Account v2 删除）

```text
src/us_quant/ibkr_readonly.py
src/us_quant/portfolio_view.py
tests/test_ibkr_readonly.py
tests/test_portfolio_view.py

IBKRReadOnlySnapshot / AccountMetric / BrokerPosition / ContractRecord /
AccountPnl / PositionPnl / BrokerMessage / ibkr_readonly.MarketQuote
        → 需要保留的 raw DTO 降级为 adapter 私有类型
          （trading/adapters/ibkr/account.py 的 _AccountMetric /
           _RawBrokerPosition / _RawAccountPnl / _RawPositionPnl /
           _BrokerMessage），不 export

AccountView / PositionView / PortfolioView / build_portfolio_view
        → BrokerAccountSnapshot / BrokerPositionSnapshot /
          BrokerAccountPortfolio

intraday_market_data_reasons
        → 删除。行情 readiness 只由 Market Data v2 的 MarketSnapshot 拥有

snapshot_to_redacted_dict / collect_readonly_snapshot
        → CLI 直接消费脱敏后的 Domain（BrokerAccountPortfolio）；
          Domain 天生不含原始账户号，因此不需要第二套 mask

AccountSnapshot
        → RiskAccountSnapshot（机械改名，字段与行为逐字保持）
```

`ibkr_readonly.mask_account_id` / `INFORMATIONAL_ERROR_CODES` /
`ensure_readonly_paper_config` 迁入
`trading/adapters/ibkr/support.py`：账户 adapter 与 Paper 订单链路都需要
`mask_account_id`，两份实现可能漂移，而漂移的那一份就是泄漏账户号的那一份。

**没有 compatibility re-export**：旧路径不放回任何 `# deprecated` 转发，
也没有 shim。

### 9.3 Strategy v1（Strategy v2 删除）

```text
src/us_quant/strategy_registry.py
src/us_quant/strategy_schema.py
tests/test_strategy_registry.py
tests/test_strategy_schema.py

StrategyRegistry            → StrategyApplication + SQLiteStrategyRepository
StrategyRecord              → StrategyVersion（+ StrategyDefinition / StrategyIdentity）
StrategyRecordView          → 删除；Port 直接交换 StrategyVersion
STRATEGY_STATUSES           → StrategyStatus
ALLOWED_TRANSITIONS         → trading/domain/strategy.py（行为逐字保持）
_canonical_parameters       → canonical_parameters_json（哈希算法逐字保持）
seed_defaults()             → DEFAULT_STRATEGY_SEEDS + StrategyApplication.bootstrap()
_retire_embedded_symbol_versions → StrategyApplication.retire_embedded_symbol_versions()

MainWindow._strategy_manager_tab           → desktop_v2/pages/strategy.py
MainWindow._populate_strategy_registry      → StrategyPage.render + 组合框视图
MainWindow._selected_strategy_record        → StrategyPage 选中行
MainWindow._strategy_registry_selection_changed → StrategyPage._selection_changed
MainWindow._clone_strategy_version          → _strategy_clone_requested
MainWindow._transition_selected_strategy    → _strategy_transition_requested
```

数据库 schema **一字节未改**：四张表的 `CREATE` 文本、列、类型与唯一约束
都是旧的，包括旧代码留下的缩进 —— SQLite 记录的是它收到的语句原文，因此
一个新建的库与一个由旧 DDL 建出的库，`sqlite_master` 内容完全相同。
`test_the_schema_matches_the_retired_registry_exactly` 逐字比对这一点，
`test_the_adapter_never_drops_deletes_or_renames` 禁止 adapter 里出现
`DROP` / `DELETE` / `ALTER` / `RENAME`。既有 `strategies.sqlite3` 直接打开，
不需要删库或重建。

两个旧行为被**刻意保留**，因为它们属于"现状"，不是可以顺手修掉的东西：

1. **`strategy_definition` 按 family 折叠。** 该表以 `strategy_id` 为主键
   且用 `INSERT OR IGNORE` 写入，所以一个家族的 name / description 来自它
   第一个注册的版本，后续版本读回同一段文本。种子里 `sector-momentum`
   的 2.1.0 带着不同的描述文字，录下来的基线就是这么记的。改成 upsert
   会静默改写一个从未被编辑过的版本在界面上显示的说明。
2. **旧的 `code_hash` 不被"修正"。** `unverified-local-source-0.7.0` 出现在
   远晚于 0.7.0 的策略上，保持原样；重新盖章等于把旧证据标成当前源码证据。

`_canonical_parameters` / `_contains_embedded_symbol` 这类模块级私有函数随
模块一起消失，语义分别进入 `trading/domain/strategy.py` 的
`canonical_parameters_json` 与 `trading/application/strategies.py` 的
`contains_embedded_symbol`。

### 9.4 Risk v1（Risk v2 删除）

```text
src/us_quant/risk.py
tests/test_risk.py

RiskLimits / SymbolRiskOverrides / SessionRiskOverrides / LayeredRiskLimits
resolve_symbol_risk_overrides / resolve_session_risk_overrides
        → trading/domain/risk.py（纯搬迁 + 新增校验）

PreTradeRiskEngine.evaluate()
        → trading/application/risk.py 的 RiskApplication.evaluate()

RiskDecision(approved, reasons)
        → RiskDecision(approved, requested_quantity, approved_quantity,
                       reasons, adjustments)   （见 §4 Risk）

MainWindow._safety_tab()
        → desktop_v2/pages/risk.py 的 RiskPage
```

`tests/test_risk.py` 拆成 `tests/test_trading_risk_domain.py` 与
`tests/test_trading_risk_application.py`。新测试不是把旧断言搬个位置：
旧文件里的 `PreTradeRiskEngine` 用 `OrderIntent` 表达请求，新套件用
`TradeProposal` + `RiskEvaluationRequest`，并且覆盖了旧引擎根本没有的
行为——缩量、`adjustments`、三层上限取最小、override 只能收紧。

**两处语义被刻意改变**（不是顺手修，是本次迁移的目的）：

1. **symbol override 只能收紧。** 旧引擎在 `symbol
   max_position_exposure_pct != None` 时**直接替代**账户上限，因此一个
   symbol 配置可以把账户自己的硬上限抬高。新实现取
   `min(account, symbol, session)`，由
   `test_a_symbol_ceiling_can_only_tighten_the_account_ceiling` 钉住。
2. **`RiskDecision` 能表达"缩量"。** 旧返回类型只有 `approved` / `reasons`，
   任何超限都是整体拒绝。Risk v2 把它扩成带数量的裁决，`approved_quantity`
   不可能超过 `requested_quantity`，且缩量必须写明原因——这些不变量在
   `__post_init__` 里强制，不合逻辑的裁决**构造不出来**。

`RiskAccountSnapshot.timestamp` 顺带补上 aware datetime 校验：两个熔断都是
拿快照和"当日起始净值"相比，一个没有时区的读数会让"这条读数有多旧"取决于
本机 locale。

### 9.5 Execution v1（Execution v2 删除）

```text
src/us_quant/ibkr_paper_orders.py
        → trading/adapters/ibkr/execution.py 的 IBKRExecutionAdapter（纯搬迁 + 两段提交）

src/us_quant/ibkr_paper_gateway.py
        → trading/adapters/ibkr/execution_gateway.py（逐字节搬迁）

src/us_quant/paper_order_journal.py
        → trading/adapters/sqlite/order_repository.py 的 SQLiteOrderRepository
          （schema 与 SQL 逐字保留，边界换成域类型）

PaperOrderIntent / PaperOrderUpdate / PaperExecution
        → trading/domain/orders.py 的 OrderIntent / OrderEvent / ExecutionFill

new_paper_order_intent()
        → ExecutionApplication 构造订单（OrderIntent.create 的唯一调用点）

AutoQuantEngine.order_sink
        → AutoQuantEngine.execution（注入 ExecutionApplication）
```

`paper_order_models.py` **保留但缩小**：剩下的只有会话侧 DTO——
`PaperOrderConnection`、`PaperBrokerState`/`PaperBrokerPosition`、
`PaperOrderReconciliation`、`ReconciliationSummary`、
`PaperBrokerOrder`、`PaperReconciliationSnapshot`。它们属于尚未迁移的
Trading Runtime（连接、券商状态、对账视图），不是 Execution v2 的漏迁。
`TERMINAL_ORDER_STATUSES` 也留在这里，因为订单库与适配器都要读它，而两者
不允许互相 import。

## 10. 现有模块的未来归宿（roadmap）

```text
market_data_service.py    → trading/application/market_data.py   ✅ 已迁移
ibkr_stream.py            → trading/adapters/ibkr/market_data.py ✅ 已迁移
alpaca_stream.py          → trading/adapters/alpaca/market_data.py ✅ 已迁移
finnhub_stream.py         → trading/adapters/finnhub/market_data.py ✅ 已迁移
ibkr_readonly.py          → trading/adapters/ibkr/account.py     ✅ 已迁移
portfolio_view.py         → trading/domain/account.py            ✅ 已删除
strategy_registry.py      → trading/adapters/sqlite/strategy_repository.py ✅ 已迁移
strategy_schema.py        → trading/domain/strategy_parameters.py ✅ 已迁移
risk.py                   → trading/domain/risk.py + trading/application/risk.py ✅ 已迁移
paper_order_journal.py    → trading/adapters/sqlite/order_repository.py ✅ 已迁移
ibkr_paper_orders.py      → trading/adapters/ibkr/execution.py   ✅ 已迁移
ibkr_paper_gateway.py     → trading/adapters/ibkr/execution_gateway.py ✅ 已迁移
paper_workflow.py         → trading/runtime/workflow.py           ✅ 已迁移
workflow_state.py         → trading/runtime/workflow_state.py     ✅ 已迁移
paper_session.py          → trading/runtime/coordinator.py        ✅ 已迁移
```

**下一轮：Strategy Runtime / Trading Runtime / Paper Session。** 三者已完成，
至此 `AutoQuantEngine`、`paper_session.py`、`paper_workflow.py` 与
`workflow_state.py` 都不存在。Execution v2 已完成：

```text
TradeProposal
        ↓
RiskDecision
        ↓
ExecutionApplication
        ↓
OrderIntent
        ↓
OrderRepositoryPort / BrokerExecutionPort
```

`PaperOrderIntent`、`new_paper_order_intent` 与 `order_sink` 已删除；
`paper_trading_service.py` 的默认工厂改为 composition 的
`build_execution_candidate`，`paper_session.py` 的事件读取改为
`fills()` / `events()`，两者都只做类型迁移、排序与语义未动。

### 10.0 过渡残留：现已清零

Market Data v2 曾留下两点已声明的过渡状态，Broker / Account v2 已全部清除：

1. **`IBKRConnectionConfig` 过渡依赖 —— 已删除。**
   `trading/application/market_data.py` 不再 import `us_quant.ibkr`，不再持有
   `self.config`，也不再有 `update_config` / `ensure_config_update_allowed`。
   连接设置的 runtime owner 是 `BrokerAccountApplication`；market data 只保留
   纯生命周期守卫 `ensure_reconfiguration_allowed()`（它不需要知道"新 config
   是什么"）。IBKR factory 在 prepare 时通过 `config_getter()` 取当前 config，
   因此不会复发 stale-config bug。
2. **`MarketQuote` 双重定义 —— 已消失。**
   `ibkr_readonly.py` 已删除，`KNOWN_TRANSITIONAL_OVERLAPS` 现为空字典。

### 10.2 执行例外：**已归零**（Execution v2）

**There is no execution-coupled strategy module left.**

Risk v2 结束时这里还写着 `AutoQuantEngine` 是唯一例外。Execution v2 把它
删掉了：`AutoQuantEngine` 不再构造订单、不再持有 sink、不再 import 任何
IBKR 执行类型。例外集合现在是**空集**，并且必须保持空集：

```python
TRANSITIONAL_EXECUTION_COUPLED_STRATEGY_FILES: set[str] = set()
```

`test_auto_quant_is_the_only_execution_coupled_strategy_module` 仍会扫描全部
策略相关文件，把实际耦合集合与这个字面量比对。现在任何耦合都会让它变红，
而不是"只要还是 auto_quant.py 就行"。

同一轮新增的守卫（`tests/test_trading_architecture.py` 的 Execution v2 段）：

- `OrderIntent.create` 的**唯一调用点**是 `trading/application/execution.py`
  （第二个调用点就是第二个订单来源，包括人工重挂：它走
  `ExecutionApplication.reissue`）；
- `ExecutionApplication` 不得 import adapter / sqlite3 / ibapi / Qt / 任何
  Paper 模块；
- `BrokerExecutionPort` / `OrderRepositoryPort` 只依赖 domain；
- 只有 `trading/composition/*` 同时认识 application 与 concrete adapter；
- `desktop.py` 不得出现 `IBKRExecutionAdapter` / `SQLiteOrderRepository` /
  `PaperOrderJournal` 等名字，必须走 `build_order_repository` /
  `build_execution_application`；
- 被删掉的旧模块（`paper_order_journal` / `ibkr_paper_orders` /
  `ibkr_paper_gateway`）**不存在**且**没有任何 import 指向它们**；
- 订单库的 DDL 仍是冻结的那三张表，唯一允许的 DDL 变化是给旧库补一个缺失
  的 `idempotency_key` 列（`ADD COLUMN`，不改写任何行）。

### 10.3 Strategy Runtime 已完成（Runtime v2A）

`AutoQuantEngine` 已删除，信号与运行时状态拆成两个运行时和支撑它们的模块：

```text
trading/runtime/
  models.py     策略可以读的东西：候选、冻结的持仓/策略视图、一次扫描的结果
  artifacts.py  会话自己的 artifact（含窗口渲染的 snapshot）
  signals.py    信号检测：分钟历史、入场门、排名
  strategy.py   StrategyRuntime：策略想要什么（TradeProposal）
  session.py    SessionState：会话状态与它的状态转换
  portfolio.py  SessionBook：持仓/现金/PnL/在途单/成交，以及向风控域的投影
  dispatch.py   OrderDispatch：唯一调用 Risk/Execution 的地方
  trading.py    TradingRuntime：把提案变成订单的交易会话
trading/composition/runtime.py
  build_trading_runtime(...)   显式组装上面两个 runtime
```

拆分后的链路是单向的：

```text
Market Data → StrategyRuntime → TradeProposal（按排名）
            → TradingRuntime → RiskApplication → ExecutionApplication
```

- `StrategyRuntime` **不能** import Risk/Execution/ports/adapters/sqlite/Qt，
  也不能出现 `OrderIntent` / `OrderEvent` / `ExecutionFill` / `placeOrder`
  这些名字：它只产出 `TradeProposal`，由 architecture guard AST 扫描钉住。
- `TradingRuntime` 是唯一同时持有 strategy + risk + execution 的对象；
  `OrderDispatch` 持有 risk + execution 但**看不到** strategy，因此
  "谁在提交"是结构性的，而不是约定。
- `auto_quant.py` 不存在、没有任何 production/scripts import、也没有
  compatibility re-export：`test_the_retired_mixed_engine_is_gone` 与
  `test_nothing_imports_the_retired_mixed_engine` 钉住这一点。
- 每个 `trading/runtime/*.py` production 模块 <= 500 行，
  `trading/composition/runtime.py` <= 200 行，由
  `test_the_runtime_modules_stay_small` 钉住；`utils.py` / `helpers.py` /
  `manager.py` / `ServiceLocator` 一类垃圾桶模块由
  `test_the_runtime_has_no_god_objects_or_junk_drawers` 禁止。

**当时冻结、现已迁移**：`paper_session.py` / `paper_workflow.py` /
`workflow_state.py` 与全部 `PaperWorkflowPhase` 转换、`ExecutionLease` 语义
在 v2A 轮次未动，Runtime v2B 只做模块化与 import 路径迁移，转换表与租约语义
一个字节都没有重新设计。`TradingRuntime` 满足 `PaperEngine` protocol，
coordinator 侧保留了原有的 `PaperEngine` / `PaperOrderPort` 契约。

### 10.4 Paper Session / Workflow / Lease 已完成（Runtime v2B）

三个过渡 root module 已删除（`paper_session.py` 712 行、
`paper_workflow.py` 477 行、`workflow_state.py` 174 行），职责按
contracts / models / proofs / sequencing / recovery / lifecycle 拆开：

```text
trading/runtime/
  paper_contracts.py    92 行  PendingOrder / EngineSnapshot / PaperEngine /
                               PaperOrderPort / PaperHealth / HealthEvaluator
  paper_models.py       53 行  PaperSessionEvent/State/Result（不可变）
  reconciliation.py    294 行  evidence 数据、identity/TTL/one-shot 校验、
                               engine digest、broker proof 比较
  recovery.py          347 行  SessionRecovery / ManualReconciliation：
                               人工驱动的恢复流程（被下面两个类 mixin）
  coordinator.py       389 行  PaperSessionCoordinator：会话顺序、超时、
                               health、halt、result 发布
  workflow_state.py    174 行  ExecutionLease / PaperWorkflowPhase /
                               WorkflowSnapshot / transition table
  workflow.py          364 行  PaperWorkflowController：生命周期、publish、
                               stop、finalization、lease 释放
```

职责边界：

```text
TradingRuntime            交易会话与策略/Risk/Execution
PaperSessionCoordinator   broker events、health、cancel/intervention、
                          reconciliation sequencing
PaperWorkflowController   Paper 生命周期、人工恢复、finalization、lease
PaperTradingService       execution channel / service lifecycle owner
```

四者互不吞并。Coordinator 不是 TradingRuntime 的一部分：它决定
*何时*让 TradingRuntime 看到一笔行情，并拥有 BUY 超时与 SELL 干预策略；
发单仍然只发生在 TradingRuntime → Risk → Execution。

必须保持的安全语义（v2B 期间逐条冻结，并有 characterization 测试与
non-vacuity 变异验证）：

- **顺序**：`fills()` → `on_execution()` → `events()` → `on_order_event()`。
  terminal broker status 可能先于本地 fill queue 被消费，反过来会让
  pending/fill 对账看到假冲突。
- **BUY 与 SELL 不同**：BUY 超过 `entry_order_timeout_seconds` 会被精确
  cancel（pause/stop 时强制 cancel）；SELL 超过
  `exit_order_intervention_seconds` 只 HALT 等人工处理，**绝不自动 cancel
  保护性 SELL**，也没有全局 cancel。
- **cancel 不确定性**：`ExecutionSubmissionUncertain` → `PAPER_CANCEL_UNCERTAIN`
  → HALT；普通失败 → `PAPER_CANCEL_BLOCKED` → HALT。不重试、不补单。
- **engine self-stop**：engine 未 active 且未 `stop_requested` →
  `PAPER_ENGINE_STOPPED` → HALT，避免 workflow 仍 RUNNING 的 zombie session。
- **health**：仍是注入的 `health_evaluator`，coordinator 不复制
  `PaperExecutionHealth` 的算法。
- **evidence**：绑定 runtime / service / session / account fingerprint /
  armed fingerprint / connection generation / reconciliation generation /
  state version / broker digest / engine digest；TTL 30 秒；same
  `evidence_id` 只能消费一次；confirmation 必须重新 refresh broker truth
  并逐字段比对，account binding 变化一律拒绝。
- **finalization 两阶段**：先 capture zero-state evidence（broker 仍连接），
  再 disconnect，再 drain 最后一轮本地回调/日志，最后
  `confirm_finalization_after_disconnect(evidence)`。任何 late fill / late
  event 都会让 finalization 失败并 HALT。
- **lease**：`begin_connecting` 时 acquire（不是连上之后，防止 async
  race）；只有 `release_paper(finalized=True)` 才释放。HALTED /
  RECONCILING / RECONCILING_READY / STOPPING 全部继续持有 PAPER lease。
- **HALTED → RUNNING 无直达路径**：只能
  `HALTED → explicit RECONCILING → RECONCILING_READY → explicit confirmation
  → RUNNING`。

同一轮新增的守卫（`tests/test_trading_architecture.py` 的 Runtime v2B 段）：

- 三个旧 root module **不存在**，且 production / scripts / tests 都不 import；
- 每个新模块有自己的行数预算（`coordinator.py <= 420`、
  `workflow.py <= 400`、`reconciliation.py <= 300`、
  `paper_contracts.py` / `paper_models.py <= 150`、
  `workflow_state.py <= 250`），由 `test_the_migrated_paper_modules_stay_small`
  钉住，不用统一的 500 行上限；
- `paper_contracts.py` / `paper_models.py` 不得 import IBKR / sqlite / Qt /
  desktop / adapters；
- `reconciliation.py` 是纯证明：不得 import adapter、application、strategy /
  trading / coordinator / workflow，也不得出现 `PaperWorkflowPhase`、
  `placeOrder`、`cancelOrder`；
- `coordinator.py` 不得出现 `RiskApplication` / `ExecutionApplication` /
  `StrategyRuntime` / `OrderIntent` / `TradeProposal` 等决策与发单名字；
- `workflow.py` 里 `PaperSessionCoordinator(` 只出现一次（publish_armed），
  且不得 import `paper_trading_service`；
- `desktop.py` 不得构造 `PaperSessionCoordinator` / evidence / lease manager，
  也不得直接 `release_paper` / `validate_paper_transition`；
- 两个被拆到 `recovery.py` 的类（`SessionRecovery` /
  `ManualReconciliation`）必须由拥有状态的类提供它声明依赖的属性与
  primitive，且任何 runtime 子类**不得重定义**基类方法：
  `test_no_runtime_class_defines_a_method_twice` 与
  `test_a_split_runtime_class_provides_what_its_other_half_relies_on` 钉住。
  后者正是 v2A `_flatten` 死代码教训的上一层版本。

### 10.5 下一阶段（Runtime v2B 之后，本轮不做）

```text
Desktop execution page decomposition
ShadowConfig naming / config separation
PaperExecutionHealth location cleanup
PaperTradingService location cleanup
workflow_controller broader cleanup
```

`PaperTradingService` 本轮只改了 type import：它仍然只负责 candidate / active
execution service 的拥有与生命周期，不负责 workflow phase、人工对账决策、
finalization proof 或 TradingRuntime 信号逻辑。

### 10.6 已关闭的风险接线缺陷（Risk v2 修复）

旧 Desktop 把风险限额放进了错误的地方：

```python
config = build_auto_rotation_config(
    ...,
    layered_risk_limits=LayeredRiskLimits(account=self.config.risk_limits),
)
engine = AutoQuantEngine(
    ...,
    # 没有传 layered_risk_limits
)
```

`layered_risk_limits` 存进了 `ShadowConfig`，而 `AutoQuantEngine` 读的是
**另一个**构造参数 `layered_risk_limits`，Desktop 从来没有把当前 config 的
`risk_limits` 传进去。结果是两条不同路径：

```text
Desktop config → ShadowConfig.layered_risk_limits   （没人读）
AutoQuantEngine._layered_risk_limits = None         （真正在跑的那个）
```

测试里直接给 Engine 传 `layered_risk_limits`，所以测试能通过；真实 Desktop
路径却可能完全没有启用这些账户级限额。

新架构只有一个真值：

```text
current config
      ↓
build_risk_application(LayeredRiskLimits(account=config.risk_limits),
                       exposure_multipliers=…)
      ↓
AutoQuantEngine(risk=…)
```

`build_auto_rotation_config()` 同时回归**纯策略/session config builder**：
`layered_risk_limits` 与 `symbol_risk_multipliers` 两个参数都已删除。
`ShadowConfig.layered_risk_limits` 字段**保留**，因为 `ShadowPaperEngine`
仍在真实使用它——那是 Shadow runtime 自己的独立问题。

`tests/test_desktop_risk_wiring.py` 从两端钉住这件事：行为上，用窗口的当前
配置构造 `RiskApplication` 再注入 Engine，断言 Engine 拿到的是**窗口配置的
那套限额**；结构上，断言 `AutoQuantEngine(...)` 调用点传了 `risk=` 且不再
出现 `layered_risk_limits` / `symbol_risk_multipliers` / `strategy_version_id`
/ `parameter_hash` 四个参数。

## 11. 后续允许删除

替代完成后直接删，不长期维护双轨：

```text
旧 MarketDataService v1                ✅ 已删除（Market Data v2）
旧 readonly account assembly           ✅ 已删除（Broker / Account v2）
旧 account page builder                ✅ 已删除（Broker / Account v2）
旧 strategy registry / schema 模块      ✅ 已删除（Strategy v2）
旧 strategy page builder 与其 6 个 handler ✅ 已删除（Strategy v2）
旧 strategy selection glue             ✅ 已删除（Strategy v2）
旧组合框即真相的取数方式                ✅ 已删除（Strategy v2）
旧 root risk.py 模块                   ✅ 已删除（Risk v2）
旧 PreTradeRiskEngine                  ✅ 已删除（Risk v2）
MainWindow._safety_tab                 ✅ 已删除（Risk v2）
AutoQuant 账户级 loss / drawdown 计算   ✅ 已删除（Risk v2）
AutoQuant symbol 风险策略缓存           ✅ 已删除（Risk v2）
AutoQuant gross / account position sizing ✅ 已删除（Risk v2）
AutoQuant layered_risk_limits 构造参数  ✅ 已删除（Risk v2）
旧 AutoQuant order_sink                ✅ 已删除（Execution v2）
旧 PaperOrderIntent / new_paper_order_intent ✅ 已删除（Execution v2）
旧 paper_order_journal.py              ✅ 已删除（Execution v2）
旧 ibkr_paper_orders.py                ✅ 已删除（Execution v2）
旧 ibkr_paper_gateway.py               ✅ 已删除（Execution v2）
旧 PaperOrderUpdate / PaperExecution   ✅ 已删除（Execution v2）
旧 auto_quant.py / AutoQuantEngine      ✅ 已删除（Runtime v2A）
旧 paper_session.py                    ✅ 已删除（Runtime v2B）
旧 paper_workflow.py                   ✅ 已删除（Runtime v2B）
旧 workflow_state.py（root）           ✅ 已删除（Runtime v2B）
MainWindow._auto_quant_tab             ✅ 已删除（Desktop Execution v2）
旧 execution widget 直写               ✅ 已删除（Desktop Execution v2）
旧 MainWindow stream lifecycle         ⏭ 后续
旧 Paper-specific orchestration glue   ⏭ 后续
旧 workflow duplicate state            ⏭ 后续
旧页面 builder（其余 route）            ⏭ 后续
旧 desktop service                     ⏭ 后续
```

## 12. 本轮冻结的行为

除 Domain import path 变化外，以下**行为完全不变**：

```text
paper_trading_service.py    paper_session.py        paper_workflow.py
ibkr_paper_orders.py        ibkr_paper_gateway.py   paper_order_journal.py
workflow_state.py
```

以上八个 Paper execution 文件在 Risk v2 中**一字节未改**：`PaperOrderIntent`、
`new_paper_order_intent`、`order_sink`、订单生命周期（`on_order_update` /
`on_execution` / 成交对账 / 在途单收尾 / 重复成交处理）、人工恢复流程
（`HALTED → RECONCILING → RECONCILING_READY → 显式确认 → RUNNING`）与
`ExecutionLease` 互斥规则全部保持原样。`resubmit_pending_intent` 也冻结：
它是人工对账后的原订单重挂，不是新的策略信号，因此**不**重新走一遍策略风控。

`ibkr_paper_orders.py` 本轮唯一的变化仍然只是 `mask_account_id` /
`INFORMATIONAL_ERROR_CODES` 的 import 来源（Broker / Account v2 的遗留），
两个符号的定义未变。

Paper execution stack 与当前 base（`ca8399c`）保持 byte-identical（上述 import
迁移除外）：账户链与执行链是不同的 socket，本轮不合并、不复用。

## 13. 安全契约（冻结）

```text
environment = paper
live_trading_enabled = false
whole_shares_only = true
allow_margin_borrowing = false
ibkr.port = 4002
paper_order_submission_enabled = false
```

人工恢复原则继续冻结，不能自动恢复：

```text
HALTED
→ explicit manual RECONCILING
→ RECONCILING_READY
→ explicit confirmation
→ RUNNING
```

## 14. 下一轮

**Desktop Execution v2 已完成。** execution route 现在是原生 v2 page：旧
`MainWindow._auto_quant_tab` 已删除，页面在
`desktop_v2/pages/execution/`，presenter 是 Qt-free 纯投影，窗口只保留
`self.execution_page` 并继续拥有取数与编排（见 §8.2）。

三个已完成轮次的顺序：

```text
Runtime v2A   AutoQuantEngine 拆成 StrategyRuntime / TradingRuntime（§10.3）
Runtime v2B   paper_session / paper_workflow / workflow_state 模块化（§10.4）
Desktop Exec  execution route 变成原生 v2 page（§8.2）
```

下一轮（本轮不做）：

```text
ShadowConfig naming / config separation
PaperExecutionHealth location cleanup
PaperTradingService location cleanup
workflow_controller broader cleanup
```

Runtime v2A / v2B / Desktop Execution v2 刻意没有做的事，留给更后面：

- 没有把 `PaperWorkflowPhase` / `ExecutionLease` 改成 trading 层类型之外的东西
  （迁移只改 import 路径，转换表与租约语义未动）；
- 没有重写其余 route 的页面 builder（`dashboard` / `market` / `research` /
  `system` 仍由旧 builder 提供）；
- 没有把 `PaperTradingService`、`paper_execution_health.py` 或
  `workflow_controller.py` 搬家（本轮只改 import 与调用点）；
- 没有合并 Account socket 与 Execution socket；
- 没有改 pricing ownership：`_limit_price` 仍产出
  `TradeProposal.reference_price`，Risk 按同一价格定量，Execution 按同一价格
  发单——三者一致，本轮不拆开；
- 没有重做 `ShadowConfig` 配置模型（名字仍然是历史包袱，先拆职责）；
- 没有新增任何真实交易能力：仍然 Paper only、整股、限价、无做空、无保证金，
  submission 默认关闭，live 永久关闭。ExecutionPage 也没有新增市价单、手工下单、
  全局撤单、live 开关、做空或数量/风险覆盖入口。

同样刻意不创建 `TradingManager`、`TradingGodService`、`GlobalAppState`、
`ServiceLocator` 或 `ApplicationContext`：runtime 由 composition root 显式
组装，不通过全局注册表解析。Execution v2 新增的
`trading/composition/execution.py` 与 Runtime v2A 新增的
`trading/composition/runtime.py` 就是这条原则的又一例，并由
`test_the_runtime_has_no_god_objects_or_junk_drawers` 钉住。

同样刻意不引入 `DatabaseMigrationManager` / `MigrationRegistry` /
`SchemaVersionFramework` / Alembic：当前 schema 迁移仍是每个 store 自己的一次性
兼容升级。订单库这一轮唯一的结构性修正是给旧库补一个缺失的
`idempotency_key` 列——`CREATE TABLE IF NOT EXISTS` 不会给已存在的表加列，
而缺这一列的老库连第一张订单都写不进去。
