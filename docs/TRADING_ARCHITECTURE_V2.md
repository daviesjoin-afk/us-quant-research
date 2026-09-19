# Trading Core v2 架构

本文件定义 0.20.0 之后的**目标架构**。`docs/DESKTOP_DECOMPOSITION.md` 保留为
历史过程记录（0.20.0 之前的十五步桌面分解），不再往里追加未来总架构。

本轮的交付边界是 **Domain + Ports + UI Shell**。真实 MarketData、IBKR、
Account、Strategy、Risk、Execution 的迁移都在后续轮次，本文档只记录它们的
归宿，不声称已完成。

> **进度更新（Market Data v2）**：Market 链已完成迁移，见 §4 Market 与
> §10 roadmap。下一条主线是 **Broker / Account v2**。本文档中除 Market 以外
> 的 pipeline 仍属路线图，未迁移。

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

### Account

```text
IBKR account adapter
        ↓
BrokerAccountPort
        ↓
Account application
        ↓
AccountSnapshot / Positions
```

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
| `account.py` | `Position`、`AccountSnapshot`（迁移）；`BrokerConnectionState`（新增） |
| `orders.py` | `Side`、`OrderStatus`、`OrderIntent`、`OrderEvent`（迁移）；`ExecutionFill`（新增） |
| `risk.py` | `RiskDecision`（迁移） |
| `strategy.py` | `TradeAction`、`StrategyIdentity`、`TradeProposal`（全部新增） |
| `session.py` | `TradingSessionPhase`、`TradingSnapshot`（全部新增） |

迁移是**逐字搬家**：`quantity` 仍是 `int`（显式拒绝 `bool`，因为
`isinstance(True, int)` 为真）、`estimated_price > 0`、整股约束、
`Position` 不可做空、`AccountSnapshot` 不可为负——全部语义不变，由
`tests/test_trading_domain.py` 钉住。

Domain 不得出现 IBKR `marketDataType`、Alpaca websocket 对象、Finnhub trade
id、`QThread` 或 `Signal`。这些属于 adapter。

### 6.1 刻意保留的两处并存

两处重复是**有意的、记录在案的**，不是遗漏：

1. **`TradingSessionPhase` 与 `PaperWorkflowPhase` 并存。**
   两者取值故意对齐。本轮不删除 `PaperWorkflowPhase`，因为真实 Paper
   runtime 还没迁——删掉它等于在没有替代品的情况下改写实盘交易代码。
   等 `paper_workflow.py` 迁入 `trading/runtime/session.py` 时，Paper 的枚举
   退役进这一个。

2. **`MarketQuote` 与 `ibkr_readonly.MarketQuote` 并存。**
   后者是既有的 IBKR 专用类型，带 `request_id` / `market_data_type`——
   正是 Domain 类型禁止携带的厂商字段。`ibkr_readonly.py` 本轮不迁，
   它的归宿是 `trading/adapters/ibkr/account.py`；届时该 quote 类型退役进
   adapter，重叠消失。

这两处由 `test_transitional_overlaps_are_exactly_the_documented_ones` 强制：
新增任何重复类型都会让测试失败，直到它被显式写进文档。

## 7. 本轮建立的 Ports

Ports 只允许 import 标准库、`typing` / `collections.abc` 和
`us_quant.trading.domain.*`。禁止 Qt、`ibapi`、`sqlite3`、任何具体 adapter。

| Port | 边界 |
| --- | --- |
| `MarketDataPort` | `run` / `stop` / `snapshot` / `health`（Market Data v2 起为 `run`，不再是 `start(subscription)`） |
| `BrokerAccountPort` | `connect` / `disconnect` / `connection_state` / `account_snapshot` / `positions`（**只读**，无 submit） |
| `BrokerExecutionPort` | `connect` / `disconnect` / `submit` / `cancel` / `fills` |
| `StrategyRepositoryPort` | `list_strategies` / `get_strategy`，配 `StrategyRecordView` |
| `OrderRepositoryPort` | `record_intent` / `record_event` / `record_fill` / `status` / `intent` / `fills` |

