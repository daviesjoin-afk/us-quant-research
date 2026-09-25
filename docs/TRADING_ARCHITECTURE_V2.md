# Trading Core v2 架构

本文件定义 0.20.0 之后的**目标架构**。`docs/DESKTOP_DECOMPOSITION.md` 保留为
历史过程记录（0.20.0 之前的十五步桌面分解），不再往里追加未来总架构。

本轮的交付边界是 **Domain + Ports + UI Shell**。真实 MarketData、IBKR、
Account、Strategy、Risk、Execution 的迁移都在后续轮次，本文档只记录它们的
归宿，不声称已完成。

> **进度更新（Shadow Framework v2）**：`shadow_paper.py` 已删除，Shadow 拆分
> 为 `shadow/{models,store,engine,trade_logic}.py` + `config.py`；唯一 state
> owner 仍是 `ShadowPaperEngine`，`ShadowTradeLogic` 只提供行为。见 §10.7 与 §14。
>
> **进度更新（Trading Framework Closure v2C）**：正式 Trading Runtime 已完全
> 脱离内部 Shadow 模拟器，正式 session config 与 Shadow overlay 分离，
> 五个过渡 root module 已删除。见 §10.6 与 §14。
>
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
>
> **进度更新（Desktop Research v2A）**：Research 的“针对性验证”二级页已迁成
> 原生 `TargetedValidationPage`，旧 `MainWindow._simulation_tab()` 已删除。
> 页面位于 `desktop_v2/pages/research/targeted/`，presenter 是 Qt-free 纯投影；
> Research aggregate 当时仍未计入一级 native route；v2R-F 完成后为 **6 / 8**。
> 见 §8.4。
>
> **进度更新（Desktop Research v2B）**：Research 的“广域标的池”和“历史数据”二级页
> 已分别迁成原生 `UniversePage`、`HistoryPage`，旧 `_universe_tab()` / `_data_tab()`
> 已删除。Universe 刷新/取消控件已从 Dashboard 搬回 UniversePage；History 队列下载、
> 进度和失败状态仍由 MainWindow 编排。Research aggregate 当时仍未计入一级
> native route；v2R-F 完成后为 **6 / 8**。见 §8.5。
>
> **进度更新（Desktop Research v2C）**：Research 的“市场扫描”二级页已迁成原生
> `ScannerPage`，旧 `_scanner_tab()` / `_populate_scan_table()` /
> `_scan_selection_changed()` 已删除，Dashboard 的“运行市场扫描”按钮也已退休。
> 手动扫描 service、任务编排、`self.scan` 真值和图表文件读取仍由 MainWindow 持有。
> Research aggregate 当时仍未计入一级 native route；v2R-F 完成后为 **6 / 8**。
> 见 §8.6。
>
> **进度更新（Desktop Research v2D）**：Research 的“回测”二级页已迁成原生
> `BacktestPage`，旧 `_backtest_tab()` / `_refresh_backtest_strategy_combo()` /
> `_backtest_result_selection_changed()` / `_show_backtest_run()` 已删除，
> MainWindow 不再持有任何 Backtest widget。`self.backtest_runs`、selected run id、
> `_backtest_busy`、StrategySelectionService、BacktestRequest construction 与
> DesktopBacktestService 仍由 MainWindow 持有。Research aggregate 当时仍未计入
> 一级 native route；v2R-F 完成后为 **6 / 8**。见 §8.7。
>
> **进度更新（Desktop Research v2E）**：Research 的“横截面研究”二级页已迁成原生
> `CrossSectionResearchPage`，旧 `_strategy_tab()` / `_populate_strategy_report()` 及
> `_run_strategy_research()` / `_strategy_finished()` / `_load_strategy_report()` 已退休。
> Research capital 真值仍在 MainWindow，Page 只持有 spinbox 并发 `capital_changed`，
> run 信号携带 immutable `CrossSectionResearchDraft`。Report truth、文件读写、算法调用、
> task orchestration 与 artifact refresh 仍在 MainWindow。Research aggregate 当时
> 仍未计入一级 native route；v2R-F 完成后为 **6 / 8**。见 §8.8。

> **进度更新（Desktop Research v2R-F）**：Research 一级 route 已迁成原生
> `ResearchPage` aggregate，二级导航由 `ResearchWorkspace` 语义 identity 驱动；
> MainWindow 仍创建、接线并编排六个 child pages。一级 native route 计数变为
> **6 / 8**。见 §8.9。

> **进度更新（Desktop System v2）**：System 一级 route 已迁成原生 `SystemPage`
> aggregate，二级导航由 `SystemWorkspace` 语义 identity 驱动；`_runtime_tab()`、
> `_settings_tab()`、`v2_system_tabs` 与 `desktop_settings_panel.py` 均已删除，
> 17 个 `settings_*` alias 与 7 个 runtime widget 归属全部退休。一级 native route
> 计数变为 **7 / 8**。见 §8.10。

> **进度更新（v2O-F1 Runtime Events orchestration）**：System 两个 workspace 的第一个
> capability 已迁出窗口——`RuntimeEventsOrchestrator` 成为 Runtime Events 的唯一
> sequencing owner（store 写入、合并重画、resolve、terminal export 成败与 last-export
> fact、页面 render），`RuntimeEventStore` 仍是唯一 persisted truth，
> `RuntimeEventsPage` 仍只 render / emit。见 §8.22。

> **进度更新（v2O-F2 Settings orchestration）**：第二个 workspace 也已迁出——
> `SettingsOrchestrator` 成为 Settings 的唯一 sequencing owner（视图组装与 render、
> 凭据 save/clear 排序、偏好事务适配与 commit fan-out、provider 双向同步、两个
> capability 确认、以及两个 presentation fact）。`UserPreferencesStore` /
> `DesktopSettingsService`（事务顺序）/ `DesktopCredentialService`（存储语义）三个
> canonical owner **逐字未动**，`SettingsPage` 仍只 render / emit，窗口只剩 composition。
> 本轮同时修掉一个真实的 provider 切换顺序 bug：保存被拒时窗口仍会切换行情源（§8.23）。
> Gateway probe **仍未迁**，是否独立 F3 需重新扫描；顶层路线现为
> **v2O-F1 ✅ / v2O-F2 ✅**，且**不**声称 System orchestration complete。

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
| Desktop MarketPage | MIGRATED |
| Desktop TargetedValidationPage | MIGRATED |
| Desktop UniversePage | MIGRATED |
| Desktop HistoryPage | MIGRATED |
| Desktop ScannerPage | MIGRATED |
| Desktop BacktestPage | MIGRATED |
| Desktop CrossSectionResearchPage | MIGRATED |
| Desktop Dashboard | MIGRATED（G1：`DashboardOrchestrator` 持有 render 与 retained chart fact；Dashboard 无 canonical business truth） |
| Desktop Research aggregate | MIGRATED |
| Desktop System | MIGRATED（页面 native v2；Runtime Events = v2O-F1 §8.22，Settings = v2O-F2 §8.23；Gateway probe 经重审定性为 shell/composition diagnostic 留在窗口 §8.24；**v2O-F System COMPLETE**） |
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
| Trading Runtime Config | MIGRATED |
| Paper Execution Health | MIGRATED |
| Paper Trading Service | MIGRATED |
| Desktop Workflow Aggregate | MIGRATED |
| Market Orchestration | MIGRATED（v2O-A，`desktop_v2/orchestration/market/`） |
| Account Orchestration | MIGRATED（v2O-B，`desktop_v2/orchestration/account/`） |
| Universe Orchestration | MIGRATED（v2O-C1，`desktop_v2/orchestration/research/universe/`） |
| History Orchestration | MIGRATED（v2O-C1，`desktop_v2/orchestration/research/history/`） |
| Scanner Orchestration | MIGRATED（v2O-C2，`desktop_v2/orchestration/research/scanner/`） |
| Backtest Orchestration | MIGRATED（v2O-C3，`desktop_v2/orchestration/research/backtest/`） |
| Research Orchestration | **COMPLETE**（v2O-C；Universe / History / Scanner / Backtest / Cross-Section / Targeted Evidence / Targeted Session 全部已迁） |
| Shadow Orchestration | MIGRATED（v2O-D，`desktop_v2/orchestration/shadow/`） |
| Paper Orchestration | MIGRATED（v2O-E1 启动链 + v2O-E2 active runtime + v2O-E3 recovery/finalization/shutdown + v2O-E4 presentation / render closure，`desktop_v2/orchestration/paper/`）；Paper 的 presentation fact 也由 capability 持有（`PaperOrchestrator.presentation`），`MainWindow` 不再持有任何 Paper session 缓存 |
| Runtime Events Orchestration | MIGRATED（v2O-F1，`desktop_v2/orchestration/system/runtime_events/`）；store 仍是唯一 truth，页面仍只 render / emit，窗口只剩 composition（事件路由 / 跨 capability 导出事实 / task-count provider / dialog） |
| Settings Orchestration | MIGRATED（v2O-F2，`desktop_v2/orchestration/system/settings/`）；`UserPreferencesStore` / `DesktopSettingsService`（validate→derive→preflight→guard→persist→apply 顺序冻结）/ `DesktopCredentialService`（存储语义冻结）仍是各自 canonical owner，`SettingsPage` 仍只 render / emit，窗口只剩 composition（九项 intent 转发、commit 采纳与 fan-out、主题、market selection/switch bridge、dialog） |
| Dashboard Orchestration | MIGRATED（G1，`desktop_v2/orchestration/dashboard/`）；`DashboardOrchestrator` 是 `DashboardPage.render` 的唯一 caller 并持有 retained chart fact；四个输入（account portfolio / market snapshot / artifact catalogue / market stop reason）全部经由 callable provider 现读，**不缓存**；无 canonical business truth |
| Generic Runtime / Shell Ownership | MIGRATED（G1）：worker collection 归 `DesktopTaskController`（`running_workers` / `has_running_workers` / `active_count`，collection 不外借），shutdown admission 归 `RuntimeSupervisor.shutting_down`（`_closing` / `closing_gate` 已删），closeEvent 为 composition-only，Gateway probe 为 shell diagnostic |

Shadow 子系统：

| 组件 | 状态 |
| --- | --- |
| Shadow Config | MIGRATED（`shadow/config.py`） |
| Shadow Models | MIGRATED（`shadow/models.py`） |
| Shadow Store | MIGRATED（`shadow/store.py`） |
| Shadow Engine | MIGRATED（`shadow/engine.py`） |
| Shadow Trade Logic | MIGRATED（`shadow/trade_logic.py`） |
| `shadow_paper.py` | DELETED |

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
缺陷见 §10.8。

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
market     → desktop_v2/pages/market/               ✅ native v2
account    → desktop_v2/pages/account.py            ✅ native v2
strategy   → desktop_v2/pages/strategy.py           ✅ native v2
risk       → desktop_v2/pages/risk.py               ✅ native v2
execution  → desktop_v2/pages/execution/            ✅ native v2
research   → desktop_v2/pages/research/           ✅ native v2 aggregate
system     → desktop_v2/pages/system/             ✅ native v2 aggregate
```

前端进度：**7 / 8 native v2**。Research 与 System 一级 route 均已由原生 aggregate
承接；只剩 Dashboard 仍是 transitional。Research orchestration 已全部抽出；System
orchestration 两个 workspace 都已抽出（Runtime Events = v2O-F1 §8.22，
Settings = v2O-F2 §8.23），只剩 Gateway probe 仍在窗口。

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

### 8.3 market 页已完成（Desktop Market v2）

`MainWindow._quotes_tab()`（188 行 builder）与它拥有的两个滚动处理器
`_quotes_scroll_started` / `_quotes_scroll_finished`，以及
`_populate_stream_snapshot`，已**删除**。market route 现在是一个原生 v2 page，
`_quotes_tab` 不存在，也没有 compatibility shim。

```text
desktop_v2/pages/market/
  __init__.py   ≤ 40 行   只导出 MarketPage
  models.py    ≤ 180 行   不可变展示模型（MarketQuoteRow / MarketPageView / …）
  rows.py      ≤ 260 行   行情行投影 + 显示格式化（price / symbol_text / feed_label）
  presenter.py ≤ 300 行   cards、readiness、health、control state、view 组装
  tables.py    ≤ 320 行   QuoteTableModel / QuoteTable（增量更新 + 排序 + 调色）
  controls.py  ≤ 280 行   MarketControls：订阅 / provider / 三个按钮
  page.py      ≤ 380 行   MarketPage：组合、render、signal 转发、滚动冻结
```

职责边界：

```text
MainWindow            取数 + 编排（credentials / StreamWorker / 安全门仍在这里）
presenter / rows      纯投影：facts → strings + tone（Qt-free）
MarketPage            只 render + emit intent；拥有 widgets 与滚动重绘状态
```

- **Page 只渲染、只报告意图。** 它不持有 `MarketDataApplication`、
  `StreamWorker`、credential store、adapter 或 broker；四个按钮只 emit signal。
  provider combo 的**程序化** setter 刻意静默，否则设置页与行情页两个 combo
  会互相驱动成环。
- **Page 不能决定 start / stop / switch 是否合法。** 它收到的是
  `MarketControlView`（一组布尔），因此它没有 Paper / Shadow 会话词汇可以据以
  行动。`worker.start()` 之后才 publish execution controls 的顺序（Execution v2
  修过的那条）保持不变，并有 characterization test 钉住；反之会让
  stop-stream 控件在刚启动的瞬间误禁用。
- **凭证与请求仍由窗口构造。** `MarketDataCredentials` /
  `MarketDataStartRequest` / `StreamWorker(MarketDataApplication, request)`
  一律留在 `MainWindow`；页面只提供 `subscription_draft()` 这个**输入快照**。
  `symbols` 的 split / strip / upper / dedupe 属展示边界，但「至少 1 个 / 最多
  30 个 / 运行期可否切换」仍是业务 gate，不靠 `QLineEdit` 实现。
- **Presenter / rows Qt-free。** `models.py` / `rows.py` / `presenter.py` 不
  import PySide6，投影可以不用 widget 测试
  （`tests/test_desktop_v2_market_presenter.py` 就是纯 Python）。表格不再解释
  `MarketDataMode` / `realtime_ready` / `stale`，只吃 `MarketQuoteRow`。
- **readiness 与 scope 仍由窗口计算。** `calculate_quote_readiness_breakdown`
  依赖 auto quant candidates、market reference symbols、universe 与 scan，
  这些不是页面业务；窗口算完转成 `MarketReadinessFacts` 与一行 scope 文本再交给
  页面。
- **滚动冻结搬进页面。** `_quotes_scroll_active` / `_pending_stream_snapshot`
  已从 `MainWindow` 删除，改为页面私有的 `_scroll_active` / `_pending_view`：
  拖动滚动条时暂缓 render，释放时只画最新一帧。
- **旧 `QuoteTableModel` 退休。** 它从 `desktop_widgets.py` 删除，行情 route
  只有 `desktop_v2/pages/market/tables.py` 一个 table model；保留了原来的
  增量行为（symbol set 变化 reset，同 symbols 更新 `dataChanged`）、排序、数值
  排序与主题重着色。数值排序键仍复用 `desktop_widgets._sortable_number`，避免
  两个实现漂移。
- **shell 全局 badge 不属于页面。** `market_badge` / `handshake_badge` /
  `signal_card` 与节流 status log 继续由窗口更新，页面不拿 shell 或导航。

守卫（`tests/test_desktop_v2_market_architecture.py`，A–H）：

- `_quotes_tab` 与两个滚动处理器必须不存在；
- 15 个旧 market widget 名不得成为 `MainWindow` 属性；
- market 包不得 import `desktop` / `trading.application` / `composition` /
  `adapters` / `credential_store` / `desktop_credentials` / `ibkr` / `shadow` /
  `paper` / `auto_quant` / `ibapi`；
- 也不得**构造** `MarketDataStartRequest` / `MarketDataCredentials` /
  `StreamWorker` / `build_market_data_application` / `probe_ibkr_socket`；
- `models.py` / `rows.py` / `presenter.py` 不得 import PySide6；
- `QuoteTableModel` 全树只有一个定义，且只在 market `tables.py`；
- 每个文件有独立行数预算（见上表），并不共用 400 行硬上限；
- 不得出现 manual order / market order / cancel / quantity input / risk
  override 一类新交易能力。

行为不变的冻结项：provider 语义（Alpaca IEX 单交易所实时；Finnhub 实时成交
±5bps 模拟带，非 NBBO；IBKR 以 marketDataType 回调为准，Type 2/3/4 只可观察）、
readiness 语义（fresh AND realtime AND bid exists AND ask exists AND bid > 0
AND ask >= bid）、30-symbol 上限、stop 安全门（Paper active / 持仓 / 在途订单
拒绝普通停止；Shadow active 先停 Shadow；允许的自动会话切换例外路径保留）、
provider switch 与 IBKR 5×24 rotation、以及表格 14 列与列宽。


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

### 8.4 targeted validation 页已完成（Desktop Research v2A）

`MainWindow._simulation_tab()`（legacy Research 第一个二级页 builder）已删除，
没有 compatibility shim。新的 native page 位于：

```text
desktop_v2/pages/research/targeted/
  __init__.py           22 行   只导出 TargetedValidationPage
  models.py            175 行   不可变展示模型（Qt-free）
  rows.py              285 行   研究结果 → 表格行 / 显示字符串（Qt-free）
  session_presenter.py 185 行   ShadowSnapshot / preflight / controls → session view
  evidence_presenter.py 197 行  replay / robustness / review 等 → evidence view
  controls.py          181 行   strategy / target / Shadow / evidence 控件
  tables.py            121 行   稳定 run-id selection、排序、tone 渲染
  session_panel.py     133 行   5 个卡片 + controls + position / fill 面板
  evidence_panel.py    220 行   7 个证据 tab
  page.py              169 行   render + intent signal 转发
```

职责边界：

```text
MainWindow                 取数、research pipeline、result caches
                           （Shadow engine ownership 已于 v2O-D 迁出，见 §8.17）
presenters / rows          facts → immutable presentation view（Qt-free）
TargetedValidationPage     只 render + emit intent
```

- **Research aggregate 当时仍 transitional。** 本轮只替换第一个二级页；其余
  二级页后来已迁完，v2R-F 后 aggregate 也已 native。
- **页面不持有业务对象。** `ShadowPaperEngine` / `ShadowPaperStore` /
  `run_targeted_*` / `MarketPage` / `MarketDataApplication` 都不在 targeted UI
  package 里；目标订阅由页面 signal 交回 `MainWindow`，再由窗口调用
  `MarketPage.set_subscription_symbols(...)`。
- **preflight / Shadow safety / Paper capital 全部留在窗口。** 页面只显示窗口
  提供的 boolean control state 和 immutable facts，不自行判断 research
  eligibility、fresh quote、Paper 资金或 Shadow 启动合法性。
- **结果选择按 run_id。** robustness history 与 review history 通过
  `robustness_run_selected(run_id)` / `review_run_selected(run_id)` 交回窗口，
  不依赖 table row index；刷新时保留当前 run_id selection。
- **表格行为保持。** 列数、列顺序、标题、排序能力和 warning/success 着色列按
  旧 UI 迁移；review gate 明细表继续禁用排序。
- **主题与行数守卫。** `MainWindow._apply_theme()` 通知页面刷新 palette；新文件
  使用 §6 的 per-file budgets，`page.py <= 320`，所有 production file <= 400。

新增守卫位于 `tests/test_desktop_v2_targeted_architecture.py`，覆盖：

```text
_simulation_tab retired
MainWindow no longer owns targeted widgets
targeted package imports no business/runtime/executor modules
presenter modules are Qt-free
page does not construct Shadow engine or run_targeted_*
MainWindow reaches the page only through its public API
per-file line budgets
no new order capability
```

### 8.5 universe / history 页已完成（Desktop Research v2B）

`MainWindow._universe_tab()` 与 `MainWindow._data_tab()` 已删除，没有 compatibility
shim。新的 native page 位于：

```text
desktop_v2/pages/research/universe/
  __init__.py      7 行   只导出 UniversePage
  models.py       51 行   不可变展示模型
  presenter.py   128 行   UniverseSnapshot → immutable rows / controls（Qt-free）
  page.py        142 行   search / filter / table / refresh intent

desktop_v2/pages/research/history/
  __init__.py      7 行   只导出 HistoryPage
  models.py       35 行   不可变展示模型
  presenter.py    70 行   HistoryQueueSnapshot → immutable rows / summary（Qt-free）
  page.py        109 行   four actions / batch input / progress / queue table
```

职责边界：

```text
MainWindow                 UniverseSnapshot / service / TaskThread / cancellation
                           HistoryService / task orchestration / progress fact
