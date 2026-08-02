"""RUN-039: capsule prompt 构造器 — 喂给阻塞独立验证 fork (verification_fork.run_verification_fork).

# spec source:
#   M-1.4 §6.2 execution-self-check / §6.1 advisor-consult / M-1.6 §6.1 fix-self-check /
#   M-1.2 §9.3 engineering-consensus forks

每个 fork 在【全新独立 context】里跑 (claude -p), 看不到主 session 的执行过程 —— 它只能 Read/
Grep/Bash 检视 capsule 投喂的 artifacts 独立验证。prompt 三段: (1) 装人格 + 独立性约束;
(2) 待验 artifacts; (3) 结构化输出 schema (fork 最后一条消息只输出该 JSON)。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

# 独立性约束块 — 每个验证 fork prompt 头部都带 (强化"看不到执行过程 + 无写权限 + 能 disprove")。
_INDEPENDENCE_BLOCK = (
    "你在【全新独立 context】里运行: 你看不到主 session 的执行/修复过程, 只能用 Read/Grep/Glob/"
    "Bash 检视下面投喂的 artifacts 独立验证。你【没有 Edit/Write】(物理防自欺 — 运动员不能改东西"
    "当裁判)。你的职责是独立判断, 不是替主 session 背书: 证据不足 / 不满足 → 大胆判 failed/拒, "
    "不要因为'主 session 说做完了'就放过 (executor 可能 unconscious bias)。"
)

# RUN-042 Cap1b — 收紧输出约束: 更强制"纯 JSON、无 markdown fence、无前后散文、单个对象"。
# 降低 fork 输出散文+JSON 混排的概率 (RUN-041 "缺 passed 键" 根因之一)。即便 fork 仍混排,
# verification_fork.parse_fork_verdict 已加固能稳健抽取 —— 两层一起把粗糙边率压下去。
_PURE_JSON_CONSTRAINT = (
    "⚠️ 硬性输出约束 — 只输出纯 JSON (违反会导致你的裁决被判 fail-closed):\n"
    "- 你最后一条消息【必须是且只能是】一个纯 JSON 对象, 第一个字符就是 `{`, 最后一个字符就是 `}`。\n"
    "- 【不要】用 markdown 代码围栏 (不要 ``` 或 ```json)。\n"
    "- 【不要】在 JSON 前后写任何说明文字 / 寒暄 / 总结 / 思考过程。\n"
    "- 你的分析推理放在 JSON 字段内部 (如 evidence / summary), 不要放在 JSON 外面。\n"
    "- 只输出【一个】顶层 JSON 对象, 不要输出多个分散的 JSON 块。\n"
)


# ════════════════════════════════════════════════════════════════════════════════
#  capsule — 通用上下文传递 (打小包裹)  @capsule-general-context-transfer@v1
# ════════════════════════════════════════════════════════════════════════════════
# T-FU-05-capsule: 把『为一个下游智能显式挑选相关上下文并结构化打包』一等化为通用传递格式,
# Capsule assembly is transport-neutral: the same bounded context can be handed
# to a fork, peer, or later session without changing its evidence contract.
# 既有主消费方是把关族 fork (gate fork 的全部输入=capsule); 本机制把那份『curated push 结构』
# 抽成 fork 无关的一等载体, 让非 fork 下游 (bg 会话派发信 / handoff 交接 / TaskPackage 组装)
# 同源装配 —— 消费方 build_dispatch_capsule_prompt (goal⑥ 的非 fork 消费方) 见文件末。


class CapsuleContractError(ValueError):
    """capsule 违反最小必含对 (task + must_return) —— 『没有任务与返回约定的包不是 capsule 是粘贴』。"""


@dataclass(frozen=True)
class Capsule:
    """@capsule-general-context-transfer@v1 的一等通用载体 —— 从 fork 专用解耦的『打小包裹』结构。

    【8 字段结构模板 (P-11 + O-02 决策 8, 字段钉死)】task / why_this_recipient / known_facts /
    nature_judgments / info_map_snapshot / files_to_read / must_return / must_not_do。字段按需
    取用 (8 字段是结构模板与命名约定, 非全字段强制); 但 **task 与 must_return 是最小必含对** ——
    没有任务与返回约定的包不是 capsule 是粘贴 (__post_init__ fail-closed, 缺一即 raise
    CapsuleContractError)。

    【curated push 语义 (与检索的分界)】Capsule 是编包者的判断产物: 显式挑选、为收件方裁剪、
    结构化命名 —— 不是全量转储 (dump transcript 不是 capsule), 不是收件方自己检索 (那是
    progressive-disclosure 的 pull)。render() 只投喂显式给到的字段, 空字段跳过、不填占位噪音。

    【解耦 (从 fork 专用)】本类不 import 任何 fork / session / graph 机制 —— 纯数据 + render,
    任何下游都能装配。既有主消费方=把关族 fork 的 curated-push 段 (机制归
    @fork-context-mode-by-family@v1); 非 fork 消费方见 build_dispatch_capsule_prompt (goal⑥)。

    【scope 反面】不管派发动作本身 (归 fork-unified-dispatch-entry); 不管返回校验 (归
    fork-unified-return-contract —— capsule 管去程包, 返回契约管回程包); 不承载 gate 族独立性语义
    (归 fork-context-mode-by-family)。
    """

    task: str
    must_return: str
    why_this_recipient: str = ""
    known_facts: Sequence[str] = ()
    nature_judgments: Sequence[str] = ()
    info_map_snapshot: str = ""
    files_to_read: Sequence[str] = ()
    must_not_do: Sequence[str] = ()

    def __post_init__(self) -> None:
        # 最小必含对 fail-closed: task 与 must_return 缺一即拒 (概念钉死 —— 没有任务与返回约定的
        # 包不是 capsule 是粘贴)。其余 6 字段按需, 空即在 render() 中跳过。
        if not self.task.strip():
            raise CapsuleContractError("capsule 缺 task —— 没有任务的包不是 capsule 是粘贴")
        if not self.must_return.strip():
            raise CapsuleContractError("capsule 缺 must_return —— 没有返回约定的包不是 capsule 是粘贴")
        # 序列字段归一为 tuple (frozen dataclass —— 经 object.__setattr__), 保不可变 + 调用方
        # 传 list 亦可。
        object.__setattr__(self, "known_facts", tuple(self.known_facts))
        object.__setattr__(self, "nature_judgments", tuple(self.nature_judgments))
        object.__setattr__(self, "files_to_read", tuple(self.files_to_read))
        object.__setattr__(self, "must_not_do", tuple(self.must_not_do))

    def render(self) -> str:
        """把 8 字段渲染成结构化 curated-push 文本块 (供 prompt 装配)。

        段序对齐概念的 8 字段枚举 (task → why → known_facts → nature_judgments → info_map →
        files → must_return → must_not_do)。task / must_return 恒在 (最小必含对); 其余空字段跳过
        —— curated push 只投喂编包者显式挑选的内容, 不填占位噪音。
        """
        parts: list[str] = [f"## TASK (你要做什么)\n{self.task.strip()}"]
        if self.why_this_recipient.strip():
            parts.append(
                f"## WHY_THIS_RECIPIENT (为什么派给你 / 你的判断边界)\n{self.why_this_recipient.strip()}"
            )
        if self.known_facts:
            facts = "\n".join(f"- {f}" for f in self.known_facts)
            parts.append(f"## KNOWN_FACTS (已核实的事实, 带出处)\n{facts}")
        if self.nature_judgments:
            judgments = "\n".join(f"- {j}" for j in self.nature_judgments)
            parts.append(f"## OWNER_APPROVED_JUDGMENTS\n{judgments}")
        if self.info_map_snapshot.strip():
            parts.append(
                f"## INFO_MAP_SNAPSHOT (相关信息需求图快照)\n{self.info_map_snapshot.strip()}"
            )
        if self.files_to_read:
            files = "\n".join(f"- {p}" for p in self.files_to_read)
            parts.append(f"## FILES_TO_READ (该读的文件)\n{files}")
        parts.append(f"## MUST_RETURN (返回物的结构约定)\n{self.must_return.strip()}")
        if self.must_not_do:
            nots = "\n".join(f"- {n}" for n in self.must_not_do)
            parts.append(f"## MUST_NOT_DO (红线)\n{nots}")
        return "\n\n".join(parts)


def _shared_knowledge_block(skill_md_path: str) -> str:
    """按 fork frontmatter 的 shared_knowledge_required 把 knowledge pack 内容注进 prompt。

    背景: fork 装人格只被告知"读自己 SKILL.md 全文" —— 它不走主 skill 的 capsule_inject_or_fail，
    所以 frontmatter 声明了 shared_knowledge_required 的 fork(共识 6 fork 等)从没真拿到那些
    方法论包(concept-taxonomy / casebook / reuse-supersede-policy …)。这里在 prompt 里补上，让
    声明不再是死声明。

    - SKR=[] (采访 / self-check / advisor / audit 等) → 空串(它们靠 SKILL.md 正文自足)。

    T-LND-08 (INV-B2-2 fork-knowledge-injection-fail-closed): **声明了 shared_knowledge_required 的
    fork, 其 knowledge 注入失败 (文件缺) 必须 fail-closed —— raise, fork 不 spawn**, 绝不静默降级到
    "靠 SKILL.md 自足"。原 `except → 返空串` 是危机3 的真根: 不同 fork 拿到的上下文深度不一致 →
    "验证深度依赖 prompt 胖瘦" 的非确定性。消除深度漂移: 要么所有 fork 拿到一致 knowledge, 要么
    fail-closed 不验, 不允许"有的 fork 胖有的瘦"。

    例外 (非本概念针对的深度漂移): manifest 未注册 (skill 整体未部署) → 返空降级。真实部署里被
    spawn 的 fork 其 skill 必已注册, manifest 缺是环境/部署问题 (由部署侧兜), 不是"已部署 fork 声明
    了 SKR 却拿不到"那条 —— 后者才 fail-closed。
    """
    p = Path(skill_md_path)
    skills_root = p.parent.parent  # .../.claude/skills/<id>/SKILL.md → .../.claude/skills
    fork_id = p.parent.name
    from towow.shell.skill_packaging import (
        SkillPackagingError,
        SkillRegistry,
        build_capsule_knowledge_context,
    )

    try:
        registry = SkillRegistry(skills_root)
        registry.discover()
        manifest = registry.get(fork_id)
    except (OSError, SkillPackagingError) as exc:
        # skills 目录读不了 / SKILL.md malformed —— 环境/部署问题, 非"已部署 fork 声明了 SKR
        # 却拿不到"的深度漂移 → 降级 (向后兼容)。注意: CapsuleInjectionFailed 与 SkillPackagingError
        # 独立, 不在此 catch —— 声明 SKR 但文件缺仍 fail-closed。
        _log.warning("fork %s skills 注册失败(环境问题), knowledge 降级: %r", fork_id, exc)
        manifest = None
    if manifest is None:
        return ""  # skill 未注册/未部署 → 降级 (向后兼容)
    if not manifest.shared_knowledge_required:
        return ""  # SKR=[] → SKILL.md 自足, 无需注入 (非失败)
    # 声明了 SKR → 注入必须成功; 文件缺 → build_capsule_knowledge_context raise
    # CapsuleInjectionFailed 传播 (fail-closed, caller 不 spawn fork + 报注入失败)。不再吞。
    ctx = build_capsule_knowledge_context(fork_id, registry=registry, knowledge_root=skills_root)
    if not ctx:
        return ""
    return (
        "## 你的 knowledge pack（按 shared_knowledge_required 注入 — 你的方法论深化, 按它工作）\n\n"
        f"{ctx}\n\n"
    )


def _persona_line(skill_md_path: str, skill_id: str, spec_ref: str) -> str:
    return (
        f"你是 `{skill_id}` 独立验证 fork ({spec_ref})。先装人格: 读 `{skill_md_path}` 全文, "
        f"按它定义的人格/procedure/输出 schema 工作。\n\n"
        + _shared_knowledge_block(skill_md_path)
    )


def build_execution_self_check_prompt(
    *,
    skill_md_path: str,
    repo_dir: Path,
    task_id: str,
    run_id: str,
    done_evidence: str | None,
    commit_ref: str | None,
    patch_summary: str | None,
    inline_checks: list[dict[str, object]],
) -> str:
    """M-1.4 §6.2 execution-self-check fork prompt — 独立验 5 项 blocking_check.

    inline_checks: main session 同进程预筛跑出的 5 项 check (作为'待复验的声明', fork 独立复核, 不照抄)。
    """
    claims = "\n".join(
        f"  - {c.get('check_id')}: status={c.get('status')} | evidence(声明): {c.get('evidence')}"
        for c in inline_checks
    )
    return (
        _persona_line(skill_md_path, "execution-self-check", "M-1.4 §6.2")
        + _INDEPENDENCE_BLOCK
        + "\n\n## 待验 task (executor 声明做完了, 你独立核)\n"
        f"- task_id: {task_id}\n"
        f"- run_id: {run_id}\n"
        f"- repo (你的 cwd): {repo_dir}\n"
        f"- done_criteria 证据 (executor 声明): {done_evidence or '(空)'}\n"
        f"- commit_ref: {commit_ref or '(无)'} —— 用 `git -C {repo_dir} show {commit_ref or '<ref>'}` 真看 diff\n"
        f"- patch_summary: {patch_summary or '(无)'}\n\n"
        "## main session 预筛声明 (你独立复核, 不照抄)\n"
        f"{claims}\n\n"
        "## 你独立跑的 5 项 blocking_check (M-1.4 §6.2, 每条独立验, evidence 必须具体不接受 'looks ok')\n"
        "1. execution.done_criteria_satisfied — done_criteria 真满足? (读 task spec + diff 独立判)\n"
        "2. execution.actual_set_recorded — 实际改动集如实记录 (patch_summary/diff 一致)?\n"
        "3. execution.obligations_maintained — active obligations 维护无违反?\n"
        "4. execution.no_unhandled_mismatch — 无未处理 mismatch?\n"
        "5. execution.git_committed — commit_ref 真存在且含改动? (`git cat-file -t` / `git show` 验)\n\n"
        + _output_schema_block(
            "self_check_result",
            check_ids=[
                "execution.done_criteria_satisfied",
                "execution.actual_set_recorded",
                "execution.obligations_maintained",
                "execution.no_unhandled_mismatch",
                "execution.git_committed",
            ],
        )
    )


def build_fix_self_check_prompt(
    *,
    skill_md_path: str,
    repo_dir: Path,
    fix_id: str,
    finding_id: str | None,
    closure_contract_summary: str,
    patch_summary: str | None,
) -> str:
    """M-1.6 §6.1 fix-self-check fork prompt — 独立验 5 项 fix blocking_check.

    T-RMD-S2-REVFIX (finding f-glob-review-fix-no-proof-of-work / M16-F2): 此前 fork 只拿到门自己的
    1200 字 pass 摘要 (closure_contract_summary) 就 "复判", 够不到 reviewer 签的真合约 pattern + patch
    diff = 空壳独立验。修法 = prompt 指令 fork 凭 finding_id 自己从 canonical 账本加载真 ClosureContract
    并跑真 grep/pytest, 摘要仅作参考 (权威以账本真合约为准)。fork 有 Read/Grep/Bash, 够得到。
    """
    ledger_hint = (
        f"{repo_dir}/.towow/events.log (+ 热段 {repo_dir}/.towow/events/hot/*.jsonl)"
    )
    return (
        _persona_line(skill_md_path, "fix-self-check", "M-1.6 §6.1")
        + _INDEPENDENCE_BLOCK
        + "\n\n## 待验 fix (fixer 声明修完了, 你独立核 closure 对合约)\n"
        f"- fix_id: {fix_id}\n"
        f"- finding_id: {finding_id or '(无)'}\n"
        f"- repo (你的 cwd): {repo_dir}\n"
        f"- closure_contract (仅摘要参考, 权威以你从账本加载的真合约为准): {closure_contract_summary}\n"
        f"- patch_summary: {patch_summary or '(无)'}\n\n"
        "## ★ 必做: 自己从账本加载真合约 + 跑真复算 (M16-F2 — 不许只复判上面的摘要)\n"
        "上面的 closure_contract 摘要只是线索, **不是**你的判据来源。空壳独立验 = 只看摘要点头, 这正是\n"
        "本 task 要根治的病。你必须自己够到真合约 + 真 patch:\n"
        f"1. 用 Grep/Bash 在 canonical 账本 {ledger_hint} 里找 finding_id={finding_id or '(无)'} 的\n"
        "   FindingCreated 事件, 从它的 payload.after_state.closure_contract (或 payload.closure_contract)\n"
        "   取**真** closure_criteria[] (每条带 verification_method / verification_pattern / "
        "test_selector / expected_*) 与 forbidden_residuals[]。\n"
        "2. 对每条 closure_criterion **按其 verification_method 真跑机器复算** (不是读摘要点头):\n"
        "   - grep 型 → `grep -rn <verification_pattern> <search_scope>` 真数命中, 比 expected_occurrences;\n"
        "   - test 型 → `python -m pytest <test_selector>` 真跑, 看真退出码;\n"
        "   - forbidden_residual → 用合约自带 pattern 真 grep, 断 found=0。\n"
        f"3. 用 `git -C {repo_dir} show` / `git -C {repo_dir} diff` 看 fix_id={fix_id} 的**真实 patch diff**,\n"
        "   核对它确实改了合约要求改的地方 (够到 patch diff, 非只看 patch_summary 字样)。\n"
        "拿不到真合约 (finding 无 closure_contract / 账本找不到) → 诚实在对应 check 标 status=failed +\n"
        "evidence 写清 '账本无真合约可复算', 不许拿摘要凑 passed (fail-closed)。\n\n"
        "## 你独立跑的 5 项 blocking_check (M-1.6 §6.1, 对**真**closure_contract 真验, 不信 fixer 自报)\n"
        "1. fix.closure_criteria_self_verified — closure_criteria 每条按 verification_method 实跑过?\n"
        "2. fix.ripple_targets_synced — ripple_targets 每个 sync_status 真 synced/not_applicable(带证据)?\n"
        "3. fix.forbidden_residuals_zero — forbidden_residual pattern grep found=0?\n"
        "4. fix.consensus_respected — 改动尊重 frozen 共识 (无 silent supersede)?\n"
        "5. fix.review_plan_respected — 尊重关联 review_plan?\n\n"
        + _output_schema_block(
            "self_check_result",
            check_ids=[
                "fix.closure_criteria_self_verified",
                "fix.ripple_targets_synced",
                "fix.forbidden_residuals_zero",
                "fix.consensus_respected",
                "fix.review_plan_respected",
            ],
        )
    )


def build_advisor_consult_prompt(
    *,
    skill_md_path: str,
    repo_dir: Path,
    consult_id: str,
    task_id: str,
    question: str,
    tentative_answer: str,
    uncertainty: str,
    triggered_by: str,
) -> str:
    """M-1.4 §6.1 advisor-consult fork prompt — 独立 OPUS 决策者给 verdict (决策非软建议)."""
    return (
        _persona_line(skill_md_path, "advisor-consult", "M-1.4 §6.1")
        + _INDEPENDENCE_BLOCK
        + "\n\n## 待裁决的 consult (executor 遇判断困难, 你做独立决策者裁决 — 决策不是软建议)\n"
        f"- consult_id: {consult_id}\n"
        f"- task_id: {task_id}\n"
        f"- repo (你的 cwd): {repo_dir}\n"
        f"- triggered_by: {triggered_by}\n"
        f"- question: {question}\n"
        f"- executor 暂定答案: {tentative_answer}\n"
        f"- executor 不确定点: {uncertainty}\n\n"
        "## 你的裁决 (M-1.4 §6.1, 5 个 decision path 之一, 决策非建议 — executor 按你的 verdict 实施)\n"
        "use_path_self_heal / use_path_consult_again / use_path_trigger_replan / "
        "use_path_abort_task / custom_action\n"
        "必须给 evidence_scope_summary (你基于什么 capsule/evidence 判的; executor 遇 scope 外新证据须重 consult)。\n\n"
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + '{"advisor_verdict": {"decision": "<5 path 之一>", "rationale": "<非空理由>", '
        '"confidence": "low|medium|high", "evidence_scope_summary": "<非空>", '
        '"specific_steps": ["custom_action 时给具体步骤, 否则空数组"], "passed": true}}\n'
        "(passed 恒 true — advisor 总能给出裁决; decision 才是实质内容。)"
    )


# M-1.2 §9.3 六个工程共识 fork 各自的职责 (主 skill 建图时按需 spawn)。
CONSENSUS_FORK_JOBS: dict[str, str] = {
    "engineering-prep-research": (
        "§9.3.1 建图前研究既有概念图: 对每个 seed 做五维比较 + 决策矩阵, 给 exact_reuse / "
        "supersede_candidate / new 判断 + confidence。"
    ),
    "concept-definition": "§9.3.2 为概念产完整定义 (name / 语义 / 字段 / 边界 / examples)。",
    "state-machine-define": (
        "§9.3.3 为 concept 设计满足 v3 语法层 6 条的状态机 (状态/迁移/SAGA 补偿)。"
    ),
    "consumer-scan": "§9.3.4 初次消费方扫描 (谁会消费这个概念, downstream 影响)。",
    "invariant-extract": (
        "§9.3.5 从 brief / nature_judgments 提取不变量候选 (机械验证 / protocol_level 标注)。"
    ),
    "engineering-consistency-verify": (
        "§9.3.6 冻结前一致性终检: list_stale_references + blocking_issues。"
    ),
}


def build_consensus_fork_prompt(
    *,
    fork_id: str,
    skill_md_path: str,
    repo_dir: Path,
    capsule_input: str,
) -> str:
    """M-1.2 §9.3 工程共识 fork prompt — 独立 fork 分析/提议 (不写 event, 返回结构化 proposal).

    capsule_input: 主 skill 投喂的输入 (seeds / brief 摘要 / 当前概念图 draft 等)。
    """
    job = CONSENSUS_FORK_JOBS.get(fork_id, f"M-1.2 §9.3 工程共识 fork {fork_id}")
    return (
        _persona_line(skill_md_path, fork_id, "M-1.2 §9.3")
        + _INDEPENDENCE_BLOCK
        + "\n\n## 你的职责\n"
        f"{job}\n\n"
        "## 你不做什么\n"
        "你不直接写 event log / 不冻结 batch —— 你只产结构化 proposal 返回给主共识 session, "
        "由它合并/判断/提交分批 envelope (M-1.2 §7.0 fork hard-constraint)。\n\n"
        "## 输入 capsule (主共识 session 投喂)\n"
        f"{capsule_input}\n"
        f"(你的 cwd: {repo_dir} — 可 Read/Grep/Bash 检视既有概念图 / brief / 代码)\n\n"
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + '{"fork_result": {"produced": true, "proposal": <你的结构化提议对象>, '
        '"summary": "<一句话总结你的提议>"}}\n'
        "produced=false 仅当你无法产出有效提议时 (并在 summary 说明为什么)。"
    )


# M-1.1 §7 六个采访 fork 各自职责 + 输出 proposal 顶层 schema 提要 (主采访 skill 无 --result-json
# 时按 RUN-039 范式同步 spawn, 取代手动 Agent 工具调)。每条 = (spec_ref, job, proposal_schema_hint)。
# proposal 的完整字段定义在 deployed SKILL.md 输出 schema, CLI 收回后用对应 pydantic <X>Result 校验
# (不匹配 → fail-closed, 见 cli interview 命令); 这里的 schema 提要给顶层形状 + 枚举约束降低粗糙边。
INTERVIEW_FORK_JOBS: dict[str, tuple[str, str, str]] = {
    "interview-prep-research": (
        "M-1.1 §7.1",
        "采访开始前研究当前代码库状态 / 相关历史决策 / 相关概念图节点, 拿 ground truth 后给 "
        "info_need 初稿 + 首轮提问建议 (不直接跟 Nature 对话)。",
        '{"current_system_state": "<当前代码现状摘要, 非空>", '
        '"related_concepts": [{"concept_id": "...", "name": "...", "relevance_reason": "..."}], '
        '"related_historical_briefs": [{"brief_id": "...", "summary": "..."}], '
        '"info_need_draft": [{"need_id": "INF-NNN", "description": "...", '
        '"source": "ai_reachable|nature_unique|nature_confirm|defer", "red_line": false, '
        '"downstream_impact": ["engineering_design|planning|execution|review"], "dependencies": []}], '
        '"first_question_suggestions": ["..."]}',
    ),
    "interview-conops-construct": (
        "M-1.1 §7.4",
        "从抽象需求 ('我想做一个 X') 引导出具体使用场景 — 提议主 session 该问 Nature 的场景型问题 + "
        "几种待验证候选场景框架 + 派生 info_need (fork 不直接问 Nature)。",
        '{"scenario_probe_questions": ["..."], '
        '"candidate_scenario_frames": [{"frame_id": "frame-A", "description": "...", '
        '"key_actors": ["..."], "trigger": "...", "expected_outcome": "...", '
        '"what_information_this_frame_would_resolve": "..."}], '
        '"derived_info_needs": [{"need_id": "INF-NNN", "description": "...", '
        '"source": "ai_reachable|nature_unique|nature_confirm|defer", "red_line": false, '
        '"downstream_impact": ["..."], "dependencies": [], "depends_on_which_frame": ["frame-A"]}], '
        '"expected_information_gain": "<非空>"}',
    ),
    "interview-influence-diagram": (
        "M-1.1 §7.5",
        "把模糊的多 trade-off 任务翻译成可分析的 influence diagram (决策/不确定性/价值三类节点 + "
        "学界 4 标准 edge_type) + 派生 info_need。",
        '{"decision_nodes": [{"node_id": "...", "label": "...", "options": ["A", "B"]}], '
        '"uncertainty_nodes": [{"node_id": "...", "label": "...", "possible_outcomes": ["..."], '
        '"linked_info_need_id": null}], '
        '"value_nodes": [{"node_id": "...", "label": "...", "metric": "..."}], '
        '"edges": [{"from": "<node_id>", "to": "<node_id>", '
        '"edge_type": "informational|conditional|functional|structural"}], '
        '"derived_info_needs": [{"need_id": "INF-NNN", "description": "...", '
        '"source": "ai_reachable|nature_unique|nature_confirm|defer", "red_line": false, '
        '"downstream_impact": ["..."], "dependencies": []}]}',
    ),
    "interview-sdm-reverse": (
        "M-1.1 §7.6",
        "任务有明确方向性决策 (选 A 还是 B) 时, 从 decision context 按 SDM 五步反推每步需要什么信息 "
        "→ 澄清决策上下文 + 目标 + 候选方案 + 派生 info_need (fork 不替 Nature 下结论)。",
        '{"decision_context_clarified": {"clarified": "<澄清后的决策上下文, 非空>", '
        '"context_metadata": {}}, '
        '"objectives_identified": [{"objective": "...", "metric": "..."}], '
        '"alternatives_recognized": [{"alternative_id": "ALT-NNN", "description": "...", '
        '"estimated_consequences": ["..."], "blocked_by_info_need_ids": []}], '
        '"derived_info_needs": [{"need_id": "INF-NNN", "description": "...", '
        '"source": "ai_reachable|nature_unique|nature_confirm|defer", "red_line": false, '
        '"downstream_impact": ["..."], "dependencies": []}]}',
    ),
    "interview-consistency-verify": (
        "M-1.1 §7.8",
        "采访接近结束时脱离对话惯性全局看信息需求图, 找三类矛盾 (直接/隐含/遗漏), 默认偏向自己解决 "
        "(ai_self_resolved), 只把真需 Nature 拍的留 needs_nature_clarification。",
        '{"contradictions_found": [{"contradiction_id": "CON-NNN", '
        '"type": "direct|implied|omitted", "involved_nodes": ["<need_id>"], '
        '"evidence_quotes": ["..."], "resolution": "ai_self_resolved|needs_nature_clarification", '
        '"ai_self_resolution_reason": "<resolution=ai_self_resolved 时必填, 否则 null>", '
        '"follow_up_question_for_nature": "<resolution=needs_nature_clarification 时必填, 否则 null>"}], '
        '"graph_modifications_proposed": []}',
    ),
}


def build_interview_fork_prompt(
    *,
    fork_id: str,
    skill_md_path: str,
    repo_dir: Path,
    capsule_input: str,
) -> str:
    """M-1.1 §7 采访 fork prompt — 独立 fork 分析/提议 (§7.0 不写 event log, 返回结构化 proposal).

    RUN-059 件A: 主采访 skill 无 --result-json 时按 RUN-039 范式同步 spawn 这个 fork (取代手动
    Agent 工具调)。fork 在全新独立 context (无 Edit/Write) 装 SKILL.md 人格做分析, 把结构化 proposal
    包进 {"fork_result": {"produced", "proposal", "summary"}} 返回; 主 session 收回后用对应
    <X>Result pydantic schema 校验 (不匹配 → fail-closed)。proposal 字段须严格匹配 SKILL.md 输出 schema。

    capsule_input: 主采访 session 投喂的输入 (序列化的 <X>Capsule JSON)。
    """
    spec_ref, job, schema_hint = INTERVIEW_FORK_JOBS.get(
        fork_id, ("M-1.1 §7", f"M-1.1 §7 采访 fork {fork_id}", "<你的结构化提议对象>"),
    )
    return (
        _persona_line(skill_md_path, fork_id, spec_ref)
        + _INDEPENDENCE_BLOCK
        + "\n\n## 你的职责\n"
        f"{job}\n\n"
        "## 你不做什么\n"
        "你不直接写 event log / 不直接跟 Nature 对话 —— 你只产结构化 proposal 返回给主采访 session, "
        "由它判断哪些 derived_info_needs / 矛盾 commit 进信息需求图 (M-1.1 §7.0 fork hard-constraint)。\n\n"
        "## 输入 capsule (主采访 session 投喂; 这是序列化的 Capsule JSON)\n"
        f"{capsule_input}\n"
        f"(你的 cwd: {repo_dir} — 可 Read/Grep/Bash 检视代码 / 概念图 / 历史 brief 拿 ground truth)\n\n"
        "## proposal 顶层 schema 提要 (完整字段定义见上面 SKILL.md 输出 schema, 严格匹配它)\n"
        f"{schema_hint}\n\n"
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + '{"fork_result": {"produced": true, "proposal": <上面 schema 的结构化对象>, '
        '"summary": "<一句话总结你的提议>"}}\n'
        "produced=false 仅当你无法产出有效提议时 (并在 summary 说明为什么 — 主 session 会 fail-closed 不编造)。"
    )


def build_interview_prep_research_prompt(
    *, skill_md_path: str, repo_dir: Path, capsule_input: str,
) -> str:
    """M-1.1 §7.1 interview-prep-research fork prompt (RUN-059 件A 自动 spawn)。"""
    return build_interview_fork_prompt(
        fork_id="interview-prep-research", skill_md_path=skill_md_path,
        repo_dir=repo_dir, capsule_input=capsule_input,
    )


def build_interview_conops_construct_prompt(
    *, skill_md_path: str, repo_dir: Path, capsule_input: str,
) -> str:
    """M-1.1 §7.4 interview-conops-construct fork prompt (RUN-059 件A 自动 spawn)。"""
    return build_interview_fork_prompt(
        fork_id="interview-conops-construct", skill_md_path=skill_md_path,
        repo_dir=repo_dir, capsule_input=capsule_input,
    )


def build_interview_influence_diagram_prompt(
    *, skill_md_path: str, repo_dir: Path, capsule_input: str,
) -> str:
    """M-1.1 §7.5 interview-influence-diagram fork prompt (RUN-059 件A 自动 spawn)。"""
    return build_interview_fork_prompt(
        fork_id="interview-influence-diagram", skill_md_path=skill_md_path,
        repo_dir=repo_dir, capsule_input=capsule_input,
    )


def build_interview_sdm_reverse_prompt(
    *, skill_md_path: str, repo_dir: Path, capsule_input: str,
) -> str:
    """M-1.1 §7.6 interview-sdm-reverse fork prompt (RUN-059 件A 自动 spawn)。"""
    return build_interview_fork_prompt(
        fork_id="interview-sdm-reverse", skill_md_path=skill_md_path,
        repo_dir=repo_dir, capsule_input=capsule_input,
    )


def build_interview_consistency_verify_prompt(
    *, skill_md_path: str, repo_dir: Path, capsule_input: str,
) -> str:
    """M-1.1 §7.8 interview-consistency-verify fork prompt (RUN-059 件A 自动 spawn)。"""
    return build_interview_fork_prompt(
        fork_id="interview-consistency-verify", skill_md_path=skill_md_path,
        repo_dir=repo_dir, capsule_input=capsule_input,
    )


def build_audit_fork_prompt(
    *,
    skill_md_path: str,
    repo_dir: Path,
    envelope_event_id: str,
    audit_triggered_event_id: str,
    trigger_reason: str,
    audit_scope: str,
    envelope_summary: str,
    capsule_text: str = "",
) -> str:
    """M-0.5 §5 audit fork prompt — 独立审计员看 envelope + diff + capsule 产 AuditVerdict.

    与 self-check 类 fork 的区别: 输出是三态 verdict (pass/fail/conditional_pass) 而非纯 bool。
    schema 同时带 `passed` (gate fail-closed 兜底信号) + `verdict` (gate §5.5 三分支真依据)。

    capsule_text: assemble_capsule(scene_type=AUDIT) 渲染的图上下文 (空则 fork 自行 Read/Grep)。
    audit_scope: gate 把所有触发主体序列化进的 audit_scope 串 (谁/为什么触发审计)。
    """
    capsule_block = (
        f"## capsule 投喂的图上下文 (AUDIT scene)\n{capsule_text}\n\n"
        if capsule_text.strip()
        else "## capsule 上下文\n(无预渲染 capsule — 自行用 Read/Grep 检视概念图 / 义务 / 代码)\n\n"
    )
    return (
        _persona_line(skill_md_path, "audit", "M-0.5 §5")
        + _INDEPENDENCE_BLOCK
        + "\n\n## 待审计的提交 (commit gate 触发审计, 你独立核——不是再跑机械检查, 是语义层复核)\n"
        f"- envelope_event_id: {envelope_event_id}\n"
        f"- audit_triggered_event_id: {audit_triggered_event_id}\n"
        f"- trigger_reason (为什么触发审计): {trigger_reason}\n"
        f"- audit_scope (触发的主体/原因明细): {audit_scope}\n"
        f"- repo (你的 cwd): {repo_dir}\n\n"
        "## envelope 内容 (提交者声明改了什么 / 声明的义务 / self_check)\n"
        f"{envelope_summary}\n\n"
        + capsule_block
        + "## 你独立核的事 (M-0.5 §5, 对触发原因下判断, 证据不足支撑'做对了'就偏向 fail)\n"
        "1. 触发的那个语义问题, 在这次真 diff 里到底处理对没有? (`git -C <repo> show <commit>` 真看)\n"
        "2. self_check 自报失败的项现在过没过? 提交者是不是知道失败还硬提交?\n"
        "3. 声明 maintained 的义务在 diff 里真维护了, 还是只是嘴上声明?\n"
        "4. supersede 是想清楚的演进, 还是错误反复横跳 (振荡)?\n\n"
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + '{"audit_verdict": {"verdict": "pass|fail|conditional_pass", '
        '"passed": <bool — verdict=fail↔false, pass/conditional_pass↔true>, '
        '"findings": [{"description": "<具体发现+证据>", "severity": "low|medium|high|critical"}], '
        '"recommended_action": "<fail/conditional_pass 时给动作; pass 可空>", '
        '"confidence": <0.0-1.0>, "summary": "<一句话裁决依据>"}}\n'
        "verdict=fail 时 findings 必须非空且每条带具体证据 (你能也应当 disprove)。"
    )


def build_closure_audit_fork_prompt(
    *,
    skill_md_path: str,
    repo_dir: Path,
    task_id: str,
    plan_id: str,
    done_criteria: list[str],
    artifact_ref_type: str,
    artifact_ref_id: str,
    audit_triggered_event_id: str,
    capsule_text: str = "",
) -> str:
    """closure-scoped 审计 fork prompt — 独立审计员核 "artifact 是否兑现被关 task 的 done_criteria".

    与 build_audit_fork_prompt 是 sibling: 同一独立性约束 + 同一 JSON 输出 schema
    ({"audit_verdict": {verdict, passed, findings, recommended_action, confidence, summary}}), 使下游
    run_closure_audit_fork / _coerce_verdict 解析零改。framing 换成 closure 版: 审的不是"一次提交对不对",
    是"某 commit/finding 到底有没有真兑现被关 task 的每条 done_criteria"。

    capsule_text 空 → fork 自行 Read/Grep (镜像 CLI audit capsule-fail 兜底; closure 无 envelope, 不复用
    assemble_audit_capsule)。
    """
    dc_lines = "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(done_criteria)) or (
        "  (无发布的 done_criteria — 自行读 task 判据)"
    )
    capsule_block = (
        f"## capsule 投喂的图上下文\n{capsule_text}\n\n"
        if capsule_text.strip()
        else "## capsule 上下文\n(无预渲染 capsule — 自行用 Read/Grep 检视概念图 / task 判据 / 代码)\n\n"
    )
    return (
        _persona_line(skill_md_path, "audit", "M-0.5 §5 (closure-scoped)")
        + _INDEPENDENCE_BLOCK
        + "\n\n## 待核的闭合 (某产物是否真兑现被关 task 的 done_criteria — 你独立核, 不背书)\n"
        f"- 被关 task: {task_id} (plan {plan_id})\n"
        f"- 声称的交付物: {artifact_ref_type} {artifact_ref_id}\n"
        f"- audit_triggered_event_id: {audit_triggered_event_id}\n"
        f"- repo (你的 cwd): {repo_dir}\n\n"
        "## 被关 task 的 done_criteria (逐条核, 每条须真兑现)\n"
        f"{dc_lines}\n\n"
        + capsule_block
        + "## 你独立核的事 (证据不足支撑'兑现'就偏向 fail — 不替关闭者背书)\n"
        f"1. commit/finding {artifact_ref_id} 是否真兑现 task {task_id} 的【每条】done_criteria?\n"
        f"   - commit → `git -C {repo_dir} show <sha>` 逐条核 diff 真做了那件事;\n"
        "   - finding → 读 finding 真核它承接的是不是这条 done_criteria;\n"
        "2. 有没有哪条 done_criteria 只是'看着像做了'但 diff/finding 里其实没兑现?\n"
        "3. 兑现是不是完整 (不是做了一半就声称 done-elsewhere)?\n\n"
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + '{"audit_verdict": {"verdict": "pass|fail|conditional_pass", '
        '"passed": <bool — verdict=fail↔false, pass/conditional_pass↔true>, '
        '"findings": [{"description": "<具体发现+证据>", "severity": "low|medium|high|critical"}], '
        '"recommended_action": "<fail/conditional_pass 时给动作; pass 可空>", '
        '"confidence": <0.0-1.0>, "summary": "<一句话裁决依据>"}}\n'
        "verdict=fail 时 findings 必须非空且每条带具体证据 (你能也应当 disprove '真兑现了')。"
    )


# M-1.5 §7.2 / F-08c 三维方法论 review fork 各自的 falsification lens (author-time 按 review_plan
# dimension 同步 spawn — RUN-059 件B)。每条 = (spec_ref, lens_job)。
METHOD_FORK_JOBS: dict[str, tuple[str, str]] = {
    "method-execution-path": (
        "F-08c 维度1",
        "执行路径模拟器 lens — 在脑里/用 Read+Bash 代码模拟执行, 追踪变量真实值, 尝试找一条具体执行 "
        "路径让 patch 崩 (边界值 / None / 空集合 / 并发 / 异常分支)。Falsification framing: 你的目标是 "
        "证伪'patch 做对了', 不是确认。",
    ),
    "method-consistency": (
        "F-08c 维度2",
        "内部一致性审计 lens — 交叉对比 doc / code / config / SKILL 里所有重复描述, 尝试找文档间矛盾让 "
        "patch 描述失真 (注释说 X 代码做 Y / SKILL 声明的字段代码没有 / spec 与实现漂移)。Falsification "
        "framing: 你的目标是找到一处描述与实现不一致。",
    ),
    "method-red-team": (
        "F-08c 维度3",
        "红队对抗 lens — 意图找攻击面 / 滥用面, 构造恶意场景让 patch 失败 (注入 / 越权 / 绕过校验 / "
        "fail-open / 资源耗尽)。Falsification framing: 你扮演试图让系统出错或被滥用的对手。",
    ),
}


def build_method_fork_prompt(
    *,
    fork_id: str,
    skill_md_path: str,
    repo_dir: Path,
    review_dimension: str,
    patch_summary: str,
    contract_summary: str,
    extra_context: str = "",
) -> str:
    """M-1.5 §7.2 / F-08c 方法论 review fork prompt — 独立 fork 按某维度 lens 尝试证伪 patch 产 findings.

    RUN-059 件B: author-time 编排 driver 读 ReviewPlanCreated.dimensions 后按 dimension 同步 spawn
    这个 fork (取代主 session 手动 Agent 调)。fork 在全新独立 context (无 Edit/Write) 装维度 SKILL.md
    人格, 按其 falsification lens 找 finding, 把 findings_proposal 返回给主 review session 去重 +
    finding-create。findings 可为空 (该维度没找到问题 — completed 仍 true); 真起不来 → ForkSpawnError
    fail-closed。每条 finding 须带 M-1.5 §3.2 扩展 (voi_rationale / target / falsification_evidence /
    closure_contract), 主 session 据此 emit canonical FindingCreated (写边界 model_validator 强制)。
    """
    spec_ref, lens_job = METHOD_FORK_JOBS.get(
        fork_id, ("F-08c", f"M-1.5 §7.2 方法论 review fork {fork_id}"),
    )
    extra_block = f"## 额外上下文\n{extra_context}\n\n" if extra_context.strip() else ""
    return (
        _persona_line(skill_md_path, fork_id, spec_ref)
        + _INDEPENDENCE_BLOCK
        + "\n\n## 你的 lens (维度职责)\n"
        f"{lens_job}\n\n"
        "## 待审 patch (你独立按本维度尝试证伪, 不背书)\n"
        f"- review_dimension: {review_dimension}\n"
        f"- patch_summary: {patch_summary or '(无 — 自行 git -C <repo> diff/show 看改动)'}\n"
        f"- contract (这次改动该满足的契约摘要): {contract_summary or '(无)'}\n"
        f"- repo (你的 cwd): {repo_dir} — 用 Read/Grep/Bash/`git -C {repo_dir} show` 真看代码\n\n"
        + extra_block
        + "## 你不做什么\n"
        "你不写 event log / 不 emit FindingCreated —— 你只产 findings_proposal 返回给主 review "
        "session, 由它跨维度去重 (review aggregate) 后逐条 emit canonical FindingCreated。\n\n"
        "## 每条 finding 必带字段 (主 session emit 时写边界 model_validator 强制, 缺则被拒)\n"
        "finding_id (本维度内唯一) / severity (critical|major|minor|observation|purple) / risk_surface "
        "(影响面, 非空) / target:{artifact, location} (定位到文件 + 行/符号) / description / "
        "review_dimension (= 你的维度 id) / voi_rationale (为什么这条值得修) / "
        "falsification_evidence:{attempt:'你怎么试图证伪', result:'证伪结果/复现'} / "
        "closure_contract:{closure_criteria:[{condition, verification_method, expected_result}]} "
        "(修好后怎么验, verification_method 优先 grep/test 让门能机器复算)。\n\n"
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + '{"findings_proposal": {"completed": true, "findings": [<上面 schema 的 finding 对象, '
        "本维度找到几条放几条>]}}\n"
        "findings 为空数组 = 本维度没找到问题 (合法, completed 仍 true)。completed=false 仅当你无法完成 "
        "分析 (并说明)。你能也应当用具体证据 disprove patch。"
    )


def build_verify_step_fork_prompt(
    *,
    skill_md_path: str,
    repo_dir: Path,
    finding_id: str,
    claim_summary: str,
    closure_contract_summary: str,
    verify_mode: str = "fix_after",
) -> str:
    """F-08d verify-step 独立 falsifier fork prompt — 尝试 disprove 一个声明 (三态), RUN-059 件B.

    verify_mode=fix_after: 被审者声明 finding 已修复闭合 — fork 独立尝试证伪'修好了', 三态裁决
    (closed / fix_insufficient / ripple_incomplete)。passed=True 仅当 closure 真达成。
    verify_mode=fix_after_degraded: 同 fix_after 三态, 但 finding 无正式 closure_contract (执行/maintenance
    顺手上报); bounded 范围 = finding 原文判据 + fixer 自报 criteria, 文案不冒充合约 (诚实告知 fork)。
    verify_mode=author_time: 被审者声明 finding 为真 — fork 独立尝试证伪'这是真问题', 三态裁决
    (verified / rejected_false_positive / unverified_inconclusive)。passed=True 仅当 finding 真成立。

    独立性 (Anthropic <1% FP 秘诀): fork 在全新 context, 看不到修复/审查过程, 只读 artifacts 自己跑;
    喂错声明 (假装修好了 / 假问题) → fork 应判 passed=False。spawn 失败 → ForkSpawnError fail-closed。
    """
    if verify_mode == "author_time":
        claim_kind = "被审者声明这是一条真 finding (值得修)"
        states = "verified (真成立) | rejected_false_positive (其实不是问题) | unverified_inconclusive (证据不足判不了)"
        passed_rule = "passed=True 仅当 verification_state=verified"
        # author_time: 喂的是 finding 的 target + 作者 falsification_evidence (独立复核'这是真问题')。
        context_line = (
            f"- finding 上下文 (target + 作者声称的 falsification_evidence, 你独立到 repo 复核是否真成立, "
            f"不信自报): {closure_contract_summary}"
        )
    elif verify_mode == "fix_after_degraded":
        claim_kind = "被审者声明 finding (无正式 closure_contract) 已修复闭合"
        states = "closed (真闭合) | fix_insufficient (原问题没修好) | ripple_incomplete (波及面没同步)"
        passed_rule = "passed=True 仅当 verification_state=closed"
        # degraded: 无合约可 bounded, 用 finding 原文判据 + fixer 自报 criteria 当 bounded 范围 (诚实, 不冒充合约)。
        context_line = (
            f"- 此 finding 无正式 closure_contract (执行/maintenance 顺手上报); bounded 范围 = 下面的 "
            f"finding 原文判据 + fixer 自报 criteria, 你按这个范围独立证伪'已闭合' (不假设有合约, 也不"
            f"自由 re-review): {closure_contract_summary}"
        )
    else:
        claim_kind = "被审者声明 finding 已按 closure_contract 修复闭合"
        states = "closed (真闭合) | fix_insufficient (原问题没修好) | ripple_incomplete (波及面没同步)"
        passed_rule = "passed=True 仅当 verification_state=closed"
        context_line = (
            f"- closure_contract (合约摘要, 按它 bounded verify, 不自由 re-review): {closure_contract_summary}"
        )
    return (
        _persona_line(skill_md_path, "verify-step", "F-08d / M-1.5 §3.3")
        + _INDEPENDENCE_BLOCK
        + "\n\n## 你的职责 (独立 falsifier — 尝试 disprove, 不背书)\n"
        f"{claim_kind}。你的任务【不是】确认它, 是【尝试证伪它】: 主动找一条理由说明这个声明站不住。"
        "找不到任何证伪理由 → 声明才成立。\n\n"
        "## 待验声明\n"
        f"- finding_id: {finding_id}\n"
        f"- claim (被审者声明): {claim_summary}\n"
        f"{context_line}\n"
        f"- repo (你的 cwd): {repo_dir} — 用 Read/Grep/Bash/`git -C {repo_dir} show` 自己跑 pattern, "
        "不信被审者自报\n\n"
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + '{"verify_verdict": {"passed": <bool>, '
        f'"verification_state": "<三态之一: {states}>", '
        '"verification_method": "<你怎么独立验的>", '
        '"falsification_attempt": "<你尝试证伪的具体过程, 非空>", '
        '"evidence": "<支撑你裁决的具体证据>"}}\n'
        f"{passed_rule}。falsification_attempt 必须非空 (没尝试证伪 = 没独立验)。"
    )


def build_dispatch_capsule_prompt(
    *,
    role_slash: str,
    task: str,
    must_return: str,
    why_this_recipient: str = "",
    known_facts: Sequence[str] = (),
    files_to_read: Sequence[str] = (),
    must_not_do: Sequence[str] = (),
) -> str:
    """非 fork 消费方 (goal⑥) —— 用通用 Capsule 机制装配 bg 会话派发信 / handoff 交接的派发信。

    这是 @capsule-general-context-transfer@v1『非 fork 下游: bg 会话派发信 / handoff 交接』
    消费方的代码路径: 派发信不再各处硬编码自己的字段拼接, 而是把 curated push 内容装进通用
    Capsule (8 字段结构模板 + task/must_return 最小必含对 fail-closed) 再 render。role_slash
    在正文前装人格 (`/execution` 等, 持久人格住各自 skill —— handoff 只钩判断不重教手册),
    Capsule.render() 出结构化正文。

    与 gate fork builders (既有主消费方) 共用同一 Capsule 载体 —— 证明 capsule 组装已从 fork
    专用解耦为通用传递格式 (fork 与非 fork 下游同源装配同一 8 字段结构)。

    落地边界 (债 debt-b23f6237afb3 追踪): 现网 forward-chain 派发信生成器
    l2.dispatch_templates.generate_forward_chain_condition_text 仍硬编码自己的字段拼接 (goal_hook
    + handoff + completion + recovery + 硬约束); 把它迁移到经本机制装配 (那份拼接即本 8 字段的
    实例) 是后续 —— 它的写集不在本 task 的 write_set, 不在本 task 一并改 (avoid scope drift)。
    """
    capsule = Capsule(
        task=task,
        must_return=must_return,
        why_this_recipient=why_this_recipient,
        known_facts=known_facts,
        files_to_read=files_to_read,
        must_not_do=must_not_do,
    )
    slash = role_slash if role_slash.startswith("/") else f"/{role_slash}"
    return f"{slash}\n\n{capsule.render()}\n"


def _output_schema_block(result_key: str, *, check_ids: list[str]) -> str:
    """self-check 类 fork 的结构化输出 schema 块."""
    sample = ", ".join(
        f'{{"check_id": "{cid}", "status": "passed|failed", "evidence": "<具体证据>"}}'
        for cid in check_ids[:2]
    )
    return (
        "## 输出 (你最后一条消息【只】输出这个 JSON, 不要别的文字)\n"
        + _PURE_JSON_CONSTRAINT
        + "schema (顶层就是这个对象, passed 用 JSON bool 不要字符串):\n"
        f'{{"{result_key}": {{"passed": <bool — 5 项全 passed 才 true>, '
        f'"blocking_checks": [{sample}, ...全 5 项], "summary": "<一句话总结>"}}}}\n'
        "passed=false 时务必在对应 check 的 evidence 写清为什么 failed (你能也应当 disprove)。"
    )


__all__ = [
    "CONSENSUS_FORK_JOBS",
    "INTERVIEW_FORK_JOBS",
    "METHOD_FORK_JOBS",
    "Capsule",
    "CapsuleContractError",
    "build_advisor_consult_prompt",
    "build_audit_fork_prompt",
    "build_closure_audit_fork_prompt",
    "build_consensus_fork_prompt",
    "build_dispatch_capsule_prompt",
    "build_execution_self_check_prompt",
    "build_fix_self_check_prompt",
    "build_interview_conops_construct_prompt",
    "build_interview_consistency_verify_prompt",
    "build_interview_fork_prompt",
    "build_interview_influence_diagram_prompt",
    "build_interview_prep_research_prompt",
    "build_interview_sdm_reverse_prompt",
    "build_method_fork_prompt",
    "build_verify_step_fork_prompt",
]
