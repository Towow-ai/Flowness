"""哨兵⑥ · CLI-RUN 噪音去重 (owner 实战#3 / TURN-ON-MONITORING A8)。

owner 实战痛点: 一个会话被误判死 → 反复发"继续"重发 → 一堆 CLI run 其实是同一个逻辑会话,
后来分不清谁是谁 (RUN006 类噪音)。哨兵要把这堆噪音**清干净 + 能分清谁是谁**, 否则观测面
自己就被噪音污染 (A2 误杀的下游残骸)。

## 本质 (为什么是纯函数 + 去重, 不是删数据)
不删任何 run 记录 (账本是真相源, 删=腐蚀); 只**折叠出逻辑身份**——把"同一逻辑会话的多次重发"
归并成一个 canonical run + 一串 noise run (标出它复制了谁、为什么算噪音)。这样:
- 观测面用 canonical_runs 算"真有几个逻辑会话"(不被重发灌水);
- noise_ratio 高 = 误杀/重发在发生 (A2 的烟), 喂回检测规则 A8;
- ambiguous = 连归属都判不清的 run (没 target_id), 是"分不清谁是谁"的硬残骸, 单列出来。

## 判噪音的规矩 (首条匹配, 保守)
一个 run 算噪音当且仅当:
  (a) kind 命中重发型 (continue/resume/retry/继续/redispatch) —— 对已存在逻辑会话的再发; 或
  (b) 同一 target 的重复 start (按 seq 留第一条 canonical, 其余 start 是孪生重发)。
target_id 缺失 → ambiguous (既不算 canonical 也不算可归属噪音, 是要 owner 看的硬残骸)。
判不准恒保守: 不轻易把一个 run 判成另一个的噪音 (误并 = 把两个真会话当一个, 比漏并更坏)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 重发型 kind: 对一个已存在逻辑会话再发一次 (不是新逻辑工作) = 噪音。
_RESEND_PATTERN = re.compile(
    r"continu|resume|re-?send|re-?dispatch|retry|继续|重发|重派", re.IGNORECASE
)


@dataclass(frozen=True)
class CliRun:
    """一条 CLI run 记录 (从账本/run 注册读, 纯输入)。

    run_id:    这条 run 的唯一 id。
    target_id: 它针对的逻辑会话/任务 (session_id / task_id)。缺失 = 归属判不清。
    kind:      这条 run 的性质 (start / continue / resume / retry / ...)。
    seq:       账本序号 (定 canonical 取第一条的次序; 不依赖墙钟)。
    """

    run_id: str
    target_id: str | None
    kind: str
    seq: int = 0


@dataclass(frozen=True)
class NoiseRun:
    run_id: str
    duplicates: str | None  # 它实际是哪个 canonical run 的重发 (None = 重发但 canonical 未知)
    reason: str             # "resend_kind" / "duplicate_start"


@dataclass(frozen=True)
class CliNoiseReport:
    canonical_runs: tuple[str, ...]          # 真·不同逻辑会话 (去重后) 的 run_id
    noise_runs: tuple[NoiseRun, ...]         # 重发/孪生 start 噪音
    ambiguous: tuple[str, ...]               # 归属判不清的 run_id (没 target)
    identities: dict[str, tuple[str, ...]]   # target_id → 它名下所有 run_id (谁是谁)
    noise_ratio: float                       # noise / 总可归属 run (0..1)

    @property
    def is_noisy(self) -> bool:
        """有任何重发噪音或归属不清的残骸 = 噪音面非空 (喂检测规则 A8)。"""
        return bool(self.noise_runs) or bool(self.ambiguous)


def dedup_cli_runs(runs: list[CliRun]) -> CliNoiseReport:
    """折叠 CLI run 噪音 → 逻辑身份。纯函数, 幂等于输入集; 不删 run, 只归类。

    canonical = 每个 target 的第一条非重发 start (按 seq); 其余对该 target 的发都是噪音。
    """
    # 按 target 分组 (保 seq 序, 缺 target 入 ambiguous)。
    by_target: dict[str, list[CliRun]] = {}
    ambiguous: list[str] = []
    for r in runs:
        if not r.target_id:
            ambiguous.append(r.run_id)
            continue
        by_target.setdefault(r.target_id, []).append(r)

    canonical: list[str] = []
    noise: list[NoiseRun] = []
    identities: dict[str, tuple[str, ...]] = {}

    for target, group in by_target.items():
        ordered = sorted(group, key=lambda r: (r.seq, r.run_id))
        identities[target] = tuple(r.run_id for r in ordered)
        canon_for_target: str | None = None
        for r in ordered:
            is_resend = _RESEND_PATTERN.search(r.kind or "") is not None
            if is_resend:
                # 重发型: 永远是噪音, 复制当前 target 的 canonical (可能还没 → None)。
                noise.append(NoiseRun(r.run_id, canon_for_target, "resend_kind"))
            elif canon_for_target is None:
                # 该 target 第一条非重发 start = canonical。
                canon_for_target = r.run_id
                canonical.append(r.run_id)
            else:
                # 已有 canonical 又来一条 start = 孪生重发 start。
                noise.append(NoiseRun(r.run_id, canon_for_target, "duplicate_start"))

    attributable = len(canonical) + len(noise)
    noise_ratio = (len(noise) / attributable) if attributable else 0.0
    return CliNoiseReport(
        canonical_runs=tuple(sorted(canonical)),
        noise_runs=tuple(noise),
        ambiguous=tuple(sorted(ambiguous)),
        identities=identities,
        noise_ratio=noise_ratio,
    )
