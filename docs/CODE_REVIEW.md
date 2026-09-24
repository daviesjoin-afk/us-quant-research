# Code Review 模型

本文件说明这个仓库**如何审查一个变更**：三层各自负责什么、不能互相替代什么、
以及本地 OpenCodeReview 集成的使用方法与边界。

本文件描述的是**开发闭环**，不是新的 CI gate。本轮没有把 OpenCodeReview
接进 GitHub Actions，也没有把它设为 required status check。

---

## 1. 三层职责

| 层 | 是什么 | 回答的问题 | 谁拥有 |
| --- | --- | --- | --- |
| **Deterministic gates** | `pytest`、architecture guards、mutation tests、`doctor`、`compileall`、desktop offscreen self-test、Python 3.14 CI | 「这条已声明的不变量，现在真的还成立吗？」 | CI，强制 |
| **OpenCodeReview** | 按路径注入规则、对 diff 做独立 AI 审查 | 「这批改动里有没有**跨文件**的逻辑错误、并发窗口、ownership 错配、非法状态迁移、异常路径漏洞？」 | 本地 / Codex，**advisory** |
| **Manual architecture review** | 人读 diff，确认 canonical owner 与架构方向 | 「这个修复是不是修在正确的层？有没有把 symptom 挪走而没修根因？」 | PR review，人工 |

### 为什么需要第三层之外的这一层

现有 deterministic gates 覆盖的是**已经写成断言的东西**。它们有两个天然盲区：

1. **跨文件语义**。一个模块释放了另一个模块拥有的 lease，pytest 里没有任何
   一条断言会失败，因为两侧各自的行为都「正确」。
2. **未被断言的失败路径**。异常被吞掉、retry 造成重复下单、partial commit、
   clean-up 只在 happy path 上执行 —— 这些要写成测试，前提是有人先想到它们。

OpenCodeReview 的价值是**在写断言之前先指出该写哪条断言**。它的价值不是
「替代测试」，而是「生成候选缺陷假设」。

### 三层都不能被替代

```text
❌ 用 OpenCodeReview 替代 pytest
❌ 用 OpenCodeReview 替代 mutation tests
❌ 用 OpenCodeReview 替代 architecture guards
❌ 用 OpenCodeReview 替代 deterministic risk rules
❌ 用 OpenCodeReview 替代 human architecture review
❌ OpenCodeReview 没报问题 ⇒ 认为代码安全
```

反过来也成立：**deterministic gates 全绿不代表没有跨文件缺陷**。两层是互补的，
不是冗余的。

OpenCodeReview 的 diff 审查本身也**不是**这道关：它的 recall 是刻意让位于
precision 的（工具自身的定位就是「宁可少报，不可误报」），所以「零 findings」
只能说明「没有高置信度的明显缺陷」，不能说明「没有缺陷」。

---

## 2. Findings 的地位

> OpenCodeReview findings are advisory during evaluation period.

按项目默认的严重级别策略：

| Severity | 处理要求 |
| --- | --- |
| **CRITICAL** | 必须处理，或明确证明是 false positive（并把证据写进 PR） |
| **HIGH** | 必须处理，或明确证明是 false positive |
| **MEDIUM** | 必须 review；是否修由证据决定 |
| **LOW** | 默认不阻止 merge |

即使 CRITICAL / HIGH 也**不自动阻塞 merge**：本轮没有自动 blocker。
阻塞来自人，不来自工具。

---

## 3. 当前 review workflow

```text
1. implementation
2. targeted verification     （scripts/verify.ps1 -CoreOnly -TestPath ...）
3. OpenCodeReview            （scripts/review.ps1）
4. findings triage / fix
5. Python 3.14 full CI       （scripts/verify.ps1 / CI）
6. final PR review
```

第 3 步在第 5 步**之前**：review 的成本远低于一次完整 CI，而一个被 review
指出来的结构性缺陷，最好在把整套 CI 跑一遍之前就修掉。

「targeted verification」不是一个 gate，是内循环的收敛手段。最终仍然必须由
第 5 步的完整 CI 说话。

---

## 4. 安装

开发机器上：

```bash
npm install -g @alibaba-group/open-code-review
ocr --help
```

要求 **Git >= 2.41**（OCR 依赖 git 生成 diff、搜索代码）。

Codex 用户可以把 OCR 作为插件安装：

```bash
codex plugin marketplace add alibaba/open-code-review
```

然后在 Codex 里用 `/plugins` 启用 **Open Code Review**。

**不要把任何用户级 API key、provider 配置或 token 写进本仓库。**
OCR 的用户级配置位于 `~/.opencodereview/`，那是用户目录，不是仓库的一部分。

---

## 5. Delegation mode 优先

默认使用 **delegation mode**：

