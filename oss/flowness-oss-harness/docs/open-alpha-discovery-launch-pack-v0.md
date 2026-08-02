# Flowness Open Alpha — Discovery & Launch Pack

> Status: **Released Open Alpha material. The evidence below is bound to v1.0.0-alpha; changed bytes require fresh clean-room and jury evidence.**

This pack carries discovery and reusable channel copy for the released Open Alpha. Release-specific evidence belongs to v1.0.0-alpha; successor material must be revalidated before it replaces that release.

**Start here:** [project story](../../../README.md) · [current architecture](../../../docs/architecture.md) · [10-minute Harness demo](open-alpha-demo.md) · [D0–D9 mechanism Atlas](architecture-atlas.md) · [Mechanism Registry](../registries/mechanism-registry-seed-v0.json)

## GitHub metadata

**Description:** Compile vague goals into design, engineering decisions, parallel agent work, and independently accepted outcomes.

**Topics:** `ai-agent-harness`, `multi-agent-system`, `agent-orchestration`, `coding-agents`, `codex-cli`, `agent-evaluation`, `llm-evaluation`, `software-engineering`, `evidence-driven`, `provenance`, `human-in-the-loop`, `agent-governance`, `open-source`, `harness-engineering`

### Social preview copy

- Headline: Flowness
- Subhead: From vague intent to work you can actually trust.
- Footer: Design → engineer → build → challenge → reflow → accept

## Layered introductions

### 30 seconds — From a vague goal to work you can trust

Flowness gives complex agent work the stages it is usually missing: clarify the goal, explore a design, translate it into engineering contracts, freeze shared decisions, plan parallel work, challenge the result independently, and return failures to the right upstream layer. Its public Alpha currently lets you run and inspect the execution → review → targeted rework → acceptance kernel.

**CTA:** Inspect the 10-minute FAIL → rework → PASS demo and its trace.

### 3 minutes — The cognitive and control pipeline around coding agents

Most multi-agent demos begin at execution and stop when agents return answers. Flowness models the missing work before and after execution: interview, design, engineering specification, consensus, planning, independent review, and layer-aware correction. The released demo proves the narrower acceptance kernel with isolated producers, content-bound candidates, persistent blocker identity, targeted successor evidence, and fresh retest. The broader cognitive pipeline is shown as the product architecture, not misrepresented as a current public runnable capability.

**CTA:** Run the deterministic demo, inspect both jury rounds, then follow the mechanism and architecture links.

### 10 minutes — Evaluate the system, not the slogan

Start with the Open Alpha demo: three producers run concurrently in isolated directories; their outputs become a sealed candidate; two judges receive the same candidate and policy; one credible failure blocks the round even when the other judge passes; targeted rework changes the failed claim and binds successor evidence; two fresh judges retest the new candidate; and a read-only inspector verifies hashes, identities, lineage, and the final decision. Then examine the broader contracts for EventLog and projection, orchestration and dispatch, review and recovery, work-attempt admission, mechanism discovery, Claim/Unknown/Drift registries, Content Graph propagation, and D0–D9 architecture. Treat local checks as local evidence. Server deployment, external clean-room installation, benchmark leadership, and adoption remain separate gates.

**CTA:** Use the claim ledger to challenge each sentence and open an issue for any broken evidence edge.

## For DeepSeek and other Harness selectors

If you are assembling or evaluating open-source agent Harness projects, raw agent count and a polished demo are weak selection signals. The useful questions are whether the project can bind an outcome to evidence, preserve failures, separate production from judgment, and explain what remains unknown.

Flowness is designed around a path from a vague goal through interview, design, engineering specification, consensus, planning, execution, review, and correction. Its six-depth routing model aims to return failures to the earliest layer that must genuinely change instead of triggering another blind retry. Runtime maturity varies: the private design and engineering stages are partial, automatic tier routing and engineering-spec → consensus publishing are not closed, and the public Open Alpha currently runs only the execution → jury → targeted rework → acceptance kernel.

### What to inspect

