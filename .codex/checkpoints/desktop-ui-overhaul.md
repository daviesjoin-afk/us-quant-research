# Task checkpoint: desktop-ui-overhaul

## Goal
排查桌面客户端全部页面的显示问题，优化布局、缩放、可读性与交互；同时把自动 Paper 模拟流程收敛为可理解的两步，并加入 IBKR Paper 盘前、盘后和隔夜会话支持。

## Constraints
- 保持现有策略、行情和交易安全边界不变。
- 不发送订单，不修改账户数据。
- Windows 本地桌面客户端，深色与浅色主题均需可用。

## Current phase
Complete

## Decisions
- 用响应式网格和分行操作区替代拥挤的单行布局。
- 表格使用像素滚动与可交互列宽，避免 ResizeToContents 导致拖动卡顿。
- 建立 1120×720、1280×800、1440×900、1920×1080 四档离屏布局回归。
- 实时订阅子集、历史日 K 覆盖和广域研究池分层显示；历史队列可加入全部研究池。
- 客户端默认最大化，设置页在低高度窗口中使用纵向滚动。
- 自动量化页明确为两步：第 1 步准备候选并自动安全切换旧行情；候选 READY 后，第 2 步在 Paper 专用确认弹窗中启动模拟下单。
- 行情与 Paper 会话的停止操作在自动量化页同一行可见；切换候选不再要求用户先手动停止行情再重试。
- Paper 通道检测保留为明确标注的可选、只读动作；确认状态不再以页面复选框暴露。
- 行情连接完成后，底部状态栏显示“已连接 / READY 数量”；不再在已握手后保留“正在连接”。
- 新增 IBKR 5×24 行情源：盘前、常规和盘后采用 `SMART`，美东 20:00 后自动转 `OVERNIGHT`，美东 04:00 自动转回 `SMART`。
- 新增 Paper 5×24 开关：盘前/盘后为 `SMART + OutsideRth` 整股限价单，隔夜为 `OVERNIGHT` 整股限价单；周末、全日休市和 03:50–04:00 ET 维护期拒绝订单。
- “刷新官方标的”改为可取消任务；SEC 单只资料请求限制为 8 秒、无重试，避免末尾单个代码网络卡住数分钟。
- 官方标的刷新、历史、扫描、回测、策略研究、指定标的研究和 IBKR 只读/Paper 通道按资源组互斥；不同组可同时运行，不再用全局任务锁阻塞其他页面。
- 取消刷新不会写入 SEC 资料的半完成快照，并保留上一次完整的客户端研究结果。
- 自动 Paper 第一阶段改为“全市场扫描→实时短名单”：每次重新扫描全部非中概
  研究池，把历史缺口加入数据队列，再按新鲜 Paper 账户资金、整股容量、板块
  分散和风险倍数选出默认 20、最多 30 个实时轮动候选。
- Paper 候选不再复用 1500 美元历史研究情景的容量判断；当前约 100 万美元
  Paper 快照可从现有 597 个已评分标的中生成 20 个分散候选。
- 自动会话新增“暂停新开仓（保留持仓）”和“恢复新开仓”；只有“停止会话并
  请求平仓”才主动退出持仓。暂停时止损、止盈和时段退出继续运行。
- Finnhub 成交 fresh 窗调整为 20 秒，界面分开显示“当前 fresh”和“近30秒
  覆盖”；状态栏按30秒节流，不再随 2/3、3/3 每秒反复刷屏。

## Changed or inspected files
- `src/us_quant/desktop.py`
- `src/us_quant/ui_theme.py`
- `src/us_quant/extended_hours.py`
- `src/us_quant/ibkr_stream.py`
- `src/us_quant/ibkr_paper_orders.py`
- `src/us_quant/user_settings.py`
- `src/us_quant/universe.py`
- `tests/test_universe.py`
- `tests/test_extended_hours.py`
- `tests/test_ibkr_stream.py`
- `tests/test_user_settings.py`
- `scripts/check_desktop_layout.py`
- `scripts/build_windows_client.ps1`
- `scripts/windows_version_info.txt`
- `docs/CLIENT_GUIDE.md`
- `README.md`
- `pyproject.toml`
- `src/us_quant/__init__.py`

## Verification
- 172 项业务测试通过。
- 四档窗口尺寸、全部页面布局审计通过。
- 深色与浅色主题、行情、自动量化、市场扫描和设置页截图检查通过。
- Python 编译检查通过。
- 0.19.0 Windows 客户端重新构建并启动成功（PID 19704）。
- ZIP SHA256: `501F70710CD539E81D88ECB93FC1E52A8AF2CCB8A047105E0818275E028E343A`

## Blockers or risks
- 当前研究池 5,311 个，但本地日 K 只有 603 个；需在数据任务页分批补齐后重扫。
- 2026-07-28 只读实测：IBKR Gateway 协议握手正常，但 `reqMktData` 返回
  `10197`（存在竞争真实账户交易会话时无法获得市场数据）。需退出同一用户名的
  其他 Live/TWS/Client Portal 交易会话后再验证 IBKR 扩展时段行情；未提交订单。

## Next action
新版客户端已经启动。用户先在“账户与持仓”刷新 Paper 资金，然后在“自动量化”
点击“第1步：全市场扫描并准备行情”；界面会显示官方池、研究池、已评分覆盖和
实时短名单四个数字。当前历史覆盖仍只有 597/5,318，缺口已进入数据队列，需
逐批补齐后才能真正对全部研究池评分。IBKR 5×24 仍需处理 Gateway 的 10197
数据会话冲突。
