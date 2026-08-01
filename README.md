# 美股量化研究台

面向 IBKR 的本地 Windows 美股量化研究程序。当前版本覆盖官方标的池、中概排除与国际股票放开、批量历史数据、市场扫描、走样本外组合研究、外部实时行情、内部策略仿真、自动多标的量化和完整桌面客户端。

当前阶段的安全边界：

- IBKR Gateway 固定为 Paper 端口 `4002`
- 默认只读；IBKR Paper 自动量化必须在设置和当前会话中分别显式开启
- 自动量化只允许单一 `DU` 模拟账户和整股限价单；默认 `SMART DAY`，可选
  IBKR 5×24 Paper 路由（盘前/盘后 `OutsideRth`、隔夜 `OVERNIGHT`）；Live、
  全撤、期权、做空和市价单硬关闭
- 不做碎股、不借保证金、不做空
- 股票和人工复核 ETF；期权暂不接入
- 针对性研究采用独立 `internal_shadow` 环境；自动量化成交以 IBKR Paper 回报为准

## 直接运行 Windows 客户端

最终压缩包：

```text
dist/USQuantResearch-win64.zip
```

解压后双击：

```text
USQuantResearch.exe
```

程序按“监控台、策略与回测、市场研究、运维与安全、系统设置”
五个工作域组织功能。

## 免费实时行情与自动量化安全门

当前外部适配器支持 Alpaca 免费层 IEX WebSocket 和 Finnhub 实时成交
WebSocket。Alpaca 是 IEX 单交易所 bid/ask；Finnhub 免费流是成交打印，所以
客户端只生成明确标注的 ±5bps 影子执行带。两者都不是全市场 SIP/NBBO，
不能冒充全市场最优价。

1. 使用 Finnhub API Key，或在 Alpaca 创建免费 Market Data API Key。
2. 不要把 Secret 发到聊天中。
3. 打开“系统·设置”，选择深色或浅色主题，并设置默认行情源。
4. 在 API 数据源下拉框中选择 Finnhub、Alpaca 或 IBKR；页面只显示所选源需要的配置。Finnhub Key、Alpaca Key/Secret 使用 Windows DPAPI 按当前用户分别加密保存，设置页不回显明文。
5. 行情连接中、延迟或报错时，数据源下拉框仍可使用；点击“切换 / 重连行情”会先停止旧源，再使用相同代码连接新源。
6. 等代码显示 `READY` 后可观察实时链路。只有在设置中开启 Paper 能力、勾选当前会话确认并点击“连接并武装”后，自动量化才可能提交模拟限价单。

需要针对某只股票或 ETF 做日内 T 时，在“监控台 → 针对性日内 T”
输入代码并应用。实时订阅、分钟回放、多日评估和内部仿真都读取
这一处状态；该入口只属于针对性工具，不会影响自动量化候选集。启动时
默认留空，不存在任何预置重点标的或自动替代映射。
随后选择不可变参数版本并点击“订阅当前标的行情”。research 版本可运行
明确标注的探索性内部影子盘来收集证据，
但不会因此晋级或获得券商订单权限。

客户端会将实时 Level-I 行情按分钟保存到本地，保留行情源、覆盖范围、
bid/ask、实时类型、stale 状态和连接代次。在“针对性日内 T”页点击
“回放已录分钟数据”，会使用所选策略版本和单一行情源重新驱动同一个
影子引擎，并保存 Run ID、版本、参数、数据哈希、成交、收益和回撤。
单会话回放只使用最近一个纽约常规交易日，禁止把多天数据串成一次结果；
分钟样本不足、不连续或只有 stale/延迟数据时会明确拒绝回放。

“多日稳健性评估”会按纽约交易日独立重置资金和引擎；只有覆盖
10:00–15:45、至少 300 条可用分钟且满足连续预热的完整评估窗口才计为
有效会话。系统分别运行基准参数、入场阈值 ±20% 和退出距离 ±20% 五组
场景。结果显示有效/跳过会话、
复合收益、最差会话、盈利会话比例、最大回撤、费用和收益方向一致率。
方向一致率只描述参数扰动后的符号是否一致，不代表盈利。少于 20 个真实
独立会话只属于探索性证据；达到数据门也仍需人工复核，不会自动晋级策略。

