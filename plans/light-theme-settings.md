# Exec plan: 浅色主题与设置中心

## Purpose and success criteria
将主题、行情源、API 凭据、IBKR 只读连接与常用运行参数集中到独立设置页；保留深色界面并新增完整浅色主题。所有敏感字段使用 Windows 当前用户加密存储，界面、日志、导出和发布包不泄露密钥。通过自动测试、离屏界面截图、发布包启动自检验证。

## Context map
- `src/us_quant/desktop.py` — 桌面页面、主题和运行控件。
- `src/us_quant/credential_store.py` — Windows DPAPI 凭据存储。
- `src/us_quant/config.py` — 现有运行配置。
- `docs/CLIENT_GUIDE.md` — 用户操作说明。
- `scripts/render_desktop_preview.py` — 界面视觉验收。

## Milestones
- [x] M1: 完成并记录内部评估、设置分类和迁移边界。
- [x] M2: 实现浅色主题、设置页、安全存储和原页面瘦身。
- [x] M3: 完成自动测试、双主题视觉验收、客户端重建与启动自检。

## Decision log
- 2026-07-26: 保留深色主题并增加可持久化浅色主题，不强制替换用户现有外观。
- 2026-07-26: API 凭据只允许 DPAPI 加密落盘；业务配置与密钥分离。
- 2026-07-26: 行情表改为增量模型和 500ms 合并刷新；滚动拖动期间只保留最新待显示快照。

## Completion record
- 96 项单元测试通过；设置保存重启验证通过；Finnhub 新界面 10 秒实时链路握手通过。
- 0.6.0 Windows ZIP 重建完成；打包后自检通过，ZIP 内凭据/用户偏好文件数量为 0。