presenters / models        facts → immutable presentation view（Qt-free）
UniversePage               local search / filter / table + refresh/cancel intent
HistoryPage                batch draft / buttons / progress / table
```

- **Dashboard 只删除了 Universe refresh/cancel 两个控件。** scan、Gateway、cards、
  chart、artifact table 与 notes 继续留到后续 Dashboard v2。
- **Universe 刷新流程冻结。** `_refresh_universe()` 继续拥有 cancellation event、
  TaskThread 和 `DesktopUniverseService.refresh()`；页面只发出
  `refresh_requested` / `cancel_refresh_requested`。
- **Universe 搜索与过滤仍是纯展示。** 页面本地使用稳定 filter key
  (`research` / `trading` / `all` / `excluded`)；`eligible_for_research`、
  `eligible_for_trading`、国家证据与排除文案的语义保持不变，表格上限为 2,500。
- **History 服务语义冻结。** `schedule_universe(limit=None)`、IBKR 不自动 reset failed、
  public 路径先 reset failed、显式 retry reset failed 均未改变。
- **History 进度状态移出 widget。** `MainWindow._history_progress_percent` 是业务
  进度事实；`_history_finished()` / `_history_task_failed()` 统一发布
  `_publish_history_view()`。generic `_task_failed()` 不再直接访问 History widget。
- **表格列与排序冻结。** Universe 9 列、History 7 列、`configure_table(...)` 的
  read-only / row selection / sorting 行为保持；2500 行只截断 UI，不截断任务队列。
- **研究内容没有改变。** Universe service、History service、queue store、IBKR/public
  history 执行器与 Targeted v2A 均未被重写；Scanner 本轮只迁 UI，Backtest 与
  Cross Section 保持 legacy。

新增守卫位于 `tests/test_desktop_v2_research_data_architecture.py`，覆盖：

```text
_universe_tab / _data_tab retired
MainWindow owns no Universe/History widgets
Dashboard owns no Universe refresh/cancel action
page packages import no executors
presenters are Qt-free
MainWindow uses only render/set_palette
per-file line budgets
```

### 8.6 scanner 页已完成（Desktop Research v2C）

`MainWindow._scanner_tab()`、`_populate_scan_table()` 与
`_scan_selection_changed()` 已删除，没有 compatibility shim。新的原生页面位于：

```text
desktop_v2/pages/research/scanner/
  __init__.py   lazy export ScannerPage
  models.py     不可变展示模型（ScannerFilterMode / ScannerRowView / ScannerPageView）
  presenter.py  ScanResult → immutable rows / coverage（Qt-free）
  table.py      ScannerTable：headers / numeric sorting / trend tone / symbol selection
  page.py       search / filter / scan intent / coverage / table / chart
```

职责边界：

```text
MainWindow        self.scan、DesktopMarketScanService、TaskThread、图表文件读取
presenter / rows  ScanResult → strings + booleans（Qt-free）
ScannerPage       local search/filter/table/chart + scan/symbol intent
```

- **Dashboard 的人工扫描按钮已退休。** Dashboard 保留 Gateway 检查、cards、chart、
  artifact table 与 notes；Scanner 唯一人工入口是 `ScannerPage → 重新扫描`。
- **扫描真值没有改变。** `_run_scan()` 仍负责 universe 检查、research capital、
  risk limit、substitutions 与 `resource_group="scan"`；service 和 scanner domain
  未被修改。
- **UI filter 只改显示。** Market watchlist 与 AutoQuant candidate 路径继续读取完整
  `self.scan`，不读取 ScannerPage 的当前显示行。
- **图表仍由窗口取数。** 页面 selection 发 `symbol_selected(symbol)`，MainWindow
  调用 `load_close_series()` 后通过 `render_chart()` 回填；页面不读 filesystem。
- **冻结行为。** 13 列表头、numeric sorting、趋势候选仅 0/4/5 列 success tone、
  score descending、首行选择与自动图表加载、空结果不发空 symbol 均保持。

新增守卫位于 `tests/test_desktop_v2_scanner_architecture.py`，覆盖 legacy surface、
MainWindow widget ownership、Dashboard scan action、executor imports/calls、
Qt-free models/presenter、lazy package initializer、MainWindow public surface、
业务路径不读取显示行与 per-file line budgets。v2R-F 完成后一级 native route
计数为 **6 / 8**；Research aggregate 见 §8.9，Cross Section 见 §8.8，Backtest 见 §8.7。

### 8.7 backtest 页已完成（Desktop Research v2D）

`MainWindow._backtest_tab()`、`_refresh_backtest_strategy_combo()`、
`_backtest_result_selection_changed()` 与 `_show_backtest_run()` 已删除，没有
compatibility shim。新的原生页面位于：

```text
desktop_v2/pages/research/backtest/
  __init__.py   lazy export BacktestPage
  models.py     immutable strategy/form/metric/table/chart/page views
  presenter.py  BacktestRun → immutable display views（Qt-free）
  controls.py   strategy / form / cost widgets + BacktestFormDraft intent
  tables.py     comparison / trades tables + numeric sorting + run-id selection
  page.py       five metric cards / controls / evidence / chart / tables
```

职责边界：

```text
MainWindow        StrategySelectionService、BacktestRun truth、selected run id、
                  busy fact、BacktestRequest construction、DesktopBacktestService、TaskThread
presenter         BacktestRun → immutable strings / points（Qt-free）
BacktestPage      render + hold UI draft + emit intent
```

- **form ownership 已迁移。** 策略 combo、symbol/date/capital/weight、费用字段、
  两个 run buttons 都由 `BacktestControls` 持有；Page 只捕获 `BacktestFormDraft`，
  真正的 `BacktestRequest` 仍在 MainWindow 构造。
- **run truth 没有迁移。** `self.backtest_runs`、`_selected_backtest_run_id` 和
  `_backtest_busy` 仍由 MainWindow 持有；run selection 使用完整 run id，不依赖 row index。
- **业务语义保持。** BACKTEST eligibility、single/combo selection、compare-all latest
  per strategy、日期/空策略 gate、Decimal cost conversion、service partial-commit
  与 metrics/trade/evidence formatting 均未改变。
- **冻结行为。** 13 列 comparison/trades headers、numeric sorting、空结果 metric
  notes、chart 最近 180 points、BUY/SELL 中文显示、substitution suffix、theme
  switch 不触发 run selection 均保持。
- **generic worker 已解耦。** `_worker_finished()` 不再触碰任何 Backtest widget；
  unrelated worker finish 不会改变 `_backtest_busy` 或解锁 run buttons。

新增守卫位于 `tests/test_desktop_v2_backtest_architecture.py`，覆盖 legacy surface、
MainWindow widget ownership、executor imports/calls、`BacktestRequest` construction、
Qt-free models/presenter、lazy package initializer、MainWindow public surface、worker
coupling 与 per-file line budgets。v2R-F 完成后一级 native route 计数为 **6 / 8**。

### 8.8 cross-section research 页已完成（Desktop Research v2E）

`MainWindow._strategy_tab()` 与 `_populate_strategy_report()` 已删除，没有兼容 shim；
`_run_strategy_research()` / `_strategy_finished()` / `_load_strategy_report()` 已分别
改名为 `_run_cross_section_research()` / `_cross_section_finished()` /
`_load_cross_section_report()`，避免和 Strategy governance 混淆。新页面位于：

```text
desktop_v2/pages/research/cross_section/
  __init__.py   lazy export CrossSectionResearchPage
  models.py     immutable draft / metric / chart / candidate / fold / page views
  presenter.py  raw report → immutable display view（Qt-free）
  controls.py   research capital spinbox + run button + warning + draft intent
  tables.py     candidate / fold tables + frozen headers + numeric sorting
  page.py       five metric cards / controls / comparison chart / tables
```

职责边界：

```text
MainWindow        central research capital、`self.cross_section_report`、report path、
                  AppConfig replacement、executable research invocation、save file、
                  task orchestration、artifact refresh
presenter         raw report → immutable strings / floats（Qt-free）
CrossSectionPage  render + hold UI controls + emit capital/run intent
```

- **research capital ownership 已收口。** `_research_capital_value` 是 MainWindow 的
  唯一标量真值；`_research_scenario_capital()` 不再读取任何 QWidget。CrossSection
  spinbox 通过 `capital_changed` 同步该标量，Account research-capital card 由同一
  handler 刷新；Scanner、Targeted、AutoQuant 等路径继续消费 MainWindow 真值。
- **run intent 带 immutable draft。** `run_requested` 负载是
  `CrossSectionResearchDraft(research_capital=...)`；MainWindow 收到后构造 research
  config，算法、`resource_group="strategy"`、进度文案与 report schema 均不变。
- **报告真值没有迁移。** `self.cross_section_report`、`self.cross_section_path`、
  JSON 读写、失败清空与 Dashboard artifact refresh 仍由 MainWindow 持有；Page 只接收
  presenter 投影出的 `CrossSectionResearchView`。
- **命名收口。** MainWindow 不再出现 `strategy_report` / `strategy_path` /
  `research_capital_input` / `strategy_chart` 或旧 Cross Section builder/materialiser。
- **冻结行为。** 五张 metric card 文案、empty headers 与 report headers、numeric
  sorting、chart adapter、候选表仍按 fold 投影、artifact refresh 均保持。

新增守卫位于 `tests/test_desktop_v2_cross_section_architecture.py`，覆盖 legacy surface、
ambiguous state names、MainWindow widget ownership、executor imports/calls、Qt-free
models/presenter、lazy package initializer、MainWindow public surface、central capital
dependency 与 per-file line budgets。v2R-F 完成后一级 native route 计数为
**6 / 8**。

#### 8.8.1 v2O-C4：上表的 ownership 已被取代

上一节记录的是 v2E 当时的边界（report 真值与 research capital 都在 MainWindow）。
**v2O-C4 已把两者都搬走**，此处只留结论，详细设计见
`docs/DESKTOP_DECOMPOSITION.md` §21：

```text
report truth        -> CrossSectionOrchestrator._report（private）
report artifact     -> DesktopCrossSectionService（load/save pair 在 executable_research.py）
research capital    -> ResearchScenarioCapitalState（7 个消费者的唯一 owner）
page render         -> CrossSectionOrchestrator（唯一 production caller）
```

MainWindow 上不再有 `self.cross_section_report` / `self.cross_section_path` /
`self._research_capital_value`，也不再声明 `_research_scenario_capital()` 或
`_research_capital_changed()` —— 没有 compatibility alias，也没有 forwarding wrapper，
所以 grep `research_scenario_capital` 就能看到全部消费者。

**commit 线契约（review 修正后）**：`_report_finished` 必须在 commit *之前* 把整条
success path 可能失败的东西全部准备完（type check / projection / **完成日志文案及其数值
格式化**），commit 之后不得再有任何可能因 result 失败的操作。原因是投影用 `float(...)`
强转、而日志用裸 `{:+.1%}`，数字字符串报告会出现「投影成功但 logger 抛
`ValueError`」，而那时 truth 已移动、页面已重画、artifact bridge 已触发。失败统一
normalize 成 `TypeError`（`__cause__` 保留原始异常）。

### 8.9 research aggregate 页已完成（Desktop Research v2R-F）

Research 一级 route 已由 `ResearchPage` aggregate 承接，旧的 MainWindow-owned Research
`QTabWidget`、`v2_research_tabs` 与六个 `addTab()` 调用已删除。目录保持很小：

```text
desktop_v2/pages/research/
  __init__.py    lazy export ResearchPage / ResearchWorkspace
  navigation.py  Qt-free ResearchWorkspace + frozen navigation table
  page.py        receive six existing QWidget instances and own QTabWidget
  targeted/ universe/ history/ scanner/ backtest/ cross_section/
```

职责边界：

```text
MainWindow       creates / wires / orchestrates the six child pages
ResearchPage     validates the complete workspace mapping and owns secondary tabs
navigation.py    stable semantic identity + fixed order / labels
```

- **语义导航。** 生产与预览统一使用
  `research_page.set_active_workspace(ResearchWorkspace.BACKTEST)`，读取使用
  `active_workspace()`；Research secondary navigation 不再依赖 magic index。
- **fail closed。** 构造时缺少或混入未知 workspace 会直接 `ValueError`；非法
  workspace 请求不会 fallback 到 Targeted，也不会半切换 UI。
- **六页冻结。** TargetedValidationPage、UniversePage、HistoryPage、ScannerPage、
  BacktestPage、CrossSectionResearchPage 的内部实现未在本轮重构。
- **编排边界。** Research UI ownership = migrated；Research orchestration =
  still MainWindow transitional。MainWindow 仍持有六个 child refs 与业务 wiring。

新增守卫位于 `tests/test_desktop_v2_research_page.py` 与
`tests/test_desktop_v2_research_architecture.py`，覆盖 navigation order/labels、child
identity/state、fail-closed behavior、MainWindow route ownership、preview semantic
navigation、no child-page imports、Qt-free contract、lazy initializer 与 line budgets。

### 8.10 system aggregate 页已完成（Desktop System v2）

System 一级 route 已由 `SystemPage` aggregate 承接，旧的 MainWindow-owned System
`QTabWidget`、`v2_system_tabs`、`_runtime_tab()` 与 `_settings_tab()` 已删除。
`src/us_quant/desktop_settings_panel.py`（452 行）也随本轮删除，不保留任何
compatibility shim。

```text
desktop_v2/pages/system/
  __init__.py        lazy export SystemPage / SystemWorkspace
  navigation.py      Qt-free SystemWorkspace + frozen navigation table
  page.py            receive two existing QWidget instances and own QTabWidget
  runtime_events/
    models.py        frozen RuntimeEventRowView / RuntimeEventsPageView
    presenter.py     Qt-free projection（错误/警告/任务计数、导出事实、行）
    table.py         numeric ID sort、tone、stable event_id selection
    page.py          owns cards / buttons / table / info panel
  settings/
    models.py        frozen SettingsDraft / CredentialDraft / views
    appearance.py    theme + default market provider + switch
    credentials.py   API provider + secret inputs + actions
    connection.py    IBKR host/port/client/timeout + Paper capability toggles
    page.py          compose sections + storage text + save button
```

契约边界：

```text
RuntimeEventsPage   owns widgets / table / cards
MainWindow          owns RuntimeEventStore / export / refresh coalescing
runtime presenter   facts → immutable view → RuntimeEventsPage.render()

SettingsPage        owns every Settings widget
MainWindow          owns transaction / credential service / safety gates
settings view       SettingsPageView → SettingsPage.render()
```

- **语义导航。** 生产与预览统一使用
  `system_page.set_active_workspace(SystemWorkspace.SETTINGS)`；System secondary
  navigation 不再依赖 magic index。
- **两类 UI 耦合清零。** `MainWindow → runtime_event_table` 与
  `MainWindow → 17 个 settings_* widget alias` 已全部退休；`SystemPage` 只做
  containment / navigation。
- **intent 是 immutable。** Runtime page 发 `refresh_requested` /
  `resolve_requested(int | None)` / `export_requested`；Settings page 发
  `SettingsDraft` / `CredentialDraft` / provider string / bool。
- **业务边界。** RuntimeEventsPage 不碰 `RuntimeEventStore`；SettingsPage 不碰
  `DesktopSettingsService` / credential service / Market / Broker application；
  Page 不构造 `UserPreferences`。
- **冻结不变。** `runtime_events.py` 的 SQLite schema、`desktop_settings.py` 的
  validate→derive→preflight→guard→persist→apply 顺序、IBKR 端口 4002、Paper 安全
  确认、active-provider credential clear gate 与 1 秒 refresh coalescing 全部未改。
- **编排仍暂留 MainWindow。** System UI ownership = migrated；System orchestration
  = still MainWindow transitional。MainWindow 仍持有 store、settings service、
  credential service 与全部 handler。
  **（前向引用）** 其中 Runtime Events 一半已由 v2O-F1 迁出（§8.22），Settings 一半已由
  v2O-F2 迁出（§8.23）：窗口不再持有 store alias、refresh stamp / pending 标志、
  last-export fact、`_settings_api_provider` / `_connection_settings_enabled`，也不再
  组装任一页面的 view 或 render 它们。本文 §8.10 描述的状态现在只对**已经不存在**的
  窗口内编排成立，保留为历史记录。**（前向引用结束）**

新增守卫位于 `tests/test_desktop_v2_system_page.py`、
`tests/test_desktop_v2_system_architecture.py`、`tests/test_desktop_v2_settings_*.py`
与 `tests/test_desktop_v2_runtime_events_*.py`，覆盖 navigation order / labels、
child identity / state、fail-closed behavior、route ownership、preview semantic
navigation、no store / service imports、Qt-free contract、lazy initializer、
stable-id resolve、programmatic-setter silence、render 只写外部事实与 line budgets。

### 8.11 market orchestration 已抽出（v2O-A）

八个一级 route 全部 native v2 之后，`MainWindow` 仍是唯一的 orchestration owner。
本轮把 **Market runtime truth** 从窗口搬进
`desktop_v2/orchestration/market/`：

```text
desktop_v2/orchestration/
  __init__.py           包 docstring；本轮只有 market 一个 capability
  market/
    __init__.py         lazy export MarketOrchestrator + 三个 fact 类型
    models.py           Qt-free：MarketReadinessInputs / MarketRuntimeEvent /
                        MarketShellHealthView
    health.py           ShellHealthPublisher：badge 投影 + 30 秒节流状态行
    renderer.py         MarketRenderer：page 投影 + scope / watchlist note +
                        recently-ready cache
    orchestrator.py     MarketOrchestrator：worker / snapshot / timer /
                        pending switch / stop-pending / lifecycle
```

职责边界：

```text
MainWindow            construct MarketPage + MarketOrchestrator、connect signals、
                      跨 workflow interlock（Paper / Shadow）、snapshot fan-out
MarketOrchestrator    worker、snapshot、poll timer、pending switch、stop-pending、
                      生命周期、runtime event 请求
MarketRenderer        page render、scope 行、watchlist note、recently-ready cache
ShellHealthPublisher  market / handshake badge 文本与状态、节流后的状态行
```

- **单一真相。** `stream_worker` / `stream_snapshot` / `stream_timer` /
  `_pending_stream_switch` / `_stream_stop_pending` / `_market_scope` /
  `_market_watchlist_note` / `_dashboard_market_stop_reason` /
  `_quote_last_ready_monotonic` / `_last_stream_event_key` /
  `_last_stream_status_key` / `_last_stream_status_log_at` /
  `_last_stream_push_monotonic` 已从 `MainWindow` 全部消失，且**没有**任何
  compatibility property 或镜像 state。
- **公开读取面。** 只有 `snapshot` / `is_live` / `worker_running` /
  `active_source_id` / `active_market_exchange` / `stop_reason` /
  `polling_active` / `was_recently_ready()` / `recently_ready_symbols()`；
  worker、timer、pending switch 与 readiness dict 保持私有，window 不允许
  reach-through `market_orchestrator._*`。
- **两个 liveness 事实，语义不同，不可互换。** `is_live` = **feed 可用性**：
  `not _stop_pending and _worker_running`，stop 一旦 pending 就为 False，
  业务门（market controls / execution stop-stream / preflight / Shadow
  launch gate）继续读它。`worker_running` = **底层 StreamWorker 线程是否真的
  还在跑**：stop 超时（`worker.wait(3000)` 返回 False）时 `is_live == False`
  而 `worker_running == True`，shutdown 必须读这一个。它是只读 bool，**不是**
  worker handle：`market_orchestrator.worker` / `._worker` 与任何
  QThread/StreamWorker 对象仍然禁止泄露到 MainWindow。
- **跨 workflow gate 暂留窗口。** 停止 / 切换仍要先读 Paper 持仓、在途订单与
  Shadow 是否 active，所以 `_request_market_start` / `_request_market_stop` /
  `_request_market_switch` / `_request_market_switch(allow_auto_session_switch=True)` /
  `_stop_market_data` 留在窗口，且只做「读事实 → 判定 → 调 orchestrator」。
  `MarketOrchestrator` 不 import Paper / Shadow / MainWindow。
- **snapshot fan-out 本轮不拆。** `_on_market_snapshot_changed()` 只做跨 workflow
  分发：`workflow_controller.market_account` readiness、Dashboard、minute
  evidence、Auto Quant 候选、targeted preflight、Paper `on_stream`、Shadow
  `on_stream`。它不再 render page、不碰 worker、不维护 readiness cache。
- **shell 与事件是请求，不是写入。** badge 由 `MarketShellHealthView` 描述后由窗口
  绘制；runtime event 通过 `runtime_event_requested` 请求窗口记录，因此不存在
  `Market → System` 反向依赖。
- **shutdown 顺序不变。** supervisor 与 `closeEvent` 使用
  `stop_polling` / `polling_active` / `worker_running`（**不是** `is_live`，
  否则超时停止会被判成 clean release）；Paper shutdown ordering 未动。
  `closeEvent` 最终检查同样是 `worker_running`：只要行情网络线程没真正退出就
  `event.ignore()`，即使 `is_live` 已经是 False 也不放行。
- **IBKR 时段轮换仍是请求。** orchestrator 检测 venue 变化后 emit
  `automatic_switch_requested`，由窗口以 `allow_auto_session_switch=True` 走
  interlock，不会自行绕过 Paper gate。

新增守卫位于 `tests/test_desktop_market_orchestration_architecture.py`、
`tests/test_desktop_market_orchestrator.py` 与
`tests/test_desktop_market_orchestration_wiring.py`，覆盖已删 state / 无
compatibility property / 无 reach-through / orchestrator 的禁止依赖 /
render 单一调用者 / StreamWorker 单一持有者 / snapshot fan-out 不夺回 ownership /
per-file line budgets（`orchestrator.py ≤ 550`）/ `desktop.py` 相对 base commit
净减少 ≥ 300 行。

`desktop.py`：**5847 → 5432 行（净减 415）**；`orchestrator.py` 544 行。

### 8.12 account orchestration 已抽出（v2O-B）

v2O-A 之后，account route 的桌面 truth 仍散在 `MainWindow`：最后一份
portfolio、refresh task、ledger append、page render 与 shell badge 由若干个
handler 分别触碰。本轮把它们搬进 `desktop_v2/orchestration/account/`：

```text
desktop_v2/orchestration/account/
  __init__.py      export AccountOrchestrator + 三个 fact 类型 + fresh-Paper query
  models.py        Qt-free：AccountPresentationInputs / AccountRuntimeEvent /
                   AccountShellHealthView
  queries.py       Qt-free：fresh_paper_net_liquidation（300 秒新鲜度规则）
  orchestrator.py  AccountOrchestrator：refresh 请求、ledger append、
                   page render、shell 事实发布
