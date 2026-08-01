# Task checkpoint: market-source-strategy-paper-orders

## Goal
修复行情连接失败时无法切换数据源，并重点优化策略与 IBKR Paper 模拟下单工作流，完成测试、桌面验证与重新打包。

## Constraints
- 只允许 IBKR Paper 端口 4002；Live 永久阻断。
- 只做股票/ETF 整股，不做中概、碎股、做空或市场单。
- 行情源与券商通道保持角色分离。
- 自动量化持仓或订单未对账时不得切换行情源。

## Current phase
已完成。

## Decisions
- 默认行情源选择器始终可操作，并提供显式“切换 / 重连行情”动作。
- 无订阅代码时只切换并保存默认来源，不弹窗、不创建线程、不引入默认观察池。
- 有订阅代码时先安全停止旧流，再启动新流；不会并行运行两个来源。
- 自动量化存在持仓或在途订单时仍禁止切换，这是订单对账边界。
- 自动量化增加六项启动前检查、会话资金上限与只读 Paper 通道诊断。
- 自动策略新增 1.1.0-research：连续性过滤、单分钟异常跳变过滤、订单超时与更严格风险参数；保留旧版用于比较。
- 未成交 BUY 超时或用户停止时只撤销本会话精确订单，不提供全局撤单。
- IBKR 握手增加共享超时和可中断连接，避免连接线程永久卡住。

## Changed or inspected files
- `src/us_quant/desktop.py`
- `src/us_quant/auto_quant.py`
- `src/us_quant/auto_intraday.py`
- `src/us_quant/strategy_registry.py`
- `src/us_quant/strategy_schema.py`
- `src/us_quant/shadow_paper.py`
- `src/us_quant/ibkr.py`
- `src/us_quant/ibkr_readonly.py`
- `src/us_quant/ibkr_stream.py`
- `src/us_quant/ibkr_paper_orders.py`
- `scripts/check_paper_order_channel.py`
- `tests/test_auto_quant.py`
- `tests/test_strategy_schema.py`
- `tests/test_ibkr.py`
- `tests/test_ibkr_paper_orders.py`
- `README.md`
- `docs/CLIENT_GUIDE.md`

## Verification
- `scripts/verify.ps1`：160 项 unittest、doctor、compileall 全部通过。
- Qt offscreen：无代码时可切换到 Finnhub，`pending=None`、`worker=None`，不弹阻断框。
- Windows 客户端实机：数据源下拉框与切换按钮保持启用；无代码切换成功，页脚给出后续订阅提示。
- IBKR Paper 只读通道诊断：唯一 DU 账户、净值/现金可读、持仓 0、API 未结订单 0、本地未对账订单 0、提交订单 0。
- 打包：USQuantResearch 0.17.0，ZIP SHA256 `998C912DFC879232E1A6966A9CCF5A0D70C52B8E131F373A2392BA4C0CD48070`。

## Blockers or risks
- IBKR 当前证券行情权限仍可能回退到 Type 3 延迟；实时日内策略应使用可用实时源（当前默认 Finnhub）或之后订阅 IBKR 实时市场数据。
- 策略为研究版本，不保证盈利；未在本轮提交任何 Paper 订单。

## Next action
等待用户输入任意股票/ETF 并启动实时行情；如需真正进行 Paper 演练，再由用户明确武装会话。