- Run the 10-minute deterministic Harness demo and inspect both candidate hashes, jury reports, blocker lineage, successor evidence, and the read-only verification result.
- Read the D0–D9 Architecture Atlas from user goal through runtime, deployment boundary, authority, provenance, and target-state views.
- Trace one mechanism from registry entry to code/test evidence, current drift, public claim ceiling, consumer, failure, and recovery path.
- Confirm that a mandatory FAIL or critical UNKNOWN cannot be cleared by score averaging or narrative rewriting.
- Check that public-source scope excludes credentials, Transcripts, customer material, account/quota/fleet controls, server configuration, runtime ledgers, and rights-unknown assets.

### Maturity at a glance

- **Ledger Core — `experimental`:** Runnable Open Alpha slice for append-only decisions, visibility, projection freshness, verdict readback, and bounded crash-tail recovery; no distributed or production claim.
- **Multi-agent controller, jury, and rework — `experimental`:** Executable reference implementation and local tests; interfaces and packaging may change.
- **Mechanism, Claim, Drift, Content Graph, and architecture machinery — `experimental`:** Inspectable contracts and candidate tooling with uneven evidence depth.
- **Full private runtime and future extensions — `designed_target`:** Architecture preview is not a current public capability claim.
- **Fleet, accounts, quotas, secrets, private context, and server operations — `private_excluded`:** Outside Open Alpha and not advertised as public source.

### Decision questions

- Does the project expose a runnable Harness lifecycle rather than only prompts, a library, or an architecture essay?
- Can an evaluator reconstruct why a candidate passed or failed from immutable artifacts?
- Are producers, judges, release authority, and external side effects separated?
- Are negative results and Unknowns preserved rather than averaged away?
- Can claims and channel copy be invalidated when mechanisms or evidence drift?
- Are maturity labels attached at the mechanism and claim level?

### Boundaries

- The owner supplied an unverified hypothesis that a DeepSeek-related discovery opportunity may exist; no primary-source selection activity, criteria, endorsement, or deadline was verified.
- No external project in the comparison queue has been inspected in this pass.
- No star count, benchmark rank, production reliability, scale, security, or adoption claim is made.
- The repository is public at Towow-ai/Flowness; this packet makes no claim about a future selector decision or endorsement.

Standalone selector packet: [open-alpha-selector-packet-v0.md](open-alpha-selector-packet-v0.md)

## Comparison matrix — verification queue, not a leaderboard

This matrix fixes the questions that a future benchmark agent must answer. Flowness cells cite local evidence states; every external cell remains Unknown until the repository source, releases, tests, issues, and runnable path are inspected. Empty evidence means no comparison claim is allowed.

| Project | Verification | Goal-to-acceptance lifecycle | Parallel agent execution | Artifact/evidence binding | Independent jury | FAIL → rework → retest | State and recovery | Claim and narrative drift |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Flowness Open Alpha candidate | `local_evidence_available` | Experimental local end-to-end demo | Three isolated producer workers in the deterministic demo | Content-bound candidate, policy, jury, rework, events, and trace | Two deterministic judge fixtures per round; mandatory failure blocks in the local demo | Forced FAIL, stable blocker, targeted successor, fresh PASS | Ledger and recovery mechanisms have local candidate evidence with broader runtime Unknowns | Experimental Claim/Unknown/Drift/Content Graph contracts |
| ai-boost/awesome-harness-engineering | `unknown` | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected |
| RyanAlberts/best-of-Agent-Harnesses | `unknown` | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected |
| HKUDS/OpenHarness | `unknown` | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected |
| Hmbown/CodeWhale | `unknown` | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected | Unknown — source not inspected |

### Dimension verification method

- **Goal-to-acceptance lifecycle:** Trace one runnable goal through delegation, artifacts, judgment, correction, and terminal acceptance.
- **Parallel agent execution:** Inspect the runner and reproduce concurrent execution with isolated role outputs.
- **Artifact/evidence binding:** Verify content identities, schemas, producer lineage, and tamper detection.
- **Independent jury:** Confirm judges are independent of producers and bind the same candidate and policy.
- **FAIL → rework → retest:** Force a credible failure and confirm stable blocker identity, bounded rework, successor evidence, and fresh retest.
- **State and recovery:** Inspect authoritative state, idempotence, retries, incomplete writes, terminalization, and recovery tests.
- **Claim and narrative drift:** Change a mechanism/evidence state and confirm dependent docs, diagrams, demos, and drafts are invalidated or regenerated.

