"""M-1.5 review 侧 migration-done-requires-old-path-removal@v1 机械验证 (T-LRF-12).

review 侧不变量: 对迁移类 patch (其 task 的 concept_refs 含迁移概念) 验【旧生产路径不可走】——
对该 task 的每条 old-path-removal grep 判据 (machine_check grep + expected_occurrences=0, 指名旧
符号), 在生产代码上重跑 grep; 实际命中 > 期望 (旧符号仍有生产侧引用) → finding (旧路径仍可走 =
该任务不算 done, 回 fix)。

机械, 非 LLM 判断: 复用 plan_freezed 的迁移类检测 (task_migration_old_path_criteria —— 与 planning
门同一套 'migration-class' + 'old-path-removal 判据' 定义, 不漂移) + closure_verification 的
gate_grep_occurrences (门自己 subprocess grep)。grep 错误 (occurrences=None) → fail-closed finding
(验不了旧路径不可走 = 不放行, 不静默通过)。

读侧防御性兼容段豁免: grep 用判据自带的 verification_pattern + search_scope —— 判据作者把 pattern
指向写/派发/emit 旧符号、scope 圈定写路径; 本模块不扩 grep 到读侧并集符号, 故刻意保留的防御性读侧
兼容段 (guard 读 registry∪单指针 并集 fallback) 不算旧路径、不会被误报。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from towow.l1.closure_verification import GateGrepTimeout, gate_grep_occurrences
from towow.schemas.payloads.plan_freezed import (
    _read_events,
    task_migration_old_path_criteria,
)

# grep 复算器签名: (pattern, search_path) → (occurrences | None, detail)。默认门自己 subprocess grep
# (gate_grep_occurrences, fail-closed: occurrences=None 表示 grep 出错)。单测注入 fake 跑器, 不起 subprocess。
GrepRunner = Callable[[str, Path], "tuple[int | None, str]"]


@dataclass(frozen=True)
class MigrationOldPathFinding:
    """一条 review-侧 finding: 迁移类 patch 的某个旧符号仍在生产代码可走 (或验不了)。"""

    task_id: str
    old_symbol: str  # 判据的 verification_pattern (指名的旧符号)
    search_scope: str | None  # 判据的 search_scope (相对 repo_dir); None = 全域
    found_occurrences: int | None  # grep 实际命中; None = grep 出错 (fail-closed)
    expected_occurrences: int  # 判据的 expected_occurrences (old-path-removal 恒为 0)
    detail: str

    @property
    def is_fail_closed(self) -> bool:
        """grep 跑不出来 (occurrences=None) 的 fail-closed finding —— 验不了旧路径不可走, 不放行。"""
        return self.found_occurrences is None


def find_migration_old_path_findings(
    towow_dir: Path,
    task_id: str,
    repo_dir: Path,
    *,
    grep_runner: GrepRunner = gate_grep_occurrences,
) -> list[MigrationOldPathFinding]:
    """对迁移类 task 的 patch 重跑每条 old-path-removal grep, 旧符号仍可走 (或验不了) → finding 列表。

    Args:
        towow_dir: v3 账本目录 (读 events.log 拿 task 的迁移判别 + old-path-removal 判据)。
        task_id: 被审 task (它的 patch 要验旧生产路径不可走)。
        repo_dir: 在其下 grep 生产代码的仓库根; 判据的 search_scope (若有) 在它之下再缩范围。
        grep_runner: grep 复算器 (默认 gate_grep_occurrences, 门自己 subprocess grep; 单测可注入)。

    Returns:
        findings 列表。task 非迁移类 / 无 old-path-removal 判据 / 每个旧符号都已 0 命中 → []。
        某旧符号 found > expected (仍有生产侧引用) 或 grep 出错 (found=None, fail-closed) → 一条 finding。
    """
    events = _read_events(towow_dir / "events.log")
    criteria = task_migration_old_path_criteria(events, task_id)
    findings: list[MigrationOldPathFinding] = []
    for dc in criteria:
        mc = dc.get("machine_check")
        if not isinstance(mc, dict):
            continue
        pattern = str(mc.get("verification_pattern", ""))
        expected = int(mc.get("expected_occurrences") or 0)
        scope_raw = mc.get("search_scope")
        scope = str(scope_raw) if isinstance(scope_raw, str) and scope_raw.strip() else None
        search_path = repo_dir / scope if scope else repo_dir
        try:
            found, grep_detail = grep_runner(pattern, search_path)
        except GateGrepTimeout as exc:
            # 超时同走 fail-closed (found=None): 迁移验证验不了旧路径不可走 = 不放行 —
            # 本模块语义与 closure verify 的降级不同, 这里"验不了"本来就该产 finding。
            found, grep_detail = None, f"grep 超时: {exc}"
        if found is None:
            findings.append(
                MigrationOldPathFinding(
                    task_id=task_id,
                    old_symbol=pattern,
                    search_scope=scope,
                    found_occurrences=None,
                    expected_occurrences=expected,
                    detail=(
                        f"旧路径不可验 (grep 错误, fail-closed): {pattern!r} under {search_path} "
                        f"— {grep_detail}"
                    ),
                ),
            )
        elif found > expected:
            findings.append(
                MigrationOldPathFinding(
                    task_id=task_id,
                    old_symbol=pattern,
                    search_scope=scope,
                    found_occurrences=found,
                    expected_occurrences=expected,
                    detail=(
                        f"旧生产路径仍可走: grep {pattern!r} 命中 {found} 处 (期望 {expected}) under "
                        f"{search_path} — 旧写/派发/emit 路径未物理移除, 任务不算 done, 回 fix。{grep_detail}"
                    ),
                ),
            )
    return findings


__all__ = [
    "GrepRunner",
    "MigrationOldPathFinding",
    "find_migration_old_path_findings",
]
