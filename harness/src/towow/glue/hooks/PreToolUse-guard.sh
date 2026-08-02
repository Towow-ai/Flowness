#!/usr/bin/env bash
# T3 — PreToolUse Guard hook (DOGFOOD-001-A repair, guard-layer-pretooluse-hook concept).
#
# Spec source:
#   docs/DOGFOOD-RUN-001-FINDINGS.md DOGFOOD-001-A
#   ~/.claude/plans/twinkling-dancing-music.md T3
#   Nature decision INF-002: 仅 v3-managed repo (.towow/ 存在) 生效
#
# 3 cases (T-FIX-B4-02 后 Case (b) 带采访入口身份门; T-FIX-B4-03 后写前先过 V-01 段):
#   (a) 当前 working dir 无 .towow/        → exit 0 (透传，非 v3 项目不拦)
#   (b) .towow/ 存在 + 有 active session   → origin 血缘三态判定 (见下文身份门段):
#         origin 合法 → exit 0; origin 伪造 → exit 2 (fail-closed);
#         origin 不可判 → exit 0 + stderr advisory (分阶段保守放行, 诚实边界)
#   (c) .towow/ 存在 + 无 active session   → exit 2 (拒 + 引导用户跑 /work)
#   (0) .towow/locks/admin-bypass.flag (owner 物理旗标, 非空) → exit 0 + stderr 留痕
#   (V-01, T-FIX-B4-03) write-class 工具在上述放行点之前先过越界写物理拦截段:
#         有 .owner 工位 (cwd 在 .towow/worktrees/<wid> 内, 或 TOWOW_TASK_ID env
#         定位) → 写前自动调真 `towow worktree guard-check` 原语, 越界 → exit 2 +
#         canonical OwnerGuardViolation 真留痕; 无 .owner → 不拦不假装, stderr
#         诚实边界提示后退回采访入口门判定。V-01 通过 ≠ 免锁通行证。
#
# Claude Code 调用 hook 时通过 stdin 传 JSON event：
#   {"tool_name": "Write" | "Edit" | "NotebookEdit" | ...,
#    "tool_input": {...},
#    "session_id": "..."}
#
# 我们只关心 write-class 工具。ChatGPT/Codex 的 apply_patch 由
# `.codex/hooks/pre_tool_use_adapter.py` 拆成 Edit 语义后进入本共享守卫。

set -e

# M-1.6 fix (finding-9c4fd4ed): fail-closed protocol — if we are in a v3-managed repo
# (.towow/ exists) AND no active session, we MUST reject regardless of whether stdin
# parsing succeeds. Previously, when jq was unavailable or stdin was empty/invalid,
# TOOL_NAME defaulted to "" and the case statement fell through to exit 0 — that was
# fail-open, violating v3 V-01 owner-guard invariant.

# 读 stdin JSON event
HOOK_EVENT="$(cat 2>/dev/null || echo '')"

# 提取 tool_name；空 stdin / 非 JSON / jq 缺 → TOOL_NAME 仍是 ""
TOOL_NAME=""
if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
    TOOL_NAME="$(echo "$HOOK_EVENT" | jq -r '.tool_name // empty' 2>/dev/null || echo "")"
fi

# DOGFOOD-001-CC fix (RUN-011): bg agent in daemon-isolated worktree
# 必须用 --project-dir=<main_repo> 跑 v3 命令, 不该在 cwd init .towow.
# 触发条件: cwd 含 .claude/worktrees/ (daemon 创的隔离 worktree convention path).
# 这条 check 在 .towow 存在性检查之前 — daemon worktree init 时 .towow 可能不存在,
# 但我们要 catch agent 试图 init 的瞬间.
CWD_FOR_CC="$(pwd)"

# Claude/Codex hook runtimes provide the active repository root through
# CLAUDE_PROJECT_DIR. Keep remote `git -C` protection portable instead of
# binding the distributed guard to one owner's workstation path.
command_targets_active_project() {
    [ -n "${CLAUDE_PROJECT_DIR:-}" ] || return 1
    case "$B406_CMD" in
        *"git -C ${CLAUDE_PROJECT_DIR}"*|*"git -C \"${CLAUDE_PROJECT_DIR}\""*|*"git -C '${CLAUDE_PROJECT_DIR}'"*) return 0 ;;
    esac
    return 1
}

# ── T-FIX-B4-03 前置: write-class 判定 + 工位上下文 (cwd 通道) ────────────────
# 工位 = .towow/worktrees/<wid>/ (WorktreeManager.create 的 per-task git worktree,
# .owner 写边界声明在工位根)。cwd 在工位内 (含子目录) 时从路径锚定 wid + 主仓根 —
# 这是 V-01 物理门最可靠的定位通道 (env marker 只是提示, 工位内真实 .owner 才是信任根)。
IS_WRITE_TOOL=0
case "$TOOL_NAME" in
    Write|Edit|MultiEdit|NotebookEdit) IS_WRITE_TOOL=1 ;;
esac
V01_TASK_ID=""
V01_MAIN_DIR=""
case "$CWD_FOR_CC" in
    */.towow/worktrees/*)
        V01_MAIN_DIR="${CWD_FOR_CC%%/.towow/worktrees/*}"
        _v01_rel="${CWD_FOR_CC#*/.towow/worktrees/}"
        V01_TASK_ID="${_v01_rel%%/*}"
        ;;
esac

# ── T-R06-4 (concept fail-closed-mcp-allowlist@v1) × T-RMD-S4-PHYS-MATCHER (薄路由单一真源): ──
#    harness 会话 MCP 暴露面 fail-closed 白名单 ──
#
# R06 实证: 仓库无 .mcp.json, harness 起的会话无差别继承宿主机全部 MCP (99 工具 76% 从没被调一次),
# 其中 Gmail create_draft / Drive create_file / Notion update-page / Zapier execute_write_action 等
# 30+ 写权限 SaaS 工具对每个会话裸暴露 — 一个研究会话理论上能往 owner Gmail 写草稿, 此前只 prompt 层拦。
# 本门: 在 v3-managed repo (.towow 可达) 内, 白名单 (本地 playwright) 外的 mcp__ 工具调用 → 物理拒。
#
# T-RMD-S4-PHYS-MATCHER (单一真源 + 真 emit IAB): 路由层 × 判定层缺一为零 (见 contracts/physical_gate.py)。
#   路由层 = matcher 含 mcp__.* (settings.json, 让 mcp 工具家族真进本 hook) —— 旧 live matcher 缺它,
#            整个本门是生产里的死电路 (finding f-sub-mcp-physical-gate-matcher-drift / M31-01)。
#   判定层 = 本门【不再】在 bash 里复刻一份判定 (旧版 bash 自判自拒、且静默 exit 2 不留任何 canonical 痕,
#            与 python DefaultPhysicalGate 两份分裂、白名单还更松有子串绕过洞)。改成薄路由 —— 把白名单外
#            的候选交给【单一真源】判定层 python -m towow.awareness.physical_gate_check: 它判定 +
#            DENY 时真 emit canonical IrreversibleActionBlocked 上看板 (provenance=SYSTEM/a6_physical_gate)。
#   bash 只保留最便宜的 playwright 白名单快路径 (精确双段, ⊆ python _MCP_ALLOWLIST, 放行的它也必放行 = 安全,
#   不必为每个 playwright 调用拉起 python) + 路由 + fail-closed 兜底。
#
# 白名单只放本地 playwright (R06 实测唯一真用的 MCP, 本地 127.0.0.1 dashboard dogfooding 走它);
# native WebSearch/WebFetch 不是 mcp__ 工具, 不受本门影响 (联网走 native 即可)。
# 出口: admin-bypass.flag (与 B4-06/07 同旗标) → 放行 + stderr 留痕 (owner 授权高于一切, 即便判定层不可达也放行)。
# fail-closed: 判定层判 DENY / python 不可达 / 定位不到账本 → 一律物理拒 (绝不 fail-open 放过高危)。
# 诚实边界: 只按 tool_name 前缀判 (mcp__); jq 缺 / TOOL_NAME 空 → 非 mcp 调用本就不触发。
case "$TOOL_NAME" in
    mcp__*)
        MCP_ALLOWED=0
        case "$TOOL_NAME" in
            # 最便宜快路径: 只放本地 playwright 的精确双段前缀 mcp__plugin_playwright_playwright_*
            # (真实工具全是 mcp__plugin_playwright_playwright__browser_* 等)。这条 ⊆ 判定层
            # _MCP_ALLOWLIST(physical_gate_impl.py, 同字面锚定 ^mcp__plugin_playwright_playwright(_|$)),
            # 故快路径放行的 python 也必放行 = 安全, 不必为每个 playwright 调用拉起 python。
            # 绝不用 *playwright* 通配(任意含该子串的恶意名绕过, R06 复查 F1);
            # 也不用单段 mcp__plugin_playwright_*(残留: mcp__plugin_playwright_evil__ 仍中, R06 复查 QC)。
            mcp__plugin_playwright_playwright_*) MCP_ALLOWED=1 ;;
        esac
        if [ "$MCP_ALLOWED" = "0" ]; then
            # v3 base = 含 .towow 真账本的目录 (也是含 pyproject 的内核包目录, uv --directory + --project-dir 同它)。
            MCP_BASE=""
            if [ -d ".towow" ]; then
                MCP_BASE="$PWD"
            elif [ -d "harness/.towow" ]; then
                MCP_BASE="$PWD/harness"
            elif [ -n "$V01_MAIN_DIR" ] && [ -d "$V01_MAIN_DIR/.towow" ]; then
                MCP_BASE="$V01_MAIN_DIR"
            fi
            if [ -n "$MCP_BASE" ]; then
                if [ -s "$MCP_BASE/.towow/locks/admin-bypass.flag" ]; then
                    # owner 一次性 admin-bypass: 即便判定层不可达也放行 (与 B4-06/07 同旗标同语义)。
                    echo "⚠ PreToolUse Guard (T-R06-4 MCP 白名单门): 白名单外 MCP 工具 '$TOOL_NAME' 经 admin-bypass.flag 放行。flag: $MCP_BASE/.towow/locks/admin-bypass.flag" >&2
                else
                    # tool_input 作 payload 传判定层 (硬信号识别 mail/funds 等需要它)。
                    MCP_PAYLOAD="{}"
                    if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
                        MCP_PAYLOAD="$(echo "$HOOK_EVENT" | jq -c '.tool_input // {}' 2>/dev/null || echo "{}")"
                    fi
                    # 薄路由 → 单一真源判定层。exit 0 = 判定层放行; 非 0 = DENY(已 emit IAB)/不可达 → fail-closed。
                    # if-form 捕获退出码 (set -e 在 if 条件内挂起, 非 0 不会提前退出脚本)。
                    _MCP_RC=127
                    if command -v uv >/dev/null 2>&1; then
                        if uv run --directory "$MCP_BASE" python -m towow.awareness.physical_gate_check \
                            --tool-name "$TOOL_NAME" --command "$MCP_PAYLOAD" --project-dir "$MCP_BASE" >&2; then
                            _MCP_RC=0
                        else
                            _MCP_RC=$?
                        fi
                    elif [ -x "$MCP_BASE/.venv/bin/python" ]; then
                        if "$MCP_BASE/.venv/bin/python" -m towow.awareness.physical_gate_check \
                            --tool-name "$TOOL_NAME" --command "$MCP_PAYLOAD" --project-dir "$MCP_BASE" >&2; then
                            _MCP_RC=0
                        else
                            _MCP_RC=$?
                        fi
                    fi
                    if [ "$_MCP_RC" != "0" ]; then
                        cat <<EOF >&2
