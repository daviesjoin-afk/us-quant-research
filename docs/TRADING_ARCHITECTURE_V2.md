# Trading Core v2 架构

本文件定义 0.20.0 之后的**目标架构**。`docs/DESKTOP_DECOMPOSITION.md` 保留为
历史过程记录（0.20.0 之前的十五步桌面分解），不再往里追加未来总架构。

本轮的交付边界是 **Domain + Ports + UI Shell**。真实 MarketData、IBKR、
Account、Strategy、Risk、Execution 的迁移都在后续轮次，本文档只记录它们的
归宿，不声称已完成。

> **进度更新（Market Data v2）**：Market 链已完成迁移，见 §4 Market 与
> §10 roadmap。
>
> **进度更新（Broker / Account v2）**：Account 链已完成迁移，见 §4 Account。
> 下一条主线是 **Strategy v2**。Strategy、Risk、Execution 三条链仍属路线图，
> 未迁移。

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

### Strategy

```text
StrategyRepository
        ↓
Strategy selection
        ↓
Strategy runtime
        ↓
TradeProposal
```

### Risk

```text
TradeProposal
        ↓
RiskEngine
        ↓
RiskDecision
```

### Execution

```text
Approved Proposal
        ↓
OrderIntent
        ↓
ExecutionService
        ↓
BrokerExecutionPort
        ↓
IBKR Paper adapter
```

## 5. 未来唯一正确的交易路径

```text
MarketSnapshot
      ↓
Strategy Runtime
      ↓
TradeProposal
      ↓
Risk Engine
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
Risk Engine 批准、再由 Execution Service 构造 `OrderIntent`。

## 6. 本轮建立的 Domain

`us_quant.domain` 已删除，全部类型迁入 `us_quant.trading.domain`，且**不保留
compatibility re-export**。同一个类型不允许有两个 import 路径。

| 模块 | 内容 |
| --- | --- |
| `common.py` | `ZERO`、`ONE`、`decimal()`、`Environment` |
| `market.py` | `Bar`、`MarketSlice`（迁移）；`MarketQuote`、`MarketSnapshot`、`MarketSubscription`、`MarketDataHealth`、`MarketDataMode`（Market Data v2 起为完整领域类型） |
| `account.py` | `Position`、`RiskAccountSnapshot`（由 `AccountSnapshot` 机械改名）；`BrokerAccountSnapshot` / `BrokerPositionSnapshot` / `BrokerAccountPortfolio` / `BrokerDiagnostic`（Broker / Account v2 新增）；`BrokerConnectionState` |
| `orders.py` | `Side`、`OrderStatus`、`OrderIntent`、`OrderEvent`（迁移）；`ExecutionFill`（新增） |
| `risk.py` | `RiskDecision`（迁移） |
| `strategy.py` | `TradeAction`、`StrategyIdentity`、`TradeProposal`（全部新增） |
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
| `StrategyRepositoryPort` | `list_strategies` / `get_strategy`，配 `StrategyRecordView` |
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

`StrategyRecordView` 的字段来自当前 `StrategyRegistry.StrategyRecord` 的真实
能力，没有凭空发明字段。

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
`PaperWorkflowController`、`AutoQuantEngine`、`PreTradeRiskEngine`、
`StrategyRegistry` 或任何 IBKR 模块，也不得 connect broker、start stream 或
submit order。`tests/test_desktop_v2_shell.py` 以结构守卫强制这一点。

页面身份被保留：传入的 widget 对象就是放进 stack 的那个对象，不复制、不重建。
未知 route **fail closed**（抛 `KeyError`）且不半途生效，不会静默回退到别的页面。

`navigation.py` 是纯数据，不 import Qt，也不 import 任何业务模块。

### 8.1 页面归属

`MainWindow` 现阶段仍是 composition root：它构造 page widgets，交给新 shell。

```text
dashboard  → _dashboard_tab()
market     → _quotes_tab()
account    → desktop_v2/pages/account.py   ✅ native v2
strategy   → _strategy_manager_tab()
risk       → _safety_tab()
execution  → _auto_quant_tab()
research   → QTabWidget（针对性验证 / 广域标的池 / 历史数据 / 市场扫描 / 回测 / 横截面研究）
system     → QTabWidget（运行事件 / 系统设置）
```

研究功能不是交易 runtime 主导航，因此收在 `research` 的二级页签里。
每个 v2 页面重写完成后，直接删除对应旧 builder。

**`account` 是第一个真正 native 的 v2 页面。** 它不再复用任何 legacy builder：
`MainWindow._account_tab` 与 `MainWindow._populate_account_view` 已删除，页面由
`desktop_v2/pages/account.py` 的 `AccountPage` 承担。页面只渲染，不取数：没有
application、没有 adapter、没有 `ibapi`，刷新由窗口执行后把
`BrokerAccountPortfolio` 交给 `AccountPage.render()`。

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

## 10. 现有模块的未来归宿（roadmap）

```text
market_data_service.py    → trading/application/market_data.py   ✅ 已迁移
ibkr_stream.py            → trading/adapters/ibkr/market_data.py ✅ 已迁移
alpaca_stream.py          → trading/adapters/alpaca/market_data.py ✅ 已迁移
finnhub_stream.py         → trading/adapters/finnhub/market_data.py ✅ 已迁移
ibkr_readonly.py          → trading/adapters/ibkr/account.py     ✅ 已迁移
portfolio_view.py         → trading/domain/account.py            ✅ 已删除
strategy_registry.py      → trading/adapters/sqlite/strategy_repository.py
risk.py                   → trading/application/risk.py
paper_order_journal.py    → trading/adapters/sqlite/order_repository.py
ibkr_paper_orders.py      → trading/adapters/ibkr/execution.py
paper_workflow.py         → trading/runtime/session.py
```

**下一条主线：Strategy v2。** 目标链路：

```text
StrategyRegistry
        ↓