达到 20 个完整会话后，同一次评估会继续生成“时间隔离验证”。系统采用
锚定 walk-forward：前 10 个会话只用于从五个固定场景中选参，后 5 个
做验证门，再后 5 个作为不参与选择的测试集；以后每增加 5 个会话扩展一折。
对照基准使用相同 10% 风险预算、整股、滑点和双边佣金，在 10:00 买入、
15:45 卖出。界面分别显示验证和测试阶段的策略、基准与超额收益，并保存
`test_used_for_selection=false`。至少两折、全部验证门通过且未触碰测试集
取得正收益和正超额，才允许标记为“可人工复核”，仍不会自动晋级或下单。

同一次多日评估还会生成“过拟合诊断”。系统对五个固定参数候选采用
10 分区 CSCV（252 个训练/样本外组合），报告回测过拟合概率 PBO、
选中候选的样本外亏损概率和平均绩效退化；同时用候选数量、会话长度、
收益偏度与峰度计算 DSR。少于 20 个同步完整会话、候选收益无方差或
统计调整项无效时，界面只显示“不可估计”，不会补造分数。该页只用于
发现研究选择偏差，不能自动批准策略或提交订单。

同一轮评估还生成“数据质量”和“执行压力”证据。数据质量按每个交易日
10:00–15:45 的 346 个预期分钟检查完整率、最长连续缺口、stale/异常报价、
源端年龄以及 Level-I 最优档数量覆盖。执行压力分别重跑配置成本、至少 5bps
以及至少 10bps 加双倍佣金，并用整股成交量相对最优档数量估算容量；这只是
Level-I 容量近似，不代表完整订单簿或真实成交保证。

“独立评审”页把证据身份、`captured_stream` 真实流来源、至少 25 个
完整会话、两折时间隔离、PBO/DSR、未触碰测试超额、整股/成本和样本外
成交逐项列为硬门。系统还重建未触碰测试会话收益，显示 lag-1 自相关、
AR(1) 有效样本量以及 Newey–West/Bartlett 长期方差下的均值为正置信度。
合成预览和导入研究数据会被来源门直接阻断；即使全部硬门通过，结论也仅为
“可进入人工独立评审”，不会记录人工批准、自动晋级或发送订单。

桌面导航按职责分为“监控台、策略与回测、市场研究、运维与安全、系统设置”。
行情数据源与券商连接是两种独立角色：Finnhub/Alpaca/IBKR 可提供行情，
IBKR Gateway Paper 负责账户、持仓和模拟订单连接。

“监控台 → 自动量化”是多标的主工作区：从最新广域扫描动态选取合格候选，
再以实时行情、非中概资格、龙头层级、整股容量和风险门筛选。首页只显示
运行状态、Paper 净值、已实现/未实现盈亏和当前持仓；候选信号与订单审计
收在二级页。当前研究版本最多同时持有一个标的，不使用固定观察池。
启动区会集中显示 Paper 能力、策略、动态候选、实时行情、账户资金和本次
确认六项检查；“会话资金上限”只限制本次策略，不会把历史研究资金写入
Paper 账户。0 表示使用 Paper 净值与可用现金中的较小值，仍禁止借款。
`1.1.0-research` 在原动量门上加入正收益步数和单分钟异常跳变过滤，并收紧
仓位、点差和交易次数；它仍是需要 Paper 样本外验证的研究版本，不保证盈利。
订单适配器默认提交 `SMART/USD` 的整股 `DAY LMT`；启用 5×24 Paper 后，
盘前/盘后用 `OutsideRth` 限价单，隔夜用 IBKR `OVERNIGHT` 限价单。仍逐会话
武装并以券商账户、
持仓、订单状态和成交回报对账。首期要求启动时 Paper 账户空仓。
连接时会恢复开放订单、当日逐笔成交和已完成订单；订单终态只有在成交数量
完全一致后才释放。拒单、断线或提交结果不确定会立即停机，禁止自动重试；
未成交 BUY 超时或用户停止时，只精确撤销本会话对应订单，不提供全局撤单。
“Paper订单”二级页集中显示执行健康和未对账原因。

