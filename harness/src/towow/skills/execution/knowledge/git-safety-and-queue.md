# Git Safety & Queue — Worktree 隔离 + 走 Submit Wrapper

> 用途：execution skill 跟 Git 物理操作的协议。
> 归属：M-1.4 execution skill 知识库
> 核心精神：物理隔离 + 不绕过统一入口——commit 排队由 M-3.1 submit wrapper 处理，不要自造

---

## 三件你必须做的事

### 1. 在独立 worktree 里写代码（不在主 branch）

P-04 物理约束：同机多 session 共享 Git 工作区——并行执行必须 worktree 隔离。

```bash
# task 开始时（实际由 M-3.1 工程化外壳帮你做）：
git worktree add ../worktree-{task_id} <base_branch>
cd ../worktree-{task_id}
```

你写代码、测试、跑 git commit (local) 都在这个 worktree 内。

### 2. V-01 owner-guard 限制你的写权限

V-01 的执行强度取决于你的工位有没有 `.owner` 写边界声明（T-FIX-B4-03 焊上的 PreToolUse 物理门）：

- **有 `.owner` 写边界声明时（isolation=on 工位 / 显式声明 worktree）**：PreToolUse hook 在每次 Write/Edit 前自动调 `worktree guard-check`，越界 file 被工具层物理拒绝（exit 2）并 emit canonical `OwnerGuardViolation` 留痕——**这是物理强制**，且 fail-closed（边界验不了也拒，不静默放行）。
- **无 `.owner` 声明时（如 isolation=off 默认态）**：文件级 V-01 边界**不物理强制**（hook 只给 stderr 诚实提示），写前须**自觉**跑 guard-check 兜底。

不论哪种状态，越界被拒/越界需求 = 触发 mismatch（调 advisor 决定扩 write_set 还是 RePlan），不要绕门。

### 3. 提交走 `./tw submit`，不绕过

所有 commit 走同一条命令——`./tw submit envelope.json`（等价旧全称写法
`uv run --directory harness python -m towow.cli.main submit envelope.json`，仅 cwd 恰为项目根时可用）。

```bash
# 完成执行后：
git commit -m "{task_id}: {description}"  # 最终 commit in worktree
./tw submit ./envelope.json  # 调 M-3.1 submit wrapper
```

submit wrapper 帮你处理：
- 把 envelope 路径 B 落盘
- 调 M-0.5 commit gate
- 等 commit gate 决定（同步阻塞）
- accept → 自动 git push worktree → main + cleanup worktree
- reject → 不 push + 保留 worktree + 返回 reject reason

## 为什么 commit 必须走 wrapper

**这就是 Nature 说的"统一到固定节点，收束在固定节点，一个一个来"。**

submit wrapper 是 M-3.1 提供的统一入口——它内部：
- 单进程 mutex 串行（同时刻只有一个 envelope 在被处理）
- 其他 worktree 的 submit 排队等
- 处理 git lock / commit gate 调用 / accept 后 push / reject 后 cleanup

**你不需要担心**：
- 并发 race
- git lock 竞争
- commit gate 调用细节
- accept 后怎么 push 到 main
- reject 后 worktree 状态

**你需要做的**：
- 写完代码 → 在 worktree git commit
- 跑 `./tw submit envelope.json`
- 等结果

## 共享 live 树严禁 git 回滚（T-FIX-B4-07 / 2026-06-10 双事故实证）

**`.towow/`（账本热段 / 全部投影 / 水位线 / capsule）是 git tracked 的运行态**——任何把
tracked 文件砸回 HEAD 的 git 操作，都会把别人正在写的账本回滚掉。2026-06-10 实证两次：
执行会话为跑干净 pytest 在共享树 `git stash && pytest && git stash pop`，stash 卷走 50 个
文件（账本热段回滚 25980→24620 + 全部投影 + 自己刚完成的活），pop 撞 daemon 并发写冲突
失败；同日另有 `git restore` 形态二次回退。

**严禁清单**（PreToolUse 物理门 B4-07 会拦，别绕）：

- `git stash`（任何子命令——它卷走全树 tracked 改动，命令文本里看不到 .towow 不代表没碰它）
- `git restore`（含 `--staged`——宁可误拦；解除暂存用只动 index 的 `git reset -- <path>`）
- `git reset --hard / --merge / --keep`（碰工作树的模式；裸 reset / `--soft` / `--mixed` 只动 index，合法）
- `git checkout -- <path>` / `git checkout .` / `git checkout -f`（路径还原形；分支切换 `git checkout <branch>` 合法）

**要干净测试基线？用隔离副本，别回滚共享树**：

```bash
git worktree add /tmp/clean-test-$$ HEAD
(cd /tmp/clean-test-$$ && <跑测试>)
git worktree remove /tmp/clean-test-$$
```

脏树跑测试通常也可接受（pytest 不要求树干净）。真需要回滚（owner 授权的恢复手术）走
`.towow/locks/admin-bypass.flag`（受控产生归 T-FIX-B4-05 CLI）。

## 不要做的事

| 误做法 | 为什么错 |
|---|---|
| 自己 `git push origin worktree-X:main` | 绕过 wrapper —— commit gate 没接受过你 envelope —— 系统状态不一致 |
| 自己调 M-0.5 API | 同上 |
| 自己处理并发 lock | wrapper 帮你处理 —— 你处理只会跟 wrapper 冲突 |
| 在主 branch 直接 commit | P-04 违反 —— 污染其他 session |
| 写 declared_write_set 外文件 | V-01 拦截（有 `.owner` 时 PreToolUse 物理拒 + OwnerGuardViolation；无 `.owner` 时不物理强制、靠自觉 guard-check）—— 触发 M7 mismatch |
| 共享树 `git stash` / `git restore` / `git reset --hard` / `git checkout --` | tracked 运行态（.towow 账本/投影）被回滚——2026-06-10 双事故实证；物理门 B4-07 拦（见上节） |

## Envelope reject 后怎么办

submit wrapper 返回 reject + reason 时：

1. **不要立即 abort** —— 看 reject reason
2. **如果是 write_conflict** —— 另一 envelope 先到了 —— git rebase 你的 worktree + 重做 + retry submit
3. **如果是 self_check_failed** —— 你的 fork 自检报错了 —— 修 + 重跑 self-check fork + retry
4. **如果是 obligation_violated** —— 你 envelope 标 maintained 但 gate 检测到 violated —— 调 advisor 评估（可能是 task 边界错 / 可能是 evidence 不足）
5. **如果是 novelty_check_failed** —— 你的 supersede 没带 novelty —— 加 novelty evidence + retry
6. **如果反复 reject 多次** —— 调 advisor 决定（可能 abort task / RePlan）

worktree 在 reject 后**保留**——你不需要重建 setup。

## Task 完成后的 cleanup

- accept → wrapper 自动 cleanup worktree
- abort → 你产 TaskRunCompleted(aborted) 后 wrapper 自动 cleanup
- retry 中 → 不 cleanup，等下次 attempt

**你不需要手动 cleanup**——除非系统级故障 wrapper 没正常结束。

## 一句话

你的工作：写代码、跑测试、组装 envelope、`./tw submit`。

wrapper 的工作：commit gate 调用、串行排队、git push / cleanup。

**界限明确——你不越界，wrapper 不越界。**
