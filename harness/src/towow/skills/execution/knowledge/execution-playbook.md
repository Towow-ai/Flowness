# Execution Playbook — 通用 Procedure + 智能选用工具

> 用途：execution skill 执行一个 task 的整体流程。
> 归属：M-1.4 execution skill 知识库
> 核心精神：不规定每种 task_type 各自模式——按任务要求选用工具就好；几个步骤怎么样都没关系

---

## 整体流程

```
[task 启动]
  ↓
Phase 1: 理解 task
  - 读 capsule（TaskPackage / concept_refs / active_obligations / knowledge files）
  - 问"我能 demo 这个 task 完成是什么样吗？"——不能 → 调 advisor
  ↓
Phase 2: 准备工作区
  - worktree 已 setup（M-3.1 工程化外壳做）
  - V-01 owner-guard 已注入
  ↓
Phase 3: 执行
  - 按任务要求选用工具
  - 写代码 / 测试 / 文档 / 配置 / 概念事件
  - 持续 mismatch 监测（按 mismatch-and-issue-handling 精神）
  - 遇到判断困难调 advisor
  - 必要时自己造工具
  ↓
Phase 4: pre-submit self-check
  - 跑 execution-self-check fork（OPUS）
  - blocking_checks 任一 failed → 修 + 重跑
  ↓
Phase 5: 组装 + 提交
  - 按 envelope-honesty-principle 组装 envelope
  - `./tw submit envelope.json` → submit wrapper 处理 commit gate
  - accept → 完成
  - reject → 按 reject reason 处理（见 git-safety-and-queue）
[task 结束]
```

## 按任务要求选用工具，不规定模式

不同 task_type（implementation / test / documentation / config_migration / concept_update / review_prep / investigation）做不同的事——但你**怎么做**应该 follow 任务的要求，不是查表"X type 必须做 Y Z W"。

例：
- **implementation task**：按 done_criteria 反推代码改动，写代码 + 测试。要不要 stub / mock / refactor 已有 helper / 加新 abstraction——按需选。
- **test task**：按 done_criteria 写测试。要不要 fixture / parametrize / property-based——按需选。
- **documentation task**：按 done_criteria 写文档。要不要 example / diagram / api reference 章节——按需选。
- **config_migration**：高风险——必须有 backup / verify / rollback 步骤。这是 task 内禀要求，不是 type 规则。
- **concept_update**：不写代码——走 M-1.2 supersede 协议（调 M-1.2 工具）。
- **review_prep**：准备 review context，不做 review 本身（M-1.5 做）。
- **investigation**：调研产出信息——done_criteria 是"信息产出"不是"代码 commit"。

**核心**：理解任务要求 → 选用工具 → 完成。工具包括：Edit / Write / Bash / Read / Grep / 你写的临时 helper / 你调的 advisor fork。

## 关键不变量（永远遵守）

不论什么 task_type：

1. **不在主 branch 写** → worktree 隔离
2. **不写 declared_write_set 外文件** → V-01 拦截：有 `.owner` 工位时 PreToolUse hook 写前自动 guard-check、越界物理拒 + emit OwnerGuardViolation；无 `.owner` 时不物理强制、写前自觉 guard-check 兜底；强行需要 → 触发 mismatch 调 advisor
3. **不读 declared_read_set 外数据没理由** → 读了必须补 actual_read_set + drift_reason
4. **不绕过 active_obligations** → envelope 逐条标 maintained / violated / not_applicable + evidence
5. **不假装 mismatch 不存在** → 发现按 mismatch-and-issue-handling 走最高效化解
6. **不 self-assess pre-submit** → 跑 execution-self-check fork（OPUS）
7. **不绕过 submit wrapper** → 走 `./tw submit`
8. **不重复造轮子** → 写新代码前 grep / 查 read_set

## 智能 + 灵活 + 造工具

Nature 强调："他应该是拥有足够的智能，然后自己判断使用工具或者是自己造工具。"

例子：
- task 要处理 100 个文件的批量重命名 → 写个 shell 脚本一次性跑，比手动 100 次 Edit 优雅 + 可观测
- task 要 verify 某个 invariant → 写个临时 python 函数 in `/tmp/verify.py`，跑一次确认 + log 结果
- task 要做 fuzz testing → 用 hypothesis 库（如果已经在 deps）/ 写个简单的 random input generator
- task 要 measure 性能 → 用 timeit / cProfile / 写个简单的 benchmark loop

**造工具的边界**：
- 临时辅助 → 跑完丢掉（不进 commit）
- 反复使用的 utility → 评估是否值得进代码库（可能要扩 write_set / 调 advisor）
- 不要为造工具而造工具——目的是高效完成 task

## "我能 demo 完成"的检验

Phase 1 的关键自检：**"我能 demo 这个 task 完成是什么样吗？"**

能 → 你理解了 task，可以开始执行。
不能 → 你没真理解——调 advisor 澄清，或者这是 M4 mismatch（done_criteria 跟 brief 矛盾）。

具体的 demo 是什么取决于 task_type：
- implementation → 跑代码看 expected behavior + 测试通过
- test → 跑测试看 expected coverage / pass-fail pattern
- documentation → 写出来 + 一个零上下文 reader 能读懂
- config_migration → before_state → after_state 验证通过 + rollback 可执行
- concept_update → 新 concept event commit + downstream 引用方收到 supersede 通知
- review_prep → review plan artifact 可执行
- investigation → 信息产出能让 planner 继续决策

## 不要做的（汇总）

- 不规定步骤死板地按表走（任务要求是 source of truth）
- 不每个小决策调 advisor（先自己想）
- 不假装"我先做 MVP 后面再加"（Nature 失败模式）
- 不没读文件就改（Nature 失败模式）
- 不写完代码不跑测试（implementation task 必须包含 verify）
- 不绕过 worktree / wrapper / V-01（V-01 在有 `.owner` 工位时是 PreToolUse 物理强制；无 `.owner` 时不物理强制——但"不绕过"照样适用，靠自觉 guard-check）