```text
OpenCodeReview 负责:  diff / file selection、project rules、review grouping、review process
Host agent 负责:      LLM reasoning
```

选择它的理由：

- 不需要为 OCR 再维护一套独立的模型 credentials；
- 降低 secrets 管理复杂度（仓库里因此**没有**任何 OCR 凭证）；
- 本机开发方便，不需要联网到某个 provider；
- 后续替换模型时不需要改仓库。

只有当 delegation mode 在当前 Codex 环境里不稳定时，才考虑 OCR-managed
provider（`ocr config provider` / `ocr config model`），配置仍然只存在于用户目录。

---

## 6. 仓库级规则：`.opencodereview/rule.json`

规则按 OCR 的**四层优先级**解析，第一个匹配到的 pattern 生效：

| 优先级 | 来源 | 路径 |
| --- | --- | --- |
| 1 | `--rule` | 命令行 |
| 2 | 项目 | `<repo>/.opencodereview/rule.json` |
| 3 | 全局 | `~/.opencodereview/rule.json` |
| 4 | 系统内置 | 二进制内嵌 |

两条必须记住的机制：

- **first match wins**。规则数组按声明顺序求值，**最具体的路径必须写在最前面**。
  同一个文件不会同时应用多条项目规则 —— 所以每一条路径规则都是自足的。
- **`merge_system_rule`**。默认 `false` 时，匹配到的项目规则会**替换**该文件
  的内置语言规则。本项目对生产代码统一设为 `true`，让内置的 Python 基线规则
  继续生效，项目规则只做**追加**。

唯一的例外是 `tests/**`：它的 `merge_system_rule` 是 `false`。
测试文件要判断的是「测试有没有真的覆盖它声称的不变量」，而内置 Python 规则
里有大量 style 类条目；两者合并会让 reviewer 去纠结测试代码风格，这不是我们
要它看的。所以测试路径**替换**掉语言基线，只留 §十六 的清单。

### 路径分层

| 路径 | 关注点 |
| --- | --- |
| `src/us_quant/trading/runtime/workflow_state.py` | `ExecutionLease` 精确比较、reserve/commit/cancel、Paper 相位迁移表、单一 owner |
| `src/us_quant/trading/application/risk.py` | risk 必须先于任何 execution side effect、rejected 不得变 accepted、Decimal 限额比较 |
| `src/us_quant/trading/application/execution.py` | 不信任调用方「已验证」、broker 不确定 ≠ success、retry 不得产生重复订单、partial success |
| `src/us_quant/trading/application/paper/**` | `HALTED` 只能经人工对账 + 显式确认恢复、zero-state proof 先于 ownership release、reservation 失败必须 cancel |
| `src/us_quant/trading/runtime/**` | 状态机合法性、晚到 / 重复 callback、shutdown 与 inflight、partial commit、naive datetime |
| `src/us_quant/trading/adapters/**` | connect/submit/cancel/replace 的幂等、callback 去重、超时=unknown、terminal order 不得复活 |
| `src/us_quant/trading/**` | ownership 唯一、lease 不得跨 capability 释放、check-then-act 竞态、异常路径资源泄漏 |
| `src/us_quant/desktop.py`、`src/us_quant/desktop_v2/**` | Page 不得直连 broker/repository/workflow、projector 必须 pure、禁止 god object、跨 capability 只能在 composition root 组合 |
| `tests/**` | 断言是否覆盖声称的不变量、fake 是否过度简化、并发测试有没有真的开竞态窗口、mutation 是否只制造 SyntaxError |
| `.github/**` | primary Python 3.14、不得无意恢复 3.12/3.13 per-PR matrix、`pull_request_target` 不得跑不可信代码、secrets 不得进 log/artifact |
| `scripts/**` | wrapper 不做业务判断且透传 exit code、不得默认削弱既有 gate、不得硬编码凭证 |
| `pyproject.toml` | `requires-python`（支持面）与 CI gated 解释器是**两个**决定，不得互相绑架 |
| `src/us_quant/**/*.py` | 全局 Python 清单：吞异常、check-then-act、mutable 跨 ownership 泄漏、cleanup 缺失、partial commit、stale snapshot、naive datetime、Decimal→float、retry 重复副作用、getter 产生 mutation、`None`/`False`/空集合等价、异常路径资源泄漏 |

查看某个路径实际解析到哪条规则：

```bash
ocr rules check src/us_quant/trading/application/risk.py
```

### `include` 是必需的，不是装饰

OCR 内置的默认排除表包含 `**/test_*.py`。本仓库的测试文件正是
`tests/test_*.py`，因此**如果不写 `include`，所有测试文件都会被静默排除**。

`rule.json` 里的：

```json
"include": ["tests/**/*.py"]
```

