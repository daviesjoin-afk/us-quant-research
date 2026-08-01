# Task checkpoint: 浅色主题、设置中心与行情性能

## Goal
实现可持久化双主题、按数据源管理的安全凭据设置，并明确广域研究池与临时实时订阅的边界。

## Constraints
- IBKR 仅允许本机 Paper 端口、只读连接；不得开放下单、Live、碎股或保证金借款。
- API 凭据只用 Windows 当前用户 DPAPI 保存，不进入偏好文件、日志、导出或发布包。
- 除中概股外的合格股票/ETF均可进入广域研究；实时订阅代码不得被描述为交易白名单。

## Current phase
Complete

## Decisions
- `preferences.json` 只保存主题、默认行情源和 IBKR 非敏感参数；不持久化股票观察池。
- 深浅主题共用语义色板；设置后立即应用并持久化。
- Finnhub、Alpaca Key/Secret 统一使用 DPAPI；设置页用数据源下拉切换对应凭据表单。
- 行情表改为 `QAbstractTableModel + QTableView` 增量更新；移除逐成交 GUI 回调，只由 500ms 定时器取最新快照；拖动滚动条时合并刷新。
- IBKR host/port/client ID 可保存，但校验强制 localhost、4002 和只读安全不变量。

## Changed or inspected files
- `src/us_quant/desktop.py` — 现有主题、行情表、凭据和导航实现。
- `src/us_quant/credential_store.py` — 可复用的 DPAPI 存储。
- `src/us_quant/paths.py` — 打包资源与用户状态边界。
- `src/us_quant/user_settings.py` — 非敏感偏好校验与原子保存。
- `src/us_quant/ui_theme.py` — 深浅主题语义色板。
- `src/us_quant/redaction.py` — 写日志前与导出时双重脱敏。
- `tests/test_user_settings.py` — 固定股票池移除与旧设置迁移测试。
- `README.md`、`docs/CLIENT_GUIDE.md`、`docs/PROJECT_STATUS_2026-07-25.md` — 广域研究和实时订阅边界说明。
- `plans/light-theme-settings.md` — 执行计划。

## Verification
- 内部 UI/性能评审 — 完成；定位双重刷新、整表重建和 ResizeToContents。
- 内部 QA 评审 — 完成；确认主题硬编码、偏好层缺失及运行中配置风险。
- `python -m unittest discover -s tests -v` — 97 项通过。
- 旧版 `watchlist` 设置迁移检查 — 启动加载时忽略并从偏好文件自动移除。
- API 数据源下拉检查 — Finnhub 仅显示 Key；Alpaca 仅显示 Key/Secret；IBKR 隐藏凭据字段。
- Finnhub UI 实时检查 — 握手成功，10 行模型稳定，未提交订单。
- 深浅主题离屏截图 — 设置页和行情页渲染成功；行情页明确单次实时订阅不限制广域研究。
- 0.6.1 发布包自检 — 退出码 0；ZIP 2000 个条目，凭据/用户偏好条目 0。
- 0.6.1 ZIP SHA256 — `AAFF3404A9E5C708B4EA7D2A1DAAA0948B1322017F7D12CF13C0B424B1CE0D9E`。

## Blockers or risks
- 免费实时源不能代表全市场扫描；实时连接窗口按单次负载选取代码，但不会持久化或限制研究池。

## Next action
等待用户在 0.6.1 中确认设置页交互；后续实时测试仍保持只读/内部影子盘边界。
