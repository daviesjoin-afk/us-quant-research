# Task checkpoint: IBKR Paper 自动量化与简洁界面 0.15.0

## Goal
持续优化本地美股量化桌面客户端：广域扫描后由多标的策略自动筛选、风控并
连接 IBKR Paper 验证执行；主界面只展示整理后的净值、盈亏、持仓、状态和
关键操作，候选、订单与检查明细放入二级页。

## Constraints
- 只允许本机 IB Gateway Paper `4002` 和单一 `DU` 账户；Live 永久阻断。
- 只做股票/ETF 整股；不做中概、碎股、做空、保证金借款、期权或市价单。
- 无固定股票池或代码专用逻辑；候选来自广域扫描，龙头/优质二线优先。
- Finnhub/Alpaca 凭据用 Windows 当前用户 DPAPI 保存，禁止明文日志和导出。
- Paper 自动量化必须设置能力开关、当次确认和点击武装三重显式授权。

## Current phase
0.15.0 已构建并完成测试、视觉验收和 Windows 打包；继续进行 Paper 真实会话
的拒单、部分成交、滑点、断线与恢复验收。

## Decisions
- “自动量化”是主执行页；最新广域扫描动态生成 3–20 个候选，当前最多一只
  持仓，实时行情门通过后才产生订单意图。
- IBKR Paper 适配器仅提交 `SMART/USD` 整股 `DAY LMT`，有单笔上限、
  候选白名单、会话 ID、可卖数量和 append-only 审计；没有 Live/全撤入口。
- Paper 账户摘要、持仓、订单状态与成交回报为真值，本地 P&L 只是备用展示。
- 针对性日内 T 输入移入自身页面，运行 `internal_shadow`，不提交券商订单。
- 针对性工具新增“执行前检查”二级页，汇总 11 个硬门和分钟证据；首页不铺
  原始技术字段。

## Changed or inspected files
- `src/us_quant/auto_quant.py`, `auto_intraday.py`：多标的自动信号与风控。
- `src/us_quant/ibkr_paper_orders.py`：受限 Paper 订单连接、回报与审计。
- `src/us_quant/desktop.py`：简洁自动量化页、Broker P&L、针对性入口和检查页。
- `src/us_quant/targeted_preflight.py`：针对性仿真执行前检查。
- `src/us_quant/export_service.py`：脱敏 `paper_orders.csv`。
- `README.md`, `docs/CLIENT_GUIDE.md`, `docs/PROJECT_STATUS_2026-07-25.md`：0.15。

## Verification
- `scripts/verify.ps1`：145 项测试、doctor、compileall 全通过（2026-07-26）。
- `scripts/render_desktop_preview.py`：30 张深/浅色截图生成并目检通过。
- EXE 5 秒启动冒烟通过；FileVersion/ProductVersion 均为 `0.15.0`。
- ZIP SHA256：`5128424E1A4A1F8FBF7ACAC34A0A43CA8918295796D554153D2178636BB9141C`。
- 交付：`dist/USQuantResearch/USQuantResearch.exe` 与
  `dist/USQuantResearch-win64.zip`；明文凭据模式扫描 0 命中。

## Blockers or risks
- 尚未对真实 IBKR Paper 会话完成订单拒绝、部分成交、断线重连和盘前/盘后验收。
- Finnhub 成交影子带不是 NBBO；Level-I 数量不保证成交容量。
- Paper 表现不代表 Live 成交或未来盈利。

## Next action
由用户保持 Gateway Paper 4002 登录，在设置中显式开启能力并从“自动量化”
手动武装；记录首个小规模 Paper 会话，再按实际订单/成交/断线审计修复。
