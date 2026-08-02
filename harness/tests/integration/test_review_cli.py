"""T2 (E.4 closure) — `review` 命令组真 emit Finding 生命周期事件测试.

钉住 concept review-completion-is-finding-lifecycle@v1: review 无 ReviewCompleted,
完成体现为 FindingCreated / FindingVerified / FindingResolved (canonical, 经 commit gate)。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from towow.cli.main import cli

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def initialized_project(tmp_path: Path, runner: CliRunner) -> Path:
    result = runner.invoke(cli, ["init", str(tmp_path)])
    assert result.exit_code == 0, f"init failed: {result.output}"
    return tmp_path


def _events(proj: Path) -> list[dict]:
    log = proj / ".towow" / "events.log"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _lock(proj: Path) -> Path:
    # T-SL-C 单指针退役: 活跃信号 = registry 工卡 sessions/review/<sid>.json (单会话场景取唯一)。
    d = proj / ".towow" / "locks" / "sessions" / "review"
    js = sorted(d.glob("*.json")) if d.exists() else []
    return js[0] if js else d / "__absent__.json"


def _meta(proj: Path) -> dict:
    # 业务字段 (trigger_event_id / task_id / ...) 迁到 .meta 旁文件 (单指针退役)。
    d = proj / ".towow" / "locks" / "sessions" / "review"
    m = sorted(d.glob("*.meta")) if d.exists() else []
    return json.loads(m[0].read_text(encoding="utf-8")) if m else {}


def _start(runner: CliRunner, proj: Path) -> None:
    r = runner.invoke(
        cli,
        ["review", "start", "--trigger-event-id", "evt-trc-1", "--project-dir", str(proj)],
    )
    assert r.exit_code == 0, r.output


_REVIEW_EXT = {
    "voi_rationale": "patch 的 loop bound 改动是 done_criteria 第 1 条覆盖范围",
    "target": {"artifact": "src/x.py", "location": "x.py:42"},
    "falsification_evidence": {
        "attempt": "构造空集 input 跑 loop, 越界访问触发",
        "result": "confirmed",
    },
    "closure_contract": {
        "closure_criteria": [
            {
                "condition": "loop bound 用 len 而非 len-1",
                "verification_method": "grep",
                "expected_result": "0 occurrences of off-by-one pattern",
                # RUN-037 P6: grep criterion 绑合约 pattern (门用它复算); marker 在 init 项目里不存在 → grep 0
                "verification_pattern": "OFFBYONE_REVIEW_MARK_RV2",
                "expected_occurrences": 0,
            },
        ],
        "ripple_targets": [],
        "forbidden_residuals": [],
    },
    "review_dimension": "method-execution-path",
    "is_preexisting": False,
}


def _ext_file(proj: Path, name: str = "ext.json") -> Path:
    p = proj / name
    p.write_text(json.dumps(_REVIEW_EXT), encoding="utf-8")
    return p


def _ext_out(proj: Path, data: dict, name: str) -> Path:
    """写 project 外 (RUN-037 件1: 门 grep repo 复算合约 pattern, input JSON 在 repo 内会被自身字面命中)."""
    inputs = proj.parent / f"_in_{proj.name}"
    inputs.mkdir(exist_ok=True)
    p = inputs / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_review_start_creates_lock(initialized_project: Path, runner: CliRunner) -> None:
    _start(runner, initialized_project)
    assert _lock(initialized_project).exists()
    data = json.loads(_lock(initialized_project).read_text(encoding="utf-8"))
    assert data["skill_id"] == "M-1.5"  # registry 工卡含 skill_id
    assert _meta(initialized_project)["trigger_event_id"] == "evt-trc-1"  # 业务字段在 .meta


def test_review_start_allows_concurrent_second_session(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """T-RCL-01: review 退役 kind 级单飞 → 第二个 review start 【放行】(exit 0), 不再"already active"。

    review-target 级完全并发: 同 review-target 第二视角 / 不同 review-target 并行都允许共存。
    provenance 由既有 review-unit (session_id) 血缘守, 不靠并发排斥。(fix start 仍串行, 见
    test_t_sl_a4_review_fix_registry.TestFixStartSerial。)
    """
    _start(runner, initialized_project)
    r = runner.invoke(
        cli, ["review", "start", "--trigger-event-id", "evt-x", "--project-dir", str(initialized_project)],
    )
    assert r.exit_code == 0, r.output
    assert "review session started" in r.output
    assert "already active" not in r.output


def test_review_start_dispatched_as_review_without_task_id_fails_closed(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """T-FIX-B3-04 (CONSTITUTION-unknown#1 子命题 c): 被派为 REVIEW-typed task 的信号
    (--dispatched-as-review-task) 为真但漏带 --task-id → fail-closed (非零退出 + 告警),
    不静默放行。否则 conclude 不 emit TaskRunCompleted, verdict 门静默 no-op,
    REVIEW task 既不完成也不被拦。"""
    r = runner.invoke(
        cli,
        [
            "review", "start",
            "--trigger-event-id", "evt-trc-1",
            "--dispatched-as-review-task",
            "--project-dir", str(initialized_project),
        ],
    )
    assert r.exit_code != 0, r.output
    # fail-closed: 显著告警, 明示漏带 task_id 会让 verdict 门无法触发。
    assert "task-id" in r.output or "task_id" in r.output
    assert "verdict" in r.output.lower() or "TaskRunCompleted" in r.output
    # 没有任何会话被建 (fail-closed 在建 lock 前拦)。
    assert not _lock(initialized_project).exists()


def test_review_start_dispatched_as_review_with_task_id_ok(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """T-FIX-B3-04: 被派为 REVIEW-typed task 且带 --task-id → 正常 start (信号兑现, 不误拦)。"""
    r = runner.invoke(
        cli,
        [
            "review", "start",
            "--trigger-event-id", "evt-trc-1",
            "--dispatched-as-review-task",
            "--task-id", "T-REVIEW-1",
            "--project-dir", str(initialized_project),
        ],
    )
    assert r.exit_code == 0, r.output
    assert _lock(initialized_project).exists()
    assert _meta(initialized_project)["task_id"] == "T-REVIEW-1"  # 业务字段在 .meta


def test_forward_chain_review_no_task_id_ok(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """T-FIX-B3-04 对照: forward-chain review (非 REVIEW-typed, 无 --dispatched-as-review-task)
    本就无 task_id → 正常 start, 不被误拦 (回归不破)。"""
    r = runner.invoke(
        cli,
        [
            "review", "start",
            "--trigger-event-id", "evt-trc-1",
            "--project-dir", str(initialized_project),
        ],
    )
    assert r.exit_code == 0, r.output
    assert _lock(initialized_project).exists()


def test_finding_create_emits_canonical(initialized_project: Path, runner: CliRunner) -> None:
    _start(runner, initialized_project)
    r = runner.invoke(
        cli,
        [
            "review", "finding-create",
            "--finding-id", "finding-1",
            "--severity", "major",
            "--risk-surface", "execution patch correctness",
            "--description", "off-by-one in loop bound",
            "--finding-kind", "adjacent_code_issue",
            "--review-extension-file", str(_ext_file(initialized_project)),
            "--project-dir", str(initialized_project),
        ],
    )
    assert r.exit_code == 0, r.output
    fc = [e for e in _events(initialized_project) if e.get("event_type") == "FindingCreated"]
    assert len(fc) == 1
    assert fc[0]["payload"]["finding_id"] == "finding-1"
    assert fc[0]["payload"]["severity"] == "major"
    assert fc[0]["payload"]["source"]["type"] == "model_review"
    assert fc[0]["payload"]["voi_rationale"]
    assert fc[0]["provenance"]["skill_id"] == "M-1.5"
    assert any(e.get("event_type") == "CommitAccepted" for e in _events(initialized_project))


def test_unscoped_grep_residual_publishes_with_warning(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive ①: 无 search_scope 的
    grep forbidden_residual 不硬拒 (发布通过), 但 CLI 显式警告作者圈定扫描面 ("不无声进合约")。"""
    _start(runner, initialized_project)
    ext = json.loads(json.dumps(_REVIEW_EXT))  # deep copy
    ext["closure_contract"]["forbidden_residuals"] = [
        {"pattern": "OLD_UNSCOPED_RESIDUAL_MARK", "rationale": "旧术语残留", "check_method": "grep"},
    ]
    ext_file = initialized_project / "ext_unscoped.json"
    ext_file.write_text(json.dumps(ext), encoding="utf-8")
    r = runner.invoke(
        cli,
        [
            "review", "finding-create",
            "--finding-id", "finding-unscoped",
            "--severity", "minor",
            "--risk-surface", "closure residual scope",
            "--description", "unscoped grep residual warning path",
            "--finding-kind", "adjacent_code_issue",
            "--review-extension-file", str(ext_file),
            "--project-dir", str(initialized_project),
        ],
    )
    assert r.exit_code == 0, r.output  # 发布通过 (不硬拒)
    assert "未绑 search_scope" in r.output  # CLI 警告可见 ("不无声")
    assert "OLD_UNSCOPED_RESIDUAL_MARK" in r.output
    fc = [e for e in _events(initialized_project) if e.get("event_type") == "FindingCreated"]
    assert any(e["payload"]["finding_id"] == "finding-unscoped" for e in fc)


def test_finding_verify_and_resolve_emit_canonical(
    initialized_project: Path, runner: CliRunner,
) -> None:
    _start(runner, initialized_project)
    # RUN-037 件1: 扩展写 project 外 (含合约 grep marker, 在 repo 内会被门 grep 自身命中)。
    runner.invoke(
        cli,
        [
            "review", "finding-create", "--finding-id", "finding-2",
            "--severity", "minor", "--risk-surface", "rs", "--description", "d",
            "--review-extension-file", str(_ext_out(initialized_project, _REVIEW_EXT, "ext-f2.json")),
            "--project-dir", str(initialized_project),
        ],
    )
    rv = runner.invoke(
        cli,
        [
            "review", "finding-verify", "--finding-id", "finding-2",
            "--verification-method", "re-ran failing test",
            "--verification-state", "verified",
            "--verify-falsification-attempt", "tried to disprove by re-running; failure reproduced",
            "--project-dir", str(initialized_project),
        ],
    )
    assert rv.exit_code == 0, rv.output
    # RUN-037 件1: confirmed_and_fixed 须带 closure_file 覆盖合约 criterion + closed; 门 grep 合约 marker found=0 过。
    closure = {
        "closure_state": "closed",
        "criteria_results": [
            {"criterion": "loop bound 用 len 而非 len-1", "passed": True, "evidence": "grep marker 0 occ"},
        ],
        "ripple_results": [],
        "residual_check_results": [],
    }
    rr = runner.invoke(
        cli,
        [
            "review", "finding-resolve", "--finding-id", "finding-2",
            "--resolution", "confirmed_and_fixed",
            "--resolution-evidence", "fix patch merged, test passes",
            "--closure-file", str(_ext_out(initialized_project, closure, "closure-f2.json")),
            "--project-dir", str(initialized_project),
        ],
    )
    assert rr.exit_code == 0, rr.output
    types = [e.get("event_type") for e in _events(initialized_project)]
    assert "FindingVerified" in types
    assert "FindingResolved" in types
    fr = next(e for e in _events(initialized_project) if e.get("event_type") == "FindingResolved")
    assert fr["payload"]["resolution"] == "confirmed_and_fixed"


def test_finding_resolve_retracted_does_not_crash(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """f-review-finding-resolve-retracted-path-broken: retracted (撤回) 终态端到端不崩 (回归).

    曾经: review_finding_resolve 无条件 supersede=Supersede(is_supersede=is_retract);
    resolution=retracted → is_retract=True → Supersede(is_supersede=True) 触发 pydantic 校验要求
    superseded_event_id/novelty/novelty_type 非 None, 但 CLI 无对应 flag → ValidationError 崩,
    reviewer 撤回 finding 走不通被迫退用 confirmed_and_accepted (把'撤回'记成'接受风险', 语义漂移)。
    撤回是 finding lifecycle 的追加终态 (payload.resolution 记录), 不是用 novelty supersede 原
    FindingCreated — detection_rule_lifecycle 的 FP-rate 统计依赖 FindingCreated 留存 → is_supersede=False。
    """
    _start(runner, initialized_project)
    runner.invoke(
        cli,
        [
            "review", "finding-create", "--finding-id", "finding-retract",
            "--severity", "minor", "--risk-surface", "rs", "--description", "premise overturned",
            "--review-extension-file", str(_ext_out(initialized_project, _REVIEW_EXT, "ext-fr.json")),
            "--project-dir", str(initialized_project),
        ],
    )
    rr = runner.invoke(
        cli,
        [
            "review", "finding-resolve", "--finding-id", "finding-retract",
            "--resolution", "retracted",
            "--resolution-evidence", "owner 回退迁移, finding 前提被推翻, 撤回",
            "--project-dir", str(initialized_project),
        ],
    )
    assert rr.exit_code == 0, rr.output
    fr = next(e for e in _events(initialized_project) if e.get("event_type") == "FindingResolved")
    assert fr["payload"]["resolution"] == "retracted"
    # 撤回不 supersede 原 FindingCreated (FP-rate 统计依赖其留存)
    assert fr["supersede"]["is_supersede"] is False


def test_review_conclude_clears_lock(initialized_project: Path, runner: CliRunner) -> None:
    _start(runner, initialized_project)
    r = runner.invoke(cli, ["review", "conclude", "--project-dir", str(initialized_project)])
    assert r.exit_code == 0, r.output
    assert not _lock(initialized_project).exists()


def test_finding_create_standalone_without_session(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """BRIEF-product-recovery-loop-fix-2026-06-26 fix #3: finding 谁都能记。无 active review
    session 也能 emit canonical FindingCreated (松"必须有 session"); §3.2 写边界 schema 照旧强制。
    standalone finding 的 review_unit_id = standalone sid, 无 REVIEW task 完成 → 不被 verdict 折叠,
    只作独立 canonical finding 上 dashboard。"""
    r = runner.invoke(
        cli,
        [
            "review", "finding-create", "--finding-id", "f-standalone", "--severity", "minor",
            "--risk-surface", "rs", "--description", "协调者发现的缺陷, 无 active review session",
            "--review-extension-file", str(_ext_file(initialized_project)),
            "--project-dir", str(initialized_project),
        ],
    )
    assert r.exit_code == 0, r.output  # 无 active session → 仍记
    fc = [
        e for e in _events(initialized_project)
        if e.get("event_type") == "FindingCreated"
        and e.get("payload", {}).get("finding_id") == "f-standalone"
    ]
    assert len(fc) == 1, "无 active review session 也必须产真 canonical FindingCreated"
    # §3.2 写边界仍强制 (source=model_review + voi_rationale 等照旧)
    assert fc[0]["payload"]["source"]["type"] == "model_review"
    assert fc[0]["payload"]["voi_rationale"]


def test_finding_create_explicit_unknown_session_still_rejected(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """安全属性不松: 给了 --session-id 但不命中 live → 仍拒 (认错会话=血缘串, 只松"0 live 回退 standalone")。"""
    r = runner.invoke(
        cli,
        [
            "review", "finding-create", "--finding-id", "f", "--severity", "minor",
            "--risk-surface", "rs", "--description", "d",
            "--review-extension-file", str(_ext_file(initialized_project)),
            "--session-id", "sess-does-not-exist",
            "--project-dir", str(initialized_project),
        ],
    )
    assert r.exit_code != 0, "显式 --session-id 不命中 live → 仍拒 (血缘串安全属性不松)"


# ── T-FIX-B2-03: 跨会话 resolve 折叠归对原 review-unit ────────────────────────


def _start_trigger(runner: CliRunner, proj: Path, trigger: str) -> None:
    r = runner.invoke(
        cli, ["review", "start", "--trigger-event-id", trigger, "--project-dir", str(proj)],
    )
    assert r.exit_code == 0, r.output


def _conclude(runner: CliRunner, proj: Path) -> None:
    r = runner.invoke(cli, ["review", "conclude", "--project-dir", str(proj)])
    assert r.exit_code == 0, r.output


def test_cross_session_resolve_folds_into_origin_unit(
    initialized_project: Path, runner: CliRunner,
) -> None:
    """T-FIX-B2-03 (REVIEW-verdict#2 grade-3 断点复现): finding 在 review 会话 A 产
    (review_unit_id=A), verified-FAIL; 独立 fix_after 会话 B(B≠A) resolve 它。FindingResolved
    必须在 payload 显式带 review_unit_id=A —— 否则 _event_review_unit_id 回退到 prov.session_id=B,
    resolve 被折进 unit B(错), unit A 永远 failed, REVIEW task 永不 verdict-passed。

    红测断言: 跨会话 emit 的 FindingResolved.payload.review_unit_id == 原 review-unit A 的 sid,
    且 compute_review_verdict(events, A) 把这条 resolve 归进 A(verdict 离开 failed)。
    """
    proj = initialized_project
    # ── 会话 A: 产 finding + verified-FAIL(major) ──
    _start_trigger(runner, proj, "evt-A")
    sid_a = json.loads(_lock(proj).read_text(encoding="utf-8"))["session_id"]
    rc = runner.invoke(
        cli,
        [
            "review", "finding-create", "--finding-id", "F-XS",
            "--severity", "major", "--risk-surface", "state-fidelity",
            "--description", "cross-session fold target",
            "--review-extension-file", str(_ext_out(proj, _REVIEW_EXT, "ext-xs.json")),
            "--project-dir", str(proj),
        ],
    )
    assert rc.exit_code == 0, rc.output
    rv = runner.invoke(
        cli,
        [
            "review", "finding-verify", "--finding-id", "F-XS",
            "--verification-method", "re-ran failing path",
            "--verification-state", "verified",
            "--verify-falsification-attempt", "tried to disprove; failure reproduced",
            "--project-dir", str(proj),
        ],
    )
    assert rv.exit_code == 0, rv.output
    _conclude(runner, proj)

    # ── 会话 B (B≠A): 独立 fix_after 会话 resolve F-XS ──
    _start_trigger(runner, proj, "evt-B")
    sid_b = json.loads(_lock(proj).read_text(encoding="utf-8"))["session_id"]
    assert sid_b != sid_a, "测试前提: 两次 start 产不同 sid (跨会话)"
    # f-stale-closure-contract-permanently-unclosable: 带合约的 review finding 主张闭合
    # (fixed/accepted) 必须交 closure 证据 —— 省掉它会让合约复算整段跳过、账本上落一条无证据的闭合。
    # 本测试要钉的性质是"跨会话 resolve 折回原 review-unit", 与证据形态无关, 故照常交证据即可;
    # 这里 criterion 复算在非 git 的 tmp 项目里拿不到信号 → 门如实记为不符, 但 accepted 口径由复查席
    # 承担判断 (门不代替人拍板), 落账照旧。
    xs_closure = _ext_out(
        proj,
        {
            "closure_state": "closed",
            "criteria_results": [
                {"criterion": "loop bound 用 len 而非 len-1", "passed": True,
                 "evidence": "fix landed in separate fix_after session"},
            ],
            "residual_check_results": [],
        },
        "closure-xs.json",
    )
    rr = runner.invoke(
        cli,
        [
            "review", "finding-resolve", "--finding-id", "F-XS",
            "--resolution", "confirmed_and_accepted",
            "--resolution-evidence", "fix landed in separate fix_after session",
            "--closure-file", str(xs_closure),
            "--project-dir", str(proj),
        ],
    )
    assert rr.exit_code == 0, rr.output

    events = _events(proj)
    fr = next(e for e in events if e.get("event_type") == "FindingResolved")
    # 红测核心: resolve 在会话 B emit, 但必须显式带原 review-unit A 的 id (非靠 prov 回退到 B)。
    assert fr["provenance"]["session_id"] == sid_b
    assert fr["payload"].get("review_unit_id") == sid_a, (
        "FindingResolved 必须显式携带原 review_unit_id=A, 否则跨会话折叠归错组"
    )

    # 折叠: 这条跨会话 resolve 必须被归进 unit A —— unit A 的那条 verified-FAIL 被认成 resolved,
    # verdict 离开 failed(pending, 待 re-review), 而非永远 failed。
    from towow.l0.projection.review_verdict import compute_review_verdict

    assert compute_review_verdict(events, sid_a) == "pending"
    # 反证: 若回退到 prov.session_id, 这条 resolve 会落进 unit B(空 unit) → A 仍 failed。
    # 显式锚定后 unit B 不含该 resolve(它属 A)。