✗ PreToolUse Guard 拒绝 (T-R06-4 MCP fail-closed 白名单 × 单一真源判定层, concept fail-closed-mcp-allowlist@v1):

harness 会话只放行本地 playwright; 白名单外的 MCP 工具被物理拒:
  $TOOL_NAME

判定走单一真源 DefaultPhysicalGate (physical_gate_check, rc=$_MCP_RC): DENY 已 emit canonical
IrreversibleActionBlocked 上看板; 非 0 = 判定层 DENY 或 python 不可达 → fail-closed 一律拒。

R06 实证: harness 会话无差别继承宿主机全部 MCP, 含 Gmail/Drive/Notion/Zapier 等写权限 SaaS —
一个研究会话理论上能往 owner 账户写东西, 此前只 prompt 层拦。本门把它变成物理 deny。

· 真需要某外部 MCP (owner 授权的一次性操作)? 联系 owner 放 admin-bypass.flag。
· 联网搜索/抓取请用 native WebSearch / WebFetch (不是 mcp__ 工具, 不受本门影响)。
EOF
                        exit 2
                    fi
                fi
            fi
        fi
        ;;
esac

# ── T-FIX-B4-06 (INCIDENT-TOWOW-WIPE-2026-06-10): Bash 删除命令指向账本/哨兵 → 物理拒 ──
#
# 事故: 2026-06-10 05:55:01, T-FIX-B5-03 dev agent 在 Bash 敲 'cd harness && rm -rf .towow',
# 把 6 天生产 canonical (seq 24614→~29850, 约 5200+ 事件) 整目录删掉 — 无任何门拦。
# safe_rmtree (T-FIX-INC-01) 只护 Python 代码路径, 拦不住 agent 直接敲 Bash —
# 本段是 Bash 形态的另一半物理门。
#
# 判据 (故意收紧到字样级, 宁可误拦 — 误拦的 agent 会换路径或上报, 丢账本不可逆):
#   删除形命令 (rm/rmdir/unlink, find … -delete, mv → /dev/null, git clean)
#   且命令文本含 ".towow" 或 ".ledger-root" (账本哨兵, T-FIX-INC-01) 字样 → exit 2。
#   · /tmp 下的 .towow (测试残留) 也拦 — 含 events.log / .ledger-root 哨兵的目录
#     碰都不能碰, "删掉重跑看会不会重建"实验正是事故病程。
#   · 不解析目标路径语义 (引号/变量/通配符展开后 hook 看不到结果, 语义解析必有洞,
#     字样匹配反而无洞可钻)。
#   · 位置: 必须先于 Case (a) 非 v3 透传 & 一切放行点 — cwd 在哪都拦, 锁/origin
#     不豁免本门 (账本删除不是"合法会话"该干的事)。
# 出口: owner 物理旗标 admin-bypass.flag (非空, 与 B4-02 同一旗标, 受控产生归 B4-05)
#   在场 → 放行 + stderr 留痕。候选位置: cwd/.towow、cwd/harness/.towow (子目录布局)、
#   工位主仓 (cwd 在 .towow/worktrees/ 内时)。
# 诚实边界 (fail-open 残面, 与 CC Bash 段同形态): jq 缺 / command 解析不出 → 本段
#   无从匹配, 跳过; 变量拼路径 (X=.towow; rm -rf "$X") 字样探不到 — 上下文层纪律
#   ("绝不 rm -rf 任何 .towow", exec workflow 全员) 是另一半, 两层互补。
if [ "$TOOL_NAME" = "Bash" ]; then
    B406_CMD=""
    if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
        B406_CMD="$(echo "$HOOK_EVENT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")"
    fi

    # ── placement finding (finding-liveness-tool-misplaced-agent-drifts-to-lagging-proxy):
    #    vitality-first 决策点 advisory (非阻塞 exit 0) ──
    # 判会话死活 / 要不要收割时, agent 反复伸手够 ps / 进程在不在 / state.json 这类【滞后代理】
    # 脑补死活 (R11 根 bug + 本 finding 实证连栽 ≥3 次)。问题不是 agent 记性差, 是【决策点上 vitality
    # 这个唯一对的工具没被暴露到第一眼】(owner: "上下文没暴露好才是问题本身")。这里在"动会话进程"
    # (kill/pkill/goal terminate/tmux kill) 这个决策点按【命令文本】触发把 vitality 点亮 —— 结构性
    # 决策点注入, 不靠 agent 记忆、不是被动 doc note (closure_contract 禁的就是再加一条被动 note)。
    # forcing 是最后手段 → 此处只 advisory 不拦 (exit 0 透传); 误匹配无害 (只多一行提示, 不拦正常 kill)。
    if [ -n "$B406_CMD" ] && { echo "$B406_CMD" | grep -qE '(^|[;&|({`/[:space:]\])(kill|pkill|killall)([[:space:]]|$)' || echo "$B406_CMD" | grep -qE 'goal[[:space:]]+terminate|tmux[[:space:]]+kill-'; }; then
        echo "⚠ PreToolUse Guard (vitality-first 提示, 非阻塞放行): 你要动会话/进程 (kill/pkill/goal terminate/tmux kill)。" >&2
        echo "  判会话死活别靠 ps / 进程在不在 / state.json / 文件 mtime 这类滞后信号猜 —— 先看真信号:" >&2
        echo "    uv run --directory harness python -m towow.cli.main vitality --session <short_id>" >&2
        echo "  裁决: alive_working=在干活别碰 / parked_resumable=该 resume 不起孪生 / stuck_waiting=该 escalate / dead=真死才可收割 / unknown=信号不足保守别动。误杀活会话=烧钱+脑裂 (R11 / placement finding 连栽 ≥3 次)。" >&2
    fi

    if [ -n "$B406_CMD" ] && { echo "$B406_CMD" | grep -qF -- '.towow' || echo "$B406_CMD" | grep -qF -- '.ledger-root'; }; then
        # 删除形判定 — rm/rmdir/unlink 词形 (前缀允许行首/分隔符/路径斜杠/反斜杠转义),
        # find -delete, mv → /dev/null, git clean (untracked 账本段正是 06-04 后 hot 段丢失形态)。
        B406_DELETION=0
        if echo "$B406_CMD" | grep -qE '(^|[;&|({`/[:space:]\])(rm|rmdir|unlink)([[:space:]]|$)'; then
            B406_DELETION=1
        elif echo "$B406_CMD" | grep -qE '(^|[[:space:]])-delete([[:space:]]|$)'; then
            B406_DELETION=1
        elif echo "$B406_CMD" | grep -qE '(^|[;&|({`/[:space:]\])mv[[:space:]]' && echo "$B406_CMD" | grep -qF -- '/dev/null'; then
            B406_DELETION=1
        elif echo "$B406_CMD" | grep -qE '(^|[;&|({`[:space:]\])git([[:space:]]+-[^[:space:]]+)*[[:space:]]+clean([[:space:]]|$)'; then
            B406_DELETION=1
        fi
        if [ "$B406_DELETION" = "1" ]; then
            B406_BYPASS=""
            for _b406_flag in ".towow/locks/admin-bypass.flag" "harness/.towow/locks/admin-bypass.flag"; do
                if [ -s "$_b406_flag" ]; then
                    B406_BYPASS="$_b406_flag"
                    break
                fi
            done
            if [ -z "$B406_BYPASS" ] && [ -n "$V01_MAIN_DIR" ] && [ -s "$V01_MAIN_DIR/.towow/locks/admin-bypass.flag" ]; then
                B406_BYPASS="$V01_MAIN_DIR/.towow/locks/admin-bypass.flag"
            fi
            if [ -n "$B406_BYPASS" ]; then
                echo "⚠ PreToolUse Guard (T-FIX-B4-06 账本删除门): Bash 删除形命令含 .towow/.ledger-root, 经 admin-bypass.flag 放行 (owner 物理旗标)。flag: $B406_BYPASS / 命令: $B406_CMD" >&2
            else
                cat <<EOF >&2
