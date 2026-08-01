# Exec plan: 指定标的多会话稳健性证据

## Purpose and success criteria
把单次分钟回放升级为按纽约交易日独立运行的多会话证据；基准与确定性参数扰动共用同一影子引擎，结果可恢复、可导出且不能自动晋级。以全量测试、深浅主题截图、打包 EXE 启动和版本哈希为完成证据。

## Context map
- `src/us_quant/targeted_replay.py` — 单会话回放和跨源/跨日/连续性硬门。
- `src/us_quant/targeted_robustness.py` — 常规时段分组、五场景评估、聚合指标和持久化。
- `src/us_quant/desktop.py` — 指定标的入口、评估历史和参数扰动工作区。
- `src/us_quant/export_service.py` — 脱敏稳健性 CSV。
- `tests/test_minute_replay.py` — 会话边界、扰动、持久化和失败路径。

## Milestones
- [x] M1: 单行情源、纽约 10:00–15:45 完整窗口、至少 300 行、独立交易日和连续预热硬门。
- [x] M2: 基准、入场阈值 ±20%、退出距离 ±20% 五场景聚合与原子保存。
- [x] M3: 桌面历史/参数表、导出、测试和深浅主题预览。
- [x] M4: 实现 10/5/5 锚定 walk-forward、未来测试集隔离和等风险整股日内基准。
- [x] M5: 固定候选集完成 CSCV/PBO 与 DSR 多重检验诊断。
- [x] M6: 建立独立证据评审硬门、来源标签和序列相关性/HAC 诊断。
- [ ] M7: 使用至少 25 个真实完整交易日运行两折验证并接受人工独立评审。

## Decision log
- 2026-07-26: 每个交易日重置资金与引擎，避免跨日复利状态污染。
- 2026-07-26: “收益方向一致率”明确不代表盈利；20 日门只允许人工复核，不自动晋级。
- 2026-07-26: 参数只由训练集选择；验证集门控，测试集不参与选择；基准使用同风险预算和相同交易成本。
- 2026-07-26: 固定候选集增加 10 分区 CSCV/PBO 与 DSR；统计条件不足显示不可估计，诊断不构成晋级。
- 2026-07-26: 合成/导入分钟数据必须带显式来源并被晋级门阻断；序列相关性折算与 HAC 置信度只基于未触碰测试会话。

## Completion record
- `python -m unittest discover -s tests` — 118 项通过。
- `python scripts/render_desktop_preview.py` — 16 张深浅主题截图生成。
- `scripts/build_windows_client.ps1` — 0.10.0 成功；EXE 启动，版本 0.10.0。
- `python -m unittest discover -s tests` — 0.11.0 共 122 项通过。
- `python scripts/render_desktop_preview.py` — 18 张截图，含深浅主题两折时间隔离页。
- `scripts/build_windows_client.ps1` — 0.11.0 成功；EXE 启动，版本 0.11.0。
- `python -m unittest discover -s tests` — 0.12.0 共 126 项通过。
- `python scripts/render_desktop_preview.py` — 20 张截图，含深浅主题过拟合诊断页。
- `scripts/build_windows_client.ps1` — 0.12.0 成功；最终 ZIP SHA256 `655225A32E76962A6089B41B675024E96BB8E02F98BAEB965FBDDF2E5B7C2ED4`。
- `python -m unittest discover -s tests` — 0.13.0 共 129 项通过。
- `python scripts/render_desktop_preview.py` — 22 张截图，含深浅主题独立评审页和工作区布局。
- `scripts/build_windows_client.ps1` — 0.13.0 成功；最终 ZIP SHA256 `BDF65629A60E7078DF99547273537D60EC08EF93BDC68E2A43F9291FE3B1CA93`。
