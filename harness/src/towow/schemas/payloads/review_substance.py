"""M-1.5 review 实质判据 (RUN-093 审计 v2 R3 / 1.5-a/b): VoI 锚定 + finding 可定位.

# spec source:
#   04-l1-intelligence/M-1.5-review-skill-detailed-design.md
#     §自检 (L588-594) — finding 提交前 5 测试:
#       #1 VoI 测试 (改善 = 0 → 丢弃) / #2 可定位测试 (改哪个文件/层, 不能 → 补 target)
#       #4 Spec 关联测试 ("指向哪个 spec / obligation / 隐含约束? 不能指向 → 漫游, 丢弃")
#     §3.2 (L203-206) target.location = "文件:行 / 函数 / entity ID"
#     §8 维护表 (L1101) "VoI criteria 太泛 = 等于没限制"
#
# 审计 v2 (REALITY-AUDIT-V2-FINDINGS.md §1 / §1.5): §3.2 字段**存在性**物理强制了
# (RUN-035 enforce_review_context_fields 拒空 voi_rationale), 但**实质**全停在提示词级 —
# "任何改进都算" / 无锚 location 全过门 (探针实证)。本模块把 spec 自检 #1/#2/#4 的**机械下限**
# 从提示词落成纯函数 (无 I/O), 由 finding.py 写边界 model_validator 物理强制:
#   - VoI 必须锚定真目标 (结构锚 / 领域锚词 / 与 risk_surface·target 共享实义), 非泛泛价值断言;
#   - finding.target.location 必须是真定位符 (文件:行 / 函数 / entity id / 行号), 非散文。
#
# 注: 语义级 "改善 > 0" 判断仍归 review fork / verify-step (本就该在 LLM 层); 这里只焊
# **物理下限** —— 把"零锚定的泛泛 VoI / 散文 location"这类无摩擦穿门的最弱形态拦下。
"""

from __future__ import annotations

import re

# ── 结构锚 token: 任一命中即视为"指向了一个具体对象" (spec test #4 / §3.2 location 形态) ──
#   文件名带扩展 / :行号 / L行号 / #L行号 / 函数调用 foo( / entity-id 前缀 / 模块点路径 / spec ref。
_STRUCTURAL_ANCHOR_RE = re.compile(
    r"""(?xi)
    (?: [\w./-]+\.(?:py|md|json|jsonl|sh|ya?ml|toml|txt|js|ts|tsx|sql|cfg|ini|lock|rs|go|c|h|cpp|html)\b )  # 文件.扩展
    | (?: :\d+ )                                  # :行号 (file:line)
    | (?: \bL\d+\b )                              # L42 行号
    | (?: \#L\d+ )                                # github #L42
    | (?: \w\(\)? )                               # foo( / foo()  函数/符号
    | (?: \b(?:evt|debt|obl|concept|task|plan|brief|finding|rp|ent|cap|node|sess|env|fork|cmt)-[0-9a-z] )  # entity id
    | (?: \bM-\d )                                # spec module ref M-1.5
    | (?: § )                                     # spec 节号
    | (?: \b\w+(?:\.\w+){2,}\b )                  # 点路径 a.b.c (模块/属性)
    """,
)

# ── 领域锚词: 指向 spec / obligation / 隐含约束 / 契约概念 (spec test #4 词汇) ──
#   小而可辩护的集合 (取 spec 自检 #4 + §3 契约词汇), 非无限增长的关键词糊弄。
_DOMAIN_ANCHOR_KEYWORDS: tuple[str, ...] = (
    # ASCII (spec / 契约 / 不变量 vocabulary)
    "done_criteria", "completion_condition", "obligation", "contract", "invariant",
    "criterion", "criteria", "spec", "closure", "provenance", "watermark",
    "write_set", "read_set", "risk_surface", "fail-closed", "fail_closed", "owner-guard",
    # CJK
    "红线", "不变量", "合约", "约束", "闭合", "风险面", "越界", "回链", "单调", "幂等",
    "证伪", "可重放", "重放", "前置条件", "后置条件", "契约", "派生", "覆盖率",
)