✗ PreToolUse Guard 拒绝 Bash (T-FIX-B4-06 账本/哨兵删除物理门):

检测到删除形命令且命令文本含 .towow 或 .ledger-root (账本哨兵):
  $B406_CMD

2026-06-10 曾有 dev agent 用 'cd harness && rm -rf .towow' 删掉 6 天生产 canonical
账本 (seq 24614→~29850, 约 5200+ 事件永久丢失) — 删除不可逆, 本门宁可误拦。

· 清理测试残留 / tmp 下的 .towow ? 不要删 — 改用只读检查 + 上报。任何含
  events.log 或 .ledger-root 哨兵的目录碰都不能碰, "删掉重跑看会不会重建"
  实验正是 06-10 事故的病程。
· 真需要删除 (owner 授权的一次性 admin 操作)? 联系 owner 放置
  .towow/locks/admin-bypass.flag (受控产生路径归 T-FIX-B4-05 CLI),
  旗标在场本门放行并留痕。
EOF
                exit 2
            fi
        fi
    fi

    # ── T-FIX-B4-07 (finding-shared-tree-git-stash-ledger-rollback-1): git 回滚形命令
    #    在 v3 环境内 → 物理拒 ──
    #
    # 事故: 2026-06-10 19:09, T-LC-02 执行会话在共享 live 树跑 `git stash && pytest &&
    # git stash pop` — stash 卷走 50 个 tracked 文件 (账本热段回滚 25980→24620 + 全部
    # graph 投影 + watermark), pop 撞 daemon 并发写冲突失败。同日另有 git restore 形态
    # 二次回退。B4-06 拦的是"删除形+路径字样"; 回滚形不同 — `git stash` 文本里根本
    # 不含 .towow (它卷全树), 所以本门按"环境"触发, 不按"路径字样"触发。
    #
    # 判据 (与 B4-06 同哲学: 字样级 / 不解析语义 / 宁可误拦 — 回滚撞并发写不可逆):
    #   回滚形 = git stash (全子命令, 含 list/show — live 树上没有合法 agent 用例,
    #            诊断属 owner admin 操作走旗标) / git restore (含 --staged, 同理误拦
    #            可受) / git reset --hard|--merge|--keep (碰工作树的模式; 裸 reset /
    #            --soft / --mixed 只动 index, 放行 — 解除暂存合法替代就是
    #            `git reset -- <path>`) / git checkout 带 ` -- ` 路径还原形 或
    #            standalone `.` pathspec 或 -f/--force (分支切换 `git checkout <br>`
    #            不拦 — 工位常规操作)。
    #   环境 = cwd 可达 .towow (./.towow 或 ./harness/.towow) — 共享 live 树主形态;
    #          或 cwd 在隔离 worktree 内 (.towow/worktrees|.claude/worktrees — 工位
    #          .towow 是回流主账本的 symlink, git 还原 .towow/ 路径会穿透 symlink
    #          写进主账本); 或命令文本含 .towow/.ledger-root (git -C 远程指向形态)。
    # 出口: admin-bypass.flag (B4-06 同旗标同候选位置) → 放行 + stderr 留痕。
    # 诚实边界: jq 缺/解析不出 → 跳过 (与 B4-06 同形态); 变量拼命令字样探不到;
    #   git switch --discard-changes 等罕见词形未列 (上下文层纪律是另一半, 两层互补);
    #   非 v3 环境 (无 .towow 可达且命令不含字样) 不拦 — 回滚别人的普通 repo 不归本门管。
    # 干净测试基线的合法替代 (stderr 也教): git worktree add <tmp> HEAD 隔离副本跑测试。
    if [ -n "$B406_CMD" ]; then
        B407_ROLLBACK=0
        # git 前缀: 允许 -C <dir> / -c <k=v> 带参数对 (git -C harness stash 是真实高频
        # 形态, 不盖会漏) + 任意 -flag。其余 git 全局选项词形未列 = 诚实边界。
        _B407_GIT_PREFIX='(^|[;&|({`[:space:]\])git([[:space:]]+-[cC][[:space:]]+[^[:space:]]+|[[:space:]]+-[^[:space:]]+)*[[:space:]]+'
        # 词尾界: 与前缀字符类镜像对称 — 闭侧分隔符 ;&|)}` 与空白/行尾同等收。
        # 后界只认空白/行尾时, 回滚词/标志位紧跟分隔符的自然习语全逃逸:
        # 'git stash; pytest' / '(git stash)' / '{ git stash; }' / 'git reset --hard;'
        # / '(git checkout .)' / 'git checkout -f;' — 与事故等价, stash 真执行=账本
        # 真回滚 (finding-b407-rollback-gate-word-boundary-escape-1, 四分支全须用它)。
        _B407_WORD_END='([[:space:];&|)}`]|$)'
        if echo "$B406_CMD" | grep -qE "${_B407_GIT_PREFIX}stash${_B407_WORD_END}"; then
            B407_ROLLBACK=1
        elif echo "$B406_CMD" | grep -qE "${_B407_GIT_PREFIX}restore${_B407_WORD_END}"; then
            B407_ROLLBACK=1
        elif echo "$B406_CMD" | grep -qE "${_B407_GIT_PREFIX}reset${_B407_WORD_END}" \
            && echo "$B406_CMD" | grep -qE "(^|[[:space:]])--(hard|merge|keep)${_B407_WORD_END}"; then
            B407_ROLLBACK=1
        elif echo "$B406_CMD" | grep -qE "${_B407_GIT_PREFIX}checkout${_B407_WORD_END}"; then
            # owner 2026-06-22 无分支隔离: 共享主树切分支=把工作树从别的并发会话脚下抽走,
            # 不再区分参数 — git checkout 子命令词命中即拦 (路径还原形 / 切分支 / 建分支全拦)。
            # _B407_GIT_PREFIX 锚定 checkout 在 git 子命令位, 故 git worktree add <含 checkout 字样路径>
            # 不被误伤 (worktree 才是子命令词)。
            B407_ROLLBACK=1
        elif echo "$B406_CMD" | grep -qE "${_B407_GIT_PREFIX}switch${_B407_WORD_END}"; then
            # git switch <分支> / switch -c <新分支> / switch --detach — 与 checkout 同灾难面,
            # 同样子命令词命中即拦; git worktree add 含 switch 字样不误伤 (同上锚定理由)。
            B407_ROLLBACK=1
        fi
        if [ "$B407_ROLLBACK" = "1" ]; then
            B407_CTX=0
            if [ -d ".towow" ] || [ -d "harness/.towow" ]; then
                B407_CTX=1
            elif echo "$CWD_FOR_CC" | grep -qE '\.towow/worktrees/|\.claude/worktrees/'; then
                B407_CTX=1
            elif echo "$B406_CMD" | grep -qF -- '.towow' || echo "$B406_CMD" | grep -qF -- '.ledger-root'; then
                B407_CTX=1
            elif command_targets_active_project; then
                # T-GIT-DISCIPLINE 修改点5 (2026-07-11): 堵 cwd=/tmp 远程指向主树的绕过
                B407_CTX=1
            fi
            if [ "$B407_CTX" = "1" ]; then
                B407_BYPASS=""
                for _b407_flag in ".towow/locks/admin-bypass.flag" "harness/.towow/locks/admin-bypass.flag"; do
                    if [ -s "$_b407_flag" ]; then
                        B407_BYPASS="$_b407_flag"
                        break
                    fi
                done
                if [ -z "$B407_BYPASS" ] && [ -n "$V01_MAIN_DIR" ] && [ -s "$V01_MAIN_DIR/.towow/locks/admin-bypass.flag" ]; then
                    B407_BYPASS="$V01_MAIN_DIR/.towow/locks/admin-bypass.flag"
                fi
                if [ -n "$B407_BYPASS" ]; then
                    echo "⚠ PreToolUse Guard (T-FIX-B4-07 git 回滚门): v3 环境内 git 回滚形命令, 经 admin-bypass.flag 放行 (owner 物理旗标)。flag: $B407_BYPASS / 命令: $B406_CMD" >&2
                else
                    cat <<EOF >&2
✗ PreToolUse Guard 拒绝 Bash (T-FIX-B4-07 git 回滚物理门):

检测到 git 回滚/切分支形命令 (stash / restore / reset --hard / checkout / switch) 且当前
环境可达 v3 账本 (.towow 是 git tracked 运行态; 回滚=把账本/投影/水位线砸回 HEAD,
切分支=把工作树从别的并发会话脚下换走):
  $B406_CMD