就是用来绕过这道默认排除闸门的。实测（同一个 diff）：

```text
无 rule.json  -> 0 个测试文件进入 review 列表
有 rule.json  -> 10 个测试文件进入 review 列表
```

`include` 不是白名单：未命中 `include` 的文件照常走后面的扩展名与默认路径检查，
所以它只是「放行」，不会把别的文件挡掉。

---

## 7. 本地 wrapper：`scripts/review.ps1`

```powershell
.\scripts\review.ps1
```

默认范围是 `main -> HEAD`，默认 delegation mode，产物写入
`.ocr-output/<时间戳>/`。

常用形式：

```powershell
# 默认：main -> HEAD，delegation mode
.\scripts\review.ps1

# 指定范围
.\scripts\review.ps1 -From main -To chore/open-code-review-integration

# 不花 token 先看选中了哪些文件
.\scripts\review.ps1 -Preview -Mode provider

# 用 OCR 自己配置的 LLM（需要先 ocr config provider / ocr config model）
.\scripts\review.ps1 -Mode provider -Format json

# 本次临时换一套规则（评估规则改动用）
.\scripts\review.ps1 -Rule .\some-rules.json
```

这个脚本**只做 wrapper**：选范围、调用 `ocr`、把产物写进 `.ocr-output/`，
然后透传退出码。它不判断某个 finding 是否真实，不判断是否该阻塞 merge，
也不判断被审代码是否安全 —— 那正是 OCR 要独立回答的问题，wrapper 再给一个
结论就等于多了一个真值来源。

Delegation mode 的产物：

```text
.ocr-output/<时间戳>/preview.json     可审查文件、merge base、排除原因
.ocr-output/<时间戳>/rules-NN.json    每个文件分组解析后的规则（含内置语言规则）
```

`.ocr-output/` 已在 `.gitignore` 中，且 root-anchored。**不要提交 review 产物**：
它带有本机绝对路径，而且是可复现的派生物；提交它还会让下一次 review 的 diff
里混进上一次 review 的 findings。

OCR 自己已经在用户目录保存 session，所以仓库内**不**再建 session store。

---

## 8. 发现 finding 之后怎么做

finding 不能被直接照修：

```text
OCR finding
  ↓ 定位真实代码
  ↓ 证明问题是否真的存在
  ↓ 找到 canonical owner
  ↓ 在 canonical owner 处修
  ↓ regression test
  ↓ 必要时补 mutation
```

明确禁止：

```text
❌ OCR 说有问题 -> 在 Desktop 层加 workaround -> symptom 消失
```

交易安全代码尤其如此。让 symptom 消失不等于让缺陷消失。

另外：**不要为了得到 `0 findings` 去改规则压结果。** 如果 OCR 报的点不成立，
把「为什么不成立」写进 PR；如果成立，修代码。调规则只能是为了提高信噪比，
不能是为了让输出好看。

---

## 9. 哪些路径默认只 review、不自动修

即使某个模式支持 `review and fix`，以下路径默认 **review only**，finding 一律
先人工确认：

```text
src/us_quant/trading/application/risk.py
src/us_quant/trading/application/execution.py
src/us_quant/trading/application/paper/**
src/us_quant/trading/runtime/**
src/us_quant/trading/adapters/**          (broker adapters)
ExecutionLease（trading/runtime/workflow_state.py）
reconciliation / finalization 相关
```

只有下面这些类别才考虑自动修：

```text
docs
tests
pure projector
formatting
obvious type error
```

---

## 10. 本轮明确不做的事

```text
❌ 把 OCR 设为 required status check
❌ OCR medium / low 自动阻塞 merge
❌ OCR 自动 merge
❌ 新增 OCR_LLM_AUTH_TOKEN / OCR_LLM_URL / OCR_LLM_MODEL 等 repository secrets
❌ 用 pull_request_target 跑未经安全审计的自动 reviewer
❌ 接 GitHub Actions
```

本地 / Codex 集成 + 仓库规则 + wrapper + 文档是这一轮的边界。GitHub Action
放到后续独立阶段，届时 §六 `.github/**` 那条规则会先于 Action 存在 ——
也就是说，reviewer 接入之前，接 reviewer 的 workflow 本身就已经有规则可审。

---

## 11. 评估期要收集的数据

在决定是否把 OCR 升级成 CI gate 之前，至少观察若干 PR 的：

```text
true positive          报对了几个
false positive         报错了几个（以及为什么）
重复意见               同一条缺陷被报几次
漏报                   事后发现但 OCR 没报的
review latency         一次 review 的墙钟时间
token / API cost       delegation mode 下即 host agent 的上下文成本
```

这份数据积累够了，再决定是否进入 CI gate。没有数据就设 gate，等于把一个
尚未测量的 precision 变成 merge 的硬约束。