```

职责边界：

```text
MainWindow           construct AccountPage + AccountOrchestrator、connect signals、
                     跨 workflow fan-out（Dashboard / Auto Quant / Targeted）、
                     构造并共享 BrokerAccountApplication
AccountOrchestrator  refresh 请求、ledger append、page render、shell 事实、
                     runtime event 请求
queries              fresh-Paper NLV 规则（纯函数，可单测）
```

- **单一真相，且不是第二份。** `BrokerAccountApplication` 仍是 canonical
  account truth（config / portfolio / last_error / refresh lifecycle）。
  `AccountOrchestrator` **不存** `self._portfolio`：
  `orchestrator.portfolio is application.portfolio`，是只读委托而不是拷贝。
  `MainWindow` 的 `self.account_portfolio` 已删除，且**没有** compatibility
  property；所有 caller 改成显式 `self.account_orchestrator.portfolio`。
- **窗口删除的方法。** `_refresh_account_snapshot` /
  `_account_snapshot_finished` / `_refresh_account_surfaces` /
  `_paper_simulation_capital` 全部删除，不留 wrapper；窗口不再调用
  `account_page.render(...)`、`account_ledger.append(...)`、
  `account_ledger.list_points(...)`，也不再调用 `broker_account.refresh`。
- **refresh 进入 orchestrator。** `AccountPage.refresh_requested` →
  `AccountOrchestrator.request_refresh` → 注入的 `submit_task`
  （`resource_group="broker"`）→ `BrokerAccountApplication.refresh(timeout_seconds=20)`。
  orchestrator 不知道 `TaskThread` / worker list / closing gate / busy dialog，
  generic task lifecycle 仍属窗口（§八）。
- **generic task lifecycle 仍在窗口。** `TaskThread`、`DesktopTaskController`、
  worker 列表、关闭准入闸门与取消处理一律不搬；orchestrator 只拿到一个窄
  callable `submit_task`。等 v2O-C Research 成为第二个真实消费者再决定是否
  提炼共享 task protocol。
- **page render 只有一个 owner。** `render_current()` 读
  `application.portfolio` + ledger 后调 `AccountPage.render(...)`；它**不 fetch**，
  否则就是第二条 refresh 路径。窗口不再是 AccountPage 的 render 调用者。
- **ledger 归 capability。** `AccountLedger` 仍由窗口作为 composition root
  构造并注入，但业务使用（append / list_points）只在 Account capability 内。
- **exposure multiplier 是 pushed presentation input。**
  `AccountPresentationInputs.of(...)` 把 `symbol -> multiplier` 冻结成
  tuple（避免外部 dict 被就地改动而悄悄改变 view），由窗口推入；
  orchestrator 不知道这些 multiplier 从哪里算出来，因此不会 import
  `RiskApplication` / Strategy / app config。
- **跨 workflow fan-out 暂留窗口。** `_on_account_portfolio_changed()` 只执行
  既有 downstream side effect（Dashboard publish、Auto Quant runtime view、
  Targeted preflight、Auto Quant preflight），不 render page、不碰 ledger、
  不请求 refresh；它只 fan-out finished `portfolio` fact。
- **research capital 仍是 Research 的事。** 本轮只把
  `account_page.research_capital_card.set_value(...)` 这个 widget reach-through
  改成 `AccountPage.set_research_capital(...)` 命名方法，scalar 仍由窗口持有；
  最终 owner 留给 v2O-C。`set_notice(...)` 已是正确的 page-level API，保留。
- **shell 与事件是请求，不是写入。** badge 由 `AccountShellHealthView` 描述后
  由窗口绘制（`_render_account_shell_health` 只写 text/state/tooltip + repolish，
  不读 portfolio、不写 page）；runtime event 通过 `runtime_event_requested`
  请求窗口记录，因此不存在 `Account → System` 反向依赖。
  **account 链永远不写 market badge**：行情是否实时是 Market Data v2 的答案。
- **gateway probe 不属于本轮。** `_probe_gateway` / `probe_ibkr_socket` /
  `gateway_badge` 是 connection/system 级 capability，留给后续 System
  orchestration 或 composition closure，不因为用了 IBKR config 就归入 Account。
- **`broker_account` 仍允许留在 composition root。** 它同时被 MarketData
  composition getter、SettingsService 的 IBKR 事务闸门与 AccountOrchestrator
  使用，这是 composition 而不是业务 ownership 泄漏；窗口不得
  `account_orchestrator._application.*` reach-through。

新增守卫位于 `tests/test_desktop_account_orchestration_architecture.py`、
`tests/test_desktop_account_orchestrator.py` 与
`tests/test_desktop_account_orchestration_wiring.py`，覆盖已删 state / 无
compatibility property / 无 reach-through / 无 `account_page.render` /
`AccountPage.render` 唯一 Desktop caller / 无 ledger 调用 /
orchestrator 的禁止依赖与禁止调用 / public surface 精确相等 /
Qt-free 的 models+queries（含子进程按文件路径加载验证）/ per-file line
budgets（`orchestrator.py ≤ 320`、`models.py ≤ 140`、`queries.py ≤ 140`）/
fresh-Paper 规则全分支（含 naive timestamp 与 300 秒边界）/
真实 ledger table 与 execution equity card 的端到端渲染。

`desktop.py`：**5450 → 5436 行（净减 14）**；`orchestrator.py` 252 行、
`models.py` 104 行、`queries.py` 83 行、`__init__.py` 36 行。

净减少之所以远小于 v2O-A 的 415 行：本轮删掉的是 4 个窗口方法与其内部逻辑
（约 128 行），但按规格必须**保留**在窗口的跨 workflow bridge 与
presentation push（约 117 行）计入新增。ownership 正确优先于行数。

### 8.13 research foundations orchestration 已抽出（v2O-C1）

v2O-B 之后，Research 两条基础 route 的桌面 truth 仍散在 `MainWindow`：
`self.universe` 快照、refresh task 的 cancel `Event`、refresh worker 句柄、
`_history_progress_percent`，以及围绕它们的 12 个 handler。本轮把它们搬进
`desktop_v2/orchestration/research/`：

```text
desktop_v2/orchestration/research/
  __init__.py      故意没有 aggregate
  universe/__init__.py         export UniverseOrchestrator
  universe/orchestrator.py     275 行：refresh 请求 / cancel / snapshot 持有 /
                               page render
  history/__init__.py          export HistoryOrchestrator
  history/orchestrator.py      227 行：history task intent + progress 展示
```

**v2O-C Research is intentionally decomposed internally.** Research 在导航上是
一个 route aggregate，在运行时**不是**一个 owner。`research/orchestrator.py`
不得存在；`ResearchOrchestrator` / `ResearchManager` / `ResearchContext` /
`ResearchServices` / `ResearchController` / `DesktopContext` 一律禁止声明。把
六个 workspace 收进一个对象等于在单文件里重新长出 `MainWindow`。这三条由
`tests/test_desktop_research_foundations_architecture.py` 断言（含「该文件不
存在」这一条），是本阶段最重要的长期维护 guard 之一。

- **`UniverseSnapshot` 的 canonical Desktop owner 是
  `UniverseOrchestrator.snapshot`。** `DesktopUniverseService` 无状态：它执行
  刷新过程、不持有结果，所以 truth 只能落在 desktop 层。窗口 `self.universe`
  已删除，且**没有** compatibility property——转发 property 会让未迁移的
  caller 继续静默工作，"谁在读 universe 真相" 就不再是一条 grep 能回答的
  问题。所有读取改成显式 `self.universe_orchestrator.snapshot`，且**在任务
  执行时读取**而非排程时捕获。
- **History queue truth 仍属 service/store。** `DesktopHistoryService` /
  `HistoryJobStore` 已经拥有 job 行、优先顺序、公共源回退与 reset_failed
  规则，所以 `HistoryOrchestrator` **不存** jobs、不缓存
  `HistoryQueueSnapshot`：`render_current()` 每次都向 service 要新的一份。它
  只拥有四个 page intent、进度百分比与唯一的 render 调用点。
- **History 不依赖 `UniverseOrchestrator`。** 它通过
  `universe_provider: Callable[[], UniverseSnapshot | None]` 取快照，因此
  universe 实现变化不会波及 History。IBKR 配置同理走
  `ibkr_config_provider`，且每次运行都重新读——构造时捕获一份会让 Settings
  的修改在本次会话剩余时间里继续打旧端点。
- **`on_finished` 是本轮新增的 generic hook。** `TaskThread` 没有通用 cancel
  hook，此前窗口靠 `workers[-1]` 与 worker 身份判断完成归属，这正是 Universe
  必须持有 worker 句柄的原因。History 成为第二个真实消费者后该耦合不再可
  接受：`_start_task` 增加可选 `on_finished: Callable[[], None] | None`，由
  caller 传入且**不接收 worker 对象**。顺序是契约——`_finish_task` 先跑通用
  清理（释放资源组、重发 execution controls），再跑 hook，因此 hook 看到的
  是已释放的 worker；成功/失败/取消三条路径共用同一个 hook。
- **shutdown 取消走 capability 生命周期。** `_request_worker_stops()` 调
  `universe_orchestrator.cancel_for_shutdown()`，窗口既不读那个 `Event` 也不
  reach into capability 取它。它与 `request_cancel()` 分开：关闭不是操作员
  请求取消，所以不写状态行、也不在窗口拆自己时重绘页面。
- **本轮不动 Scanner / Backtest / Cross-Section / Targeted。** 它们是后续独立
  小刀；Market scope bridge（`_on_universe_changed`）与 `_run_scan` 的读取
  迁移是必要连带改动，不是顺手重构。

新增守卫位于 `tests/test_desktop_research_foundations_architecture.py`（51 项
结构守卫）、`tests/test_desktop_universe_orchestrator.py`（26 项行为）、
`tests/test_desktop_history_orchestrator.py`（16 项行为）、
`tests/test_desktop_research_foundations_wiring.py`（14 项真实 `MainWindow`
端到端：按钮点击、真实 refresh 后下游读到的 snapshot、shutdown 取消 live
refresh、`_start_task` 三条完成路径）。依赖守卫是**白名单**而非禁用清单，且
双向断言（未声明依赖失败、过期声明也失败）。

`desktop.py`：**5436 → 5385 行（净减 51）**；`universe/orchestrator.py` 275
行、`history/orchestrator.py` 227 行、三个 `__init__.py` 共 93 行。

**v2O-C1 Universe + History ✅**；顶层路线仍为 **v2O-C Research in progress**，
不得标整个 Research 完成。

### 8.14 scanner orchestration 已抽出（v2O-C2）

v2O-C1 之后，Scanner route 的桌面 truth 仍散在 `MainWindow`：`self.scan`
同时承担手动扫描结果、startup restored scan、AutoQuant preparation scan、
Scanner page render、AutoQuant candidate input、intraday watchlist input 与
market scope summary。它已经是一个**共享 Desktop fact**，只是还没有 owner。

本轮把唯一 owner 改成 `ScannerOrchestrator`：

```text
src/us_quant/desktop_v2/orchestration/research/scanner/
  __init__.py      lazy export
  models.py        ScannerRunInputs（frozen / slots / Qt-free）
  orchestrator.py  ScannerOrchestrator
```

**MarketScan canonical Desktop owner = `ScannerOrchestrator`。** 窗口不再有
`self.scan`，也没有 compatibility property：所有消费者显式写
`self.scanner_orchestrator.scan`，所以"谁在消费 scan truth"仍然是一个 grep。

#### 14.1 三种到达路径必须分义

`MarketScan` 有三种完全不同的到达方式，本轮刻意做成**三个入口**而不是一个带
布尔 flag 的 setter：

```text
request_scan()           操作员点击「扫描」
restore_saved()          startup 重读本地 artifact
adopt_external_scan(scan) 其他 workflow 已完成一次 scan
```

| | render | `scan_changed` | 「扫描完成」日志 |
| --- | --- | --- | --- |
| `request_scan` | ✅ | ✅ | ✅ |
| `restore_saved` | ✅ | ❌ | ❌ |
| `adopt_external_scan` | ✅ | ✅ | ❌ |

`restore_saved` 不 publish 任何东西：**重读本地文件不是一次新扫描**，宣布完成
就是撒谎。`adopt_external_scan` 是新扫描但**不是手动**的，所以不写"扫描完成"。

拒绝 `set_scan(scan, emit=True, log=False, render=True)` 这种 API：startup
restoration ≠ cross-workflow new scan ≠ manual scan success，以后改其中一个时
不能靠布尔 flag 猜 side effects。

#### 14.2 两个时间语义相反，且都是刻意的

手动扫描有两个**方向相反**的读取时机，本轮明确保留：

```text
research capital / risk pct / substitutions
  → request_scan() 的 UI thread 冻结（ScannerRunInputs）
UniverseSnapshot
  → task 真正执行时重新读取
```

原因：操作员点「扫描」后、worker 启动前改研究资金，不应该改变即将跑的扫描；
而扫描排队期间落地的官方标的刷新，**应该**是被扫描的那一份。

如果 task 执行时 universe 意外变成 `None`：**fail closed**（raise），不扫描
request 时捕获的旧副本——扫描一个操作员已经看不到的快照比任务失败更糟。

`ScannerRunInputs` 用 frozen tuple 存 substitutions（而不是 dict），所以"输入
在 request 时被冻结"是**数据的性质**而不是约定。

#### 14.3 AutoQuant 仍然直接 scan（本轮最重要的边界）

AutoQuant preparation 仍然直接调：

```text
scan_market(...) + save_market_scan(...)
```

**本轮继续保留。** 它连着 Paper `PREPARING`、候选准备失败清理与
`_auto_market_scan_finished`，属于高风险启动链；`_prepare_auto_quant_candidates`
的扫描执行 ownership 一行未改（旧 frozen guard 继续钉住这一点）。

AutoQuant 结果进入 canonical truth 的方向是：

```text
AutoQuant workflow
  ↓ finished MarketScan fact
MainWindow composition bridge（_auto_market_scan_finished）
  ↓
ScannerOrchestrator.adopt_external_scan(scan)
```

**不是** `ScannerOrchestrator → PaperWorkflow → Execution`，所以边界正确：
Scanner 从不知道 Paper 存在。`_auto_market_scan_finished` 只允许改
`self.scan = result` → `adopt_external_scan(result)`，并移除旧 Scanner render
调用；它原有的 history gap scheduling、history render 与
`_select_auto_quant_candidates` 全部保留。

#### 14.4 Scanner artifact 与 chart 读取归 Scanner data boundary

`DesktopMarketScanService` 仍然是扫描/I/O owner，本轮把它从"manual scan only"
扩成更完整、仍然内聚的 **Scanner data/application boundary**：

```text
scan(universe, ...)        scan_market + save_market_scan
load_saved()               scan_path → MarketScan | None
load_chart(symbol)         load_close_series(symbol, data_root=..., fallback=...)
```

三个职责是同一类东西（Scanner 所需的数据读取/扫描/持久化），比把路径与 JSON
parsing 留在 `MainWindow` 更容易维护。

`MainWindow._load_scan_file()` 因此退休：窗口不再手写 `json.loads`、
`ScanResult` reconstruction 与 `MarketScan` 的 datetime/date parsing，
**不再知道 Scanner artifact schema**。

`load_saved()` 三种结果刻意不同：

```text
文件不存在   → None（首次运行的正常状态，不是错误）
合法文件     → MarketScan（generated_at / data_date / trading_date /
                            max_position_risk_pct / skipped 全部恢复）
坏文件       → 异常向上传播（静默降级成"没有扫描"会掩盖损坏）
```

`restore_saved()` 捕获异常并把 truth 置空后**仍然渲染一次**，所以坏 artifact
不会阻止桌面启动——与窗口自己解析 JSON 时同样的容忍度。

chart loading 同样迁出（`MainWindow._scanner_symbol_selected` 退休）。失败语义
保持：**只 log、不弹 dialog、不清掉旧 chart**，文案
`{symbol} 图表读取失败：{error}` 不变。清掉旧图会把操作员还能读的东西换成空白，
那是丢信息而不是报告失败。

#### 14.5 render 只有一个 caller

迁移后 `ScannerOrchestrator` 是 Desktop 层**唯一**调用
`scanner_page.render(...)` 与 `scanner_page.render_chart(...)` 的对象。窗口只能
construct page、connect page signals、set_palette。

`render_current()` 的 research count 规则原样保留：

```text
有 Universe          → universe.summary()["research_eligible"]
无 Universe + 有 scan → len(scan.results) + len(scan.skipped)
两者都没有            → 0
```

#### 14.6 留在窗口的 cross-workflow consumers

以下逻辑继续留在原 capability，只把 truth source 换成
`self.scanner_orchestrator.scan`：

```text
_apply_intraday_watchlist      Scanner + Market + Account capital
_select_auto_quant_candidates  Paper + account truth + strategy + risk
                               multipliers + market references + Execution page
_refresh_market_scope_summary  scan + universe + local history count
Execution context consumers
```

`_apply_intraday_watchlist` 与 `_select_auto_quant_candidates` 绝不能进 Scanner：
前者是 Scanner/Market/Account 的 composition，后者同时拥有 Paper workflow、
account truth、strategy、risk multipliers、market references 与 Execution page。

`ScannerOrchestrator.scan_changed` 接到 `_refresh_market_scope_summary`，所以手动
扫描与 AutoQuant external adoption 都自然更新 scope line；startup restore 不
emit change，因为 `_load_local_state()` 最后本来就统一 refresh scope。

窗口新增的只有两个薄 bridge：`_scanner_run_inputs()`（composition：把当前研究
资金、`max_position_exposure_pct` 与 substitutions 冻成 `ScannerRunInputs`）与
`_report_scanner_refusal()`（`QMessageBox.information`，severity 不变）。
`_connect_scanner_page()` 只 connect，不做业务。

#### 14.7 依赖与守卫

`ScannerOrchestrator` 禁止 import：`MainWindow`、其他 orchestrator、
`Paper*` / `Shadow*` / `Execution*` / `Market` / `Account` / `RiskApplication` /
`StrategyApplication` / `TaskThread` / `DesktopTaskController` /
`HistoryJobStore` / `RuntimeSupervisor`，也不持有 `QMessageBox`。
允许：`DesktopMarketScanService`、Scanner page/presenter/models、`MarketScan`、
`UniverseSnapshot` 类型、`ScannerRunInputs`、`QObject`/`Signal`、`Callable`。
History 与 Scanner 也不互相 import（都通过 callable provider 拿 universe）。

public surface 保持很小：

```text
scan
restore_saved()
request_scan()
request_chart(symbol)
adopt_external_scan(scan)
render_current()
signals: scan_changed / refused / log_requested
```

不增加 `results` / `skipped` / `summary` / `scanned_count` / `trade_candidates` /
`research_count` / `scan_path` / `service` / `page`——这些不是 capability API。

新增守卫位于 `tests/test_desktop_scanner_orchestrator.py`（30 项行为）、
`tests/test_desktop_research_scanner_orchestration.py`（60 项结构）、
`tests/test_desktop_research_scanner_wiring.py`（18 项真实 `MainWindow`：按钮点击
→ capability、单 truth identity、AutoQuant bridge 全链路、page filter 不改
truth、startup exactly-once render）。旧 scope guards
（`test_desktop_backtest_service.py` / `test_desktop_history_service.py` /
`test_desktop_universe_service.py` / `test_desktop_market_scan_service.py` /
`test_desktop_research_foundations_architecture.py` / `test_trading_architecture.py`）
同步更新 delta：5 个方法退休（`_run_scan` / `_scan_finished` /
`_load_scan_file` / `_publish_scanner_view` / `_scanner_symbol_selected`）、
2 个新增（`_scanner_run_inputs` / `_report_scanner_refusal`）、6 个改写。

`desktop.py`：**5392 → 5351 行（净减 41）**；
`scanner/orchestrator.py` 296 行、`scanner/models.py` 74 行。

**v2O-C1 Universe + History + v2O-C2 Scanner ✅**；顶层路线仍为
**v2O-C Research in progress**，Backtest / Cross-Section / Targeted 待续，
不得标整个 Research 完成，也不得提前进入 Shadow。

### 8.15 backtest orchestration 已抽出（v2O-C3）

v2O-C2 之后，Backtest route 的桌面 truth 仍散在 `MainWindow`：
`self.backtest_runs`、`self._selected_backtest_run_id`、`self._backtest_busy`
三个属性，被八个 handler 在千行范围内反复读写。本轮全部迁入：

```text
src/us_quant/desktop_v2/orchestration/research/backtest/
    __init__.py        53 行：导出 orchestrator 与 queries
    queries.py        162 行：纯规则（Qt-free）
    orchestrator.py   298 行：capability 本体