2026-06-10 曾有执行会话在共享 live 树跑 'git stash && pytest && git stash pop' —
stash 卷走 50 个文件 (账本热段回滚 25980→24620 + 全部投影 + 自己刚完成的活),
pop 撞 daemon 并发写冲突失败。同日另有 git restore 形态二次回退。

· 要干净测试基线? 用隔离副本, 别回滚共享树:
    git worktree add /tmp/clean-test-\$\$ HEAD && (cd /tmp/clean-test-\$\$ && <测试>)
· 要解除暂存 (unstage)? 用只动 index 的形态: git reset -- <path> (本门放行)。
· 要丢弃自己改坏的某个文件? 重新 Edit 回去, 不要 git restore (它同样砸 tracked 运行态)。
· 要并发隔离干活? 用无分支隔离工位 (git worktree add 独立目录), 绝不 checkout/switch 切分支 —
  单机多会话共享树切分支会把工作树从别的会话脚下抽走 (owner 红线: 六并发六分支=切乱代码)。
· 真需要回滚 (owner 授权的恢复手术)? 联系 owner 放置
  .towow/locks/admin-bypass.flag (受控产生路径归 T-FIX-B4-05 CLI),
  旗标在场本门放行并留痕。
EOF
                    exit 2
                fi
            fi
        fi
    fi

    # ── T-GIT-DISCIPLINE-P1 (owner 死命令 2026-07-11, GIT-DISCIPLINE-AUDIT §5 行为层):
    #    v3 环境禁 gh pr create / git push 任务分支 —— 共享单主干工作流, 不提 PR、不 push
    #    任务分支; 回流走 worktree promote 串行 apply。main/命名主干的 push 放行。──
    if echo "$B406_CMD" | grep -qE '\bgh\b.*\bpr\b +create|\bgit\b.*\bpush\b'; then
        P1_CTX=0
        if [ -d ".towow" ] || [ -d "harness/.towow" ]; then P1_CTX=1
        elif command_targets_active_project; then P1_CTX=1
        fi
        if [ "$P1_CTX" = "1" ]; then
            P1_TRUNK=""
            [ -f "harness/.towow/trunk-branch" ] && P1_TRUNK="$(cat harness/.towow/trunk-branch 2>/dev/null)"
            P1_OK=0
            if echo "$B406_CMD" | grep -qE '\bgh\b.*\bpr\b +create'; then
                P1_OK=0  # gh pr create 一律拦
            elif echo "$B406_CMD" | grep -qE "\bpush\b[^|;&]*\b(main|${P1_TRUNK:-__none__})\b"; then
                P1_OK=1  # push main / 命名主干 放行
            fi
            P1_BYPASS=""
            for _p1_flag in ".towow/locks/admin-bypass.flag" "harness/.towow/locks/admin-bypass.flag"; do
                [ -s "$_p1_flag" ] && P1_BYPASS="$_p1_flag"
            done
            if [ "$P1_OK" = "0" ] && [ -z "$P1_BYPASS" ]; then
                cat >&2 <<P1EOF
✗ PreToolUse Guard 拒绝 Bash (T-GIT-DISCIPLINE-P1 单主干工作流门):

检测到 gh pr create / git push 非主干分支, 且当前环境可达 v3 账本:
  $B406_CMD

共享单主干工作流 (owner 死命令 2026-07-11): 不提 PR、不 push 任务分支。
· 工位产出回流走 promote 机制 (orchestrator promote-completed / auto-promote), 不走 PR。
· push main 或命名主干 (harness/.towow/trunk-branch) 本门放行。
· 真需要例外? owner 放置 admin-bypass.flag (T-FIX-B4-05 CLI), 旗标在场放行留痕。
P1EOF
                exit 2
            fi
        fi
    fi

    # ── T-BRANCH-GUARD-01 (owner 死命令 2026-07-09, finding-shared-tree-branch-create-divergence-1):
    #    共享主树上禁止【建新分支】(`git branch <新名>`) ──
    #
    # owner 观察: 任务经常自说自话在共享主树 `git checkout -b` / `git switch -c` 开新分支导致
    # 本地分叉。那两个形态其实已经被上面 B4-07 拦住了 —— B4-07 对 checkout/switch 子命令词是
    # "命中即拦、不再区分参数"(owner 2026-06-22 决策, 见 test_rollback_shapes_blocked_in_v3_cwd
    # 里 "git checkout -b feature-x" / "git switch -c feature-x" 两条锚测试), 本门不重复拦它们。
    # 真正的缺口是【裸】`git branch <新名>` —— 不切换 HEAD、只建一个新分支引用, B4-07 完全不认
    # branch 这个子命令词, 现状可以毫无阻力地在共享树上生出一个孤悬分支。本门只补这一个缺口。
    #
    # 范围 (owner 措辞: "共享主树上不许随意换/建分支"; worktree 内允许任意 git):
    #   只在【共享主树】拦 —— cwd 落在 .towow/worktrees/ 或 .claude/worktrees/ (工位隔离目录)
    #   内则完全不拦: 工位是 detached HEAD 的独立 git checkout (WorktreeManager, M-3.1 §5),
    #   工位内部怎么摆弄分支不影响其它并发会话共享的那棵主树, 且工位改动回流走串行
    #   git-apply、不是分支 merge (patch-serial-reflow@v1) —— 分支状态压根不参与 reflow。
    #
    # 判据 (同 B4-07 风格: 字样级 / 不解析真实 git 语法 / 宁可误拦):
    #   branch 子命令词命中 (复用 B4-07 已解过 word-boundary-escape 的
    #   _B407_GIT_PREFIX/_B407_WORD_END, 不重新发明一套正则) 且排除已知"非建新分支"形态后,
    #   "branch" 后仍跟着实质参数 → 判定为建分支形:
    #     - 命中 -d/-D/--delete (删除) / -m/-M/--move (改名) /
    #       -a/-r/-v/-vv/--list/--show-current/--contains/--merged/--no-merged/--points-at
    #       (纯查询列举) 任一 → 不算"建", 放行 (清理孤悬分支是好事, 改名/列举也不是
    #       owner 担心的"凭空开新岔路")
    #     - 否则若 "branch" 后还有非分隔符的实质字符 (即带了参数) → 建分支形, 拦
    #     - 裸 `git branch`(无参数, 纯列举全部分支) → 放行
    #   ⚠️ 故意不排除 -c/-C/--copy (branch 子命令自己的"复制"flag): 它们的字母跟 git 【全局】
    #   flag `-c <k=v>` / `-C <dir>` (在 branch 词之前出现, 如 `git -C harness branch foo`)
    #   完全同形 —— 本门是纯字样匹配、不分辨某个 flag 出现在"branch 词之前(全局)"还是"之后
    #   (子命令自己的)", 若把 -c/-C 也列进排除表, `git -C harness branch foo` 这种【最常见】
    #   的建分支形态 (从仓库根用 -C 指向 harness/ 子目录建分支) 会被误判成"复制"而放行 ——
    #   这是本门抓的实证 bug (test_branch_create_blocked_in_shared_tree 的
    #   "git -C harness branch feature-x" 用例), 宁可误拦 branch 自身的复制形(-c/-C 使用频率
    #   远低于建分支), 也不留下这个漏洞。
    #   注意: _B407_GIT_PREFIX/_B407_WORD_END 由上面 B4-07 块 (同样以 `[ -n "$B406_CMD" ]`
    #   为门槛、且文本顺序在本块之前) 无条件赋值, 本块复用它们前提是二者已跑过 —— 两块共享同一
    #   $B406_CMD 门槛, 顺序耦合是有意为之, 不是意外依赖。
    # 出口: admin-bypass.flag (同 B4-06/07 旗标) → 放行 + stderr 留痕。
    # 诚实边界: -m (改名) 严格说也生出一个新分支名, 本门先只锁最直白的"裸建"形; 若后续发现
    #   rename 也造成本地分叉, 属范围扩大, 不在本次决策内 (owner 只点名 "checkout -b /
    #   switch -c / branch <新名>" 三种形态)。裸 `-v`/`-a` 若出现在 branch 词【之前】
    #   (git 全局位置, 罕见组合) 同样可能被误判非建 — 与 -c/-C 同类已知边界, 概率远低于
    #   -c/-C 的高频 `-C <子目录>` 组合, 暂不处理。
    if [ -n "$B406_CMD" ] && ! echo "$CWD_FOR_CC" | grep -qE '\.claude/worktrees/|\.towow/worktrees/'; then
        B408_IN_SHARED_TREE=0
        if [ -d ".towow" ] || [ -d "harness/.towow" ]; then
            B408_IN_SHARED_TREE=1
        elif echo "$B406_CMD" | grep -qF -- '.towow' || echo "$B406_CMD" | grep -qF -- '.ledger-root'; then
            B408_IN_SHARED_TREE=1
        fi
        if [ "$B408_IN_SHARED_TREE" = "1" ] && echo "$B406_CMD" | grep -qE "${_B407_GIT_PREFIX}branch${_B407_WORD_END}"; then
            B408_CREATE=0
            if ! echo "$B406_CMD" | grep -qE "(^|[[:space:]])(-d|-D|--delete|-m|-M|--move|-a|-r|-v|-vv|--list|--show-current|--contains|--merged|--no-merged|--points-at)${_B407_WORD_END}"; then
                if echo "$B406_CMD" | grep -qE "${_B407_GIT_PREFIX}branch[[:space:]]+[^[:space:];&|)}\`]"; then
                    B408_CREATE=1
                fi
            fi
            if [ "$B408_CREATE" = "1" ]; then
                B408_BYPASS=""
                for _b408_flag in ".towow/locks/admin-bypass.flag" "harness/.towow/locks/admin-bypass.flag"; do
                    if [ -s "$_b408_flag" ]; then
                        B408_BYPASS="$_b408_flag"
                        break
                    fi
                done
                if [ -z "$B408_BYPASS" ] && [ -n "$V01_MAIN_DIR" ] && [ -s "$V01_MAIN_DIR/.towow/locks/admin-bypass.flag" ]; then
                    B408_BYPASS="$V01_MAIN_DIR/.towow/locks/admin-bypass.flag"
                fi
                if [ -n "$B408_BYPASS" ]; then
                    echo "⚠ PreToolUse Guard (T-BRANCH-GUARD-01 分支建门): 共享主树内建新分支, 经 admin-bypass.flag 放行 (owner 物理旗标)。flag: $B408_BYPASS / 命令: $B406_CMD" >&2
                else
                    cat <<EOF >&2