“策略与回测 → 回测工作区”可按不可变策略版本运行买入持有、双均线趋势、
唐奇安突破和 RSI 均值回归，对比收益、Sharpe、回撤、交易与成本。每个结果
保存 Run ID、版本 ID、参数哈希、代码哈希和数据哈希，并记录信号时间、
次日开盘成交、滑点、佣金、仓位与现金。复权日 K 结果始终属于研究代理。

内部策略仿真启动前必须读取 5 分钟内的 IBKR Paper `NetLiquidation`，并绑定
指定标的策略版本；“执行前检查”二级页汇总身份、非中概、行情、资金、整股和
分钟证据。它不提交券商订单。配置中的 $1,500 只是可调整的历史研究情景，
不代表 Paper 或未来 Live 账户余额。

## 本地开发

项目使用 Python 3.13 环境：

```powershell
.\.venv313\Scripts\python.exe -m pip install -e ".[research,desktop]"
.\.venv313\Scripts\python.exe -m us_quant doctor
.\.venv313\Scripts\python.exe -m unittest discover -s tests -v
.\.venv313\Scripts\python.exe scripts\check_desktop_layout.py
.\.venv313\Scripts\python.exe desktop_main.py
```

安装 IBKR 官方 Python API：

```powershell
.\scripts\install_ibkr_api.ps1 -PythonClientPath "D:\TWS API\source\pythonclient"
```

## 研究流程

刷新官方目录和已有 SEC 核验缓存：

```powershell
.\.venv313\Scripts\python.exe -m us_quant universe-refresh --sec-profiles 0
```

创建、运行 IBKR 历史队列：

```powershell
.\.venv313\Scripts\python.exe -m us_quant history-schedule --limit 500
.\.venv313\Scripts\python.exe -m us_quant history-run --maximum-jobs 25
```

桌面客户端的“市场研究 → 数据任务”可一次把全部非中概研究池加入持久化队列，
再分批下载。命令行的 `--limit` 只控制本次加入队列的数量，不是市场扫描或
实时交易白名单。

当 IBKR 因其他 IP 会话拒绝历史数据时，可使用只供研究的备用公开日 K：

```powershell
.\.venv313\Scripts\python.exe -m us_quant history-run-public --maximum-jobs 50
```

扫描与组合研究：

```powershell
.\.venv313\Scripts\python.exe -m us_quant scan-market
.\.venv313\Scripts\python.exe -m us_quant research-cross-sectional
.\.venv313\Scripts\python.exe scripts\validate_cross_sectional.py
```

Windows 打包：

```powershell
.\scripts\build_windows_client.ps1
```

## 关键产物

- `data/reference/universe.json`：官方标的、国家/行业核验、层级和排除原因
- `research/results/market_scan.json`：扩大样本后的最新市场扫描
- `research/results/cross_sectional_research.json`：8 组参数、6 折走样本外结果
- `research/artifacts/desktop_*.png`：桌面端视觉验收截图
- `dist/USQuantResearch-win64.zip`：可交付 Windows 客户端

## 风险结论

当前较强的回测结果不能视为盈利保证。样本仍有当前成分幸存者偏差，多数扩展历史来自备用源，且日 K 无法证明日内可成交性。Finnhub 模拟带也不是真实盘口。IBKR Paper 自动量化是执行与对账验证，不是盈利能力证明；必须持续观察拒单、部分成交、滑点、断线恢复和样本外表现。

详见：

- [项目状态 0.16.0](docs/PROJECT_STATUS_2026-07-25.md)
- [客户端说明](docs/CLIENT_GUIDE.md)
- [成熟量化软件案例复盘](docs/MATURE_QUANT_CASE_REVIEW.md)
- [项目架构](docs/PROJECT_BLUEPRINT.md)
- [需求与决策](docs/PROJECT_INTAKE.md)