```

外加本轮唯一允许的 shared simplification：

```text
src/us_quant/desktop_v2/orchestration/tasking.py   86 行
```

#### 8.15.1 runs 不公开（本轮刻意为之）

`BacktestOrchestrator._runs` 是当前 session 的 Backtest 桌面 truth，但**没有**
`runs` / `selected_run` / `busy` accessor。理由不是省事：目前没有任何其他
workflow 读取 `BacktestRun` 列表，而"以防以后需要"加的 accessor 会立刻被下一个
capability 拿去用，Backtest workspace 就不再自洽。出现真实第二消费者时再加，
并在本文件记录。

#### 8.15.2 纯规则归 `queries.py`

三条规则从窗口和 orchestrator 里拿出来，逐字义不变：

- `strategy_options(versions)`：先 `STRATEGY_SPECS` family order（未知 family
  排在 999），同 family 按 `semver`；
- `select_backtest_versions(versions, *, compare_all, selected_version_id)`：
  单版本只匹配给定 id（不命中即空，**不回退到最新**）；compare-all 每个
  `strategy_id` 取 provider 列表中的**第一个**（即最新，因为 selection service
  已按 newest-first 返回），最终按 `STRATEGY_SPECS` 顺序；
- `build_backtest_requests(versions, draft)`：`Decimal` 转换与
  `target_weight_percent / 100` 精度完全保持。

`queries.py` 禁止 PySide6 / `QMessageBox` / `QObject` / Page / MainWindow /
`DesktopBacktestService` / `TaskThread`——所以"策略选择"和"draft → request"
可以不启动 Qt 就单元测试。

#### 8.15.3 StrategySelection 走 provider，不走 service

`BacktestOrchestrator` 不 import `StrategySelectionService` /
`StrategyApplication`，只消费 `Callable[[], tuple[StrategyVersion, ...]]`。
这样以后 strategy application 重构，Backtest 不跟着变。守卫同时锁定
import 与符号两个方向。

#### 8.15.4 busy ownership

`_busy` 属于 orchestrator，且**不再**从 worker 列表推导。流程：

```text
request → validate → _busy = True → render → submit_task
                                                    ↓ False（准入被拒）
                                              _busy = False → render
```

failure → `_busy = False` + render + **保留 last-good runs**（不清空）。
success → `_busy = False` + 替换 runs + 选第一条 + render + 完成日志。

`_worker_finished` 现在完全不认识 Backtest：无关的 history worker 结束
不会解锁回测控制。这是本轮最重要的回归断言。

#### 8.15.5 拒绝 severity 不变，且不新建 Notice 框架

三种拒绝保持原 severity 与文案：busy（`information` / 任务忙）、无版本
（`warning` / 没有可运行版本）、日期非法（`warning` / 日期无效）。capability
不发 `QMessageBox`，只发 `refused = Signal(str, str, str)`（level / title /
message），窗口的 `_report_backtest_refusal` 只负责按 level 选 dialog，
不含 business logic。

本轮**没有**创建 `DesktopNoticeBus` / `NotificationService` /
`DialogManager`：多个 capability 都有 `refused`，但 severity 模型尚不一致，
等 Targeted / System 出现相同真实结构后再决定是否统一。

#### 8.15.6 结果类型必须检查

`_runs_finished` 不再 `list(result)` 直接相信任意 object：验证
`result` 是 `tuple`/`list` 且每项是 `BacktestRun`，否则 `raise TypeError`。
把错误 object 塞进 UI state 比当场失败难诊断得多。

#### 8.15.7 依赖与 public surface

`BacktestOrchestrator` 禁止 import：`MainWindow`、其他任何 orchestrator、
`Paper*` / `Shadow*` / `Execution*` / `TradingRuntime` / `RiskApplication`、
`StrategySelectionService` / `StrategyApplication`、`TaskThread` /
`DesktopTaskController`。它是最干净的独立 Research capability，不 import
Universe / History / Scanner / Cross-Section / Targeted 中的任何一个。

public surface 精确为：

```text
signals:  log_requested, refused
methods:  refresh_strategy_options, request_selected,
          request_compare_all, select_run, render_current
```

#### 8.15.8 页面只有一个 caller

`BacktestPage.render` 与 `BacktestPage.set_strategy_options` 的唯一 caller 是
`BacktestOrchestrator`。窗口只允许 construct page、connect signals、
`set_palette`（theme 是窗口的）。以后改 Backtest display 只需看
`page/` + `presenter/` + `orchestrator/`。

#### 8.15.9 未做的事（明确留给后续）

- 没有 startup restore saved runs。看到 JSON artifacts 就擅自加载历史 run
  属于产品行为变化，本轮不做；
- 没有并发化 / asyncio / transaction rollback / cancellation。
  `DesktopBacktestService.run(...)` 边界已合理，本轮不动；
- 没有改任何 Decimal / commission / slippage / target_weight /
  initial_equity / parameter_hash / code_hash / date filtering /
  `BacktestEngine` / position sizing。本轮是 Desktop ownership extraction，
  不是回测算法优化；
- 没有碰 Cross Section（含 `research capital`）、Targeted、Shadow、Paper。

#### 8.15.10 体积、守卫与突变结果

`desktop.py`：**5351 → 5224 行（净减 127）**；
`backtest/orchestrator.py` 298 行、`backtest/queries.py` 162 行、
`tasking.py` 86 行。

新增守卫：`tests/test_desktop_backtest_orchestrator.py`（27 项行为，无窗口）、
`tests/test_desktop_research_backtest_orchestration.py`（79 项结构）、
重写 `tests/test_desktop_v2_backtest_wiring.py`（17 项真实 `MainWindow`）。
architecture guards keep their rules local; shared AST support is deferred until
at least three real consumers exist（本轮只有两个 consumer，不足以支撑抽公共库）。

15 项 mutation 全部由**对应的具名测试**捕获（RED）：窗口恢复 state / 加
compatibility property / 恢复 `render` / 恢复 `set_strategy_options`、
capability import `StrategySelectionService` / `TaskThread` /
`ScannerOrchestrator`、compare-all 不再取 latest、compare-all 不跟
`STRATEGY_SPECS` 顺序、invalid date 仍启动 service、no eligible strategy 仍启动
service、admission 被拒后 busy 保持 True、failure 清空 last-good runs、success
不选第一条 run、catalogue refresh 不更新 Backtest options。

**v2O-C1 Universe + History + v2O-C2 Scanner + v2O-C3 Backtest ✅**；
**v2O-C4 Cross Section ✅**；**v2O-C5A Targeted Evidence ✅**；
**v2O-C5B Targeted Session + Preflight ✅**；**v2O-D Shadow orchestration ✅**；
**v2O-E1 Paper launch orchestration ✅**；**v2O-E2 active Paper runtime orchestration ✅**；
**v2O-E3 recovery / finalization orchestration ✅**；**v2O-E4 presentation / render closure ✅**；
**v2O-F1 Runtime Events orchestration ✅**；**v2O-F2 Settings orchestration ✅** ——
顶层路线现在是 **v2O-E Paper COMPLETE + v2O-F1 ✅ + v2O-F2 ✅**。Paper 能力分四刀：启动链
（§8.18）、active runtime（§8.19）、recovery/finalization/shutdown（§8.20）与
presentation/render closure（§8.21）全部完成，`MainWindow` 不再决定 active session 何时
poll / 何时吃行情 / 何时 pause/resume/stop，不再持有 runtime handle，不再决定 HALT 之后
如何对账、zero-state 证明何时开始、ownership 何时可以释放，也不再持有任何 Paper session
的展示缓存——它保留的 presentation fact 是 capability 发布的 immutable 投影，且不参与任何
业务判断。v2O-F1 把 System 的 Runtime Events 一半收进 `RuntimeEventsOrchestrator`（写入 /
合并重画 / resolve / 导出成败 / last-export / render，§8.22）；v2O-F2 把 Settings 一半收进
`SettingsOrchestrator`（视图组装与 render、凭据 save/clear、偏好事务适配、provider 双向
同步、两个 capability 确认、两个 presentation fact，§8.23），并修掉一个保存被拒仍切换
行情源的顺序 bug。**没有** `SystemOrchestrator`：两个 workspace 除共用页面外互不相关。
接下来要**重新扫描** System 剩余职责（Gateway probe 是否需要独立 F3），随后是 MainWindow
composition closure 与 Final Architecture Closure；本轮不声称 System orchestration
complete。

维护导航见 `docs/DESKTOP_CAPABILITY_MAP.md`；C5B 的设计依据见
`DESKTOP_DECOMPOSITION.md` §25，本文的 §8.16 只记该轮改变了哪些 boundary。

### 8.16 targeted session + preflight orchestration 已抽出（v2O-C5B）

```text
desktop_v2/orchestration/research/targeted/session/
    __init__.py      命名理由与完整规则集
    models.py        TargetedSessionSnapshot + 默认文本 + refusal severity + 文案
    queries.py       纯规则：normalize / symbol 门 / Universe 与 quote 查找 /
                     两条 status 文本 / controls 投影
    orchestrator.py  命令、顺序、refresh、render owner
desktop_targeted_session_service.py   Qt-free：MinuteQuoteStore + preflight evaluator
```

canonical truth 是 `TargetedSessionOrchestrator.snapshot`（immutable）。它**公开**
是因为有两个真实消费者：Targeted Evidence 需要 draft 作为 Replay/Robustness 的
symbol，Shadow start 需要 target。**不**提供四个独立 accessor。

**它不拥有 Shadow。** `shadow_snapshot_provider` 让它在 `render_current()` 里每次
绘制时读取快照；`shadow_engine` / `shadow_snapshot` / `_start_shadow` /
`_stop_shadow` 属 v2O-D，**已迁入** `ShadowOrchestrator`（见 §8.17），
`ShadowPaperStore` 与 `ShadowWorkflow` 作为 composition fact 留在窗口。因此名字是
`TargetedSessionOrchestrator`，不是 `ShadowSessionOrchestrator` —— 这里的 session
指 Targeted 工作区的呈现会话。

**依赖全部是 Callable / immutable domain fact**：universe、market snapshot、
account、strategy selection、exposure multipliers、Shadow snapshot 六个 provider，
加两条极窄的 Market 命令（`set_subscription_symbols`、`start`）。它不 import 任何
orchestrator，也不能 poll / stop / switch market 或选择 provider。

**Timing 契约**（继承且被测试钉住）：

```text
draft signal       → 只记录 draft（每个字符一次赋值，零 fan-out）
refresh_minute     → 不重算 preflight
refresh_preflight  → 不跑 evidence
preflight          → derived fact；只有 evaluator 返回才 commit；
                     失败继续传播（不吞异常，不 repaint 过期 verdict）
```

**错误契约（继承，不是新策略）**：`refresh_preflight()` 里**没有** try/except。
base 的 `_refresh_target_preflight()` 也没有 —— 任何 Universe / Market / Account /
provider / store / evaluator 异常原本都会显式暴露。在 capability 里加
`except Exception: return` 会静默吞掉全部失败，让操作员把过期 verdict 继续当成当前
的读，而它携带的 Paper account freshness / 行情 freshness / whole-share gate 恰恰是
刷新失败后绝不能看起来仍然有效的那几个。吸收异常属于 composition / error boundary。

**Safety 语义**（逐字保持）：invalid symbol = warning；active Shadow 时拒绝切
target（page 与 canonical draft 同时回到引擎实际在做的 symbol）；Market live 时拒绝
subscribe；apply 只预置 subscription 不启动行情；subscribe 的 preflight 早于
market start。`broker_orders_available` 在 service 里硬编码 `False` 且不是参数。

`render_session` 现在与 `render_evidence` 一样只有一个 production caller
（各自的 capability）；`MainWindow` 不再画 Targeted 页面的任何一半。MainWindow
退休了 `_target_status` / `_minute_status` / `target_preflight_result` 三个状态和
十个 handler，且不留任何 compatibility property。

### 8.17 shadow orchestration 已抽出（v2O-D）

C5B 之后，Shadow runtime 仍是 `MainWindow` 上的一组散落属性，被六个地方读写：

```text
_start_shadow / _stop_shadow       两个页面 intent
_on_market_snapshot_changed        stream ingress + session repaint
_stop_market_data                  Paper / Shadow 停止 interlock
_start_auto_quant                  Paper 启动前的互斥门
closeEvent                         关闭时停止
_export_terminal_state             shadow_store.recent_fills(500)
```

全部收口到 `desktop_v2/orchestration/shadow/`（30 / 220 / 276 / 382 行）：

```text
models.py        冻结 request、拒绝 shape、runtime event shape、ShadowLease 协议
queries.py       十道启动门，纯规则、Qt-free、无 I/O
orchestrator.py  只做 sequencing：读一次事实 → 过门 → 建引擎 → 启动 → 喂快照 → 发布
```

**单一 truth 不变。** `ShadowPaperEngine` 仍是 session id / active / cash / PnL /
position / fills / marks 的**唯一** owner；orchestrator 只持有引擎**引用**与引擎最后产出的
snapshot，`is_active` 问引擎（不缓存布尔），`recent_fills` 委托 store。它不算 PnL、
不模拟 fill、不写 store —— Shadow 算法 / fill math / 手续费 / 滑点一行未动。

**`_holds_lease` 不是第二份交易真相。** orchestrator 还持有这一个 capability-local
布尔，它回答的是另一个问题：**"本 capability 是否取得了共享执行租约"**。共享
`ExecutionLeaseManager` 是 Shadow 与 Paper 共用的，所以 `lease.active` 在**任何一方**
持有时都为真，表达不了 ownership；需要 release 判断时只能由本地记录回答。它**不**镜像
`engine.active`、**不**镜像任何交易状态，因此不违反上面的 single truth。

这一点是安全要求而非风格：早期版本用 `self._lease.active` 作为释放条件，造成两处真实
缺陷——重复 `start()` 在失败路径释放了**正在运行**会话的租约，`stop()` / `shutdown()`
则可能释放**Paper** 后来取得的租约。两处都会让"Shadow XOR Paper"静默失效。现在
`self._lease.stop()` 全文件只出现一次（`_release_lease()` 内），由 `_holds_lease` 把关。
**不要把它改回 `lease.active`。**

**重复启动是 no-op（安全不变量）。** `start()` 最前面有 own-active gate：已 active 时
不读任何 provider、不构造第二个 engine、不碰 lease、不改 snapshot。这是本 capability
自己的生命周期完整性，不是 Paper 逻辑，也不需要改 Shadow core。

**import 边界。** 该 package 不 import Market / Account / Research 的 orchestrator，
也不 import `desktop_v2/workflows.py`。后者是个真实陷阱：`ShadowWorkflowController`
与 `PaperWorkflowController` 同住一个模块，直接 import 会让 Shadow → Paper 成为依赖。
解法是 `models.py` 里的 `ShadowLease` Protocol——窗口把**共享**的
`ExecutionLeaseManager` 句柄传进去，Paper 拿到的是同一个，所以"Shadow 与 Paper 不能
同时持有执行权"仍是结构性的，而 orchestrator 从不 import 那个模块。

**两处逐字保留的行为：**

```text
资金金额   在资金门读；资金来源（account alias）只在建引擎时读
           ⇒ 被拒绝的启动从不触碰 portfolio（退休 handler 的原始顺序）
shutdown() 只停引擎 + 释放 lease；不重绘、不记录事件、不改 snapshot
           ⇒ 关闭不会把操作员从未停止的会话写成"已停止"
```

**一处刻意不保留的行为。** 退休 `_start_shadow` 的重复启动后果（第二个 engine 被 lease
拒绝后，失败路径释放**第一个** run 的租约）**没有**逐字迁移：那是安全漏洞，不是产品
语义。原样迁移再加上一个保护它的测试，会把漏洞固化进新的 canonical owner，并让
v2O-E Paper 更难、更危险。已替换为上面的 no-op 契约。

**窗口剩下的 Shadow 代码**只有：构造与接线、五个 composition helper、两处
cross-capability interlock 读取，以及两个非 runtime truth 的属性——
`shadow_store`（导出经 capability 读取的持久化）与 `shadow_workflow`（与 Paper 共享的
lease 句柄）。`_selected_shadow_strategy_record` 是 strategy selection 的 composition
读取，本轮不改名。

Shadow 研究算法、Paper workflow 生命周期、Risk / Execution 都未触碰，Paper
ownership 留给 v2O-E。

### 8.18 Paper launch orchestration 已抽出（v2O-E1）

v2O-D 之后，Paper **启动链**仍是 `MainWindow` 上的一对 handler 加四个只服务它们的
helper：

```text
_start_auto_quant                    读 preflight → 冻结 plan → begin_connecting → 提交 broker task
_auto_order_service_connected        过期判定 → 二次 preflight → identity 复验 → 校验券商 → 建 runtime
                                     → arm → ensure → publish_armed → promote
