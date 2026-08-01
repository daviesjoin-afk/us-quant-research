# 成熟量化软件案例复盘与本项目落地

日期：2026-07-24  
范围：本地桌面、免费数据优先、IBKR 模拟账户、股票/ETF、整股、严格排除中概

## 结论

本项目不直接复制某一个框架，而是吸收三类已经被成熟软件验证的边界：

1. 数据提供商与券商执行分离；
2. 研究、实时模拟、券商模拟和实盘是不同环境；
3. 行情、订单、持仓、账户和故障都通过显式事件与状态转换；
4. 指标预热期间不允许交易；
5. 策略只产生意图，风险和执行层拥有最终否决权。

## QuantConnect LEAN

官方资料：

- [Live Trading](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading)
- [Live Data Providers](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/data-providers)
- [Live Deployment](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/deployment)
- [Backtest Results](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/results)
- [Optimization Parameters](https://www.quantconnect.com/docs/v2/cloud-platform/optimization/parameters)
- [Warm Up Periods](https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/warm-up-periods)

值得借鉴：

- 同一算法可以在回测与实时环境运行，但数据提供商、券商和环境配置分离。
- 实时数据可以来自券商，也可以来自第三方。
- 预热会把历史数据泵入指标；预热期间不能下单。
- 实时数据存在延迟、缺点、当前交易日数据与事后清洗数据不一致等现实差异。
- 部署向导分别选择券商和数据供应商；同一 IBKR 集成可以承担两种角色，但角色状态必须独立。
- 回测结果页以关键指标、权益曲线、订单/交易和日志为主；参数与代码分离，并警告在同一时期反复调参会引入样本内偏差。

本项目落地：

- `AlpacaIEXStream`、`FinnhubTradeStream` 和 `IBKRReadOnlyStream`
  输出统一的不可变
  `StreamSnapshot`。
- 行情必须逐代码满足 fresh、实时类型、bid/ask 完整，才能进入日内链路。
- 内部影子盘至少积累 10 个独立分钟样本才评估信号，预热期订单数恒为 0。
- 第三方数据只改变数据适配器，不会取得 IBKR 订单权限。
- 桌面导航重组为工作域；回测工作区展示版本、运行参数、关键指标、权益曲线、Run 对比和交易明细。
- 每个回测 Run 绑定策略版本 ID、参数哈希与数据哈希，结果不会覆盖前一次运行。

## NautilusTrader

官方资料：

- [Architecture](https://nautilustrader.io/docs/nightly/concepts/architecture/)
- [Events](https://nautilustrader.io/docs/latest/concepts/events/)

值得借鉴：

- Data、Execution、Portfolio、Risk 是独立引擎。
- 回测、Sandbox、Live 是显式环境，不共享含糊状态。
- 订单、持仓、账户改变都是事件；执行引擎维护生命周期，风险引擎可拒绝命令。
- 缓存与持久化支持重启恢复和事后审计。

本项目落地：

- 账户、行情、策略注册、运行事件、影子成交分别持久化，不把研究预算和
  IBKR 模拟账户净值混在一起。
- 策略版本不可变；调用方不能自报研究门通过，影子盘必须绑定已晋级版本。
- 内部影子盘根本不暴露券商订单方法，成交、成本、持仓和盈亏写入 SQLite。
- 运行中心导出脱敏账户、持仓、行情、策略、事件和影子成交。

## Backtrader

官方资料：

- [Operating the Platform](https://www.backtrader.com/docu/operating/)
- [Interactive Brokers Live Trading](https://www.backtrader.com/docu/live/ib/ib/)

值得借鉴：

- Data Feed、Strategy、Engine/Broker 是独立角色。
- IB Store 作为券商适配层，比策略直接连接券商更清晰。
- 官方示例等待数据状态明确进入 `LIVE` 后才允许交易，并建议先用 Paper。

本项目落地：

- 只有逐代码 `READY` 行情能进入影子信号；delayed、stale、错误 10197、
  缺 bid/ask 都被硬拒绝。
- 外部实时行情和 IBKR 账户读取并行但不互相冒充。
- 首轮只进入 `internal_shadow`，后续才考虑具有独立开关和订单日志的
  `ibkr_paper` 适配器。

## 与成熟系统仍有差距

- Alpaca 免费层是 IEX 单交易所行情，不是 SIP/NBBO；Finnhub 免费 WebSocket
  是成交流，影子 bid/ask 是程序生成的模拟带。两者都只能用于工程链路和
  策略观察，不能证明真实可成交利润。
- 当前没有拆股/退市/历史成分股的完整点时数据库，广域回测仍有幸存者偏差。
- 当前已加入 IBKR Paper 受限订单入口、开放/完成订单恢复、逐笔成交去重、
  部分成交数量对账和 fail-closed 断线处理。仍缺代码签名、长时间真实 Paper
  灾备演练，以及持仓状态下的自动恢复交易，因此重连只恢复事实、不恢复策略。
- 当前历史 OHLC 是事后复权研究代理，不能证明拆股前整股成交、现金和费用；
  因此日内影子策略默认被晋级门阻断，不是经过足够样本验证的盈利策略。

## 下一晋级门

1. 连续 20 个美股交易日无行情/状态机事故；
2. 至少 100 笔成本后影子交易；
3. IEX 与 IBKR 可用行情的点差、时间戳和成交偏差对账；
4. 最大日亏损、断线、重连、重复事件与收盘强平测试全部通过；
5. 用户单独确认后才逐会话武装 IBKR Paper；重连后仍需重新人工确认；
6. 实盘仍需新的显式确认、独立配置和更低限额。
