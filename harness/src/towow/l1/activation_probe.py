"""activation-multisource-cli-probe@v1 — C/E 型激活证据的纯同步探针。

# spec source:
#   concept activation-multisource-cli-probe@v1 (ConceptCreated, source_brief evt-077bbd8735a9483098110a580a126d97)
#     "C/E 型(部署面漂移 / 消费端不知情)及其他需'账本之外代码/文本核验'的缺口的激活证据采集契约。
#      C 型要比对 src/harness/.claude/root 多面部署是否一致，E 型要核验消费端 skill 文本/派发模板
#      是否真引用该基建。契约保证：本探针做成纯 CLI 同步检查(无 fork、无子会话)，直接多源比对后
#      返回激活证据或欠账判定。复用既有 deploy-surface-byte-identity@v2 的三面部署字节比对能力。"
#   concept activation-gap-type-taxonomy@v1 —— gap_type 值域 A-G (C=部署面漂移, E=消费端不知情)。

为何纯同步 (inv5, 硬前提):
    采访阶段 fork 通道两次故障已实锤其不可靠 —— activation-acceptance-gate 是每次闭合必经的强制门,
    强制门上线不能依赖会挂的 fork。故本探针无 agent fork、无子会话, 只做进程内文件读 + 字节/文本比对
    (允许同步 subprocess 如 diff, 但这里直接复用 l0 byte-identity 的 sha256 比对, 更稳且无外部依赖)。

为何独立模块函数而非注册 ./tw 命令:
    cli/main.py 正被本波多个冻结任务并发写, 编辑它会撞跨 plan 写集致冻结被拒。故本探针实现为 l1 模块
    函数 probe_ce_activation(...), 由 activation-acceptance-gate (下游 T-ACT-4) 闭合时对 C/E 型缺口
    内联调用, 不占用 CLI 表达面。

【失败方向 · fail-closed】(镜像 activation-evidence-organic-only@v1 的判据方向)
    放行方向的误判是灾难性的: 把"没真接线"误判为"已激活"= 静默放行假激活 = 击穿整个 goal。故凡
    **无可核验的比对项** (C: 无 src↔现存 live 部署对; E: 无 marker 或无消费文件) → 绝不返回激活证据,
    一律 fail-closed 判为欠账 (activated=False)。宁可把真激活误判为欠账 (让 activation-debt 挂一条可
    追踪欠条, 后续有人来核), 也绝不把假激活放行。

【纯边界】无副作用、不 emit、不读账本。只据传入的 src/live 路径与 marker 做本地比对; 是否登记
    activation-debt、是否放行闭合, 由调用方 (activation-acceptance-gate) 据本结果决定, 不在本探针。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from towow.l0.commit_gate.deploy_identity_check import WorkMdRender, check_deploy_identity
from towow.l0.commit_gate.deploy_manifest import (
    deploy_surface_roots,
    enumerate_pairs,
    src_tail,
)
from towow.schemas.enums import ActivationGapType

_DEPLOY_SURFACE = "deploy"
_CONSUMER_SURFACE = "consumer"


@dataclass(frozen=True)
class ActivationProbeResult:
    """C/E 型探针的判定结果 —— 激活证据 xor 欠账判定。

    activated: True = 激活证据 (多源一致 / 消费端已引用); False = 欠账判定 (漂移 / 不知情 / 无可核验)。
    gap_type: 欠账时标 C 或 E (引 activation-gap-type-taxonomy@v1); 激活时为 None。
    surface: 本次核验的面 —— "deploy" (C 型部署面) | "consumer" (E 型消费面)。
    evidence: 人读证据 / 判据原因 (调用方登 activation-debt 时可原样带进 description)。
    checked_count: 真比对的项数 (C: 现存 live 面数; E: 检过的消费文件数)。0 = 无可核验 → 必 fail-closed。
    """

    activated: bool
    gap_type: ActivationGapType | None
    surface: str
    evidence: str
    checked_count: int


def _existing_roots(surface_roots: list[Path] | None) -> list[Path]:
    roots = surface_roots if surface_roots is not None else deploy_surface_roots()
    return [r for r in roots if r.exists()]


def _count_verifiable_faces(
    touched_paths: list[str],
    *,
    skills_src: Path | None,
    glue_src: Path | None,
    existing_roots: list[Path],
) -> int:
    """本 run 碰过的 src 部署源里, 有多少个 (src↔现存 live) 面真能比对 —— 复用 deploy-surface-byte-
    identity 的部署对枚举 (enumerate_pairs) + 尾段归一 (src_tail) 单一真源, 不另造映射。

    用于把"无可核验" (vacuous, 无对/live 面全缺) 从"真比对过" 里**结构化**区分开 (不靠解析 evidence
    字符串), 让 fail-closed 判据不脆弱。
    """
    pairs = enumerate_pairs(skills_src=skills_src, glue_src=glue_src)
    touched_tails = {src_tail(t) for t in touched_paths}
    faces = 0
    for pair in pairs:
        if src_tail(str(pair.source)) not in touched_tails:
            continue
        for root in existing_roots:
            if (root / pair.live_rel).exists():
                faces += 1
    return faces


def _probe_c_deploy_drift(
    touched_paths: list[str],
    *,
    skills_src: Path | None = None,
    glue_src: Path | None = None,
    surface_roots: list[Path] | None = None,
    work_md_render: WorkMdRender | None = None,
) -> ActivationProbeResult:
    """C 型 (部署面漂移): 比对 src/harness/.claude/root 三面部署是否逐字节一致。

    真比对复用 l0 的 check_deploy_identity (= activation-acceptance 引的 deploy-surface-byte-identity
    门函数本体, 非 mock): 对本 run 碰过的 src 部署源, 比 sha256(render(src)) == sha256(现存 live)。
      - 无任何 (src↔现存 live) 可核验面 → fail-closed 判 C 型欠账 (无从出示"已激活"的正向证据)。
      - 现存面全逐字节一致 → 激活证据。
      - 任一现存面 ≠ src (漂移 / 渲染类缺 render_fn 无法核) → C 型欠账。
    """
    existing_roots = _existing_roots(surface_roots)
    verifiable_faces = _count_verifiable_faces(
        touched_paths,
        skills_src=skills_src,
        glue_src=glue_src,
        existing_roots=existing_roots,
    )
    if verifiable_faces == 0:
        return ActivationProbeResult(
            activated=False,
            gap_type=ActivationGapType.C_DEPLOY_SURFACE_DRIFT,
            surface=_DEPLOY_SURFACE,
            evidence=(
                "无可核验的部署面 (本 run 无 src↔现存 live 部署对) — 无法出示激活证据, "
                "fail-closed 判 C 型欠账"
            ),
            checked_count=0,
        )
    passed, evidence = check_deploy_identity(
        touched_paths,
        work_md_render=work_md_render,
        skills_src=skills_src,
        glue_src=glue_src,
        surface_roots=surface_roots,
    )
    if passed:
        return ActivationProbeResult(
            activated=True,
            gap_type=None,
            surface=_DEPLOY_SURFACE,
            evidence=evidence,
            checked_count=verifiable_faces,
        )
    return ActivationProbeResult(
        activated=False,
        gap_type=ActivationGapType.C_DEPLOY_SURFACE_DRIFT,
        surface=_DEPLOY_SURFACE,
        evidence=evidence,
        checked_count=verifiable_faces,
    )


def _read_text_safe(path: Path) -> str:
    """读消费文件文本; 解码失败用 replace 兜底 (marker 搜索不需要精确解码, 只需能扫子串)。"""
    return path.read_text(encoding="utf-8", errors="replace")


def _probe_e_consumer_awareness(
    reference_markers: list[str],
    consumer_paths: list[Path],
) -> ActivationProbeResult:
    """E 型 (消费端不知情): 核验消费端 skill 文本 / 派发模板是否真引用该基建。

    reference_markers: 该基建"已被消费端引用"的必现标记 (如命令名 / 概念 id / 函数名)。每个 marker
      至少要在一份消费文件里出现, 才算消费端知情。
    consumer_paths: 待核验的消费文件 (SKILL.md / 派发模板等)。
      - 无 marker 或无现存消费文件 → fail-closed 判 E 型欠账 (无从核验知情)。
      - 有 marker 全部命中 → 激活证据。
      - 任一 marker 在所有消费文件里都缺席 → E 型欠账 (基建在但消费端仍教旧法)。
    """
    markers = [m for m in reference_markers if m.strip()]
    existing = [p for p in consumer_paths if p.is_file()]
    if not markers or not existing:
        return ActivationProbeResult(
            activated=False,
            gap_type=ActivationGapType.E_CONSUMER_UNAWARE,
            surface=_CONSUMER_SURFACE,
            evidence=(
                "无可核验消费面 "
                f"(marker={len(markers)}, 现存消费文件={len(existing)}) — fail-closed 判 E 型欠账"
            ),
            checked_count=len(existing),
        )
    corpus = "\n".join(_read_text_safe(p) for p in existing)
    missing = [m for m in markers if m not in corpus]
    if missing:
        return ActivationProbeResult(
            activated=False,
            gap_type=ActivationGapType.E_CONSUMER_UNAWARE,
            surface=_CONSUMER_SURFACE,
            evidence=(
                f"消费端未引用基建 marker {missing} (检 {len(existing)} 份消费文件) — "
                "E 型欠账 (基建在但消费端仍教旧法)"
            ),
            checked_count=len(existing),
        )
    return ActivationProbeResult(
        activated=True,
        gap_type=None,
        surface=_CONSUMER_SURFACE,
        evidence=(
            f"消费端 {len(existing)} 份文件全部引用 {len(markers)} 个基建 marker — 消费端已知情"
        ),
        checked_count=len(existing),
    )


def probe_ce_activation(
    *,
    gap_type: ActivationGapType,
    touched_paths: Iterable[str] | None = None,
    reference_markers: Iterable[str] | None = None,
    consumer_paths: Iterable[str | Path] | None = None,
    skills_src: Path | None = None,
    glue_src: Path | None = None,
    surface_roots: list[Path] | None = None,
    work_md_render: WorkMdRender | None = None,
) -> ActivationProbeResult:
    """C/E 型激活证据的纯同步探针入口 —— 多源比对后返回激活证据 or 欠账判定。

    被 activation-acceptance-gate@v1 在闭合时对 C/E 型缺口内联调用。纯同步 (无 fork / 无子会话)。

    gap_type: 必为 C_DEPLOY_SURFACE_DRIFT 或 E_CONSUMER_UNAWARE (本探针只管这两型)。
    C 型入参: touched_paths (本 run 碰过的 src 部署源, git-root-relative) + 可选 skills_src/glue_src/
      surface_roots/work_md_render (缺省走真源 —— importlib.resources 定位 src、deploy_surface_roots
      定位两个 .claude 面)。
    E 型入参: reference_markers (必现标记) + consumer_paths (消费文件)。
    skills_src/glue_src/surface_roots 可注入 —— 供测试喂 fixture 做真实三面比对 (非 mock)。

    非 C/E 型 → fail-loud ValueError (不静默返回, 免得调用方误以为"已核"): 其他型的账本外核验载体
    (A/B/D/F/G) 另有归属, 不由本探针冒名处理。
    """
    if gap_type is ActivationGapType.C_DEPLOY_SURFACE_DRIFT:
        return _probe_c_deploy_drift(
            list(touched_paths or []),
            skills_src=skills_src,
            glue_src=glue_src,
            surface_roots=surface_roots,
            work_md_render=work_md_render,
        )
    if gap_type is ActivationGapType.E_CONSUMER_UNAWARE:
        return _probe_e_consumer_awareness(
            list(reference_markers or []),
            [Path(p) for p in (consumer_paths or [])],
        )
    raise ValueError(
        f"probe_ce_activation 只核验 C/E 型缺口, 收到 gap_type={gap_type.value!r} "
        "(其余型的账本外核验载体另有归属, 本探针不冒名处理)"
    )


__all__ = ["ActivationProbeResult", "probe_ce_activation"]