_reject_unpublished_auto_candidate   丢弃单个未武装 candidate + reject 匹配的 plan
_reject_auto_launch_without_service  连接失败、从未产生 candidate 的收尾
_current_auto_launch_matches         UI 输入是否仍与冻结 plan 一致
_reset_auto_launch_controls          只重置属于该 attempt 的控件
```

全部收口到 `desktop_v2/orchestration/paper/`：

```text
models.py        冻结 request / 券商读数 / build 结果 / publication / event；两个协作方 Protocol；逐字 operator 文案
queries.py       启动门、冻结 plan（含参数脱钩与 hash 校验）、identity 比对、券商门，纯规则、Qt-free、无 I/O
orchestrator.py  只做 sequencing
__init__.py      只导出 PaperOrchestrator
```

**本轮的架构要求不是行数。** 早先版本写了逐文件行数上限并做成阻断性 guard，后来退成一条
"导航阈值"，**两者都已彻底删除**——换名字不改变性质，它仍是一个会让 CI 失败的强制行数上限。
现在断言的是职责单一、ownership 明确、依赖方向稳定、无重复 truth / context bag / god
object、关键安全顺序有测试锁住；本 capability 的 guard 里不存在任何 LOC 断言。

**没有第二份 truth。** 这是本轮最重要的一条。`PaperWorkflowController` 仍拥有 phase、
active plan、execution lease；`PaperTradingService` 仍拥有 candidate 与 active 连接；
`TradingRuntime` 仍拥有 session / book / risk / execution dispatch。
orchestrator **不保存**其中任何一样——特别是**不保存** `self._active_plan`。

关键推论：**"启动 attempt 在飞"这件事由 workflow 的 phase 回答，不由镜像 plan 回答。**
退休代码用 `_active_auto_launch_plan is not None`；它与
`phase is PaperWorkflowPhase.CONNECTING` 完全等价（`begin_connecting` 是进入该 phase 的
唯一入口，两条退出路径都清 plan），而且 phase 在 publish **之后**依然正确——那时 plan
合法地跨越 attempt 存续进 `RUNNING`，`active_plan is None` 就不再是判据。窗口的
`_launch_locked` 与确认门因此都改读 `paper_trading.phase()`。

**arm / reserve / publish / commit 的顺序是硬安全约束。** broker connect 成功只证明可达，
不证明可信：

```text
candidate_service(candidate_id)   借用引用，只存在于本 callback 调用栈
→ validate_broker_state           净值 > 0 / 空仓 / cash 存在（Decimal 全链路）
→ build_session                   组合根只建 runtime 与 execution application，并 runtime.start()
→ service.arm(...)                **orchestrator 自己执行**，不再藏进 build seam
→ reserve_candidate_promotion     装入 active **并锁槽**（ownership 在此取得）
→ publish_armed
→ commit_candidate_promotion      结束本次启动的占用，不再移动任何东西
```

`reserve_candidate_promotion` **必须早于** `publish_armed`，而且是结构性的：owner 在发布
之前就已经存在，因此"已发布的 session 没有 owner"不是窗口有多短的问题，而是**不存在这样
一个顺序**。早期版本在发布前只做 `ensure_candidate_can_promote`（纯检查、零变更），槽位
在发布期间仍是空的，发布后 promotion 一旦被拒就留下 `RUNNING` + coordinator 持着已武装通道
+ 无人接管，而所有 recovery 路径都从 `has_order_service()` 起步、直接 return。这不再是
"promote 必须最后"：**broker connect 成功 ≠ session 已获信任**这条约束没有变，只是取得信任
的时点必须早于 publication，否则会出现无 owner 的运行中会话。publication 失败时由
`cancel_candidate_promotion` 收回槽位，走下面的原有 rollback。

`arm` 必须由 orchestrator 明确执行。首轮实现把它放在 `_build_paper_session()` 里，于是
orchestrator 只看到 `_build_session → ensure → publish → promote`，架构 guard 也只能断言
`_build_session < ensure < publish`——真正的 `arm < ensure < publish < promote` 锁不住。
既然本轮标题就是 **Launch / Arm orchestration**，这里已收口：build seam 返回
`session_id / runtime / max_order_notional`，arm 在 orchestrator 里执行。

**publish 前回滚 vs publish 后不变量失败，必须分开。** `publish_armed()` 抛异常发生在
workflow 转入 `RUNNING` **之前**，所以到它抛为止都还是 rollback（**先 cancel reservation
把槽位收回**，再丢弃 candidate、reject plan、释放 PAPER）。它返回之后只剩一件事——结束本次
启动的占用——而 `commit_candidate_promotion()` 已经没有可失败的残余：ownership 是 `reserve`
时取得的，那时就验证过候选存在、槽位为空且无人占用。若它仍因 bug 被拒，走
`_fail_after_publication()`：candidate / 租约 / 已发布 workflow **原状保留**，以独立 code
`PAPER_PROMOTION_INVARIANT` 在 error 级别报出。这是 safety invariant 破坏而非启动失败，
静默清理只会把真实缺陷伪装成合理的启动失败；而此时的代价是 fail closed 的——下一次启动会被
拒，当前会话仍然有 owner、可恢复。

`cancel_candidate_promotion()` **刻意不抛异常**：它跑在 rollback 里，抛出会跳过 rejection
并把 `CONNECTING` 永久卡在持有 PAPER 的状态。所以它**用返回值报告**是否真的释放了，而
orchestrator 必须**把它当控制信号**而不是日志：返回 `False` 时 ownership 无法证明已归还，
此时继续回滚会丢掉一个可能仍持有槽位的 service、reject plan 并释放 PAPER（与 Shadow 共享的
租约），于是改走 `_fail_to_release_promotion()` —— 不回滚、保持 `CONNECTING` 与租约，用独立
code `PAPER_LAUNCH_ROLLBACK_FAILED` 报出。已发布 LIVE 会话与卡住的启动是两个不同的处境，
所以 code 也分开。

占用锁的是**槽位与 id 两者**：`clear_active()` 在占用期间直接拒绝，否则 reservation 只是
"打算"锁住槽位——finalization / recovery 调用方可以在 reserve 与 commit 之间把 owner 清掉，
启动便发布一个 owner 已被丢弃的会话；而 `connect_candidate()` 拒绝重新登记被占用的 id，因为
占用期间该 id 已不在候选表里、只查重复是查不到的，一旦重新登记，回滚的 `cancel` 就会覆盖掉新
候选，使其 broker 连接成为孤儿。所以 `cancel` 自己也在改动任何状态之前确认该 id 未被占住，是则
返回 `False` 让启动 fail closed。`commit` 另有独立确认：槽位仍持有该 service。
这是 `RUNNING ⇒ has_order_service()` 成为结构性推论的另一半。相应地 `closeEvent` 里那次
`clear_active()` 按名字捕获这个拒绝（否则异常会从该 Qt override 逃出去，并跳过
shadow / 心跳 / 行情 / worker 的收尾），弹窗并 `event.ignore()` 交还给操作员。

**冻结的 request 必须真正冻结参数。** `StrategyVersion` 虽是 frozen dataclass，但
`__post_init__` 把 `parameters` 规范成普通 `dict`（仍可变），而 `parameter_hash` 只是
`identity.parameter_hash` 的投影、**从不重算**。直接保存 live `StrategyVersion` 会让
连接期间的原地修改在 hash 不变的情况下生效：plan 记 hash A，实际运行参数 B。因此 request
保存的是 `PaperStrategyLaunchFact`——identity / version_id / hash 加一份 **deepcopy** 的
parameters，并用 domain 的 `parameter_hash_for()` 校验快照确实等于 governed hash；不一致
即抛 `PaperLaunchIntegrityError` 拒绝启动（不是以旧 hash 跑新参数）。callback 上的
identity 门把该错误转成 mismatch：在 Qt slot 里抛会逃逸出槽并把 attempt 卡在 `CONNECTING`
持有租约，而 mismatch 走正常 rejection 释放 PAPER。两条路径都 fail closed。

**borrow 不是 store。** `candidate_service()` 全 package 只有一个调用点
（`_arm_and_publish`），结果只进局部变量 `service`，永不赋给属性、永不越过 promotion、
永不交给其他 capability。架构 guard 同时锁住"窗口一次都不再调"和"capability 只有一处"。

**异步 callback 的 stale 保护逐条保留：**

```text
shape 非法        fail loudly（不吞异常——那正好会让启动既不武装也不回滚）
connect 失败      只结束本方 attempt，不碰任何 candidate / active service / 更新的 attempt
plan 已过期       只 discard 自己的 candidate，只 reject 自己未武装的 plan
                  reject_connecting 返回 False 即"这是过期 callback"：
                  不清更新的 attempt 的 arm_confirmed、不重绘其控件、不为它弹窗
