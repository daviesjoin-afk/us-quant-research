# Task checkpoint: Paper 执行对账最终版 0.16.0

## Goal
完成本轮 USQuant 桌面客户端优化并停止后续迭代；提高 IBKR Paper 订单、
部分成交、断线与重启后的可审计性，同时保持主界面简洁。

## Constraints
- 仅本机 Paper 4002、单一 DU、整股 DAY LMT；Live/市价/碎股/做空/期权禁用。
- 自动量化需要设置能力、当次确认、点击武装三重授权。
- 非中概、无固定股票池、凭据 DPAPI 加密且不进入导出。
- 用户要求本轮完成测试、视觉验收和 Windows 打包后结束。

## Current phase
0.16.0 已完成并交付；不再自动开启下一轮。

## Decisions
- `Filled`/撤单/拒单等终态只有在 reported filled 与逐笔 execution 数量一致后
  才释放本地 pending，避免终态先到时重复开仓。
- 拒单、非整股/超量成交、持仓差异、断线和提交结果不确定全部 fail-closed，
  禁止按分钟自动重试。
- 连接/重连读取开放订单、已完成订单和当日逐笔成交；重连只恢复事实，不自动
  恢复交易。券商存在持仓或未对账订单时不能安全结束会话。
- `execution_id` 唯一持久化；回调队列用锁原子读取，避免轮询期间漏事件。
- Paper 订单二级页只显示执行健康摘要和整理后的对账表；主页面保持净值、
  盈亏、持仓、状态和核心操作。

## Changed or inspected files
- `src/us_quant/ibkr_paper_orders.py`：恢复快照、逐笔成交表、终态对账、线程安全。
- `src/us_quant/auto_quant.py`：回报乱序保护、拒单/不确定提交停机。
- `src/us_quant/paper_execution_health.py`：券商/本地持仓与订单对账健康。
- `src/us_quant/desktop.py`：执行健康、断线重连对账、整理后的订单表。
- `src/us_quant/export_service.py`：新增 `paper_executions.csv`。
- `tests/test_auto_quant.py`, `test_ibkr_paper_orders.py`,
  `test_paper_execution_health.py`, `test_runtime_export.py`。
- README、CLIENT_GUIDE、PROJECT_STATUS、BLUEPRINT、案例评审：0.16 说明。

## Verification
- `scripts/verify.ps1`：153 项测试、doctor、compileall 通过（2026-07-26）。
- `scripts/render_desktop_preview.py`：32 张深浅色截图生成并目检通过。
- Gateway 4002 当时不可达，因此未进行外部连接或订单提交。
- EXE 5 秒启动通过；FileVersion/ProductVersion `0.16.0`。
- 明文凭据模式 0 命中；旧 0.15.0 版本引用 0 命中。
- ZIP SHA256：`1162E03AD55CC6C5AC8B4567781CA232A0A544BA73B1264CF58A4372B0EE8AFD`。

## Blockers or risks
- 未在开放 Gateway 上完成真实 Paper 拒单、部分成交和断线演练。
- 本地包未代码签名；Finnhub 成交影子带不是 NBBO。
- Paper 结果不代表 Live 成交或未来盈利。

## Next action
无自动后续动作。若用户将来重新开始，应先打开 Gateway Paper 4002，并以极小
Paper 会话验证执行对账；不得直接扩展到 Live。