✗ PreToolUse Guard 拒绝 Bash (T-BRANCH-GUARD-01 分支建物理门, owner 死命令 2026-07-09):

检测到在共享主树上【建新分支】:
  $B406_CMD

工位分支只能由 worktree 机制统一创建 (detached HEAD 隔离工位, M-3.1 §5) —— 共享主树
上任务各自 git checkout -b / git branch 建分支, 会让本地仓库悄悄分叉 (owner 实测复发)。
checkout -b / switch -c 已被上面的 B4-07 门拦住, 本门补的是裸 \`git branch <新名>\` 这个缺口。

· 需要隔离工位并发干活? 用 EnterWorktree 工具, 或 CLI \`towow worktree create\` /
  \`git worktree add\` 建独立 detached 目录 —— 绝不在共享主树上 checkout/switch/branch。
· 只是想看现有分支列表? \`git branch\`(裸, 不带参数) 本门放行, 不受影响。
· 真需要在共享主树上建分支 (owner 授权的一次性操作)? 联系 owner 放置
  .towow/locks/admin-bypass.flag (受控产生路径归 T-FIX-B4-05 CLI),
  旗标在场本门放行并留痕。
EOF
                    exit 2
                fi
            fi
        fi
    fi
fi

# B1 (PARALLEL-EXEC-FIX): 触发条件扩到 .towow/worktrees/ — daemon 派的隔离执行工位
# (per-task git worktree, .towow 经 symlink 回流主账本) 同样要求 --project-dir 显式回指。
if echo "$CWD_FOR_CC" | grep -qE '\.claude/worktrees/|\.towow/worktrees/'; then
    if echo "$CWD_FOR_CC" | grep -q '\.claude/worktrees/'; then
        MAIN_REPO_GUESS="${CWD_FOR_CC%%/.claude/worktrees/*}"
        PROJECT_DIR_GUESS="$MAIN_REPO_GUESS/harness"
    else
        # .towow/worktrees/<工位>: 截到 /.towow/worktrees/ 之前 = v3 项目根本体
        MAIN_REPO_GUESS="${CWD_FOR_CC%%/.towow/worktrees/*}"
        PROJECT_DIR_GUESS="$MAIN_REPO_GUESS"
    fi
    case "$TOOL_NAME" in
        Write|Edit|MultiEdit|NotebookEdit)
            FILE_PATH=""
            if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
                FILE_PATH="$(echo "$HOOK_EVENT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || echo "")"
            fi
            if [ -n "$FILE_PATH" ] && echo "$FILE_PATH" | grep -qE "(^|/)\.towow(/|$)"; then
                # T-FIX-B4-03: 工位内部文件豁免 — 工位本身住在 .towow/worktrees/<wid>/ 下,
                # 工位内文件的绝对路径必然含 ".towow" 段, 会被本段误拒 (isolation=on 下
                # 所有绝对路径写全灭 = V-01 门永远到不了)。剥掉工位根前缀后若不再含
                # .towow 段 → 不是账本内部, 放过本段、交给下方 V-01 段判 write_set。
                # 工位内的 .towow (symlink 回流主账本) 剥前缀后仍含 .towow 段 → 照拒。
                V01_CC_EXEMPT=0
                if [ -n "$V01_TASK_ID" ]; then
                    _v01_ws_root="$V01_MAIN_DIR/.towow/worktrees/$V01_TASK_ID"
                    case "$FILE_PATH" in
                        "$_v01_ws_root"/*)
                            _v01_tail="${FILE_PATH#"$_v01_ws_root"/}"
                            if ! echo "/$_v01_tail" | grep -qE "/\.towow(/|$)"; then
                                V01_CC_EXEMPT=1
                            fi
                            ;;
                    esac
                fi
                if [ "$V01_CC_EXEMPT" = "1" ]; then
                    :  # 工位内部文件 → 不归本段管, 落到 V-01 段
                else
                cat <<EOF >&2
✗ PreToolUse Guard 拒绝 Write/Edit (DOGFOOD-001-CC enforcement):

你 cwd ($CWD_FOR_CC) 在隔离 worktree (.claude/worktrees/ = daemon 偶发自创;
.towow/worktrees/ = 执行工位, 其 .towow 应是回流主账本的 symlink). 你不该在这里
init .towow / 写 .towow/ 内文件 — 否则你 emit 的事件进独立日志, 主对话完全看
不到 (V-04 单源破裂).

要写 v3 事件: 用 v3 CLI 命令 + --project-dir=$PROJECT_DIR_GUESS
(假设主仓库 v3 在 harness/ 子目录, 否则向上找到不含 .claude/worktrees 的层).

代码 / git 改动可以在 cwd 做 (worktree 隔离的本意是 git branch 隔离).

详 docs/agent-behavior-enforcement-pattern.md.
EOF
                exit 2
                fi
            fi
            ;;
        Bash)
            COMMAND=""
            if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
                COMMAND="$(echo "$HOOK_EVENT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")"
            fi
            # ── DOGFOOD-001-CC over-match 收窄 (finding f-dogfood-001-cc-guard-regex-overmatch-sibling): ──
            # 病灶: 早期正则前置分隔符类含裸空格 [[:space:]], 于是 worktree cwd 下任何命令只要文本里
            # 出现"空白+towow+空白"或"python…-m towow"子串(且命令无 --project-dir 子串)就被判成"未带
            # --project-dir 的真调用"而 exit 2 —— 不区分真调用还是引号内/注释里的文本。实锤: echo
            # "…python -m towow.cli.main…" 与 git commit -m "…towow 调用…" 被误拦, 反挡住维护者在
            # worktree 里提交·记录·文档化任何顺带提到 towow 调用字样的文字, 与本门"只拦真调用"的本意相反。
            # 修法与 T-TWU-C 同源(运行器感知三向量): 只认三种真实调用向量, 每个都把【运行器词】锚在
            # 命令段起始位(行首 / ; & | ( { ` 之后, 可跟空白; 【不含裸空格】), 引号内/prose 里的
            # towow/python 因不在命令段起始位而放行。三向量:
            #   (A) 裸 towow 作命令词 —— towow 处命令段起始位。
            #   (B) uv 运行器包裹 —— `uv … (towow | -m towow)`(uv 处命令段起始位; 覆盖
            #       `uv run --directory harness python -m towow.cli.main …` 与 `uv run … towow …`)。
            #   (C) python -m 模块 —— python 处命令段起始位, 后接 `-m towow[.cli.main]`。
            # 诚实残余(已知、可接受): 若引号里【逐字照抄】一条完整 `uv … -m towow…` 老形态, 仍会命中
            #   (规避: 断词/换标点); 变量拼出的运行器(X=towow; $X …)探不到。本段只做字样匹配, 不解析
            #   完整 shell 语法。narrowing 前"python 在命令中段(非段起始, 无 uv 包裹)"的罕见形态不再拦
            #   —— 与 T-TWU-C 同样的运行器锚定取舍, 换回"仅提及"不再误伤 commit/echo/注释。
            _DOGFOOD_SEG='(^|[;&|({`])[[:space:]]*'
            if [ -n "$COMMAND" ] && {
                     echo "$COMMAND" | grep -qE "${_DOGFOOD_SEG}towow[[:space:]]" \
                  || echo "$COMMAND" | grep -qE "${_DOGFOOD_SEG}uv[[:space:]].*[[:space:]](towow[[:space:]]|-m[[:space:]]+towow)" \
                  || echo "$COMMAND" | grep -qE "${_DOGFOOD_SEG}python[^[:space:]]*[[:space:]]+(-m[[:space:]]+towow|.*towow\.cli\.main)"; \
               } && ! echo "$COMMAND" | grep -q '\-\-project\-dir'; then
                cat <<EOF >&2
✗ PreToolUse Guard 拒绝 Bash (DOGFOOD-001-CC enforcement):

你 cwd ($CWD_FOR_CC) 在隔离 worktree (.claude/worktrees/ daemon 隔离, 或
.towow/worktrees/ 执行工位). v3 CLI 必须显式 --project-dir 指向主仓库, 否则会在你独立 worktree init 独立
.towow, 事件进不了主日志.

修改命令为:
  <你的命令> --project-dir=$PROJECT_DIR_GUESS

