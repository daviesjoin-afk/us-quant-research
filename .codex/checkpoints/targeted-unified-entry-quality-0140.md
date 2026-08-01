# Task checkpoint: 统一指定标的与执行证据 0.14.0

## Goal
持续完善本地美股量化桌面客户端；用户输入任意合格股票/ETF 后，实时订阅、
分钟回放、多日研究和内部影子做 T 共用同一标的状态，并补齐数据质量与执行
压力证据。保持 IBKR Paper、整股、非中概和无券商订单边界。

## Constraints
- 指定标的默认留空且只由用户输入；没有股票/ETF 专用替代映射。
- 行情源与券商角色分离；IBKR 固定 Paper 4002，订单提交入口仍不存在。
- 只做整股；中概硬排除；研究结果不构成盈利保证或自动晋级。
- Finnhub/Alpaca 凭据用 Windows 当前用户 DPAPI 保存，禁止明文日志和导出。

## Current phase
0.14.0 已交付，继续等待真实分钟会话积累与下一轮优化。

## Decisions
- 主窗口顶部提供唯一“当前指定做 T 标的”入口；行情订阅、多日研究、回放和
  影子会话均调用同一 accessor，影子会话运行中禁止切换。
- 实时订阅窗口仍可承载广域观察列表，但针对性做 T 的目标只来自统一入口。
- 分钟证据记录源端年龄与 Level-I bid/ask size；质量报告按 346 个预期分钟
  检查完整率、连续缺口、stale、异常报价、年龄和数量覆盖。
- 执行压力重跑配置成本、至少 5bps、至少 10bps+双倍佣金；最优档参与率只是
  Level-I 容量近似。
- 独立评审扩展到 22 项门控，不自动批准、不晋级、不下单。

## Changed or inspected files
- `src/us_quant/desktop.py`：统一标的栏、质量/压力页面与研究链路。
- `src/us_quant/ibkr_stream.py`, `alpaca_stream.py`, `minute_data.py`：年龄与最优档数量。
- `src/us_quant/targeted_data_quality.py`, `targeted_execution_stress.py`：新证据模块。
- `src/us_quant/targeted_review.py`, `export_service.py`：22 项门控与 CSV 导出。
- `scripts/render_desktop_preview.py`：26 张深浅主题截图。
- `README.md`, `docs/CLIENT_GUIDE.md`, `docs/PROJECT_STATUS_2026-07-25.md`：0.14 说明。

## Verification
- `python -m unittest discover -s tests`：134 项通过（2026-07-26）。
- `python scripts/render_desktop_preview.py`：26 张截图生成；统一入口、质量与压力页
  深浅主题可读（2026-07-26）。
- 活跃 `src` 无 `MUU`/单一代码专用分支；历史说明已标记废弃。
- `python -m us_quant doctor`：Paper 4002、只读、整股、订单关闭通过。
- `scripts/build_windows_client.ps1`：0.14.0 Windows 包成功；EXE 5 秒启动冒烟通过，
  FileVersion/ProductVersion 均为 0.14.0。
- 最终 ZIP SHA256：`1DE95E52E42C5A5BC4716528322F1E84EADFC9CB71001D60AFDFA847C7D61BDD`。

## Blockers or risks
- 合成预览不具备晋级资格；真实评审仍需至少 25 个高质量 captured_stream 会话。
- Level-I 数量不是完整订单簿，不能保证实盘容量或成交。

## Next action
由用户在统一入口输入目标代码，订阅真实行情并积累 captured_stream 会话；下一轮
根据真实数据质量、成本压力和影子成交审计继续迭代，不开放券商下单。