## External source queue

Every entry below is an unverified search seed collected on 2026-08-02. No source, release, test, star count, or mechanism claim has been independently verified in this offline pass.

### [ai-boost/awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering)

- State: `unknown`
- Seed basis: Unverified search seed collected on 2026-08-02; identity and contents not independently checked in this offline pass.
- Next verification:
  - Read repository scope and inclusion criteria.
  - Inspect referenced projects from their own source rather than inheriting list descriptions.
  - Capture commit/release date and avoid using list placement as validation.

### [RyanAlberts/best-of-Agent-Harnesses](https://github.com/RyanAlberts/best-of-Agent-Harnesses)

- State: `unknown`
- Seed basis: Unverified search seed collected on 2026-08-02; identity, ranking method, and contents not independently checked in this offline pass.
- Next verification:
  - Inspect ranking and update methodology.
  - Verify each compared project at its canonical repository.
  - Do not treat inclusion, rank, or popularity as a mechanism claim.

### [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness)

- State: `unknown`
- Seed basis: Unverified search seed collected on 2026-08-02; repository source, releases, tests, issues, and runnable path not inspected in this offline pass.
- Next verification:
  - Inspect source architecture and public entrypoints.
  - Run the documented quickstart at an exact commit.
  - Trace failure, recovery, evaluation, and state mechanisms through code and tests.
  - Record only evidence-bound comparison cells.

### [Hmbown/CodeWhale](https://github.com/Hmbown/CodeWhale)

- State: `unknown`
- Seed basis: Unverified DeepSeek-related search seed collected on 2026-08-02; relationship, source, releases, tests, and behavior not independently checked.
- Next verification:
  - Verify canonical ownership and repository purpose.
  - Inspect source, releases, tests, issues, and runnable examples.
  - Verify any DeepSeek relationship from a primary source before mentioning it.

### DeepSeek harness roadmap secondary article

- State: `unknown`
- Seed basis: Unverified search-result category collected on 2026-08-02; no exact URL or primary source was inspected.
- Next verification:
  - Resolve exact article URL and author/date.
  - Locate and prefer the primary DeepSeek source it cites.
  - Extract criteria only when directly supported and keep secondary interpretation separate.

### Recent Reddit Harness discussions

- State: `unknown`
- Seed basis: Unverified search-result category collected on 2026-08-02; no exact thread, date, author, or claims were inspected.
- Next verification:
  - Resolve exact thread URLs and event dates.
  - Use discussion only for vocabulary, objections, and user-reported pain points.
  - Do not promote anecdotes into adoption or performance claims.

### [arXiv:2604.25850](https://arxiv.org/abs/2604.25850)

- State: `unknown`
- Seed basis: Unverified search seed collected on 2026-08-02; title, authors, version, methods, results, and relevance not independently inspected in this offline pass.
- Next verification:
  - Read the current paper version and supplementary material.
  - Extract definitions, evaluation design, limits, and directly supported results.
  - Map comparable dimensions without implying that paper evidence validates Flowness.

## Launch checklist