(假设主仓库 v3 在 harness/ 子目录, 否则向上找到不含 .claude/worktrees 的层).
详 docs/agent-behavior-enforcement-pattern.md.
EOF
                exit 2
            fi
            ;;
    esac
fi

# ── T-TWU-C (老形态调用安全网, tw-mechanism-modifier-obligation@v1 第6条 / tw-unified-
#    invocation-entry@v1 (D)): --project-dir 焊错位置的老形态命令, 在 click 硬崩前拦住 ──
#
# 病灶: towow CLI 的 --project-dir 是每个【叶子子命令】各自声明的选项 (work start /
# vitality / plan freeze ...), 顶层 `towow` group 本身不接受它。把 --project-dir 焊在
# `towow` 词与子命令之间 (`towow --project-dir=X work start ...`) 在任何 cwd 都 100%
# 触发 click "Error: No such option '--project-dir'." —— 曾经不是猜的边界情况: 仓库根
# `./tw` 启动器早期在 worktree 内自动注入 --project-dir 时, 把它焊在 `towow` 词
# 紧后面 (`towow --project-dir "$PROJECT_DIR" "$@"`), 生成过这个必崩形态 —— 该行为已由
# fix-tw-wrapper-project-dir-misplaced-1 修复(现改为追加在完整子命令之后, 不再生成错位
# 形态); 但手敲 `towow --project-dir=X <子命令>` 的老习惯仍会触发。上面 DOGFOOD-001-CC 段只判
# "命令文本里有没有 --project-dir 这个子串"(worktree cwd 专属), 焊错位置时子串确实
# 存在, 该段会放行 —— 命令直接闯到 click 解析层硬崩, 是一条不受任何门保护的缺口。
#
# 本段补这个缺口: 不按 cwd 收窄 (焊错位置的崩溃与 cwd 无关, 任何地方敲都崩)。判定的核心
# 是"towow 是否真的在被调用" —— 只认三种真实调用向量, 命中即代表这条命令注定在 click 顶层
# group 解析处失败, 在失败发生前给出【正确路由】(挪到子命令之后的改法), 而不是让它裸奔进
# 一条无上下文的 click UsageError (退而不废: 老形态不是被放弃在报错里等死, 是被引导到一条
# 真能跑到 tw 内核的路径)。三向量:
#   (A) 裸 towow 作命令词 —— towow 紧接 --project-dir, 且 towow 处在【命令段起始位】。
#   (B) uv 运行器包裹 —— `uv … towow --project-dir` (./tw 早期在 worktree 内生成的确切形态)。
#   (C) python -m 模块 —— `python … -m towow[.cli.main] --project-dir`。
#
# 诚实边界 (over-match 收窄, finding f-twu-c-oldform-safety-net-regex-overmatch):
# (A) 的"命令段起始位"只认 行首 / ; & | ( { ` 之后 (可跟空白), 【不含裸空格】—— 早期版本把
# 裸空格也当前置分隔符, 于是任何 Bash 命令里只要出现"空格+towow+空白+--project-dir"子串就被
# 拦, 连 git commit -m "fix towow --project-dir crash" / echo "…towow --project-dir…" / 含该
# 子串的注释都误杀, 反挡住维护者提交·记录·文档化这个 bug 本身。收窄后这些"仅提及"不再触发。
# (B)(C) 靠 uv / python-m 运行器字样 + towow 紧邻 --project-dir 精确定位真调用, 引号里的
# git commit -m "…" / echo "…" 不含这些运行器字样(或 -m 后紧跟的是引号不是 towow), 故放行。
# 仍存的残余 over-match (已知、可接受): 命令若在引号里【逐字照抄】一条完整 uv/python 老形态
# 调用 (如 git commit -m "…uv run … towow --project-dir…"), 仍会命中 —— 规避法: 让引号内的
# towow 与 --project-dir 不紧邻 (断词 / 换标点)。本段只做字样匹配, 不解析完整 shell 语法/变量
# 展开; --project-dir 出现在子命令之后 (合法位置) 不触发本段。
if [ "$TOOL_NAME" = "Bash" ]; then
    OLDFORM_CMD=""
    if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
        OLDFORM_CMD="$(echo "$HOOK_EVENT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")"
    fi
    # 命令段起始位前缀 (行首 / ; & | ( { ` 之后, 可跟空白; 【不含裸空格】—— 见上"诚实边界")。
    _OLDFORM_SEG='(^|[;&|({`])[[:space:]]*'
    if [ -n "$OLDFORM_CMD" ] && {
              echo "$OLDFORM_CMD" | grep -qE "${_OLDFORM_SEG}towow[[:space:]]+--project-dir" \
           || echo "$OLDFORM_CMD" | grep -qE "${_OLDFORM_SEG}uv[[:space:]].*[[:space:]]towow[[:space:]]+--project-dir" \
           || echo "$OLDFORM_CMD" | grep -qE '(^|[[:space:]])-m[[:space:]]+towow(\.cli\.main)?[[:space:]]+--project-dir'; }; then
        cat <<EOF >&2
✗ PreToolUse Guard 拒绝 Bash (T-TWU-C 老形态安全网: --project-dir 位置错, 必崩形态):

检测到 --project-dir 紧跟在 towow 之后(顶层子命令之前):
  $OLDFORM_CMD

towow 的 --project-dir 是【叶子子命令】的选项(work start / vitality / plan freeze 等
各自声明), 不是顶层 group 选项 —— 焊在 towow 与子命令之间在任何 cwd 都 100% 触发 click
"Error: No such option '--project-dir'." 硬崩, 到不了 tw 内核。

改法: 把 --project-dir 挪到【子命令与其参数之后】, 例如:
  towow work start TASK_ID --project-dir=<项目根>
  (不是 towow --project-dir=<项目根> work start TASK_ID)

注意 —— ./tw 脚本早期在 worktree cwd 内自动注入 --project-dir 时曾生成过这个错位形态,
该 bug 已由 fix-tw-wrapper-project-dir-misplaced-1 修复(现改为追加在完整子命令之后), 用
./tw 已不会再生成必崩形态; 本门拦的是你手敲的老形态命令。它在到达 100% 复现的 click
崩溃前拦住, 给出真能跑通的形态。
EOF
        exit 2
    fi
fi

# 定位 v3 root: cwd 自身或 cwd/harness 子目录（Harness/ 顶层 cwd + harness/ 子项目 layout）.
# 这一步在 hook 上移到 Harness/.claude/ 顶层后必需 — 否则 cwd 无 .towow 会 fall through 到
# 透传, Guard 物理拦截失效.
V3_ROOT=""
if [ -d ".towow" ]; then
    V3_ROOT="."
elif [ -d "harness/.towow" ]; then
    V3_ROOT="harness"
fi

# Case (a): 非 v3 repo → 透传（非 v3 项目本就不归 Guard 管）
# T-FIX-B4-03 例外: cwd 在工位深处时 cwd 探不到 .towow (工位子目录没有 symlink),
# 但工位写仍归 V-01 门管 — 主仓根就在工位路径前缀里, 锚定它继续走门, 不透传绕过。
if [ -z "$V3_ROOT" ]; then
    if [ -n "$V01_TASK_ID" ] && [ "$IS_WRITE_TOOL" = "1" ]; then
        V3_ROOT="$V01_MAIN_DIR"
    else
        exit 0
    fi
fi

# ── T-LRG-B1 (concept ledger-read-static-guardrail@v1): 账本全量读静态护栏 · 内容级拦 ──
#
# 新增一处**无豁免**的账本全量读公开符号 all_records() 在写入瞬间被拒 —— v1 只拦公开符号
# all_records() 的新增未豁免调用 (owner nj-9367a728; 无界 get_events_in_range 有边界参数、静态判
# 不出无界用法, 不进 v1 静态拦, 靠 inventory 清单 + 运行时探针兜)。
#
# 判定与豁免正则的**单一真源**在 python 判定层 towow.shell.ledger_read_guard (镜像 T-RMD-S4-
# PHYS-MATCHER 薄路由): bash 只做最便宜的字样预筛 (tool_input 含 all_records 字样才路由), 精确的
# exemption-aware 判定 + canonical OwnerGuardViolation(reason=ledger-read-static-guardrail@v1)
# emit + 三段拒绝消息 (概念id/替代API/豁免语法全文) 全在 python —— 层1 hook 与层2 守护测试导入同一
# 条正则, 逐字节一致由"只有一份定义"自动保证 (比在 bash 复刻一份更满足 nj-8cfdb4f6, 不会漂移)。
#
# 拒绝: python rc=2 → exit 2 (已 emit + 打印三段消息); rc=0 → 放行 (无命中 / 已豁免)。
# 出口: admin-bypass.flag (与 B4-06/07 同旗标) 在场 → 跳过本护栏 (owner 授权高于一切)。
# 诚实边界: jq 缺 / 抽不出 tool_input → 预筛探不到, 跳过 (与既有内容级门同形态; 上下文层纪律互补)。
# python 解析: 优先 $LRG_BASE/.venv/bin/python (无需 pyproject, 直接可调) → harness/.venv → uv run。
if [ "$IS_WRITE_TOOL" = "1" ]; then
    LRG_BASE=""
    if [ -d ".towow" ]; then
        LRG_BASE="$PWD"
    elif [ -d "harness/.towow" ]; then
        LRG_BASE="$PWD/harness"
    elif [ -n "$V01_MAIN_DIR" ] && [ -d "$V01_MAIN_DIR/.towow" ]; then
        LRG_BASE="$V01_MAIN_DIR"
    fi
    if [ -n "$LRG_BASE" ] && [ ! -s "$LRG_BASE/.towow/locks/admin-bypass.flag" ]; then
        # cheap 字样预筛: 本次 tool_input 是否**可能**含账本全量读调用字样 (命中才拉起 python)。
        LRG_INPUT=""
        if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
            LRG_INPUT="$(echo "$HOOK_EVENT" | jq -c '.tool_input // {}' 2>/dev/null || echo '{}')"
        fi
        if [ -n "$LRG_INPUT" ] && echo "$LRG_INPUT" | grep -qF -- 'all_records'; then
            # 薄路由 → 单一真源判定层。stdin 传整个 hook event (python 精确抽新增文本 + exemption 判定)。
            _LRG_RC=0
            if [ -x "$LRG_BASE/.venv/bin/python" ]; then
                if echo "$HOOK_EVENT" | "$LRG_BASE/.venv/bin/python" -m towow.shell.ledger_read_guard --project-dir "$LRG_BASE" >&2; then
                    _LRG_RC=0
                else
                    _LRG_RC=$?
                fi
            elif [ -x "$LRG_BASE/harness/.venv/bin/python" ]; then
                if echo "$HOOK_EVENT" | "$LRG_BASE/harness/.venv/bin/python" -m towow.shell.ledger_read_guard --project-dir "$LRG_BASE" >&2; then
                    _LRG_RC=0
                else
                    _LRG_RC=$?
                fi
            elif command -v uv >/dev/null 2>&1; then
                if echo "$HOOK_EVENT" | uv run --directory "$LRG_BASE" python -m towow.shell.ledger_read_guard --project-dir "$LRG_BASE" >&2; then
                    _LRG_RC=0
                else
                    _LRG_RC=$?
                fi
            fi
            if [ "$_LRG_RC" != "0" ]; then
                exit 2
            fi
        fi
    fi
