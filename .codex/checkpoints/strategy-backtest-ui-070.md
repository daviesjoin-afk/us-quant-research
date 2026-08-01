# Task checkpoint: 策略、回测与工作域重构 0.7.0

## Goal
按成熟量化终端的职责边界重组桌面客户端，建立多策略版本绑定回测工作区，并为未来 IBKR Paper 订单适配器增加默认关闭的能力门。

## Constraints
- 行情角色与券商角色独立；IBKR 可同时承担两种角色，但连接状态不得混为一谈。
- 除中概股外的合格股票/ETF可进入广域研究；不保存固定股票白名单。
- 回测必须绑定策略版本 ID、参数哈希和数据哈希；复权日 K 只标为研究代理。
- IBKR Paper 能力默认关闭；只允许回环地址、端口 4002 和唯一 DU 账户。能力开关不等于会话武装或订单授权。
- Live、碎股、做空、保证金借款和期权订单仍禁止。

## Current phase
Complete

## Decisions
- 采用 5 个一级工作域：监控台、策略与回测、市场研究、运维与安全、系统设置。
- 策略与回测下设策略目录与版本、回测工作区、横截面研究。
- 首批单标的回测族为买入持有基准、双均线趋势、唐奇安突破、RSI 短期均值回归。
- 每次回测生成独立 Run ID 和原子 JSON 产物，不覆盖前一次结果。
- 策略默认种子改为增量迁移，已有用户数据库也会新增策略族。
- 设置中的 Paper 能力开关只持久化非敏感能力标志；当前版本仍没有券商订单提交入口。

## Changed or inspected files
- `src/us_quant/desktop.py` — 工作域导航、回测工作区、连接角色说明、Paper 能力开关。
- `src/us_quant/strategy.py` — 唐奇安突破和 RSI 均值回归策略。
- `src/us_quant/backtest_workspace.py` — 策略目录、版本绑定回测、指标和 Run 产物。
- `src/us_quant/strategy_registry.py` — 增量默认策略迁移和 0.7.0 策略版本。
- `src/us_quant/user_settings.py` — Paper 能力标志持久化。
- `tests/test_backtest_workspace.py`、`tests/test_strategy.py`、`tests/test_strategy_registry.py`、`tests/test_user_settings.py` — 回归测试。
- `scripts/render_desktop_preview.py` — 新导航和有结果的回测截图。
- `README.md`、`docs/CLIENT_GUIDE.md`、`docs/MATURE_QUANT_CASE_REVIEW.md`、`docs/PROJECT_STATUS_2026-07-25.md` — 用户与架构说明。

## Verification
- `python -m unittest discover -s tests -v` — 101 项通过。
- `python -m us_quant doctor` — Paper、整股、无保证金、订单关闭默认值通过。
- `python -m compileall -q src tests` — 通过。
- 4 策略 UI 集成回测 — 4 个 Run、4 行对比结果、交易明细成功。
- 深浅主题离屏截图 — 5 工作域、策略目录、回测结果、行情与设置页渲染成功。
- 0.7.0 发布包自检 — 退出码 0；ZIP 2000 个条目，凭据/用户偏好条目 0。
- 0.7.0 ZIP SHA256 — `4A3A30804610CE6CA033848AA02181901BDDABD203406D11254F283B9C7FCC82`。

## Blockers or risks
- 当前通用回测器是单标的/单目标；横截面策略仍使用独立组合研究引擎。
- 当前策略版本、回测 Run 已绑定，但影子盘尚未由同一策略工厂驱动。
- Paper 能力开关尚无订单适配器；必须先完成 OMS 回调、对账、kill switch、演练限额和逐会话武装。
- 当前历史数据为复权研究代理，仍有历史成分股与公司行动偏差。

## Next action
实现统一组合回测引擎与策略参数 schema，修复横截面总风险/单仓预算退化和 MUU 最长持有期；随后建设独立 `IBKRPaperBroker` 的 what-if 与人工确认小额限价单流程。