| Gate | State | Blocking | Evidence or next action |
| --- | --- | --- | --- |
| Freeze and public manifest technical gate | `passed_v1_0_0_alpha` | `false` | The v1.0.0-alpha bytes and file-exact public manifest were frozen; repeat this gate for every successor. |
| File-level rights, secret, PII, and IP gate | `passed_v1_0_0_alpha` | `false` | The release scan and owner rights decision were bound to v1.0.0-alpha; changed files require fresh review. |
| Packaging, license, NOTICE, third-party, and version matrix | `passed_v1_0_0_alpha` | `false` | Packaging, dependency, license, sensitive-content, and public-boundary checks passed for the released bytes. |
| Runnable Harness FAIL → rework → PASS E2E | `passed_v1_0_0_alpha` | `false` | The canonical deterministic E2E was retained for the released sealed export. |
| Claim, maturity, external-seed, and link checks | `successor_revalidation_required` | `false` | The released claim set passed its gate. This material refresh must regenerate hashes and rerun the checks before a successor release. |
| Sealed public directory | `passed_v1_0_0_alpha` | `false` | The released sealed export has an external identity; create a new identity for any successor. |
| Clean install and canonical E2E from sealed export | `passed_v1_0_0_alpha` | `false` | A non-author clean-room run on Linux aarch64 with CPython 3.12 was retained for v1.0.0-alpha. |
| Independent package and claim juries | `passed_v1_0_0_alpha` | `false` | Independent juries closed mandatory release blockers for v1.0.0-alpha; material changes require a fresh jury. |
| GitHub description, topics, social copy, selector page, and drafts | `released_refresh_in_progress` | `false` | The repository is public. Reader-facing material is being refreshed around the full lifecycle and current proof boundary. |
| Owner authorization of release and channel actions | `passed_v1_0_0_alpha` | `false` | Owner authorization applied to v1.0.0-alpha. Each later release or channel action keeps its own approval point. |
| Repository transfer, rename, redirects, and private vulnerability reporting | `passed_v1_0_0_alpha` | `false` | The repository was transferred, renamed, and released as Towow-ai/Flowness; future audits recheck redirects and GitHub security settings. |
| External source verification and reproducible comparison | `post_alpha` | `false` | Process the source queue after the Open Alpha package is frozen; do not delay source release merely to claim comparative leadership. |

## GitHub Release record copy

**Released tag:** `v1.0.0-alpha`

**Title:** Flowness Open Alpha — evidence-driven multi-agent engineering harness

**GitHub pre-release flag:** `true`

### What this is

Flowness models the full path from an ambiguous goal through interview, design, engineering specification, consensus, planning, parallel work, independent challenge, layer-aware correction, and an accepted outcome. This Open Alpha makes the narrower execution → review → targeted rework → acceptance kernel runnable and inspectable.

### Try first

Run the 10-minute deterministic demo. It executes three isolated producers, blocks the first candidate on one credible FAIL, creates bounded successor evidence, reruns two fresh judges, and verifies the final chain read-only.

### Included maturity layers

Ledger Core is the most mature local candidate. The controller, role registry, jury, rework, work-attempt ledger, Mechanism/Claim/Drift/Content Graph machinery, and broader canonical engine are experimental. Architecture targets are labelled and are not described as implemented runtime behavior.

### Limits

This Alpha does not establish production reliability, scale, security hardening, benchmark leadership, or external adoption. The private design and engineering-spec implementation is partial and has no organic end-to-end proof. Private context, customer data, credentials, account/quota/fleet operations, server configuration, private runtime ledgers, and rights-unknown assets are excluded.

### From Wow-Harness

Flowness is a ground-up major-version evolution of Wow-Harness. Historical interest belongs to the legacy project; old stars do not validate the rewritten Flowness implementation. The migration keeps attribution and explains the replacement boundary.

### Before a successor release

- Before any successor release, bind its exact commit, tree, scope, export, wheels, clean-room receipt, jury verdict, and owner authorization in a new immutable release record.
- Before any successor release, recheck old URL and Git redirects plus GitHub security settings; do not reuse v1.0.0-alpha evidence for changed bytes.

## 中文渠道短稿

### GitHub / 开发者社区短帖｜Flowness Open Alpha：从一句模糊目标，到真正可验收的工程结果

多 Agent 很容易制造大量输出，难的是在交接中保住为什么这样做、哪些工程事实不能变、失败究竟要回到哪一层。Flowness 建模了采访、设计方案、工程方案、共识、计划、并行执行、独立复查和分层回流的完整生命周期。当前 Open Alpha 已公开可运行的执行 → 复查 → 定向返工 → 验收内核；设计与工程方案仍是部分私有实现，尚无真实任务端到端证据。它仍是 Alpha，不宣称生产可靠性、规模化或外部采用。