fi

# ── T-FIX-B4-03 (USABILITY-walk#5 / PARALLEL-locks#2): V-01 越界写物理拦截段 ────
#
# 把『agent 自觉跑 guard-check』升级为『工具层自动调, 绕不过』。判定就地做 (分布式
# 自愈, 无中心总控), 单一真相源 = 既有 canonical 原语 `towow worktree guard-check`
# (shell/worktree.py enforce_owner_guard): 越界真 emit OwnerGuardViolation + 非零退出。
#
# 二分诚实边界 (INV-B4-2 / 设计裁决, 别把 built 洗成 enforced):
#   有 .owner (isolation=on 工位 / 显式声明 worktree) → 真物理门: 写前必过 guard-check,
#     越界 exit 2 + canonical 留痕; 通过 ≠ 免锁 — 继续走采访入口门 (B4-02)。
#   无 .owner (isolation=off 默认态 / 无工位声明) → 不 fail-open 放行任意写 (那正是
#     PARALLEL-locks#2 的洞 — 仍受采访入口门约束), 但也不假装强制: stderr 诚实标注
#     『V-01 文件级边界对本会话不强制』+ 获得物理边界的条件。不调 CLI (no_owner_file
#     会向 canonical 灌 violation, 每次正常写都污染账本)。
# fail-closed: .owner 在但 file_path 解析不出 / actor 不可判 / towow bin 解析不到 /
#   guard-check 调用出错 → 一律 exit 2, 不静默放行。
if [ "$IS_WRITE_TOOL" = "1" ]; then
    # file path 提取 (NotebookEdit 用 notebook_path)
    V01_FILE_PATH=""
    if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_EVENT" ]; then
        V01_FILE_PATH="$(echo "$HOOK_EVENT" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null || echo "")"
    fi
    # 工位定位: cwd 通道 (上文锚定) 优先; 否则 TOWOW_TASK_ID env marker (B4-01 注入,
    # isolation=off 时 cwd 不是工位, env 才是稳的绑定 — 但 .owner 本体仍是信任根)。
    if [ -z "$V01_TASK_ID" ] && [ -n "${TOWOW_TASK_ID:-}" ]; then
        V01_TASK_ID="$TOWOW_TASK_ID"
        V01_MAIN_DIR="$V3_ROOT"
    fi
    if [ -n "$V01_TASK_ID" ]; then
        V01_WS_ROOT="$V01_MAIN_DIR/.towow/worktrees/$V01_TASK_ID"
        V01_OWNER_FILE="$V01_WS_ROOT/.owner"
        if [ -s "$V01_OWNER_FILE" ]; then
            # ── 有 .owner → 真物理门 ──
            # file_path 相对化: write_set 是 plan claim 键 (仓库相对路径), 工位内
            # 绝对路径剥工位根前缀再喂 guard-check (否则绝对路径永不匹配 = 假门全拒)。
            # 工位外路径原样传 — 不在本工位 write_set 里, 由原语判越界 (语义正确)。
            V01_CHECK_PATH="$V01_FILE_PATH"
            case "$V01_CHECK_PATH" in
                "$V01_WS_ROOT"/*) V01_CHECK_PATH="${V01_CHECK_PATH#"$V01_WS_ROOT"/}" ;;
            esac
            # actor: env TOWOW_ACTOR_ID 优先 (B4-01 注入 = 派发链权威), .owner.actor_id 兜底。
            V01_ACTOR="${TOWOW_ACTOR_ID:-}"
            if [ -z "$V01_ACTOR" ] && command -v jq >/dev/null 2>&1; then
                V01_ACTOR="$(jq -r '.actor_id // empty' "$V01_OWNER_FILE" 2>/dev/null || echo "")"
            fi
            # towow bin: 只认内核 venv 形态 (裸 towow 被全局 towow_mcp 撞名占用, 不可用)。
            V01_TOWOW=""
            if [ -n "${TOWOW_KERNEL_VENV_BIN:-}" ] && [ -x "${TOWOW_KERNEL_VENV_BIN}/towow" ]; then
                V01_TOWOW="${TOWOW_KERNEL_VENV_BIN}/towow"
            elif [ -x "$V01_MAIN_DIR/.venv/bin/towow" ]; then
                V01_TOWOW="$V01_MAIN_DIR/.venv/bin/towow"
            elif [ -x "$V01_MAIN_DIR/harness/.venv/bin/towow" ]; then
                V01_TOWOW="$V01_MAIN_DIR/harness/.venv/bin/towow"
            fi
            if [ -z "$V01_FILE_PATH" ] || [ -z "$V01_ACTOR" ] || [ -z "$V01_TOWOW" ]; then
                cat <<EOF >&2
✗ V-01 越界写物理门 fail-closed 拒绝 (T-FIX-B4-03):
工位 .owner 在 ($V01_OWNER_FILE) 但边界验不了 —
  file_path: '${V01_FILE_PATH:-<解析不出>}' / actor: '${V01_ACTOR:-<不可判>}' / towow bin: '${V01_TOWOW:-<解析不到>}'
验不了边界 = 不放行 (不静默 fail-open)。修法: spawn 链应注入 TOWOW_KERNEL_VENV_BIN /
TOWOW_ACTOR_ID (T-FIX-B4-01), 或确认主仓 .venv/bin/towow 存在。
EOF
                exit 2
            fi
            if V01_OUT="$("$V01_TOWOW" worktree guard-check --task-id "$V01_TASK_ID" --file-path "$V01_CHECK_PATH" --actor-id "$V01_ACTOR" --project-dir "$V01_MAIN_DIR" 2>&1)"; then
                : # V-01 通过 — 不是免锁通行证, 继续走采访入口门 (B4-02)
            else
                cat <<EOF >&2
✗ V-01 越界写物理拦截 (T-FIX-B4-03): guard-check 拒绝本次写 (canonical OwnerGuardViolation 已留痕)。
  task=$V01_TASK_ID actor=$V01_ACTOR file=$V01_CHECK_PATH
  $V01_OUT
只能写本工位 .owner.write_set 声明的文件。越界需求 → 回推派发链扩 write_set, 不要绕门。
EOF
                exit 2
            fi
        else
            echo "⚠ V-01 诚实边界 (T-FIX-B4-03): TOWOW_TASK_ID=$V01_TASK_ID 但工位 .owner 不存在 ($V01_OWNER_FILE) — V-01 文件级写边界对本会话不强制 (isolation=off / 工位未建), 仍受采访入口门约束。要物理边界须 isolation=on (隔离工位)。" >&2
        fi
    else
        echo "⚠ V-01 诚实边界 (T-FIX-B4-03): 此会话无 .owner 写边界声明 (无工位 / 未注入 TOWOW_TASK_ID) — V-01 文件级写边界不物理强制, 仍受采访入口门约束。要物理边界须 isolation=on (隔离工位) 或显式声明 worktree。" >&2
    fi
fi

# ── T-FIX-B4-02 (INT-content#1): 采访入口身份门 — origin 血缘校验 ──────────────
#
# 旧 Case (b) 是"有任一锁就 exit 0"的粗门: 只验"有没有 active session", 不验"这个
# session 是不是从真实采访链进来的" — 采访可被完全跳过 (起个裸锁/goal 锁就能写)。
# 本段把"锁存在"升级为"锁存在 + origin 血缘可追溯", 三态判定:
#
#   valid   — origin ∈ {orchestrator_auto, inline_continuation, cli_goal_spawn}
#             (T-FIX-B4-01 spawn 注入的派发链取值, 与 l2/claude_bg_helper.py
#             _VALID_SESSION_ORIGINS 是同一冻结集合) → 放行。
#   invalid — env 有值但 ∉ 合法集 = 伪造/篡改 → fail-closed 拒 (唯一硬拒形态)。
#   unknown — env 缺失且锁 meta 无 origin → 保守放行 + stderr advisory 留痕。
#
# unknown 为什么不拒 (分阶段决策, 诚实边界 — 别把这段注释删了再收紧):
#   现存主对话 / owner 手动会话 / 历史 spawn 都没有 TOWOW_SESSION_ORIGIN env;
#   锁文件 meta 今天也还没写 origin 字段 (l1/session_lock.py LockRecord 无此字段)。
#   unknown 直接拒会把整个系统瞬间锁死 (所有现存会话全拒)。所以第一阶段只对
#   "明确伪造"硬拒, unknown 记 advisory 放行留痕; 待派发链 origin 注入全覆盖
#   (B4-01 铺满 + 锁 meta 带 origin) 后, 把 _grant_via_origin_gate 的 unknown
#   分支收紧为 return 1 — 到那一步采访入口门才是全量物理强制; 在此之前对
#   unknown 范围诚实标注"不强制" (INV-B4-3: 不把 built 洗成 enforced)。

ORIGIN_REJECT_REASON=""

_origin_class() {
    # $1 = origin 字符串 → 输出 valid|unknown|invalid
    case "$1" in
        "") echo "unknown" ;;
        orchestrator_auto|inline_continuation|cli_goal_spawn) echo "valid" ;;
        *) echo "invalid" ;;
    esac
}

_lock_meta_origin() {
    # 从锁文件 JSON 提取 origin 字段 (meta 通道, 向前兼容: 今天的 LockRecord 还没
    # 写该字段, 将来锁 meta 带 origin 后此通道自动生效)。拿不到 → 空串 (unknown)。
    if command -v jq >/dev/null 2>&1 && [ -f "$1" ]; then
        jq -r '.origin // empty' "$1" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

_grant_via_origin_gate() {
    # $1 = 即将放行的锁路径。env TOWOW_SESSION_ORIGIN 优先, 锁 meta origin 兜底。
    # valid → exit 0; unknown → advisory + exit 0 (分阶段, 见上); invalid → return 1
    # (不放行, 记拒绝理由, 落到底部拒绝路径 exit 2)。
    local origin="${TOWOW_SESSION_ORIGIN:-}"
    local source="env TOWOW_SESSION_ORIGIN"
    if [ -z "$origin" ]; then
        origin="$(_lock_meta_origin "$1")"
        source="锁 meta origin ($1)"
    fi
    case "$(_origin_class "$origin")" in
        valid)
            exit 0
            ;;
        invalid)
            ORIGIN_REJECT_REASON="origin '$origin' (来源: $source) ∉ 合法集 {orchestrator_auto, inline_continuation, cli_goal_spawn} — env 有值但非法 = 伪造, fail-closed 拒 (T-FIX-B4-02)"
            return 1
            ;;
        *)
            echo "⚠ PreToolUse Guard advisory (T-FIX-B4-02 采访入口身份门): 持锁会话放行, 但 origin 血缘不可判 (env TOWOW_SESSION_ORIGIN 缺失, 锁 meta 无 origin; 锁: $1)。当前为分阶段保守放行; 派发链 origin 注入全覆盖后该分支将收紧为拒绝。" >&2
            exit 0
            ;;
    esac
}

# (0) admin-bypass: owner 亲手放置的物理旗标 → 放行任意写 (把"Class A owner-level
#     decision 绕过 Guard"从口头约定变成需要 owner 物理动作的旗标)。要求非空 (-s):
#     旗标内容应含 owner 授权理由 + 时间戳 (产生/撤销由 T-FIX-B4-05 的
#     --owner-confirm CLI 约束), 0 字节意外文件骗不过 (M5 精神)。优先级最高 —
#     owner 物理动作 > env 提示, 即使 env origin 非法也放行 (但 stderr 必留痕)。
ADMIN_BYPASS_FLAG="$V3_ROOT/.towow/locks/admin-bypass.flag"
if [ -s "$ADMIN_BYPASS_FLAG" ]; then
    echo "⚠ PreToolUse Guard: 本次写经 admin-bypass.flag 放行 (owner 物理旗标, T-FIX-B4-02)。flag: $ADMIN_BYPASS_FLAG" >&2
    exit 0
fi

# Case (b): 有 active phase session 且 origin 血缘过门 → 允许 Write/Edit。
# 会话锁根治 (T-SL-B / SESSION-LOCK-FIX-DESIGN.md §3, concept session-resolution-registry-unified):
# registry 工卡是活跃信号 (存在性 + 内容粗校验, 无 mtime/新鲜度判断), 终身并集保留单指针兼容段。
# 三段并集放行点统一走 _grant_via_origin_gate (`|| true` 吞 invalid 的 return 1,
# set -e 安全; 循环里 grep/[ ] 失败被 if 条件吞掉, 不触发 set -e 退出)。
shopt -s nullglob 2>/dev/null || true
# (1) registry 工卡 (SessionLockRegistry): 存在 + 非空(-s) + 内容含 session_id (M5 粗校验 —
#     0 字节 / 半写锁骗不过 guard; 无 mtime 判断 (B3) — 不把"几小时没跑子命令"的活会话拒死,
#     崩溃残留交 registry 自己的 reap 回收)。glob 深度钉死两段 sessions/<kind>/<sid>.json —
#     registry 永不写更深 (M6); 更深目录的 .json 不被匹配 (深度约定)。meta 旁文件命名
#     <sid>.meta (非 .json 结尾, INV-SL-01) → *.json 天然不匹配它。sessions/<kind>/ 目录纯度
#     约定 (R3): 只许 registry 锁本体 .json 落此目录 ("含 session_id 即放行" 靠目录纯度兜)。
for f in "$V3_ROOT/.towow/locks/sessions/"*/*.json; do
    if [ -s "$f" ] && grep -q '"session_id"' "$f" 2>/dev/null; then
        _grant_via_origin_gate "$f" || true
    fi