StrategyRepositoryPort
        ↓
StrategySelectionService
        ↓
StrategyRuntime
        ↓
TradeProposal
```

并开始终结 `AutoQuant strategy → order_sink`。

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

### 10.1 AutoQuant 的未来归宿

`AutoQuantEngine` 暂时保留。未来拆成：

```text
Strategy Runtime
+
Trading Runtime
```

尤其最终删除 `order_sink: Callable[[PaperOrderIntent], int]` 这种
Strategy → Execution 直连。**本轮不改它。**

## 11. 后续允许删除

替代完成后直接删，不长期维护双轨：

```text
旧 MarketDataService v1                ✅ 已删除（Market Data v2）
旧 readonly account assembly           ✅ 已删除（Broker / Account v2）
旧 account page builder                ✅ 已删除（Broker / Account v2）
旧 MainWindow stream lifecycle         ⏭ 后续
旧 strategy selection glue
旧 AutoQuant order_sink
旧 Paper-specific orchestration glue
旧 workflow duplicate state
旧页面 builder
旧 desktop service
```

## 12. 本轮冻结的行为

除 Domain import path 变化外，以下**行为完全不变**：

```text
paper_trading_service.py    paper_session.py        paper_workflow.py
ibkr_paper_orders.py        ibkr_paper_gateway.py   paper_order_journal.py
workflow_state.py

PreTradeRiskEngine.evaluate()      （仅 AccountSnapshot → RiskAccountSnapshot
                                    的机械改名，函数体等价）
```

`ibkr_paper_orders.py` 唯一的变化是把 `mask_account_id` /
`INFORMATIONAL_ERROR_CODES` 的 import 从 `ibkr_readonly` 改到
`trading/adapters/ibkr/support.py`——这是为了删除 `ibkr_readonly.py` 所必需的
纯 import 迁移，两个符号的定义未变。

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

**Strategy v2 migration** —— 从策略开始填 `StrategyRepositoryPort` 与
`StrategyRuntime`，把 `strategy_registry.py` 的读取链路搬到
`trading/adapters/sqlite/strategy_repository.py`，并以 `TradeProposal` 为边界
终结 `AutoQuant strategy → order_sink`。

本轮刻意不创建 `TradingManager`、`TradingGodService`、`GlobalAppState`、
`ServiceLocator` 或 `ApplicationContext`：runtime 由 composition root 显式
组装，不通过全局注册表解析。

同样刻意不引入 `DatabaseMigrationManager` / `MigrationRegistry` /
`SchemaVersionFramework` / Alembic：当前 schema 迁移仍是每个 store 自己的一次性
兼容升级。