二次 preflight    行情 / 账户 / 策略 / candidate / 资金上限都可能已变
identity 复验     strategy_version_id / parameter_hash / candidate_symbols / requested_capital_limit
```

`reject_connecting` 的返回值正是退休 `_reset_auto_launch_controls` 的谓词，所以
presentation 也 gate 在它上面；log 行**不** gate，操作员仍应看到"过期结果已忽略"。

**GUI 与 generic task infrastructure 都不进来。** 拒绝以 payload **发布**，窗口弹窗；
操作员的确认步骤留在窗口（`_confirm_and_start_auto_quant`），因为 capability 不能 import
`QMessageBox`。异步 connect 仍走窗口的 `TaskSubmitter`（`resource_group="broker"`），
不引入 asyncio，也不复制 `TaskThread` / controller / closing gate。任务**未被接纳**时
必须 `reject_connecting` + 还账：否则会留下持有租约的 CONNECTING zombie。

**session 组装走一条窄 seam。** `RiskApplication` / `ExecutionApplication` /
`TradingRuntime` 的构造仍是组合根的（窗口的 `_build_paper_session`），orchestrator 只拿到
已校验券商读数与**借用的** candidate，换回两个 port 加 runtime。因此该 package 不 import
IBKR adapter、不 import risk / execution implementation、不 import composition 模块。
资金链保持 `Decimal`：`resolve_paper_session_capital` 以 **cash** 为约束，绝不用
buying power 替代，也就不会经 float 引入融资。

**发布成功后交付。** `session_published` 携带一个 `PaperLaunchPublication` 值对象
（runtime + workflow result + session id + candidate count）；窗口的小 handler 沿用既有
`_apply_paper_workflow_result` 渲染，并保留 `trading_runtime` / `auto_quant_snapshot`
赋值——这两个 active-session 事实属 E2。

**E1 当时窗口剩下的 Paper 代码**（这一刀结束时的事实，其中前四项已由 §8.19 迁走）：
`_apply_paper_workflow_result`、`_poll_auto_quant_orders`、
`_on_market_snapshot_changed` 中的 Paper ingress、pause / resume / stop、
manual reconciliation、finalization refresh、`_finish_auto_quant_session_if_safe`、
`closeEvent` teardown、execution page 的 session 渲染。

后续阶段：

```text
v2O-E3  HALT / manual reconciliation / finalization / shutdown   ✅ 已完成（§8.20）
v2O-E4  Paper presentation / render closure + MainWindow guards  ✅ 已完成（§8.21）
```

本轮**未触碰**任何 frozen core：`trading/runtime/*`、`trading/application/*`、
broker adapter、IBKR callback/gateway、Paper journal/schema、Shadow core 均无改动。

### 8.19 active Paper runtime orchestration 已抽出（v2O-E2）

E1 迁走的是**启动 sequencing**；`RUNNING` 之后那一段仍散在窗口：什么时候 poll、什么时候
把行情喂给会话、pause/resume/stop 各自意味着什么、以及结果由谁渲染。这一刀把前四件事
收进同一个 owner，窗口只留下 E3/E4 的职责。

```text
desktop_v2/orchestration/paper/
    __init__.py      命名理由与完整规则集
    models.py        冻结事实 + 文案（含 session 事件共用的一种 event 形状）
    queries.py       纯规则：launch 门 + active phase 集合 + 两个 snapshot 判读
    orchestrator.py  启动序列 + active 序列 + 唯一的 result publication
```

| 事实 / intent | 唯一 owner | E2 之前由谁驱动 | E2 之后由谁驱动 |
| --- | --- | --- | --- |
| 何时把行情喂给会话 | `PaperOrchestrator.on_market_snapshot` | 窗口的 `_on_market_snapshot_changed`（自检 phase + 打时间戳） | capability（窗口只做 cross-capability fan-out） |
| 何时跑 watchdog poll | `PaperOrchestrator.poll` | 窗口的 `_poll_auto_quant_orders`（timer 直连） | capability（timer 直连 `poll`） |
| pause / resume | `PaperOrchestrator.pause` / `resume` | 窗口两个 handler | capability（页面信号直连） |
| orderly stop | `PaperOrchestrator.stop` | 窗口读 `market_orchestrator.snapshot` | capability，通过注入的 market-snapshot provider |
| active session 的 runtime | `PaperWorkflowController` → coordinator → engine | 窗口额外持有 `trading_runtime` | 只由 canonical chain 持有 |
| result 的发布 | `PaperOrchestrator._publish_result` | 五条路径各自调窗口渲染 | 一条路径：`result_changed` + 每个 event 一次写入请求 |

三条判据，按重要性排序：

**一、四个 intent 各只有一个 owner。** 窗口不再 import `PaperWorkflowPhase` 来判
active session（唯一剩下的 phase 读都是 launch / reconciliation 的）；`pause_requested`
/ `resume_requested` / `stop_requested` / `QTimer.timeout` 四条 wiring 全部**直连
capability**，没有任何 window handler 中转——中转点就是第二 owner 的出生地。

**二、Phase 门只有一个定义。** `queries.active_session_phase` 决定 `RUNNING` /
`PAUSED` / `STOPPING` 三个合法 ingress 相位，`STOPPING` 必须在其中：退出、broker event
和 zero-state 证明都还要读行情。HALT 是**粘的**——它是这一刀最关键的 regression：
一次 tick 把会话打到 `HALTED` 之后，下一个 tick 和下一次 timer 都不得再进
coordinator，而且 orchestrator 不得自行"修复"相位（recovery 属 E3）。

**三、result 只有一条出口。** 启动成功也必须走 `_publish_result(result)`：它 emit
`result_changed`（窗口唯一的结果 handler / 渲染入口）并为 `result.events` 各请求一次
runtime event 写入。于是"一次操作 → 一个 result → 每个 event 写一次"成立，任何一条
操作自己 emit 都会静默跳过 event 请求，这也被 guard 钉住。

**四个跨边界 seam，都是注入而非 import：** market snapshot（stop 判定用，**call time
读**而不是构造时冻结）、`finalization_inflight_provider`（**TEMPORARY，E3 删除**）、
session-build seam（E1 已有）、task submitter。Paper 仍然不 import Market，也不知道
Shadow 存在。

**窗口刻意留下的两件事**：`_on_paper_result_changed`（一半渲染、一半是
`_handle_paper_e3_result_bridge`），以及三条仍然直调 workflow 的 E3 路径
（finalization proof / reconnect / manual resume 确认），后者走窗口侧的
`_publish_window_paper_result`。这三个名字是 E3 的施工面，不是兼容层。

**删掉的第二份 truth**：`trading_runtime`、`paper_execution_health`（只写不读的缓存）、
`_last_stream_ingress_monotonic`（迁进 capability，语义从 `0.0` 改成 `None` 哨兵，因为
"还没发生过 ingress"和"在时钟原点发生过 ingress"是两件事）。`auto_quant_snapshot` 改名
`_paper_render_snapshot` 并降级为**只给渲染路径读**；三个 interlock 改读
`has_runtime_obligations`（snapshot.active OR positions OR pending_orders），从
canonical result 现读不缓存。

本轮 **13 项 mutation 全部 RED**（`scripts/mutation_e2.ps1`，可复跑）：删 ingress 相位门 /
把 HALTED 算作 live phase / 抑制窗口改成闭区间 / poll 不看 finalization seam /
poll 自己 emit result / stop 在构造时冻结 snapshot / ingress 不打时间戳 / 拒绝的 pause
仍报成功 / 窗口重新持有 runtime handle / timer 不再指向 capability / fan-out 丢掉 Paper /
interlock 恒返回 False。

本轮**未触碰**任何 frozen core：`trading/runtime/*`、`trading/application/*`、broker
adapter、execution lease、Shadow core 均零 diff——包括 E1 刚完成的 promotion reservation
（`reserve/commit/cancel_candidate_promotion` 与 reserved-id ownership）。

### 8.20 Paper recovery / finalization orchestration 已抽出（v2O-E3）

E2 之后，`MainWindow` 仍然决定 HALT 之后怎么对账、zero-state 证明什么时候开始、以及一个
finalized 的 session 什么时候可以交出 ownership。这一刀把这三件事（连同关闭时对 Paper 的
判断）收进**同一个** `PaperOrchestrator`——没有 `PaperRecoveryOrchestrator` /
`PaperFinalizationManager` / `PaperShutdownController`，Paper capability 仍然只有一个
sequencing owner。

```text
HALTED → reconcile → RECONCILING → 一次性证据 → RECONCILING_READY
       → 操作员明确确认 → resume 既有 session → RUNNING

STOPPING → 排程 zero-state 证明（5s backoff，engine 仍 active 时才生效）
         → capture evidence < disconnect < confirm evidence
         → workflow.finalize_if_safe() → clear_active() → PAPER lease 释放
```

**三条硬不变量**，也是本轮最终验收标准：

```text
1. HALT 之后没有任何自动路径可以绕过人工 reconciliation
   （reconcile 只收证据；resume 必须在用户确认之后重读证据；stale / consumed /
     changed 证据一律拒绝，只 log，不造 result、不修相位）

2. 没有完整证明（local zero-state + broker zero-state + reconciliation complete +
     finalization evidence valid + workflow finalize_if_safe == True）之前，
     active Paper ownership 与 PAPER lease 都绝不释放
   （broker disconnected != finalized；finalize_if_safe 是唯一 canonical gate）

3. MainWindow 已退出 recovery / finalization sequencing，
     且没有提前把 E4 的 presentation / composition 职责塞进 capability
```

**窗口交出的 sequencing**：`_reconnect_auto_order_service` /
`_auto_order_service_reconnected` / `_resume_auto_quant_from_reconciliation` /
`_auto_order_resume_failed` / `_auto_order_reconciliation_failed` /
`_schedule_paper_finalization_refresh` / `_start_paper_finalization_refresh` /
`_paper_finalization_completed` / `_paper_finalization_failed` /
`_finish_auto_quant_session_if_safe` / `_handle_paper_e3_result_bridge` /
`_publish_window_paper_result`，以及 `_paper_needs_manual_recovery` /
`_release_close_drain_if_recovery_required`（后者是窗口自己的 Paper 相位推理）。窗口也不再
持有 `_paper_finalization_inflight` / `_last_paper_finalization_started`，并且**不留
forwarding property**。

**窗口留下的**：两个 `QMessageBox` 确认（launch 与 resume——capability 不能 import
`QMessageBox`）、`closeEvent` 的 presentation 与 generic teardown、execution render
ownership（E4）。

**新增 public API**：`reconcile()`、`confirm_reconciliation_resume()`、
`prepare_shutdown() -> PaperShutdownResult`（`PaperShutdownDisposition` =
`READY` / `WAITING_FOR_FINALIZATION` / `MANUAL_RECOVERY_REQUIRED` /
`OWNERSHIP_BLOCKED`），以及三个**无 payload** 的 signal：`presentation_refresh_requested`
（没有新 result 但控件状态变了）、`session_finalized`、`manual_recovery_required`。三者都不
携带 phase copy / bool mirror / state dict。

**result 仍只有一条出口**：`_publish_result` 之外多了一个 `_after_result`——一个 `STOPPING`
result 会从 stop、stream tick 和 poll 三处到达，三份"该不该开始证明"就是其中一个开始自己
排程的方式。`desktop.py` 里已不存在 `for event in result.events`。

**release 的证据走窄 provider**：`reconciliation_rows_provider: Callable[[str],
Sequence[object]]`（composition root 里是 `order_repository.reconciliation_rows` 的
lambda），而不是把 repository 交进 capability。

**release 在 slot 侧是两阶段的**（本轮唯一一处 canonical-owner 改动，单独披露）：
`reserve_active_release()` → `finalize_if_safe()` → `commit_active_release()`，拒绝时
`cancel_active_release()` 把锁还回去。理由是 `finalize_if_safe` 是 check-and-commit 且**不是
本轮的**：它一旦返回 `True` 就已经 `release_paper()` 并清掉 workflow 的
coordinator / result / evidence。原来的顺序是 `disconnect → finalize_if_safe() →
clear_active()`，于是 `clear_active()` 因 E1 的 promotion reservation 拒绝时，状态会变成
"ownership 还在、PAPER lease 已经 NONE"——E1 的 ownerless invariant 被反向打破。反过来先
clear 又会在 workflow 拒绝时丢失 ownership，所以 slot 必须可锁。新增
`src/us_quant/trading/application/paper/active_release.py`（`PaperActiveRelease` mixin，
按仓库既有 `trading/runtime/recovery.py` 的模式拆出，`service.py` 仍在既有的 thin-boundary
结构守卫内），`models.py` 新增 `PaperActiveReleaseReservation`，`service.py` 的
`connect_active` / `clear_active` / `reserve_candidate_promotion` 各加一处 release 守卫
（只有一处定义：`_refuse_if_release_in_flight`）。协议不连接、不断开、不提交、不取消，
也不碰 execution lease。

**claim 先装，并且 connect 与 release 互斥**（第二次 review 的 blocker）：把 reservation
装在第二次加锁时、以及 `connect_active` 在锁外裸调 `service.connect()`，都还是
check-then-act，而且是双向的——两个重叠的 reserve 可以互相覆盖 token（于是先那个的 commit
跑在已经释放的 lease 之后必然失败），release 与 connect 交错可以留下
`broker connection alive + active slot gone + PAPER lease gone`。所以
`connect_active` 在临界区内标记 `_active_connect_inflight`（`finally` 清除），
`reserve_active_release` 在同一个临界区里就装上 reservation、然后才在锁外读连接
（读失败或读到 connected 就 cancel 自己的 reservation 再抛）。两条 claim 都在调用 broker
之前生效，`commit` 的 total 才是结构性的，而网络调用仍然不在锁内。

**`clear_active` 走同一套 claim**（第三次 review 的 blocker）：它一度保留自己的检查块，因此
re-open 完全不在它的视野里——`connect_active` 已装 claim 并在 `service.connect()` 中时，
`clear_active` 仍可能读到旧的 "disconnected" 把 slot 清掉，最终得到
`broker socket live + active owner = None`。而在它顶部补一次检查**也没用**（还是
check-then-act）。所以它不再有检查：`clear_active(expected_service=...)` 就是
`reserve_active_release(expected_service=...)` + `commit_active_release(...)`，
`expected_service` 在装 reservation 的同一个临界区里校验。clear / release / connect /
promotion 对 active slot 的互斥因此全部使用同一套 claim，没有第四种 pre-check。

**`READY` 对应三份 ownership 全空，且 lease 那一份必须 `is PAPER`**（第二、三次 review）：
`_ownership_verdict()` 依次检查 candidate（`PaperTradingService.has_candidate_ownership()`）、
active slot、以及 **`workflow.lease is ExecutionLease.PAPER`**。service 拥有两个 slot，而 E1
的 `discard_candidate()` 失败会留下 candidate 仍被 track、相位回到 `READY`、PAPER 已释放——
"没有 active service"并不等于"什么都没持有"。lease 判据必须是 `is PAPER` 而不能是
`is not NONE`：`ExecutionLeaseManager` 与 Shadow **共享**，`lease` 返回持有它的一方，所以
`is not NONE` 会把一个正在正常运行的 Shadow 会话读成 Paper 的未释放 ownership，从而让应用
无法关闭（而 `closeEvent` 是 Paper READY 之后才轮到 Shadow teardown）。SHADOW 属于
`ShadowOrchestrator`，由它自己 stop 时交回。E3 不自动清理 candidate，返回
`OWNERSHIP_BLOCKED`。

**删掉的数值门禁**：`tests/test_paper_trading_service.py` 原有的 `span < 60` /
`len(lines) < 500` / `implementation > 2.5 * boundary` 已删除（用户约束：不设行数上限，
也不许改名保留），换成按**调用名**判定的"锁内禁止 broker 调用"、精确的 ownership 写者集合、
闭合的 import allowlist、以及"无 Qt / 无 order API / 无 service bag"。

**`TaskSubmitter` 返回 `False` != task 失败**：`False` 只是 broker resource group busy、
什么都没发生，因此不 `fail_finalization_refresh`、不 HALT，下一次合法 result 按 backoff
重试；只有真正跑过并失败的 task 才把 `STOPPING` 推到 `HALTED`。把"忙"当"失败"会把一次排程
冲突升级成一次 HALT。

**shutdown 从 canonical phase 分类**：`CONNECTING` 明确拒绝（它合法地没有 result 且持有
PAPER lease，一个未发布的 launch 可能已经连上 candidate）；`RUNNING`/`PAUSED` 复用
`self.stop()` 之后**重新读取**相位与结果再分类——同一次 `request_stop` 可能直接落到
`HALTED`，也可能 fast-stop 到 `FINALIZED`；只有 `_ownership_verdict()` 会返回 `READY`，
且只在 `has_order_service()` 为假时。

本轮 **41 项 mutation 全部 RED**（`scripts/mutation_e3.ps1`，可复跑），脚本比 E2 版多一道
**语法闸门**：篡改后先 `ast.parse`，语法不合法判 `HARNESS-ERROR` 而不是"抓住"——一个丢掉
缩进的 `repl` 会让 pytest 报 collection error，那次运行对被测属性什么都没说。

本轮 canonical-owner 改动只有上文的 active-release reservation 一处；
`trading/runtime/*`（`workflow.py` / `recovery.py` / `reconciliation.py` / `coordinator.py` /
`trading.py`）、RiskApplication / ExecutionApplication、broker adapter、execution lease 与
Shadow core 仍然零 diff。

设计依据见 `DESKTOP_DECOMPOSITION.md` §29。

### 8.21 Paper presentation / render closure 已抽出（v2O-E4）

E1/E2/E3 迁走了 Paper 的 sequencing，窗口里剩下的最后一份 Paper 状态是
`MainWindow._paper_render_snapshot`：一个 presentation-only cache。它不是业务 truth，但它
**仍然是一个窗口拥有的 Paper session fact**，而且它有存在的理由——`finalize_if_safe()` 释放
PAPER 时会一并清空 canonical result，UI 若直读 `paper_workflow.result`，会话结束的瞬间页面
就空了。E4 解决这件事，做法不是把那个 attribute 改名搬进 orchestrator。

**两个 truth 的边界**：

```text
PaperWorkflowController.result
    当前业务生命周期的 canonical result；finalize_if_safe() 成功时清空它
PaperPresentationSnapshot
    最后一次被正式发布、可展示的 immutable presentation fact
    只由 result publication path 写入，canonical result 被清空时故意保留
```

`presentation.py`（新）持有 `PaperPresentationSnapshot` 与纯函数
`project_presentation(result)`。模型**不机械复制** `PaperSessionResult`：health、两组
broker / reconciliation 计数、events 都不进去——一个逐字段镜像 result 的模型就是本轮要删掉
的第二份 truth。字段按 execution page 真正的消费者（`presenter.py` / `rows.py`）选取，行事实
（positions / pending orders / fills）以 plain value 复制出来，避免 view 里还能摸到 live
runtime 与 order intent。

**数据流**：

```text
PaperSessionResult
   → PaperOrchestrator._retain_presentation(result)          （唯一写入点）
   → presentation.project_presentation(result)               （纯投影，独立模块）
   → PaperPresentationSnapshot  （保留为 _presentation）
   → result_changed.emit(result)                              （先装 view，再发信号）
   → MainWindow._on_paper_result_changed → _render_auto_quant_snapshot
        fetch（quotes / account / broker reading / journal rows）
   → pages/execution/projector.build_session_view(...)         （纯 read model，新）
   → ExecutionRuntimeView → ExecutionPage.render(view)
```

`_presentation` 没有 clear / reset / invalidate，唯一写入点在 result publication path 上，
所以"点 Start 就清空上一轮"、"连接失败后丢失上一轮记录"都写不出来：

```text
PREPARING / READY / CONNECTING / connect 失败 / 启动被拒   不发布 result → 保留上一轮
新 session 发布第一个 result                              原子替换
```

**窗口退出的东西**：`_paper_render_snapshot`（不留 alias / property / shim）、
`_render_auto_quant_snapshot` 里的 read model 组装（按候选过滤券商持仓、pending order 建表、
journal audit row 按 session_id 过滤——都搬进 `projector.py`，连同三张表的行预算常量）、
`_publish_execution_controls` 里的 phase → 控件判断、`_launch_locked` 里的 `CONNECTING`
字面比较。窗口留下的只有 fetch + 委托，以及 composition / presentation（两个
`QMessageBox`）/ generic teardown。

**窗口仍然读 canonical**：§15 的硬不变量——启动与关闭不得从 retained view 推导。映射本身
成为 capability 的纯规则 `queries.control_facts(phase, awaiting_confirmation=…)`，经
`paper_orchestrator.session_control_facts` 取用；`CONNECTING` 改用 capability 已有的
`queries.launch_attempt_in_flight`。窗口仍读 canonical phase，只是不再**解释**它。

**新增 guards**：`_paper_render_snapshot` 不得出现（含换名：`*render_snapshot*` /
`*_presentation` / `*_session_view`）；render 路径必须委托 `build_session_view` 且不得内联
scoping、不得出现 phase 词；不得直读 `paper_workflow.result` / `engine_snapshot`；
presentation 模块不得 import Qt widget / adapter / service；retained model 不得被
`trading/**` 反向 import；projector 不得 import orchestrator 或 service；retained view 的
写入者集合精确（唯一写入点 + 无 clear）；"先装 view 再 emit"的顺序。

**不变量**：retained view 不参与 start / preflight / launch / stop / reconcile / finalize /
ownership / lease / shutdown / risk / execution。20 个决策方法在**代码层**被断言不读
`_presentation`（docstring 排除，因为移除本身要在注释里点名旧 cache）。对称面也测了：页面上
摆着 `active=False` 的已结束会话时 `start()` 仍必须走到自己的 preflight gate。

`tests/test_desktop_paper_presentation_closure.py` 新增 21 项；`scripts/mutation_e4.ps1`
**11 项全部 RED**（M1 finalized 不保留 / M2 释放 canonical 时清空 / M3 窗口重新缓存 /
M4 shutdown 读 view / M5 start gate 读 view / M6 active phase 之外丢弃 view /
M7 新 session 不替换 / M8 改读 canonical result / M9 projector 调 broker mutation /
M10 窗口重新拼 view / M11 先 emit 再保留）。

**零 diff**：`trading/runtime/*`（`workflow.py` / `recovery.py` / `reconciliation.py` /
`coordinator.py` / `trading.py`）、RiskApplication / ExecutionApplication、broker adapter、
`ExecutionLeaseManager`、Shadow、`PaperTradingService` 的 ownership 协议（含 E3 刚稳定的
`reserve_active_release` / `commit_active_release` / `cancel_active_release` /
`connect_active` / `clear_active` / `finalize_if_safe`）全部未改。本轮**没有**发现需要在
canonical owner 修的 bug，因此**没有** canonical-owner exception。

设计依据见 `DESKTOP_DECOMPOSITION.md` §30。

### 8.22 Runtime Events orchestration 已抽出（v2O-F1）

System route 的页面是 native v2（§8.10），但它两个 workspace 的**运行时**仍全在窗口。
这一刀只处理第一个：**Runtime Events**（Settings 是 v2O-F2，Gateway probe 冻结）。

**新 home**：

```text
desktop_v2/orchestration/system/
  __init__.py            只写清"这里没有 SystemOrchestrator"及其原因
  runtime_events/
    __init__.py          export RuntimeEventsOrchestrator + RuntimeEventsEnvironment
    models.py            Qt-free：RuntimeEventsEnvironment（version + 四个 root）
    orchestrator.py      record / refresh / resolve / export / notify_task_count_changed
                         + FlushTimer 注入 seam + QtFlushTimer + 三个消息信号
```

**数据流**：

```text
Capability ──runtime_event_requested──▶ MainWindow._route_runtime_event（只转发四个字段）
                                          → RuntimeEventsOrchestrator.record
                                          → RuntimeEventStore.add（唯一写入口，唯一 redaction）
                                          → schedule / coalesce → build_runtime_events_view
                                          → RuntimeEventsPage.render

RuntimeEventsPage.refresh_requested / resolve_requested / export_requested
    → orchestrator.refresh / resolve / export（直连，不再经过窗口）
```

**窗口退出的东西**：`self.runtime_events`（store alias）、`_last_runtime_events_refresh`、
`_runtime_events_refresh_pending`、`_last_runtime_export`、`_runtime_info_text`、
`_record_runtime_event`、`_schedule_runtime_events_refresh`、`_flush_runtime_events_refresh`、
`_refresh_runtime_events`、`_resolve_runtime_event`、`_export_terminal_state`，以及五个
`_record_*_runtime_event` per-capability adapter（全部无 alias / 无 property / 无 shim）。

**窗口留下的东西**：构造 store（内联在 orchestrator 构造里，窗口不留句柄）/ page /
orchestrator；注入 `active_task_count` provider 与 export provider；连接页面三个 intent 与
orchestrator 三个消息信号；`_route_runtime_event`（唯一 runtime-event adapter，只转发四个
字段）；窗口自己的四个事实走 `orchestrator.record(...)`（strategy `STATUS_CHANGE` /
minute_data `MINUTE_PERSIST_FAILED` / runtime `RUNTIME_SHUTDOWN_PARTIAL` / task
`TASK_FAILED`）；`_start_task` / `_worker_finished` 各一次
`notify_task_count_changed()`；`QMessageBox`；以及跨 capability 的
`_export_runtime_bundle(events) -> Path`。

**terminal export 的切法**：跨 capability 事实收集留在 composition root
（Account + Market + Strategy + Shadow + Targeted 七族 + Paper 两张 audit 表 →
`export_terminal_bundle`），成败 sequencing / `last_export` / `EXPORT_OK` / refresh /
成功与失败信号全部属于 capability。provider 不写事件、不 render、不持 `last_export`、
不弹框、不 schedule。顺序为：读取当前 events（一次）→ exporter → 更新 `last_export`
→ 写 `EXPORT_OK` → refresh → `export_succeeded.emit(target)`；失败则 `last_export`
保持原值、不写 `EXPORT_OK`、不发成功信号、发 `warning_requested`。

> **顺序披露。** 退休前窗口是 refresh 之后再写 `EXPORT_OK`，那一行因此出现在约 1 秒后的
> 合并 refresh 上；本轮先记录再 paint，`EXPORT_OK` 出现在同一次 paint 上。这是 sequencing
> 变化而非行为回归，由 `test_a_successful_export_sequences_fact_event_paint_then_report`
> 锁定（断言最后一次 view 的首行就是 `EXPORT_OK`，且只 paint 一次）。

**coalescing**：显式 Refresh 与 resolve(valid) 立即重画；runtime event 与 task-count 变化
按 1 秒窗口合并为一次 flush；flush 重新读 store 当前 truth；被显式重画取代的迟到 callback
发现 pending 已清空，什么都不画。clock 与 scheduler 都是注入 seam，因此该时序测试不需要
事件循环、也**不需要 sleep**。

**不变量**：`RuntimeEventStore` 仍是唯一 persisted truth（schema / add / resolve /
list_recent / redaction 全未改，也没有在 orchestrator 再做一次 redaction）；
orchestrator 不缓存 event list（每次 render / export 都 `list_recent(500)`，state 集合被
guard 精确断言）；不 import 任何其它 capability、trading、sqlite；generic task lifecycle
（`TaskThread` / `DesktopTaskController` / worker list / closing gate / busy dialog）仍在
窗口；`RuntimeEventsPage` 仍只 render / emit；`SystemPage` 仍 containment only，且不认识
orchestrator；Settings 15 项职责与 Gateway probe 原样冻结。

> **前向引用（v2O-F2）**：最后一句中"Settings 15 项职责原样冻结"只对 v2O-F1 那一轮成立；
> 它们已在 v2O-F2 迁入 `orchestration/system/settings/`（§8.23），Gateway probe 仍留在
> 窗口、是否独立 F3 待重新扫描。**（前向引用结束）**

**没有 SystemOrchestrator。** System 是 containment route：一个同时持有 event store、
settings service 与 credential service 的对象正是本轮点名禁止的 god object。
`orchestration/system/orchestrator.py` 与 `SystemOrchestrator` / `SystemManager` /
`DesktopSystemManager` / `SystemContext` / `ApplicationContext` / `ServiceBag` /
`RuntimeManager` 的**不存在**由 guard 断言。

> **前向引用（v2O-F2）**：`orchestration/system/settings/` 当时"必须不存在"，是 v2O-F2 的
> 交付物；现在它存在（§8.23），而 `orchestration/system/orchestrator.py` 与那七个 god
> object 名字仍然**不存在**，guard 相应改为"两个 sibling 各有 owner、且没有聚合 owner"。
> **（前向引用结束）**

**新增 guards**：`tests/test_desktop_runtime_events_orchestration_architecture.py` 14 条
（无 store alias / 无 refresh 与 last-export state / 不调 store 方法 / 不 render 页面 /
view 投影唯一调用者 / orchestrator 无 capability import / page 与 table 不碰 store /
SystemPage 仍 containment / export provider 只做 composition / Settings 当时仍在窗口 /
Gateway probe 未动 / 不缓存 event list / 不 import task lifecycle / 只剩一个纯转发
adapter），加上 capability map 的 System 行断言。**（v2O-F2 更新）** 其中"Settings 仍在
窗口"那一条已按其自身惯例反转为 `test_settings_orchestration_has_left_the_window`，
"settings 目录必须不存在"也改为"两个 sibling 各有 owner"；新增 Settings 侧的 23 条守卫
见 §8.23 与 `DESKTOP_DECOMPOSITION.md` §32.12。

**行为测试**：`tests/test_desktop_runtime_events_orchestrator.py` 22 项（record 单写、
refused write 上抛、每次 paint 重新读 store、burst 只 arm 一次、flush 读当前 truth、
迟到 flush 不重画、显式 refresh 与 resolve 立即、task count 逐次读取、export 成败顺序
与幂等）+ 重写的 `tests/test_desktop_v2_runtime_events_wiring.py` 15 项（页面 intent 直连
capability、五个 capability 的 fan-in 各一次入库、task 生命周期、resolve 用稳定 id、
导出 payload 未变与新语义）+ `tests/test_desktop_runtime_events_timer_seam.py` 3 项
（真实 `QtFlushTimer`：投递一次、single-shot、disarm 后不投递；以及真实窗口的 burst 走完
真实 1 秒窗口 —— 这几项是独立 review 指出"fake timer 覆盖不到真实 seam"之后补的）。

**mutation**：`scripts/mutation_system_runtime_events_f1.ps1`，**14 个 mutant 全部 RED**
（M1 record 不写 / M2 不 schedule / M3 用缓存 events / M4 resolve(None) 仍写 /
M5 resolve(valid) 不重画 / M6 失败仍更新 last_export / M7 失败仍写 EXPORT_OK /
M8 成功不更新 last_export / M9 task count 构造时捕获 / M10 重复 arm /
M11 窗口重新直接写 store / M12 窗口重新持有 last_export /
M13 真实 Qt timer 只存 callback 不 arm / M14 被取代的迟到 flush 仍重画），无
`HARNESS-ERROR`。M13 正是 review 找出来的存活 mutant（详见
`DESKTOP_DECOMPOSITION.md` §31.11）。

**同时修好一个被本刀打断的既有 gate**：`scripts/mutation_e3.ps1` 的 M27 把插入锚点写在本刀
删掉的那一行上，于是"窗口再次持有 `_paper_finalization_inflight`"静默失去验证（脚本当时
也没有非零退出码）。M27 已改锚到窗口仍存在的 `__init__` 行，并补上退出码尾巴；重跑
`mutation_e3.ps1` 全部 RED、exit 0。`mutation_e2.ps1` / `mutation_e4.ps1` 的同类退出码弱点
留给后续。

> **前向引用（v2O-F2）**：M27 的那个"仍存在的 `__init__` 行"正是 F2 删掉的
> `self._connection_settings_enabled = True`，因此它**第二次**静默失效；F2 已改锚到
> `self.preferences = self.preferences_store.load(defaults)`，并实跑 e2/e3/e4 三个脚本，
> 顺带把 e2 里两个更早的死锚点（M4 / M9）一并修复。`mutation_e2.ps1` / `mutation_e4.ps1`
> 的非零退出码尾巴**仍然**没有补（F2 未做，避免超范围）。**（前向引用结束）**

**零 diff**：`trading/runtime/**`、`trading/domain/**`、RiskApplication /
ExecutionApplication、`PaperTradingService`、`PaperActiveRelease`、`ExecutionLease`、
broker adapter、Shadow、Paper orchestration、Market / Account / Research business
logic、`DesktopSettingsService`、credential service、`UserPreferencesStore`、
`SettingsPage`、`SystemPage`、`RuntimeEventStore` schema、`export_service.py` artifact
schema 全部未改。本轮没有发现需要在 canonical owner 修的业务 bug，因此**没有**
canonical-owner exception。

设计依据见 `DESKTOP_DECOMPOSITION.md` §31。

### 8.23 Settings orchestration 已抽出（v2O-F2）

System route 的第二个 workspace。页面早已是 native v2（§8.10），但它的**运行时**——视图
组装、凭据 save/clear 排序、偏好事务适配、provider 双向同步、两个 capability 确认，以及
两个 presentation fact——全在窗口。这一刀把它们收进
`desktop_v2/orchestration/system/settings/`，与 §8.22 的 Runtime Events 成为 sibling。

**新 home**：

```text
desktop_v2/orchestration/system/settings/
  __init__.py           export SettingsOrchestrator + CredentialSavePlan + CredentialSaveOutcome
  models.py             Qt-free：CredentialSaveOutcome（NOT_REQUIRED / NO_CHANGE /
                        INCOMPLETE / SAVE）+ frozen CredentialSavePlan（provider + 两个值）
  queries.py            Qt-free / service-free 纯规则：credential_save_plan、
                        credential_status_text、preferences_from_draft、
                        settings_draft_from_preferences、settings_storage_view、
                        provider_requires_api_key、provider_label
  orchestrator.py       render_current / select_api_provider / select_market_provider /
                        adopt_market_provider / set_connection_settings_enabled /
                        preview_theme / save_credentials / clear_credentials /
                        save_preferences / request_provider_switch / 两个 toggle 与 confirm
                        + 九个信号
```

**数据流**：

```text
SettingsPage ──九个 intent──▶ SettingsOrchestrator（直连）
                                render_current(): credential_service.status + 活动行情源 + 两个 fact
                                save/clear credentials → DesktopCredentialService
                                save_preferences / request_provider_switch → DesktopSettingsService.commit
                                发布 settings_committed / market_switch_requested /
                                     theme_preview_requested / market_provider_selection_requested /
                                     两个 confirmation_requested / information / warning / log

MainWindow（composition only）
  提交采纳（self.config / self.preferences）→ 主题、market provider、safety badge、preflight、状态行
  theme_preview_requested → _apply_theme（全工作台）
  market_provider_selected → 读 selected_provider() → adopt_market_provider（静默同步）
  market_switch_requested  → 无订阅直设 + 日志 / 有订阅 _request_market_switch（Paper interlock）
  information / warning → QMessageBox；log → _log
  connection_settings_enabled_changed（Market 发布）→ capability.set_connection_settings_enabled
```

**窗口退出的东西**：`self._settings_api_provider`、`self._connection_settings_enabled`、
`_settings_draft`、`_settings_storage_view`、`_publish_settings_view`、`_credential_status_text`、
`_settings_provider_selected`、`_stream_provider_selected`、`_switch_to_settings_provider`、
`_api_provider_changed`、`_set_connection_settings_enabled`、`_save_user_preferences`、
`_save_api_credentials`、`_clear_selected_api_credentials`、`_clear_saved_finnhub_key`（全仓
零 caller 的死 handler，直接退休、无 shim）、`_preview_theme_changed`、
`_paper_order_capability_toggled`、`_extended_hours_paper_toggled` —— 全部无 alias / 无
property / 无 forwarding shim。

**窗口留下的东西**：`self.config` / `self.preferences`（全局 composition fact，被 Market /
Paper / Risk / Research / Gateway 共用，搬进 Settings 会造成反向依赖）、`settings_page`、
`settings_orchestrator`、三个窄 provider（`current_config` / `broker_config` /
`runtime_guards`）、两个 bridge（`_on_market_provider_selected` /
`_on_market_switch_requested`）、`_on_settings_committed`（采纳 + fan-out）、两个 confirm、
两个 message、`_connect_settings_page`、`_apply_theme` 与 Gateway probe。

**三个 canonical owner 逐字未变**：`UserPreferencesStore`（persisted preferences truth，
schema / validate / 原子写）、`DesktopSettingsService`（validate→derive→preflight→guard→
persist→apply 顺序，仍无 rollback / two-phase）、`DesktopCredentialService`（provider→字段
映射、DPAPI、status/save/clear 语义）。`settings_service.commit` 的调用点全仓仍然只有一处，
只是从窗口移到 `SettingsOrchestrator._commit`。

**单一 render owner 与三次现读**：`SettingsPage.render(SettingsPageView)` 只有 capability
一个调用者；每次重画都现读凭据状态与活动行情源——缓存状态会让"保存成功后状态行仍显示未
保存"，构造时捕获行情源会让之后启动的行情绕过清除门禁（mutation M3 / M4 / M5 都 RED）。

**凭据排序（语义冻结，含一个刻意保留的顺序）**：`credential_save_plan` 是唯一决策点
（NOT_REQUIRED / NO_CHANGE / INCOMPLETE / SAVE，trim 只做一次，写的就是 plan 里的值），
失败只报 warning 且不清输入；Clear **先**比较 `provider == 活动行情源`、**再**走"是否
需要 key"分支——若反过来，`provider == 活动源 == "ibkr"` 时会从"行情运行中"变成"无需清除"，
那是行为变化。

**provider 双向同步的非对称（刻意保留）**：Settings 侧选 provider 会带动 selected API
provider（操作者选它就是为了配置它）；Market 侧变化**不带动**。所有 programmatic setter
都是 `emit_change=False`（guard 要求字面 `False`），否则两个 combo 会互相驱动成环。

**本轮修的 provider 切换顺序 bug（§8.23 的核心）**：退休前窗口在一个**没有返回值**的
`_save_user_preferences(draft)` 之后直接进入行情分支，所以保存被**拒绝**时仍会
`request_switch` / `set_selected_provider`，并打出"默认行情源已切换"——磁盘上的偏好却还是
旧值。canonical fix 在 Settings：`request_provider_switch` 只在 commit 返回后发布
`settings_committed`（窗口采纳）→ `market_switch_requested`（用落盘后的 provider）。回归
测试 `tests/test_desktop_settings_provider_switch_regression.py` 4 项（含"在切换回调内部采样
`window.preferences`"以真正测顺序），mutation M10 / M11 RED。

**guards 与 mutation**：新增
`tests/test_desktop_settings_orchestration_architecture.py` 23 条（presentation fact 唯一
owner、render 唯一调用者、窗口不调 service、`except` 集合精确、无 capability import、state
精确九项、公开 API 精确、页面 nine signals 未变、活动源不缓存、程序化 setter 静默、主题与
market interlock 不入 Settings、SystemPage 仍 containment、F1 边界未回退、Gateway probe 未
动、无 god object、死 handler 无 shim、18 个退休方法缺席、真实窗口无残余状态），并按其自身
惯例更新 F1 的两条 guard（反转 + 从"settings 目录不存在"改为"两个 sibling 各有 owner"）。
`scripts/mutation_system_settings_f2.ps1`：**21 个 mutant 全部 RED**，0 survived，
0 harness-error（M11 首次存活——断言只测"两个信号各自发生过"、对顺序不敏感——测试加强后
RED，这是"测试必须真的测它声称的属性"的现场例子）。

**零 diff**：`trading/runtime/**`、`trading/domain/**`、RiskApplication /
ExecutionApplication、`PaperTradingService`、`PaperActiveRelease`、`ExecutionLease`、
broker adapter、Shadow、Paper orchestration、Market / Account / Research business logic、
`RuntimeEventsOrchestrator`、`RuntimeEventStore`、`export_service.py`、
`DesktopSettingsService` 事务顺序、`DesktopCredentialService` 存储语义、
`UserPreferences` 校验与 schema、IBKR 只读默认、Paper 安全门全部未改。

**未做**：Gateway probe 未迁（是否独立 F3 待重新扫描）、MainWindow composition closure、
Dashboard、trading/live/AI 全部未开始。本轮**不**声称 "v2O-F System COMPLETE"。

> **前向引用（G1）**：重审结论是 Gateway 不需要 F3（§8.24），Dashboard 的 render 由
> `DashboardOrchestrator` 持有，`v2O-F System` 正式 COMPLETE。**（前向引用结束）**

设计依据见 `DESKTOP_DECOMPOSITION.md` §32。


### 8.24 MainWindow composition closure G1：generic runtime / shell ownership

这一轮不按行数拆窗口。它把 `MainWindow` 剩余的 generic runtime / shell ownership 收到终局，
使窗口只保留合法的五类职责：dependency construction、signal wiring、cross-capability
fan-out、global shell presentation、dialog 与 application-wide finished-fact adoption。

**worker collection 归 `DesktopTaskController`（G1）。** 退休前 `self.workers =
self.task_controller.workers` 持有的是 controller 内部可变 list 的别名。G1 删除该 property 与
窗口别名，新增窄查询 API：`running_workers() -> tuple`（每次现算的不可变快照）、
`has_running_workers()`、`active_count`（只计 `isRunning()`）。Runtime Events 的 task-count
provider 改为 `lambda: self.task_controller.active_count`；register / finish 仍是 collection
唯一修改点，Qt signal wiring 留在窗口。

**shutdown admission 归 `RuntimeSupervisor.shutting_down`（G1）。** 退休前
`MainWindow._closing` 与 `RuntimeSupervisor.shutting_down` 是两个靠调用顺序同步的 admission
boolean，`closing_gate` 作为 drain component 存在只为置位前者。G1 删除 `_closing` /
`_close_admission_gate` / `closing_gate` 三者：`begin_shutdown` 直接置位、`cancel_shutdown`
直接复位、`_start_task` 读 `self.runtime_supervisor.shutting_down`。安全语义逐字保留——
`shutdown_essential` 豁免、MANUAL_RECOVERY_REQUIRED 拒绝后经 `cancel_shutdown` 重开、
WAITING_FOR_FINALIZATION 与 OWNERSHIP_BLOCKED 不自动开门（各有 wiring 测试）。

**RuntimeSupervisor 边界不动。** 仍 Qt-free / broker-free / capability-free / callback-driven；
`_register_runtime_components()` 是纯 composition registration（五个 generic component，无
Paper phase 判断、无 lease 释放、无事件写入、无 render）。

**closeEvent 是合法的 composition point**（不为它建 ShutdownCoordinator），且只消费 PUBLIC
facts/verdicts：`begin_shutdown`、`task_controller.running_workers()`、
`paper_orchestrator.prepare_shutdown()`（Paper 安全判定的唯一 owner）、disposition 呈现、
`shadow_orchestrator.shutdown()`、`runtime_supervisor.shutdown()`、
`market_orchestrator.worker_running`。AST guard 锁死：closeEvent 对 `paper_orchestrator`
的访问恰好是 `{prepare_shutdown}`，不读 phase / lease / Paper result 内部。

**Gateway probe 终局：shell diagnostic，不设 F3。** 重审后它没有独立的 canonical state、
lifecycle、状态机、worker、repository、lease 或 transaction；`_probe_gateway` 留在窗口，guard
锁死为四件事（读 `self.config.ibkr` → `probe_ibkr_socket` → badge text/state → repolish）。
**v2O-F System 至此 COMPLETE。**

**Dashboard ownership（G1）。** 新增本轮唯一允许的轻量 page owner
`desktop_v2/orchestration/dashboard/`：`DashboardOrchestrator`（state 恰好 `_page` /
`_providers` / `_chart`）是 `DashboardPage.render` 的唯一 caller，持有 retained chart fact，
四个输入经 callable provider 现读、绝不缓存；`set_chart` 恰好一次 repaint；不 import 任何其它
orchestrator；Gateway probe 不进入（它是 shell badge，不是 Dashboard truth）。

**guards 与 mutation。**
`tests/test_desktop_composition_closure_architecture.py` 20 条结构 guard +
`scripts/mutation_mainwindow_composition_g1.ps1` 17 个 mutant 全部 RED（0 survived /
0 harness-error）。M17：started-task notification 必须观察 `worker.start()` 之后的
`active_count`——提前发布会把 task-count 卡片刷成 0 并保持到任务结束。e2/e3/e4/F1/F2 五个既有 harness 重跑 0 not-caught。行为测试：
shutdown 12 项（含 refused-close 后 admission 恢复、stuck worker 重试且不 terminate、组件失败
隔离且写 RUNTIME_SHUTDOWN_PARTIAL）、TaskController 8 项、DashboardOrchestrator 8 项。

**G1 后的 residual inventory**：A composition dependencies / B shell presentation（badges、
status label、theme）/ C 共享 canonical state（`ResearchScenarioCapitalState`）/ D generic
runtime（supervisor、task controller）之外，本轮移除的 E 类是 `workers` / `_closing` /
`_dashboard_chart_view`——但窗口仍持有 route-specific ownership，如实列为 **G2 candidates**：
Strategy governance（`_strategy_clone_requested` / `_strategy_transition_requested` /
`_refresh_strategy_page` / `StrategyApplication → StrategyPage.render` 窗口直 render）、
Execution / AutoQuant（`_launch_busy` / `_channel_check_inflight` / `auto_quant_candidates` /
candidate preparation / channel probe sequencing / `ExecutionPage` 的窗口直 render 与 control
presentation）；Risk 仅列为 G2 audit item（若只有 read-only 初始渲染且无独立 runtime /
intents，不预先承诺 `RiskOrchestrator`）。因此 **G2 = Strategy Governance +
Execution/AutoQuant residual orchestration，为 required**；本轮不声称 MainWindow
Composition Closure COMPLETE 与 Final Architecture Closure complete。

设计依据见 `DESKTOP_DECOMPOSITION.md` §33。

### 8.25 G2-A Strategy Governance orchestration

G2 按 ownership 拆成两个顺序 PR，本轮是第一个。§8.24 residual inventory 里 Strategy
governance 那一组全部退休：新增 `desktop_v2/orchestration/strategy/`
（`StrategyGovernanceOrchestrator` + Qt-free `queries.py` / `models.py`），public API
恰好 `refresh` / `select_version` / `clone` / `transition` 加五个发布信号
（`catalog_changed` / `account_notice_requested` / `warning_requested` /
`log_requested` / `runtime_event_requested`）。

**Canonical owner 不变**：`StrategyApplication` 仍是 catalogue / lifecycle authority，
orchestrator state 恰好 `_application` / `_page`、不缓存 catalogue；**runtime selection
truth 不变**：governance 页上点击某个 StrategyVersion 只表示"operator 正在看这个版本"，
`select_version` 只发布 account notice 文本，从不触达 `StrategySelectionService`
（AST guard 锁死 strategy 包内零 `StrategySelectionService` import、零 `.select(` 调用），
AUTO_ROTATION / TARGETED_SHADOW / BACKTEST 的 runtime selection 仍只由各自 combo 经
service 改写。clone 的 JSON adapter 迁入 Qt-free `parse_clone_parameters`，三种
account-notice 文案迁入 `strategy_account_notice`，逐字保留；transition 经窗口的
generic runtime-event bridge 发恰好一条 `STATUS_CHANGE`，Strategy capability 不 import
System。窗口对 account 只剩 `account_notice_requested → AccountOrchestrator.set_notice`
（新增极窄 seam，只收最终 text）；`catalog_changed` 的 cross-capability fan-out
（backtest / targeted `refresh_strategy_options()`）留在窗口，Execution combo 的旧 sync
标记为 G2-B transitional seam。

Guards：`test_desktop_strategy_governance_architecture.py` 15 条（窗口不再
render / 唯一 orchestration caller / 窗口 `self.strategies` 面恰好只剩 terminal export /
退休 handler 消失 / 零越界 import / governance 不触 runtime selection / page
service-free / 不缓存 catalogue / STATUS_CHANGE 只发布 / 无 StrategyManager 型 god
object / notice bridge 只收 text）。行为测试 12 项（refresh 序列与读失败不伪造、clone
四态、transition 两态、governance select 不改 runtime、缺失版本 no-op、fan-out 各恰好
一次）。Mutation：`scripts/mutation_strategy_governance_g2a.ps1` **12 个 mutant 全部
RED**（0 survived / 0 harness-error）。**G2-A ✅；G2-B Execution / AutoQuant ⏭
（required，单独 PR）**。

设计依据见 `DESKTOP_DECOMPOSITION.md` §34。

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

### 10.-1 Future seam：Research Scenario Capital ≠ Broker Equity

v2O-C4 把研究情景资金单独立 owner（`ResearchScenarioCapitalState`）。这一刀不是在做
Live capital allocation，它的意义是**语义隔离**：

```text
Research Scenario Capital                Broker Account Truth
        │                                        │
        ├─ historical research                   └─ future CapitalAllocator
        ├─ scanner affordability                         ↓
        ├─ targeted replay                         Portfolio Risk
        └─ exploratory portfolio research                ↓
                                                     Risk Kernel
                                                         ↓
                                                     Execution
```

**未来 `CapitalAllocator` 必须**从以下三者之上计算：

```text
fresh broker / account truth
strategy allocations
portfolio risk constraints
```

**绝不能**是 `CapitalAllocator = ResearchScenarioCapitalState`。研究情景金额不是
`NetLiquidation`、不是购买力、不是 risk capital、也不是真实仓位资金；它没有真实账户
资金权限。名字本身是这条边界的一部分：任何 AI / 策略接入时都不得把
`research scenario dollars` 误当成可下单资金。

本轮不实现 allocator；只钉住这条 seam，供 Paper Autonomous Trading → Live-ready
Execution / Risk Kernel → Risk Kernel → Small-capital Live Canary 阶段使用。

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

### 10.6 Trading Framework Closure v2C 已完成

上表 §10.5 列出的五项**全部在本轮完成**，另外把正式 runtime 与 Shadow 模拟器
之间的反向依赖一并解决。

#### 10.6.1 解决的核心架构问题

迁移前：

```text
TradingRuntime
StrategyRuntime
composition/runtime.py
        ↓
import us_quant.shadow_paper.ShadowConfig
```

正式 Trading Runtime 依赖内部 Shadow 模拟器，依赖方向是倒的；而且这个
config 类型同时携带 `layered_risk_limits` 与 `symbol_risk_multipliers`，
即"策略携带账户风险"。迁移后：

```text
trading/runtime/config.py            TradingSessionConfig（正式 session 参数）
          ↑
StrategyRuntime / TradingRuntime / composition/runtime.py

TradingSessionConfig
        ↑
shadow/config.py                     ShadowSimulationConfig（+ overlay）

TradingRuntime → RiskApplication → ExecutionApplication
```

Guard A 钉住"trading core 不得 import shadow / shadow_paper"，Guard B 钉住
"`TradingSessionConfig` 不得出现 risk overlay 字段"，Guard C 钉住继承方向只允许
`ShadowSimulationConfig → TradingSessionConfig`。

#### 10.6.2 最终目录

```text
src/us_quant/trading/
    application/
        paper/
            __init__.py        (<= 50)
            contracts.py       (<= 180)  Paper*Port 协议
            models.py          (<= 140)  snapshot / status / lifecycle error
            service.py         (<= 380)  PaperTradingService
    composition/
        runtime.py
        session_config.py      (<= 180)  build_auto_rotation_config
                                        resolve_paper_session_capital
    runtime/
        config.py              (<= 180)  TradingSessionConfig
        health.py              (<= 240)  PaperExecutionHealth / evaluator

src/us_quant/shadow/
    __init__.py                (<= 40)
    config.py                  (<= 180)  ShadowSimulationConfig
                                        build_targeted_shadow_config

src/us_quant/desktop_v2/
    workflows.py               (<= 220)  WorkflowController 聚合
```

`build_auto_rotation_config` 现在返回 `TradingSessionConfig`；
`build_targeted_shadow_config` 返回 `ShadowSimulationConfig`。字段语义与默认值
逐字保持，只有类型和归属变了。

#### 10.6.3 删除的过渡模块

```text
src/us_quant/auto_intraday.py            → trading/composition/session_config.py
src/us_quant/targeted_intraday.py        → shadow/config.py
src/us_quant/paper_execution_health.py   → trading/runtime/health.py
src/us_quant/paper_trading_service.py    → trading/application/paper/
src/us_quant/workflow_controller.py      → desktop_v2/workflows.py
```

全部**删除**，没有 compatibility re-export；`ShadowConfig` 这个名字在
production 中不再存在（Guard D 与
`test_no_module_defines_a_shadow_config_compatibility_alias` 钉住）。

`desktop_v2/workflows.py` 仍然创建**同一个** shared `ExecutionLeaseManager` 并
把它交给 shadow 与 paper 两个 controller，"两个 workflow 不可能同时持有执行权"
仍是结构性的（Guard G 钉住它不得创建 broker adapter / RiskApplication /
ExecutionApplication）。

#### 10.6.4 当时仍然冻结（已由 §10.7 完成）

```text
shadow_paper.py（Shadow engine + store + models）  TRANSITIONAL
```

v2C 时它没有被拆分、没有被重构，只把 `ShadowConfig` 换成从 `shadow/config.py`
导入的 `ShadowSimulationConfig`。该拆分已在 Shadow Framework v2 完成，见 §10.7。

v2C 同样冻结：正式交易算法（signals / ranking / risk sizing / OrderIntent /
fill reconciliation）、Paper 安全语义（fills-before-events、BUY exact cancel、
SELL HALT、reconciliation evidence、two-stage finalization、PAPER lease、
HALTED 显式恢复路径）以及 `desktop_v2/pages/execution/`。后者只允许 import
path 调整，不继续装修。

### 10.7 Shadow Framework v2 已完成

Shadow 是最后一个 875 行 root transitional module。本轮把它拆成五个模块，
职责分开，然后删除 `shadow_paper.py`。

#### 10.7.1 最终目录

```text
src/us_quant/shadow/
    __init__.py        (<= 60)   薄 re-export，非 facade
    config.py          (<= 180)  ShadowSimulationConfig（v2C 已建，本轮冻结）
    models.py          (<= 150)  ShadowPosition / ShadowFill /
                                 ShadowSessionProvenance / ShadowSnapshot
    store.py           (<= 300)  ShadowPaperStore（SQLite，schema 未变）
    engine.py          (<= 380)  ShadowPaperEngine（lifecycle + 唯一 state owner）
    trade_logic.py     (<= 330)  ShadowTradeLogic（entry/exit 行为，无 state）
```

`ShadowPaperEngine(ShadowTradeLogic)` 是一个 **stateless behavior mixin**：
它没有 `__init__`、不持有 position / cash / config / store 的任何副本，只对
engine 的 `self` 状态做决策。Guard E 与 Guard F 分别钉住"无 `__init__`"与
"两半不得定义同名 method"（Runtime v2A 的 duplicate `_flatten` 教训）。

#### 10.7.2 依赖方向

```text
TradingSessionConfig
        ↑
ShadowSimulationConfig

MarketSnapshot
        ↓
ShadowPaperEngine
        ↓
ShadowTradeLogic
        ↓
simulated position / fill

ShadowPaperEngine
        ↓
ShadowPaperStore
        ↓
SQLite
```

Shadow **永不触达 `BrokerExecutionPort`**：整个 package 不得 import IBKR
adapter、trading application、`OrderIntent`，也不得出现 `placeOrder` /
`cancelOrder` / `globalCancel` / `submit_approved` 任何名字。

正式 Trading Core 依然完全不知道 Shadow engine 存在（Framework v2C 的
Guard A 本轮重新验证）。

#### 10.7.3 行为与 schema 冻结

模拟行为**逐字未改**：fresh bid/ask 要求、分钟预热、动量计算、spread 过滤、
min/max momentum、positive-step 与 one-minute-move gate、入场窗口、force-flat、
profit target / stop loss / trailing stop / maximum hold、每日交易次数与亏损线、
`max_position_fraction`、symbol risk multiplier、`LayeredRiskLimits` overlay、
整股数量、commission、slippage、现金记账、realized 与 daily realized PnL。

SQLite schema 未改：表名、列名、`ALTER TABLE` 增量升级与
`shadow_fill → shadow_session` 外键全部保持，迁移前的数据库仍可被迁移后的
`ShadowPaperStore` 读取。本轮不引入 schema versioning、migration framework、
repository abstraction 或 generic `DatabaseManager`。

#### 10.7.4 下一阶段

阶段 1（框架闭环）至此 **COMPLETE**。阶段 2（前端全部 native v2）进行中，
Desktop Market v2 已完成（§8.3）：

```text
Desktop Market v2        ✅（§8.3）
Desktop Research v2A     ✅（§8.4）
Desktop Research v2B     ✅（§8.5）
Desktop Research v2C     ✅（§8.6）
Desktop Research v2D     ✅（§8.7）
Desktop Research v2E       ✅（§8.8）
Desktop Research v2R-F     ✅（§8.9）
Desktop System v2
Desktop Dashboard v2
```

后续再做 `SystemPage` / `DashboardPage`，暂不动 `MainWindow` decomposition。

### 10.8 已关闭的风险接线缺陷（Risk v2 修复）

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
旧 auto_intraday.py                    ✅ 已删除（Trading Framework Closure v2C）
旧 targeted_intraday.py                ✅ 已删除（Trading Framework Closure v2C）
旧 paper_execution_health.py           ✅ 已删除（Trading Framework Closure v2C）
旧 paper_trading_service.py            ✅ 已删除（Trading Framework Closure v2C）
旧 workflow_controller.py              ✅ 已删除（Trading Framework Closure v2C）
ShadowConfig（含 compatibility alias）  ✅ 已删除（Trading Framework Closure v2C）
shadow_paper.py                        ✅ 已删除（Shadow Framework v2）
旧 MainWindow stream lifecycle         ✅ 已删除（v2O-A Market orchestration）
旧 MainWindow account refresh/ledger/render ✅ 已删除（v2O-B Account orchestration）
旧 MainWindow universe snapshot / refresh task / cancel event / refresh worker
                                        ✅ 已删除（v2O-C1 Research foundations）
旧 MainWindow history queue intents / progress state
                                        ✅ 已删除（v2O-C1 Research foundations）
旧 MainWindow scan truth / 三条到达路径 / chart read
                                        ✅ 已删除（v2O-C2 Scanner orchestration）
旧 MainWindow backtest runs / selection / busy + 8 个 handler
                                        ✅ 已删除（v2O-C3 Backtest orchestration）
旧 Paper-specific orchestration glue   ⏭ 后续
旧 workflow duplicate state            ⏭ 后续
旧页面 builder（其余 route）            ⏭ 后续
旧 desktop service                     ⏭ 后续
```

后续 orchestration 抽取路线：

```text
v2O-A Market orchestration      ✅ 已完成（§8.11）
v2O-B Account orchestration     ✅ 已完成（§8.12）
v2O-C Research orchestration    ✅ COMPLETE（§8.13–§8.16）
v2O-D Shadow orchestration      ✅ 已完成（§8.17）
v2O-E Paper orchestration       ✅ COMPLETE：E1 启动链（§8.18）+ E2 active runtime（§8.19）
                                    + E3 recovery/finalization/shutdown（§8.20）
                                    + E4 presentation/render closure（§8.21）
v2O-F1 Runtime Events orchestration  ✅ 已完成（§8.22）
v2O-F2 Settings orchestration        ✅ 已完成（§8.23）
v2O-F System                         ✅ COMPLETE（Gateway probe 重审定性，见 §8.24）
G1 MainWindow composition closure    ✅ 已完成（generic runtime / shell）
G2-A Strategy Governance             ✅ 已完成（§8.25）
G2-B Execution / AutoQuant           ⏭ required（单独 PR）
Final Architecture Closure           ⏭ G2 之后
```

维护导航：`docs/DESKTOP_CAPABILITY_MAP.md`（capability → truth owner /
page render owner / service 依赖 / public API / cross-workflow bridge）。
每个 orchestration PR 更新那一张表。

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

> 注意：该清单是 Risk v2 轮次的历史记录。清单中的
> `paper_trading_service.py` 已在 Trading Framework Closure v2C 迁入
> `trading/application/paper/`；其余模块的归宿见 §10.6 与 §11。

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

**Shadow Framework v2 已完成，阶段 1（框架闭环）COMPLETE。** Shadow 不再是
单个 875 行 root module，而是 `shadow/{config,models,store,engine,trade_logic}.py`
五个职责清晰的模块，`shadow_paper.py` 已删除；唯一的 state owner 仍是
`ShadowPaperEngine`（见 §10.7）。

五个已完成轮次的顺序：

```text
Runtime v2A   AutoQuantEngine 拆成 StrategyRuntime / TradingRuntime（§10.3）
Runtime v2B   paper_session / paper_workflow / workflow_state 模块化（§10.4）
Desktop Exec  execution route 变成原生 v2 page（§8.2）
Framework v2C runtime 脱离 shadow_paper，五个 root module 归位并删除（§10.6）
Shadow v2     shadow_paper.py 拆成 shadow package 并删除（§10.7）
```

**Desktop Market v2 已完成**（§8.3），market route 变成原生 v2 page。
**Desktop Research v2A 已完成**（§8.4），Research 的“针对性验证”二级页已 native
v2。**Desktop Research v2B 已完成**（§8.5），Universe/History 两个二级页也已 native
v2。**Desktop Research v2C 已完成**（§8.6），ScannerPage 已 native v2，Dashboard
人工扫描入口已退休。**Desktop Research v2D 已完成**（§8.7），BacktestPage 已 native
v2。**Desktop Research v2E 已完成**（§8.8），CrossSectionResearchPage 已 native v2；
**Desktop Research v2R-F 已完成**（§8.9），Research aggregate 已 native v2，
但 Research orchestration 仍暂留在 MainWindow。**Desktop System v2 已完成**（§8.10），
System aggregate 已 native v2 且两类大 UI 耦合已清零；System orchestration 两个
workspace **都已迁出 MainWindow**（Runtime Events = v2O-F1 §8.22，Settings = v2O-F2
§8.23），Gateway probe 仍留在窗口。**v2O-A Market orchestration 已完成**（§8.11），Market runtime truth 已
从 `MainWindow` 迁入 `desktop_v2/orchestration/market/`，窗口只保留跨 workflow
safety bridge 与 snapshot fan-out。**v2O-B Account orchestration 已完成**（§8.12），
Account 的 refresh / ledger / page render / shell 事实已迁入
`desktop_v2/orchestration/account/`，`BrokerAccountApplication` 仍是唯一 account
truth，窗口只保留 composition 与跨 workflow fan-out。**v2O-C1 Research
foundations 已完成**（§8.13），Universe 的 snapshot / refresh / cancel 与 History
的 queue intents / progress 已迁入 `desktop_v2/orchestration/research/{universe,
history}/`；Research 内部刻意不设 aggregate。**v2O-C2 Scanner orchestration
已完成**（§8.14），`MarketScan` 的 canonical Desktop owner 变成
`ScannerOrchestrator`，窗口不再有 `self.scan`，Scanner artifact parsing 与 chart
loading 归 `DesktopMarketScanService`；AutoQuant preparation **仍然直接**
`scan_market` / `save_market_scan`，只把完成的 scan fact 通过窗口
`adopt_external_scan` 交给 Scanner，方向是 AutoQuant → Scanner，不形成反向依赖。
**v2O-C3 Backtest orchestration 已完成**（§8.15），Backtest 的 runs / selection /
busy 三个属性与 8 个 handler 已迁入
`desktop_v2/orchestration/research/backtest/`，纯规则（option 顺序、版本选择、
draft → request）归 Qt-free 的 `queries.py`，窗口只保留 composition 与
refusal dialog bridge。**v2O-C4 Cross Section orchestration 已完成**，Cross-Section
的 report truth / run request / page render 迁入
`desktop_v2/orchestration/research/cross_section/`，Research Scenario Capital 单
owner 化。**v2O-C5A Targeted Evidence orchestration 已完成**，Targeted 工作区的
研究证据runtime（七类 result + 两个 selection + 两个请求 + evidence render）迁入
`desktop_v2/orchestration/research/targeted/evidence/`。**v2O-C5B Targeted Session + Preflight orchestration 已完成**（§8.16），session 一半
（target draft / target status / minute status / preflight / session render）迁入
`desktop_v2/orchestration/research/targeted/session/`。设计依据见
`DESKTOP_DECOMPOSITION.md` §25。

**`v2O-C Research orchestration` 整体 COMPLETE**：Universe、History、Scanner、
Backtest、Cross-Section、Targeted Evidence、Targeted Session 各自拥有 canonical
truth，`MainWindow` 不再持有任何 Research capability state，且没有引入
`ResearchOrchestrator` / `ResearchManager` / `ResearchContext`。

**Research closure does not own execution authority.** 特别地：
`TargetPreflightResult.shadow_ready` **不等于**券商订单授权 —— 它只表示内部 Shadow
仿真的就绪状态，而 preflight 的 `broker_orders_available` 在
`DesktopTargetedSessionService` 内硬编码为 `False`。未来真金实盘需要独立的 Risk
Kernel，那是更高信任层，本轮不提前做，也不把 preflight 重命名成
`RiskDecision` / `ExecutionDecision` / `TradeApproval`。

阶段 2 剩余：

```text
Desktop Dashboard v2
```

后续 orchestration 抽取：

```text
v2O-C Research orchestration    ✅ COMPLETE（C1–C5B 全部完成）
v2O-D Shadow orchestration      ✅ 已完成（§8.17）
v2O-E1 Paper launch             ✅ 已完成（§8.18）
v2O-E2 active Paper runtime     ✅ 已完成（§8.19）
v2O-E3 HALT / reconciliation / finalization / shutdown   ✅ 已完成（§8.20）
v2O-E4 presentation / render closure + MainWindow guards  ✅ 已完成（§8.21）
v2O-F1 Runtime Events orchestration                      ✅ 已完成（§8.22）
v2O-F2 Settings orchestration                            ✅ 已完成（§8.23）
v2O-F System                                             ✅ COMPLETE（Gateway probe = shell diagnostic）
G1 MainWindow composition closure                        ✅ 已完成（§8.24）
G2-A Strategy Governance                                 ✅ 已完成（§8.25）
G2-B Execution / AutoQuant                               ⏭ required（单独 PR）
Final Architecture Closure                               ⏭ G2 之后
```

Shadow Framework v2 刻意没有做的事，留给更后面：

- 没有给 Shadow 建立 protocol 层（`ShadowStorePort` / `ShadowEnginePort` 等）
  或任何 `ShadowManager` / `ShadowService` / `ShadowApplication`；
- 没有改 SQLite schema，也没有引入 migration framework；
- 没有重命名 `ShadowPaperEngine`（命名细修后置）；
- 没有提前做 `SystemPage` / `DashboardPage` 或 `MainWindow` decomposition。

Desktop Market v2 刻意没有做的事，留给更后面：

- 没有提前拆 stream orchestration：没有 `MarketController` / `MarketWorkflow` /
  `MarketOrchestrator` / `StreamManager`。既定路线是先全部页面 native v2，
  再统一拆 `MainWindow` orchestration，否则会边迁页面边反复拆 controller；
  **该路线已执行：`MarketOrchestrator` 在 v2O-A 落地（§8.11）**；
- 本轮只迁 Research aggregate，System / Dashboard 仍冻结；settings provider
  combo 仍在旧 System route，只做必要的双向 wiring（§63）；
- 没有碰 `ExecutionPage`，只让 `_publish_execution_controls()` 因为 stream
  state 继续工作；
- 没有改 `MarketDataApplication` / provider lifecycle / push-poll 划分 /
  IBKR venue resolver / stale 阈值。

v2O-A 刻意没有做的事，留给更后面：

- 只抽 Market，没有 Account / Research / Shadow / Paper / System orchestration；
- 没有继续拆 snapshot fan-out：Paper / Shadow 的 `on_stream` 仍由窗口转发；
- 没有把 Paper / Shadow interlock 搬进 orchestrator（那需要先完成 v2O-D / v2O-E）；
- 没有引入 `DesktopManager` / `ApplicationContext` 之类依赖袋；
- 没有改 `MarketDataApplication`、readiness 判定语义、30 秒 recently-ready 窗口、
  1 秒 push-idle 阈值或 30 个订阅上限。

v2O-B 刻意没有做的事，留给更后面：

- 只抽 Account，没有 Research / Shadow / Paper / System orchestration；
- 没有搬 generic task lifecycle（`TaskThread` / `DesktopTaskController` / worker
  列表 / closing admission gate / `_worker_finished` / `_task_cancelled` /
  busy dialog）——orchestrator 只拿到窄 callable `submit_task`，也没有借机
  创造全局 `TaskFramework`；
- 没有把 gateway probe（`_probe_gateway` / `probe_ibkr_socket` /
  `gateway_badge`）归入 Account，尽管它使用 IBKR config；
- 没有把 exposure multiplier 的来源（Risk / Strategy / app config）引入
  Account，也没有让 AccountOrchestrator import 任何其他 capability；
- 没有给 research capital 换 owner，也没有动 strategy notice——只把
  `research_capital_card` 的 widget reach-through 收成 `AccountPage` 命名方法；
- 没有把跨 workflow fan-out（Dashboard / Auto Quant / Targeted）搬进
  orchestrator，它们仍是窗口订阅 `portfolio_changed` 后的路由；
- 没有给 orchestrator 加 `net_liquidation` / `account_alias` / `positions` /
  `cash` / `daily_pnl` 等 accessor：domain snapshot 本身已是只读 finished fact；
- 没有改 `BrokerAccountApplication` 的失败语义（失败保持 last good portfolio
  并设置 `last_error`），也没有新增任何业务行为。

v2O-C2 刻意没有做的事，留给更后面：

- 只抽 Scanner，没有 Backtest / Cross-Section / Targeted orchestration，也没有
  `ResearchOrchestrator` / `ResearchManager` / `ScannerManager` / `ScannerContext`
  / `DesktopContext` / services bag；
- 没有把 AutoQuant preparation 搬进 Scanner：`_prepare_auto_quant_candidates`
  仍然直接 `scan_market` + `save_market_scan`，因为它连着 Paper `PREPARING` 与
  候选准备失败清理，属于高风险启动链。本轮只改它的**结果如何进入 truth**
  （`adopt_external_scan`），没有改它的执行 ownership；
- 没有把 `_select_auto_quant_candidates`（Paper workflow + account truth +
  strategy + risk multipliers + market references + Execution page）或
  `_apply_intraday_watchlist`（Scanner + Market + Account capital）搬进 Scanner：
  它们是 cross-workflow composition，搬进去会立刻造出反向依赖；
- 没有搬 generic task lifecycle（`TaskThread` / `DesktopTaskController` / worker
  列表 / busy dialog）——Scanner 只拿到窄 callable `submit_task`，也不持有 worker；
- 没有持有 `QMessageBox`：缺 universe 走 `refused` 信号，窗口用
  `_report_scanner_refusal` 以原有 severity 显示；
- 没有给 ScannerOrchestrator 加 `results` / `skipped` / `summary` /
  `scanned_count` / `trade_candidates` / `research_count` / `scan_path` /
  `service` / `page` 这类 accessor：domain `MarketScan` 本身已是只读 finished
  fact，这些不是 capability API；
- 没有为了"命名好看"大规模 rename `DesktopMarketScanService`，也没有改
  `scanner.py` 的扫描算法、score policy、China exclusion policy、whole-share
  sizing 或 History scheduling 行为。

v2O-D 刻意没有做的事，留给更后面：

- 只抽 Shadow，没有 Paper / System orchestration，也没有 `ShadowManager` /
  `ShadowCoordinator` / `ShadowContext` / `ApplicationContext` / services bag；
- 没有搬 Paper session 生命周期：connect / promote / discard、workflow 状态迁移、
  HALT / recovery、reconciliation、finalization、lease-facing sequencing 全部仍是
  `MainWindow` 的，属 v2O-E。Shadow orchestrator 只拿到共享 lease 的窄 Protocol，
  从不 import `desktop_v2/workflows.py`（`PaperWorkflowController` 就住在那儿）；
- 没有搬 generic task lifecycle（`TaskThread` / `DesktopTaskController` / worker
  列表 / busy dialog）：Shadow start 是在调用线程上的同步操作（仿真由 market
  snapshot 驱动，没有自己的 worker），因此本轮不需要 `submit_task`，
  也没有造 `ShadowWorker` / `ShadowTaskRegistry`；
- 没有持有 `QMessageBox`：拒绝走 `refused` 信号，窗口用 `_report_shadow_refusal`
  以原有 severity 显示；
- 没有为了"架构好看"拆出 `manager.py` / `controller.py` / `service.py` /
  `facade.py` / `workflow.py` / `context.py` / `state.py` / `helpers.py` /
  `utils.py`：三个文件（models / queries / orchestrator）够用；
- 没有改 Shadow 引擎 / trade_logic / store / models / config，没有改 fill math、
  手续费、滑点、warmup、momentum、force-flat 或跨交易日行为；十道门的顺序与文案
  逐字保留，`_money` 语义（含 `不可用`）逐字保留；
- **没有保留**退休 `_start_shadow` 的重复启动后果。这不是 "ownership extraction
  不做设计改动" 的例外，而是它唯一**必须**改的一处：旧行为会让一个仍在运行的会话
  失去共享租约，"Shadow XOR Paper" 静默失效。重复启动现在的契约是 no-op
  （见 §8.17），并且由 architecture guard 锁死；
- 没有提前建 `CapitalAllocator` / Risk Kernel / Champion-Challenger / AI / Live
  broker / Live order：未来目标不进这一轮；
- 没有清理本轮之外的 legacy。

v2O-F1 刻意没有做的事，留给更后面：

- 只抽 Runtime Events，没有 Settings orchestration（v2O-F2），也没有搬 Gateway
  probe（`_probe_gateway` / `probe_ibkr_socket` / `gateway_badge`）；
  **（v2O-F2 更新）** Settings 那一半已由 v2O-F2 迁出（§8.23）；Gateway probe 仍留在
  窗口，"是否独立 F3"需要重新扫描 System 剩余职责后再定；
- 没有创建 `SystemOrchestrator` / `SystemManager` / `DesktopSystemManager` /
  `SystemContext` / `ApplicationContext` / `ServiceBag` / `RuntimeManager`：System 是
  containment route，Runtime Events 与 Settings 是两个不相关的 capability，共用一个
  对象会同时持有 event store、settings service 与 credential service，那正是本轮要
  避免的 god object；
- 没有搬 generic task lifecycle（`TaskThread` / `DesktopTaskController` / worker
  列表 / closing admission gate / busy dialog）：orchestrator 只拿到窄 provider
  `active_task_count`，窗口在 task 数量变化时通知它；
- 没有把跨 capability 的导出事实收集搬进 orchestrator：provider 留在 composition
  root，只做事实汇总，不做 sequencing；
- 没有给 orchestrator 加 `events` / `rows` / `recent_events` 之类的 accessor 或缓存：
  每次 render / export 都从 store 重新读；
- 没有改 `RuntimeEventStore` 的 schema，也没有把 redaction 复制进 orchestrator；
- 没有改 `export_service.py` 的 artifact schema、`DesktopSettingsService` 的
  validate→derive→preflight→guard→persist→apply 顺序、Paper release 协议、
  reconciliation / finalization、execution risk ordering、broker retry 或 strategy
  lifecycle；
- 没有统一 Market / Account / Shadow / Paper / Research 各自的 runtime event model：
  窗口的一个纯转发 adapter 就是它们唯一需要的公共点。

v2O-F2 刻意没有做的事，留给更后面：

- 只抽 Settings orchestration，没有搬 Gateway probe
  （`_probe_gateway` / `probe_ibkr_socket` / `gateway_badge`），也没有做
  MainWindow composition closure、Dashboard 或 trading/live/AI；
  **（G1 更新）** Gateway probe 重审定性为 shell diagnostic 且**不设 F3**（§8.24），
  Dashboard render 由 `DashboardOrchestrator` 持有，MainWindow composition closure
  的 generic runtime / shell 一半由 G1 完成；
- 没有创建 `SystemOrchestrator` / `SystemManager` / `SettingsManager` /
  `DesktopManager` / `ApplicationContext` / `ServiceBag` / `GlobalController`：
  Runtime Events 与 Settings 是两个互不相关的 capability，只共用一个页面，
  没有共同 state 与共同生命周期，聚合它们等于把 event store、settings service 与
  credential service 塞进同一个对象；
- 没有把 `self.config` / `self.preferences` 搬进 `SettingsOrchestrator`：它们是全局
  应用 composition fact，Market / Paper / Risk / Research / Gateway 都在用，搬进去会
  让所有 capability 反向依赖 Settings；
- 没有改三个 canonical owner 的任何语义：`UserPreferencesStore` 的 schema / validate /
  原子写、`DesktopSettingsService` 的事务顺序（仍无 rollback / two-phase）、
  `DesktopCredentialService` 的存储与 status / save / clear 语义；
- 没有把 market switch / Paper / Shadow 的 interlock 搬进 capability：无订阅直设与
  `_request_market_switch` 都留在窗口；
- 没有把全局主题 fan-out（`_apply_theme`）搬进 capability：它要给整个 workbench 换
  palette，不是 Settings 的行为；
- 没有改 `SettingsPage` 的九个 intent 与 `SettingsPageView` 契约，也没有给它加
  service 依赖：页面仍只 render / emit；
- 没有"顺手修" provider 同步的非对称（Settings→Market 带动 selected API provider，
  Market→Settings 不带动）：那是既有行为，本轮原样保留并写进文档；
- 没有给 capability 加 `status` / `credentials` / `config` / `preferences` 之类的
  accessor 或缓存：每次重画都现读服务与活动行情源。

Framework v2C 刻意没有做的事，留给更后面：

- 没有拆分或重构 `shadow_paper.py`（该项已在 Shadow Framework v2 完成）；
- 没有继续装修 `desktop_v2/pages/execution/`（只允许 import path 调整）；
- 没有改任何正式交易算法与 Paper 安全语义。

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