done
# (2) goal 锁: goal-spawn 的 bg 会话整段靠它满足 guard (interview 尚未 start 的窗口 / goal 直写)。
#     无新鲜度 = 已知放行后门 (登记 SESSION-LOCK-FIX-DESIGN.md §9, 本次不碰 goal 锁)。
#     T-FIX-B4-02: goal 直写同样过 origin 门 — 伪造拒, 不可判记 advisory 放行 (分阶段)。
if [ -f "$V3_ROOT/.towow/locks/goal_session.lock" ]; then
    _grant_via_origin_gate "$V3_ROOT/.towow/locks/goal_session.lock" || true
fi
# (3) 单指针兼容段 —— 终身保留不删 (INV-SL-04 / B6: 任何角落残留旧 guard 或旧状态时, 最坏退回
#     旧行为, 永不死锁)。注: *_session.lock 也匹配 goal_session.lock (隐性 OR), 故 (2) 对 goal
#     的显式认是冗余安全网而非唯一来源。单指针退役后该 glob 长期为空, 段保留无害 (DOGFOOD-001-J 谱系)。
single_locks=("$V3_ROOT/.towow/locks/"*_session.lock)
if [ ${#single_locks[@]} -gt 0 ]; then
    _grant_via_origin_gate "${single_locks[0]}" || true
fi

# v3 repo + 无 active session — fail-closed semantics:
#   - tool_name 是 write-class (已知)        → 拒绝
#   - tool_name 是其他 (已知 non-write)      → 透传
#   - tool_name="" (parse 失败 / 未知 tool)  → 拒绝（fail-closed default）
#
# M-1.6 fix (finding-9c4fd4ed): 未知状态默认拒，违反 v3 fail-closed protocol 不允许.

case "$TOOL_NAME" in
    Write|Edit|MultiEdit|NotebookEdit)
        ;;  # write-class → 拒（fall through）
    "")
        ;;  # parse 失败 / 未知 — fail-closed (fall through)
    *)
        exit 0 ;;  # 已知 non-write tool → 透传
esac

# 拒 + 引导
# T-FIX-B4-02: origin 伪造拒绝时先点名理由 (与"无 session"拒绝区分开)。
if [ -n "$ORIGIN_REJECT_REASON" ]; then
    echo "✗ 采访入口身份门拒绝: $ORIGIN_REJECT_REASON" >&2
    echo "  合法路径: 经 /work 采访链派发 (spawn 注入 TOWOW_SESSION_ORIGIN), 或 owner 放置 .towow/locks/admin-bypass.flag。" >&2
    exit 2
fi
cat <<'EOF' >&2
✗ PreToolUse Guard 拒绝 Write/Edit：当前在 v3-managed repo 但无 active v3 session。

按 v3 协议，正式工作请求必须从 /work 进入：
    /work "你的工作请求"

如果这是 read-only 操作（查代码 / 读文件 / 调试），用对应工具（Read / Grep / Bash 等）；
不要用 Write/Edit。

如果你确定要在没有 v3 session 的情况下动手（如 init 阶段 / 一次性 admin task），
请告诉 Nature 你为什么需要绕过 Guard — 这是 Class A owner-level decision。
EOF
exit 2
