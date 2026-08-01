# Exec plan: 行情切换、策略与 IBKR Paper 模拟下单优化

## Purpose and success criteria
- 行情连接中、延迟、报错或重连时，用户仍能选择并切换其他数据源。
- 无订阅代码时也能保存默认来源，不强制默认观察池。
- 切换过程先停止旧流再启动新流；持仓/在途订单时保留对账保护。
- 自动量化页直接展示启动检查、会话资金上限与 Paper 通道只读检查。
- 新自动策略具有连续性、异常跳变、资金和订单超时保护。
- 完整测试、桌面交互验证和 Windows 重新打包通过。

## Milestones
- [x] M1: 审计失败场景与安全边界。
- [x] M2: 完成可中断行情连接、可恢复数据源切换和无代码默认源切换。
- [x] M3: 完成策略、启动检查、会话资金与 Paper 订单生命周期优化。
- [x] M4: 160 项自动测试、Qt/Windows 桌面验证、Paper 零订单诊断和重新打包通过。

## Completion record
- 版本：0.17.0
- 应用：`dist/USQuantResearch/USQuantResearch.exe`
- 压缩包：`dist/USQuantResearch-win64.zip`
- SHA256：`998C912DFC879232E1A6966A9CCF5A0D70C52B8E131F373A2392BA4C0CD48070`
- 本轮未提交任何 IBKR Paper 订单。