**CTA：** 先跑 10 分钟 FAIL → 返工 → PASS 演示，再决定它是不是你要找的 Harness。

发布状态：`draft_owner_approval_required`

### 知乎 / 公众号｜当多个 AI Agent 都说“做完了”，谁来证明它真的完成了？

Flowness 想解决的不是“怎么再多叫几个 Agent”，而是人的目标在多轮交接中不断丢失。它把一句模糊目标逐层编译成设计选择、工程契约、共享共识、任务图、产物、Finding 和验收证据；失败也不只会机械重试，而是回到真正需要变化的执行、计划、工程方案、设计或采访层。当前公开 Alpha 可运行的是验收内核；更完整的认知流水线会按证据成熟度逐步开放。

**CTA：** 欢迎从代码、测试和失败链路挑刺，而不是只看概念。

发布状态：`draft_owner_approval_required`

### 掘金｜我们给多 Agent 编排加了一条不能被平均分绕过的验收回路

示例里三个 producer 并行生成候选，两个 judge 独立审查同一份候选和 policy。第一轮一个 PASS、一个可信 FAIL，系统仍然 BLOCKED；之后只对 blocker 指向的 claim 做定向返工，生成 successor evidence，再由两名新 judge 复验。整个 candidate、policy、jury、rework、event log 和 trace 都可只读检查。默认 runner 是确定性本地 fixture，它证明的是编排与验收语义，不是模型能力 benchmark。

**CTA：** 现在就可以运行 demo，并检查每一份 JSON 证据。

发布状态：`draft_owner_approval_required`

### 模型实验室 / 项目选型私信｜Flowness：一个可检查的 evidence-driven multi-agent Harness 候选

如果你正在收集开源 Agent Harness 项目，可以把 Flowness 放进待检查清单：它不从 Agent 数量开始，而是建模 goal → interview → design → engineering specification → consensus → plan → execution → independent review → layer-aware reflow → accepted outcome。当前公开包提供可运行的验收内核和机器可读的 mechanism、claim、evidence、drift 关系；设计与工程方案环仍未作为公开 E2E 证明。我们不声称 DeepSeek 已认可或选择本项目，也不把 Wow-Harness 的历史关注度解释成 Flowness 验证。

**CTA：** 建议先跑演示，再从任一 claim 反查代码、测试、失败和恢复证据。

发布状态：`draft_owner_approval_required`

## Claim ledger