# 实义 token: ASCII 字母数字 ≥3 (跳过 the/and 等过短) 用于和 risk_surface/target 比对。
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{3,}")
# CJK 连续 2 字 (中文无空格分词 → 用 bigram 近似实义片段)。
_CJK_RE = re.compile(r"[一-鿿]")
# 过泛的英文 stopword (出现也不算 anchor — 防 "the code" 类蒙混)。
_STOPWORDS: frozenset[str] = frozenset(
    {"the", "code", "this", "that", "and", "for", "with", "any", "all", "fix",
     "bug", "issue", "improve", "improvement", "quality", "better", "cleaner",
     "readability", "general", "various", "etc"},
)


def has_structural_anchor(text: str) -> bool:
    """text 含结构锚 (文件:行 / 函数 / entity-id / spec-ref / 点路径) → True。"""
    return bool(_STRUCTURAL_ANCHOR_RE.search(text or ""))


def has_domain_anchor_keyword(text: str) -> bool:
    """text 含领域锚词 (指向 spec/obligation/契约/不变量概念) → True。"""
    low = (text or "").lower()
    return any(kw in low for kw in _DOMAIN_ANCHOR_KEYWORDS)


def _ascii_tokens(text: str) -> set[str]:
    return {t.lower() for t in _ASCII_TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS}


def _cjk_bigrams(text: str) -> set[str]:
    chars = _CJK_RE.findall(text or "")
    # 仅由连续 CJK 字符序列生成 bigram (避免跨非 CJK 拼接)。
    bigrams: set[str] = set()
    seq: list[str] = []
    for ch in text or "":
        if _CJK_RE.match(ch):
            seq.append(ch)
        else:
            if len(seq) >= 2:
                bigrams.update(seq[i] + seq[i + 1] for i in range(len(seq) - 1))
            seq = []
    if len(seq) >= 2:
        bigrams.update(seq[i] + seq[i + 1] for i in range(len(seq) - 1))
    _ = chars  # noqa: keep readability — 单字不入 bigram
    return bigrams


def shares_substantive_token(text: str, references: tuple[str, ...]) -> bool:
    """text 与 references (risk_surface / target.artifact / target.location) 共享实义 token。

    ASCII ≥3 (去 stopword) 直接交集; CJK 用连续 2 字 bigram 交集 (中文无空格)。
    共享 = VoI 真指向了 finding 的具体主题 (非泛泛价值断言)。
    """
    ref_blob = " ".join(r for r in references if r)
    if not ref_blob.strip():
        return False
    if _ascii_tokens(text) & _ascii_tokens(ref_blob):
        return True
    return bool(_cjk_bigrams(text) & _cjk_bigrams(ref_blob))


def voi_rationale_substantive(
    voi_rationale: str,
    *,
    risk_surface: str = "",
    target_artifact: str = "",
    target_location: str = "",
) -> bool:
    """VoI 是否锚定真目标 (spec 自检 #1/#4 机械下限)。

    实质 = 任一: (a) 含结构锚; (b) 含领域锚词; (c) 与 risk_surface/target 共享实义 token。
    纯泛泛价值断言 ("任何改进" / "提升质量" / "可读性提升" / "并发安全" 且不指向具体主题) → False。
    """
    if not voi_rationale or not voi_rationale.strip():
        return False
    return (
        has_structural_anchor(voi_rationale)
        or has_domain_anchor_keyword(voi_rationale)
        or shares_substantive_token(
            voi_rationale, (risk_surface, target_artifact, target_location),
        )
    )


def finding_location_anchored(location: str) -> bool:
    """target.location 是否真定位符 (spec §3.2: 文件:行 / 函数 / entity ID), 非散文。

    结构锚命中 → True; 纯散文 ("somewhere" / "代码里某处" / "general") → False。
    """
    if not location or not location.strip():
        return False
    return has_structural_anchor(location)


__all__ = [
    "finding_location_anchored",
    "has_domain_anchor_keyword",
    "has_structural_anchor",
    "shares_substantive_token",
    "voi_rationale_substantive",
]
