"""R01 §2 — ActualImpactSetRecoverer + 精确率/召回率: 从真实后续事件回收 AIS, 度量 CIS。

# concept source: c-actual-impact-set-recovery-and-precision-recall@v1
#   实际影响集 AIS = 一次变更**真正**碰了哪些概念, 由系统从**真实后续事件**回收 (post-hoc),
#   绝不让 agent 自己声明 (那等于让运动员当裁判, 与 SIS 声明门同一条纪律的反面: SIS 是
#   "我打算碰什么"、小、可声明; AIS 是"最终真碰了什么"、由证据派生)。
#   AIS 是 T-R01-3 的候选影响集 CIS (candidate_impact_set) 的**被比对方**:
#     precision = |CIS ∩ AIS| / |CIS|   —— 系统预测的波及面里有多少真命中 (过估治理)
#     recall    = |CIS ∩ AIS| / |AIS|   —— 真实波及面里有多少被系统预测到 (漏报治理)
#     CIS \\ AIS = FPIS (假阳性影响集, 过估部分);  AIS \\ CIS = 漏报 (欠估部分)。

## 为什么 AIS 必须"系统派生", 不能声明

经典 CIA (Bohner & Arnold, *Software Change Impact Analysis*): SIS ⊆ CIS ⊆ System; AIS 是变更
落地后**实测**的影响, 用来度量 CIS 估得准不准。若 AIS 也靠人声明, 它就和 SIS 一样带主观偏差,
拿它当"真值"去标定 CIS 就失了意义。所以本模块从 canonical 事件流回收 AIS —— 与 m04 的
readset/writeset 系统派生范式同源 (write_set 从 git 派生, 不靠 agent 填), 只是派生对象换成
"概念级实际触达"。

## AIS 的三类真实证据 (event carriers)

一次变更落地后, canonical 里"实际碰了哪些概念"散落在三处, 本模块按 id 求并 (同 concept_id 空间):

- **TransactionEnvelopeSubmitted.write_set** 中 `entity_type == "concept"` 的项 —— 最强信号:
  这是总闸真正受理的"这个 envelope 写了哪些概念"。是 AIS 的主源。
- **NodeTouched** 中 `target_entity_type == "concept"` 且 `touch_type == "write"` —— 概念图被写的
  直接痕迹。⚠ 排除 `stub-*` 占位 (那是非概念事件投影成的 stub 节点, 不是真概念变更)。
- **state_transition 类 payload 的 `affected_concept_ids`** (如 TaskRunCompleted) —— 变更声明的
  受影响概念。注意这条偏"声明"性质, 默认纳入但可关 (`include_declared=False`) 以求纯实测 AIS。

## 作用域 (scope): AIS 是"针对某次变更"的

AIS 永远绑定一次具体变更。回收时按 `run_id` / `since_seq` 窗口圈定属于这次变更的事件, 只从
窗口内的 carrier 回收 —— 否则把别人的概念触达也算进来, 度量失真。caller 给窗口, 本模块不猜。

## 空集约定 (显式, 不藏在除零里)

- |CIS| = 0: 没预测任何东西 → 没有假阳性。precision = 1.0 (空预测无错), 但若 AIS 非空则 recall = 0。
- |AIS| = 0: 没有真实触达可回收 → recall = 1.0 (空真值已全覆盖)。若 CIS 非空, 那些都是假阳性 → precision 反映。
原始计数 (tp/fp/fn) 一律暴露, caller 不认同约定可自行据计数判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from towow.l1.cia.candidate_impact_set import CandidateImpactSet


# 概念图里非真概念的占位前缀 (非概念事件投影成的 stub 节点)。AIS 回收必须排除。
_STUB_PREFIX = "stub-"


def _is_real_concept(concept_id: str) -> bool:
    """真概念 id (排除 stub 占位与空串)。"""
    return bool(concept_id) and not concept_id.startswith(_STUB_PREFIX)


@dataclass(frozen=True)
class AISMember:
    """AIS 的一个成员 —— 一个**实测**被碰的概念, 带"从哪条证据回收来的" provenance。"""

    concept_id: str
    via_envelope_write: bool  # 出现在某 envelope 的 write_set concept 项 (最强)
    via_node_touched: bool  # 出现在 NodeTouched(concept, write)
    via_declared: bool  # 出现在 state_transition.affected_concept_ids (声明性)
    source_event_ids: tuple[str, ...]  # 揭示它的事件 id (升序去重)

    def as_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "via_envelope_write": self.via_envelope_write,
            "via_node_touched": self.via_node_touched,
            "via_declared": self.via_declared,
            "source_event_ids": list(self.source_event_ids),
        }


@dataclass(frozen=True)
class ActualImpactSet:
    """一次变更的实际影响集 — 从真实后续事件回收, 每成员带证据 provenance。"""

    members: tuple[AISMember, ...]  # 按 concept_id 升序
    run_id: str | None  # 回收窗口绑定的变更 (None = 未按 run 圈定)
    source_event_count: int  # 扫过的 carrier 事件数 (回收覆盖度自证)

    def concept_ids(self) -> set[str]:
        return {m.concept_id for m in self.members}

    def as_dict(self) -> dict[str, Any]:
        return {
            "members": [m.as_dict() for m in self.members],
            "run_id": self.run_id,
            "source_event_count": self.source_event_count,
        }


@dataclass(frozen=True)
class ImpactSetAccuracy:
    """CIS 对 AIS 的精确/召回度量 + 原始集合 (FPIS / 漏报暴露给下游剪枝与诊断)。"""

    precision: float
    recall: float
    f1: float
    true_positives: tuple[str, ...]  # CIS ∩ AIS (升序)
    false_positives: tuple[str, ...]  # CIS \ AIS = FPIS (过估, 升序)
    false_negatives: tuple[str, ...]  # AIS \ CIS = 漏报 (欠估, 升序)
    cis_size: int
    ais_size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "true_positives": list(self.true_positives),
            "false_positives": list(self.false_positives),
            "false_negatives": list(self.false_negatives),
            "cis_size": self.cis_size,
            "ais_size": self.ais_size,
        }


def _concept_id_set(impact: Any) -> set[str]:
    """从 set/iterable/CIS/AIS 对象统一取 concept_id 集 (鸭子类型, 避免循环 import)。"""
    if impact is None:
        return set()
    if isinstance(impact, (set, frozenset)):
        return {str(x) for x in impact}
    concept_ids = getattr(impact, "concept_ids", None)
    if callable(concept_ids):
        return {str(x) for x in concept_ids()}
    # 退化: 任意可迭代的 id 串
    return {str(x) for x in impact}


def precision_recall(
    cis: set[str] | CandidateImpactSet | ActualImpactSet | Iterable[str],
    ais: set[str] | ActualImpactSet | Iterable[str],
) -> ImpactSetAccuracy:
    """算 CIS 对 AIS 的 precision/recall/f1 + FPIS / 漏报集。

    `cis`/`ais` 可以是 set、CandidateImpactSet、ActualImpactSet, 或任意 concept_id 可迭代。
    空集约定见模块 docstring (显式, 计数一律暴露)。
    """
    cis_ids = _concept_id_set(cis)
    ais_ids = _concept_id_set(ais)

    tp = cis_ids & ais_ids
    fp = cis_ids - ais_ids
    fn = ais_ids - cis_ids

    # 显式空集约定 (不藏在除零里)。
    precision = 1.0 if not cis_ids else len(tp) / len(cis_ids)
    recall = 1.0 if not ais_ids else len(tp) / len(ais_ids)
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

    return ImpactSetAccuracy(
        precision=precision,
        recall=recall,
        f1=f1,
        true_positives=tuple(sorted(tp)),
        false_positives=tuple(sorted(fp)),
        false_negatives=tuple(sorted(fn)),
        cis_size=len(cis_ids),
        ais_size=len(ais_ids),
    )


class ActualImpactSetRecoverer:
    """从 canonical 事件流回收一次变更的 AIS (系统派生, 不靠声明)。

    `recover(events, ...)` 接一串规范化事件 (dict, 形同 EventLog 落盘的 {event_id, event_type,
    payload, seq, ...}), 按窗口圈定属于本次变更的事件, 从三类 carrier 取并集回收实测概念触达。
    本模块只做"扫 carrier + 取并 + 汇 provenance", 不查询图、不挖共变 (那是 CIS 两路的职责)。
    """

    def __init__(self, *, include_declared: bool = True) -> None:
        # include_declared=False → 只认 envelope write / NodeTouched 两类**实测**证据, 丢声明性
        # affected_concept_ids, 求"纯客观" AIS (代价: 漏掉只在声明里出现、未落 write 的概念)。
        self._include_declared = include_declared

    def recover(
        self,
        events: Iterable[dict[str, Any]],
        *,
        run_id: str | None = None,
        since_seq: int | None = None,
    ) -> ActualImpactSet:
        """从事件回收 AIS。

        - `run_id`: 只回收归属此 run 的事件 (按 payload.run_id 或 payload.after_state.run_id 匹配)。
          None → 不按 run 过滤 (caller 自行用别的窗口圈定, 如已切好片的事件列表)。
        - `since_seq`: 只回收 seq >= since_seq 的事件 (变更声明之后的窗口)。
        """
        env_write: dict[str, set[str]] = {}  # concept_id → 揭示它的 envelope 事件 id 集
        node_touched: dict[str, set[str]] = {}
        declared: dict[str, set[str]] = {}
        scanned = 0

        for e in events:
            if not isinstance(e, dict):
                continue
            payload = e.get("payload") or {}
            if since_seq is not None and (e.get("seq") or 0) < since_seq:
                continue
            if run_id is not None and not self._event_belongs_to_run(payload, run_id):
                continue

            etype = e.get("event_type") or ""
            eid = str(e.get("event_id") or "")
            touched_here = False

            if etype == "TransactionEnvelopeSubmitted":
                for w in payload.get("write_set") or []:
                    if isinstance(w, dict) and w.get("entity_type") == "concept":
                        cid = str(w.get("entity_id") or "")
                        if _is_real_concept(cid):
                            env_write.setdefault(cid, set()).add(eid)
                            touched_here = True

            elif etype == "NodeTouched":
                if payload.get("target_entity_type") == "concept" and payload.get("touch_type") == "write":
                    cid = str(payload.get("target_entity_id") or "")
                    if _is_real_concept(cid):
                        node_touched.setdefault(cid, set()).add(eid)
                        touched_here = True

            if self._include_declared:
                for cid in self._declared_concepts(payload):
                    if _is_real_concept(cid):
                        declared.setdefault(cid, set()).add(eid)
                        touched_here = True

            if touched_here:
                scanned += 1

        all_ids = set(env_write) | set(node_touched) | set(declared)
        members = tuple(
            AISMember(
                concept_id=cid,
                via_envelope_write=cid in env_write,
                via_node_touched=cid in node_touched,
                via_declared=cid in declared,
                source_event_ids=tuple(
                    sorted(env_write.get(cid, set()) | node_touched.get(cid, set()) | declared.get(cid, set())),
                ),
            )
            for cid in sorted(all_ids)
        )
        return ActualImpactSet(members=members, run_id=run_id, source_event_count=scanned)

    @staticmethod
    def _event_belongs_to_run(payload: dict[str, Any], run_id: str) -> bool:
        if payload.get("run_id") == run_id:
            return True
        after = payload.get("after_state") or {}
        return isinstance(after, dict) and after.get("run_id") == run_id

    @staticmethod
    def _declared_concepts(payload: dict[str, Any]) -> list[str]:
        """从 state_transition 类 payload 取 affected_concept_ids (顶层或 after_state 内)。"""
        out: list[str] = []
        top = payload.get("affected_concept_ids")
        if isinstance(top, list):
            out.extend(str(x) for x in top)
        after = payload.get("after_state") or {}
        if isinstance(after, dict) and isinstance(after.get("affected_concept_ids"), list):
            out.extend(str(x) for x in after["affected_concept_ids"])
        return out