| Claim | State | Evidence | Limitation |
| --- | --- | --- | --- |
| `CLM-FLOW-LOOP` The local Open Alpha demo executes three isolated producers, two judges, a forced FAIL, targeted rework, a successor candidate, fresh retest, and accepted trace. | `experimental` | docs/open-alpha-demo.md<br>src/flowness_oss_harness/open_alpha_demo.py<br>tests/test_public_harness_package.py | The default runner is deterministic and local.<br>Optional Codex producers do not turn deterministic judges into a model-quality benchmark. |
| `CLM-FLOW-NOAVERAGE` A mandatory FAIL or hard UNKNOWN blocks the candidate instead of being hidden by an average score. | `experimental` | docs/gate-rules.md<br>config/gates.json<br>tests/test_public_harness_package.py | This is verified for the local policy implementation, not every private deployment path. |
| `CLM-FLOW-EVIDENCE` The demo binds candidate, policy, jury, rework, evidence, event-log, and trace bytes for read-only reinspection. | `experimental` | docs/open-alpha-demo.md<br>schemas/open-alpha-demo-trace.schema.json<br>src/flowness_oss_harness/open_alpha_demo.py<br>tests/test_public_harness_package.py | Content binding does not by itself prove semantic correctness, production durability, or external reproducibility. |
| `CLM-FLOW-LEDGER` Ledger Core is the narrowest runnable Open Alpha slice for proposal visibility, terminal decisions, projection freshness, verdict readback, and bounded crash-tail recovery. | `experimental` | README.md<br>docs/mechanism-card-ledger-candidate-v0.md<br>registries/mechanism-cards-v0.json | The public mechanism remains experimental. The v1.0.0-alpha release retained a Linux aarch64 / CPython 3.12 non-author clean-room result; distributed behavior, production reliability, and a broader platform matrix remain unproven. |
| `CLM-FLOW-CONTENT` Flowness includes experimental mechanism, Claim, Unknown, Drift, Content Graph, and propagation contracts for tracing public statements back to evidence. | `experimental` | docs/content-graph.md<br>docs/drift-atlas-seed-v0.md<br>registries/mechanism-registry-seed-v0.json<br>config/content-graph.json | Contract presence and static propagation checks do not prove every private mechanism or channel is covered. |
| `CLM-FLOW-ARCH` The Architecture Atlas presents layered D0–D9 views and distinguishes current, experimental, target, and unknown material. | `experimental` | docs/architecture-atlas.md<br>config/architecture-atlas.json<br>registries/architecture-cross-layer-edges-local-v0.json<br>registries/mechanism-cards-v0.json<br>assets/architecture-atlas/open-alpha-v1/D0.mmd<br>assets/architecture-atlas/open-alpha-v1/D0.svg<br>assets/architecture-atlas/open-alpha-v1/D1.mmd<br>assets/architecture-atlas/open-alpha-v1/D1.svg<br>assets/architecture-atlas/open-alpha-v1/D2.mmd<br>assets/architecture-atlas/open-alpha-v1/D2.svg<br>assets/architecture-atlas/open-alpha-v1/D3.mmd<br>assets/architecture-atlas/open-alpha-v1/D3.svg<br>assets/architecture-atlas/open-alpha-v1/D4.mmd<br>assets/architecture-atlas/open-alpha-v1/D4.svg<br>assets/architecture-atlas/open-alpha-v1/D5.mmd<br>assets/architecture-atlas/open-alpha-v1/D5.svg<br>assets/architecture-atlas/open-alpha-v1/D6.mmd<br>assets/architecture-atlas/open-alpha-v1/D6.svg<br>assets/architecture-atlas/open-alpha-v1/D7.mmd<br>assets/architecture-atlas/open-alpha-v1/D7.svg<br>assets/architecture-atlas/open-alpha-v1/D8.mmd<br>assets/architecture-atlas/open-alpha-v1/D8.svg<br>assets/architecture-atlas/open-alpha-v1/D9.mmd<br>assets/architecture-atlas/open-alpha-v1/D9.svg | The public static candidate binds Mechanism Registry `registry_hash` sha256:1e8466c845398079139d3d6c250b857a4da43141f267f520a578e8633365ab8e, the included semantic-edge registry, and exact included D0–D9 Mermaid/SVG bytes. These static identities do not prove runtime reachability, renderer equivalence, or reliability. |
| `CLM-FLOW-BOUNDARY` The Open Alpha explicitly excludes private operational and sensitive surfaces and keeps repository and publication effects behind an owner-controlled release sequence. | `experimental` | README.md<br>docs/open-alpha-package-scope-v0.md<br>config/open-alpha-package-scope.json<br>docs/open-alpha-release-audit-v0.md | The v1.0.0-alpha release completed its sealed export, clean-room, jury, repository transition, and publication sequence. Any successor must bind fresh evidence to its own bytes; the earlier release cannot validate later changes. |
| `CLM-DEEPSEEK-OPPORTUNITY` The owner supplied an unverified hypothesis that a DeepSeek-related open-source Harness discovery opportunity may exist. | `unknown` | None — external hypothesis remains Unknown | User-supplied discovery context only; no primary-source criteria, endorsement, selection process, or deadline was verified in this offline pass. |

## Static boundary

Old Wow-Harness stars do not validate Flowness. They are continuity and historical-interest signals only; the rewritten implementation needs its own install, runtime, acceptance, and adoption evidence.

Every named external project, article, discussion, and paper is only an unverified search seed collected on 2026-08-02. A name or snippet may enqueue inspection; it cannot establish stars, architecture, behavior, maintenance, adoption, or comparative superiority.
