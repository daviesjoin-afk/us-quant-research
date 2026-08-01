# Task checkpoint: 独立研究评审 0.13.0

## Goal
持续完善本地美股量化桌面客户端；用户输入任意合格股票或 ETF 后，分钟证据、回放、稳健性、时间隔离、过拟合和独立评审均可审计且不保证收益。

## Constraints
- 标的由用户输入；不含特定股票/ETF专用分支或默认替代映射。
- 排除中概，只做整股；不做空、借保证金或执行期权。
- 行情源与券商分离；IBKR 只读，模拟下单默认关闭。
- 禁止跨源/跨日拼接；测试集不参与选择；统计不足显示不可估计。
- 评审结果不记录人工批准，不自动晋级，不提交订单。

## Current phase
Handoff

## Decisions
- 分钟记录显式标记 `captured_stream`、`synthetic_preview` 或 `imported_research`；只有真实流采集可通过来源硬门。
- 独立评审交叉核验稳健性、时间隔离和过拟合结果的 Run、标的、版本、参数、数据哈希及行情源。
- 15 项硬门包含 25 个完整会话、至少两折、测试隔离、验证门、样本外正收益/正超额、PBO<50%、DSR≥95%、整股成本和测试成交。
- 未触碰测试收益计算 lag-1 自相关、AR(1) 有效样本量及 Newey-West/Bartlett HAC 均值为正概率；零方差和样本不足均阻断。
- 全部门通过仅返回 `ELIGIBLE_FOR_INDEPENDENT_REVIEW`，不是策略批准。

## Changed or inspected files
- `src/us_quant/minute_data.py` — 证据来源字段、SQLite 迁移、摘要与指纹。
- `src/us_quant/targeted_review.py` — 相关性/HAC、15 项硬门、原子持久化。
- `src/us_quant/desktop.py` — 独立评审页及实时影子/研究证据双工作区。
- `src/us_quant/export_service.py` — `targeted_review.csv` 与证据来源。
- `tests/test_targeted_review.py` — 可评审、合成阻断、缺失证据和持久化。
- `scripts/render_desktop_preview.py` — 显式合成来源及深浅主题评审截图。

## Verification
- `python -m compileall -q src tests scripts` — 通过。
- `python -m unittest discover -s tests` — 129 项通过（2026-07-26）。
- `python scripts/render_desktop_preview.py` — 22 张截图；深浅主题评审硬门可读，合成来源被阻断。
- `scripts/build_windows_client.ps1` — 0.13.0 Windows 包成功。
- 打包 EXE 冒烟 — 5 秒后保持运行，FileVersion/ProductVersion 均为 0.13.0。
- 最终 ZIP SHA256 — `BDF65629A60E7078DF99547273537D60EC08EF93BDC68E2A43F9291FE3B1CA93`。

## Blockers or risks
- 尚无至少 25 个真实 `captured_stream` 完整会话，真实策略不会通过独立评审。
- AR(1) 有效样本与 HAC 为有限样本近似；正相关或结构变化会降低可信度。

## Next action
持续采集真实完整分钟会话；下一轮加入数据缺口/延迟分布、成交容量与滑点敏感性评审，不开放自动下单。