`MarketDataPort` 另有 `SnapshotListener`（`Callable[[MarketSnapshot], None]`）
与 provider 中立的 `MarketDataCredentialsError`。IBKR 不给 listener：它由桌面
的 snapshot 定时器轮询，再挂 listener 会让每条行情发布两次。

`BrokerAccountPort` 与 `BrokerExecutionPort` 刻意分开：只读账户链路绝不能被
误认为可交易通道。这与现有代码把只读连接和 Paper 订单连接分开是同一条原则。

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

### 8.1 过渡映射

`MainWindow` 现阶段仍是 composition root：它构造旧 page widgets，交给新 shell。

```text
dashboard  → _dashboard_tab()
market     → _quotes_tab()
account    → _account_tab()
strategy   → _strategy_manager_tab()
risk       → _safety_tab()
execution  → _auto_quant_tab()
research   → QTabWidget（针对性验证 / 广域标的池 / 历史数据 / 市场扫描 / 回测 / 横截面研究）
system     → QTabWidget（运行事件 / 系统设置）
```

研究功能不是交易 runtime 主导航，因此收在 `research` 的二级页签里。
每个 v2 页面重写完成后，直接删除对应旧 builder。

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

## 10. 现有模块的未来归宿（roadmap）

```text
market_data_service.py    → trading/application/market_data.py   ✅ 已迁移
ibkr_stream.py            → trading/adapters/ibkr/market_data.py ✅ 已迁移
alpaca_stream.py          → trading/adapters/alpaca/market_data.py ✅ 已迁移
finnhub_stream.py         → trading/adapters/finnhub/market_data.py ✅ 已迁移
ibkr_readonly.py          → trading/adapters/ibkr/account.py     ⏭ 下一轮
strategy_registry.py      → trading/adapters/sqlite/strategy_repository.py
risk.py                   → trading/application/risk.py
paper_order_journal.py    → trading/adapters/sqlite/order_repository.py
ibkr_paper_orders.py      → trading/adapters/ibkr/execution.py
paper_workflow.py         → trading/runtime/session.py
```

**下一条主线：Broker / Account v2。** 它负责把 `ibkr_readonly.py` 的只读账户
与持仓链路搬进 `trading/adapters/ibkr/account.py`，并顺带移除
`trading/application/market_data.py` 里对 `IBKRConnectionConfig` 的过渡依赖
（该依赖已用 `TRANSITIONAL DEPENDENCY` 标注）。

### 10.0 Market Data v2 的过渡残留

以下两点是**已知且已声明**的过渡状态，不属于缺陷：

1. **`IBKRConnectionConfig` 过渡依赖。** `trading/application/market_data.py`
   仍 import 它，因为连接设置事务由尚未迁移的 Broker/Account 链路持有。
   注意即便如此，该模块也**没有** import 任何 adapter、`ibapi` 或
   `ibkr_stream`。
2. **`MarketQuote` 与 `ibkr_readonly.MarketQuote` 并存。** 后者是既有的
   IBKR 专用类型，带 `request_id` / `market_data_type`；它随 Broker/Account
   v2 迁入 adapter 后消失。该重叠在
   `tests/test_trading_architecture.py::KNOWN_TRANSITIONAL_OVERLAPS` 中登记，
   并由 `test_transitional_overlaps_are_exactly_the_documented_ones` 强制
   「不多不少」。

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
旧 MainWindow stream lifecycle         ⏭ Broker/Account v2 之后
旧 readonly account assembly
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

MarketDataService.build_stream / run / stop / update_config /
ensure_config_update_allowed

PreTradeRiskEngine.evaluate()
```

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

**Market Data v2 migration** —— 从 Market Data 开始填 `application/` 与
`runtime/`，把 `market_data_service.py` 迁到
`trading/application/market_data.py`，并以 `MarketDataPort` 为边界接入第一个
真实 adapter。

本轮刻意不创建 `TradingManager`、`TradingGodService`、`GlobalAppState`、
`ServiceLocator` 或 `ApplicationContext`：runtime 由 composition root 显式
组装，不通过全局注册表解析。
