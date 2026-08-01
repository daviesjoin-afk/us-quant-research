# USQuant 0.5.0 内部评审与五轮整改

> 历史评审快照：0.16.0 已加入受限 IBKR Paper 订单适配与成交对账；
> 当前状态以 `PROJECT_STATUS_2026-07-25.md` 为准。

日期：2026-07-25  
范围：桌面交互、券商与实时行情、策略/数据、运行与发布

## 评审分工

- 架构与桌面：逐页检查 11 个页面、任务线程、状态真值、可写路径和发布包。
- 券商与行情：复现断线、10197、1100/1101/1102、迟到 tick、账户环境和只读守卫。
- 策略与数据：复核 603 份历史数据、5,311 只研究池、整股定仓、通用替代执行规则和 OOS 证据。
- 主线整合：对照成熟产品、修复 P0/P1、重跑研究、视觉 QA、构建与全新状态目录启动。

## 成熟产品模式及落地

- [QuantConnect Backtest Results](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/results)：
  结果页应同时呈现统计、回撤、暴露、订单、成交、日志和下载。当前结果页先把
  晋级门置顶，后续仍需补回撤水下图、暴露和成交页。
- [TradingView Supercharts](https://www.tradingview.com/support/solutions/43000746464-getting-started-with-supercharts/)：
  列表、图表和详情共享选中标的上下文。市场扫描现在默认进入可交易资格并联动图表。
- [TradingView Watchlists](https://www.tradingview.com/support/solutions/43000745825-mastering-the-tradingview-watchlists/)：
  候选池与实际订阅分开；本项目不再把“候选 30”冒充“已订阅 30”。
- [IBKR TWS](https://www.interactivebrokers.com/en/trading/tws.php) 与
  [Monitor Panel](https://www.interactivebrokers.com/campus/trading-lessons/getting-started-with-monitor-panel/)：
  账户、持仓、当日/未实现盈亏、观察列表和扫描器各自保留来源与状态。

没有照搬自由拼装订单面板、画线工具、社交功能或保证金操作，因为 0.5.0
仍是本地只读研究与行情观察客户端。

## 五轮整改

1. **真值与行情安全**
   - hard-stale 不再被后续成交 tick 清除；
   - bid/ask 分字段计时，拒绝倒挂和乱序；
   - 1102 必须收到新的 bid 和 ask 才恢复；
   - 停止行情立即失效旧快照，影子盘不能使用停止前的 READY。

2. **账户、运行状态与数据目录**
   - Paper 必须是 `DU` 账户，Live/Paper 不能互相伪装；
   - Paper 净值快照 TTL 为 5 分钟；
   - 资源目录只读，缓存、数据库、下载和新研究结果写入 AppData；
   - SQLite 统一 WAL、30 秒 busy timeout 和外键；
   - 导出对 token、secret、API key 脱敏并防 CSV 公式注入。

3. **策略与数据真实性**
   - 识别 Yahoo OHLC 为事后复权研究代理，旧“可执行回测”说法撤销；
   - 晋级门新增复权价不可执行、风险超限和收益折集中度理由；
   - 调用方不能自行声明 gate passed，影子盘必须绑定已晋级版本；
   - Scanner 与正式整数定仓器统一：按单仓风险和执行品风险倍数计算容量；
   - 最新结果为 47 只可整股交易资格、22 只趋势候选。

4. **桌面交互**
   - 导航按监控、策略、研究、系统分组；
   - 策略结果第一张卡显示“晋级门硬阻断”，收益标为复权价研究代理；
   - 行情 Key 隐藏、空状态可操作、数字列按数值排序；
   - 策略生命周期按钮按状态禁用，失效旧版本不能克隆；
   - 标的池明确显示匹配总数和 2,500 行界面上限；
   - 运行事件支持待确认/已确认和脱敏导出。

5. **发布复核**
   - 87 项自动化测试通过；
   - 8 张关键页面截图完成视觉复核；
   - Windows EXE/产品版本为 0.5.0；
   - 使用全新临时状态目录启动 6 秒，应用保持运行并创建 9 个用户状态文件；
   - 启动前后安装资源 SHA256 不变。

## 仍然阻断晋级的事项

- 必须同时保存历史原始 OHLC、拆股、分红和总回报序列；成交、整股和现金只能用历史名义价格。
- 必须补历史时点成分、退市样本、ticker 变更和统一行业版本。
- 必须补现金/短债和风险匹配基准、DSR/PBO、bootstrap 置信区间及多重检验。
- 必须先完成至少 60 个交易日的未来影子验证，再讨论 IBKR Paper 订单适配器。
- Finnhub 是成交打印加模拟执行带，Alpaca 免费层是 IEX；两者都不是 SIP/NBBO。

因此 0.5.0 不开放 IBKR Paper/Live 下单，不承诺收益，也不把当前 +4.45%
研究代理宣传成历史可执行利润。
