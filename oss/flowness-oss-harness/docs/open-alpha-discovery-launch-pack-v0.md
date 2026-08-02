# Flowness Open Alpha — Discovery & Launch Pack

> Status: **Open Alpha pre-release material; independent clean-room, fresh jury, and exact release records are still required.**

This pack carries Open Alpha pre-release discovery and release copy. It self-contains local candidate evidence only; exact freeze, non-author clean-room, fresh jury, authorization, repository, and publication records remain required before release.

**Start here:** [project story](../README.md) · [10-minute Harness demo](open-alpha-demo.md) · [D0–D9 Architecture Atlas](architecture-atlas.md) · [Mechanism Registry](../registries/mechanism-registry-seed-v0.json) · [Open Alpha scope](open-alpha-package-scope-v0.md)

## GitHub metadata

**Description:** Evidence-driven multi-agent engineering harness: parallel agents, sealed evidence, independent juries, targeted rework, and traceable acceptance.

**Topics:** `ai-agent-harness`, `multi-agent-system`, `agent-orchestration`, `coding-agents`, `codex-cli`, `agent-evaluation`, `llm-evaluation`, `software-engineering`, `evidence-driven`, `provenance`, `human-in-the-loop`, `agent-governance`, `open-source`, `harness-engineering`

### Social preview copy

- Headline: Flowness
- Subhead: Parallel agents are easy. Trustworthy acceptance is the hard part.
- Footer: Evidence → independent jury → targeted rework → accepted outcome

## Layered introductions

### 30 seconds — A Harness for work that must survive review

Flowness is an evidence-driven multi-agent engineering harness. Its runnable proof today uses deterministic local producer and judge fixtures to exercise one goal → sealed evidence → independent checks → targeted rework → acceptance. Optional Codex mode replaces only the producers with real Codex processes; judges remain deterministic policy probes. This demonstrates inspectable orchestration and acceptance semantics, not model quality, production reliability, scale, security, or external adoption.

**CTA:** Inspect the 10-minute FAIL → rework → PASS demo and its trace.

### 3 minutes — The control loop around coding agents

Most multi-agent demos stop when several agents return answers. Flowness adds the control layer around that work: versioned producer and judge roles, isolated outputs, content-bound candidates, deterministic gates, persistent blocker identity, successor evidence, and independent retest. Its Ledger Core is the narrowest runnable slice; orchestration, jury, rework, mechanism, Drift, and Content Graph surfaces remain experimental. Architecture views distinguish current, experimental, designed-target, and unknown states. Linux aarch64 with CPython 3.12 is the intended first independent Open Alpha coordinate, but a retained non-author clean-room receipt and fresh jury decision for the exact release bytes are still required.

**CTA:** Run the deterministic demo, inspect both jury rounds, then follow the mechanism and architecture links.

### 10 minutes — Evaluate the system, not the slogan

Start with the Open Alpha demo: three producers run concurrently in isolated directories; their outputs become a sealed candidate; two judges receive the same candidate and policy; one credible failure blocks the round even when the other judge passes; targeted rework changes the failed claim and binds successor evidence; two fresh judges retest the new candidate; and a read-only inspector verifies hashes, identities, lineage, and the final decision. Then examine the broader contracts for EventLog and projection, orchestration and dispatch, review and recovery, work-attempt admission, mechanism discovery, Claim/Unknown/Drift registries, Content Graph propagation, and D0–D9 architecture. Treat local checks as local evidence. Server deployment, external clean-room installation, benchmark leadership, and adoption remain separate gates.

**CTA:** Use the claim ledger to challenge each sentence and open an issue for any broken evidence edge.

## For DeepSeek and other Harness selectors

If you are assembling or evaluating open-source agent Harness projects, raw agent count and a polished demo are weak selection signals. The useful questions are whether the project can bind an outcome to evidence, preserve failures, separate production from judgment, and explain what remains unknown.

Flowness is a candidate for inspection because its Open Alpha is organized around an explicit goal → producers → sealed evidence → independent jury → targeted rework → acceptance loop. The default proof is a deterministic local fixture; optional Codex mode replaces only producers, while judges remain deterministic policy probes. Its launch claims and assets are hash-bound to mechanisms, evidence and Drift in a candidate Content Graph. This is an invitation to inspect the source and trace, not a claim that DeepSeek has endorsed, selected, or specified criteria for Flowness.

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
- Repository state and external actions must be verified in GitHub at release time, and exact acceptance identities must be retained externally; both remain pending in this packet.

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
| Freeze and public manifest technical gate | `pre_release_required` | `true` | Freeze the exact successor bytes, regenerate the file-exact public manifest, and retain their external identities. |
| File-level rights, secret, PII, and IP gate | `pre_release_required` | `true` | Bind the owner rights authorization and final scan results to the exact successor export outside mutable repository prose. |
| Packaging, license, NOTICE, third-party, and version matrix | `pre_release_required` | `true` | Rerun package, dependency, license, sensitive-content, and public-boundary checks against the exact successor bytes. |
| Runnable Harness FAIL → rework → PASS E2E | `pre_release_required` | `true` | Run the deterministic canonical E2E from the exact sealed export and retain its result. |
| Claim, maturity, external-seed, and link checks | `local_candidate_ready` | `false` | Local registry, renderer, Content Graph, and static checks are inspectable; rerun them after the final freeze. |
| Sealed public directory | `pre_release_required` | `true` | Create and verify the exact successor sealed export, then retain its external identity. |
| Clean install and canonical E2E from sealed export | `pre_release_required` | `true` | A non-author must run all clean-room stages against the exact sealed export on Linux aarch64 with CPython 3.12 and retain the receipt. |
| Independent package and claim juries | `pre_release_required` | `true` | Fresh independent juries must review the exact successor and close every mandatory blocker. |
| GitHub description, topics, social copy, selector page, and drafts | `local_candidate_ready` | `false` | Launch assets are assembled locally and remain non-publishing until all pre-release gates pass. |
| Owner authorization of release and channel actions | `pre_release_required` | `true` | Record final owner approval against the exact successor only after technical, clean-room, and jury evidence is complete. |
| Repository transfer, rename, redirects, and private vulnerability reporting | `release_time_gate` | `true` | At release time, verify repository identity and old URL/Git redirects, and validate GitHub Private Vulnerability Reporting; record the observed state externally. |
| External source verification and reproducible comparison | `post_alpha` | `false` | Process the source queue after the Open Alpha package is frozen; do not delay source release merely to claim comparative leadership. |

## GitHub Release draft

**Tag candidate:** `v1.0.0-alpha`

**Title:** Flowness Open Alpha — evidence-driven multi-agent engineering harness

**Pre-release:** `true`

### What this is

Flowness turns an engineering goal into parallel agent work, sealed artifacts and evidence, independent judgments, targeted rework, and a traceable accepted outcome. This Open Alpha makes the Harness control loop inspectable while marking incomplete surfaces as experimental.

### Try first

Run the 10-minute deterministic demo. It executes three isolated producers, blocks the first candidate on one credible FAIL, creates bounded successor evidence, reruns two fresh judges, and verifies the final chain read-only.

### Included maturity layers

Ledger Core is the most mature local candidate. The controller, role registry, jury, rework, work-attempt ledger, Mechanism/Claim/Drift/Content Graph machinery, and broader canonical engine are experimental. Architecture targets are labelled and are not described as implemented runtime behavior.

### Limits

This pre-release does not establish production reliability, scale, security hardening, benchmark leadership, or external adoption. Private context, customer data, credentials, account/quota/fleet operations, server configuration, private runtime ledgers, and rights-unknown assets are excluded.

### From Wow-Harness

Flowness is a ground-up major-version evolution of Wow-Harness. Historical interest belongs to the legacy project; old stars do not validate the rewritten Flowness implementation. The migration keeps attribution and explains the replacement boundary.

### Do not publish until

- Bind the exact successor commit, tree, scope, export, wheels, clean-room receipt, jury verdict, and owner authorization in the immutable external freeze/release record.
- Complete the repository transfer and rename, verify old URL and Git redirects, and enable and validate GitHub Private Vulnerability Reporting.

## 中文渠道短稿

### GitHub / 开发者社区短帖｜Flowness Open Alpha：不是让更多 Agent 开工，而是让结果经得起验收

多 Agent 很容易制造大量输出，难的是判断结果能不能被接受。Flowness 把一项目标拆给多个 producer，把产物和证据密封后交给独立 judge；任何可信 FAIL 或关键 UNKNOWN 都会形成 blocker，进入定向返工和新一轮复验，不能靠平均分糊过去。Open Alpha 会公开可运行的本地闭环、Ledger Core、实验性的编排／评审／返工机制，以及分层架构和证据边界。它仍是 Alpha，不宣称生产可靠性、规模化或外部采用。

**候选 CTA：** 先跑 10 分钟 FAIL → 返工 → PASS 演示，再决定它是不是你要找的 Harness。

发布状态：`draft_owner_approval_required`

### 知乎 / 公众号｜当多个 AI Agent 都说“做完了”，谁来证明它真的完成了？

Flowness 想解决的不是“怎么再多叫几个 Agent”，而是多 Agent 工程最难收口的一段：谁生产、谁判断、证据属于哪个候选版本、失败后只返工什么、怎样确认新结果真的覆盖了旧 blocker，以及谁有权最终发布。我们准备开放的是一套分层 Open Alpha：最成熟的 Ledger Core、可运行的多 Agent 验收演示、实验性的 orchestration／jury／rework／Drift／Content Graph，以及把现状和理想目标分开的 D0–D9 架构。所有能力按成熟度讲，未知就保留未知。

**候选 CTA：** 欢迎从代码、测试和失败链路挑刺，而不是只看概念。

发布状态：`draft_owner_approval_required`

### 掘金｜我们给多 Agent 编排加了一条不能被平均分绕过的验收回路

示例里三个 producer 并行生成候选，两个 judge 独立审查同一份候选和 policy。第一轮一个 PASS、一个可信 FAIL，系统仍然 BLOCKED；之后只对 blocker 指向的 claim 做定向返工，生成 successor evidence，再由两名新 judge 复验。整个 candidate、policy、jury、rework、event log 和 trace 都可只读检查。默认 runner 是确定性本地 fixture，它证明的是编排与验收语义，不是模型能力 benchmark。

**候选 CTA：** Open Alpha 后可直接运行 demo 并检查每一份 JSON 证据。

发布状态：`draft_owner_approval_required`

### 模型实验室 / 项目选型私信｜Flowness：一个可检查的 evidence-driven multi-agent Harness 候选

如果你正在收集开源 Agent Harness 项目，可以把 Flowness 放进待检查清单：它提供 goal → deterministic local producers → sealed evidence → deterministic policy judges → targeted rework → accepted outcome 的可运行 fixture，并把 mechanism、claim、evidence、drift 和传播素材之间的关系做成机器可读契约。owner 只提出了一个尚未核验的 DeepSeek 相关传播机会假设；我们不声称 DeepSeek 正在选型、已经认可或选择了项目，也不把 Wow-Harness 的历史关注度解释成 Flowness 验证。

**候选 CTA：** 建议先跑演示，再从任一 claim 反查代码、测试、失败和恢复证据。

发布状态：`draft_owner_approval_required`

## Claim ledger

| Claim | State | Evidence | Limitation |
| --- | --- | --- | --- |
| `CLM-FLOW-LOOP` The local Open Alpha demo executes three isolated producers, two judges, a forced FAIL, targeted rework, a successor candidate, fresh retest, and accepted trace. | `experimental` | docs/open-alpha-demo.md<br>src/flowness_oss_harness/open_alpha_demo.py<br>tests/test_public_harness_package.py | The default runner is deterministic and local.<br>Optional Codex producers do not turn deterministic judges into a model-quality benchmark. |
| `CLM-FLOW-NOAVERAGE` A mandatory FAIL or hard UNKNOWN blocks the candidate instead of being hidden by an average score. | `experimental` | docs/gate-rules.md<br>config/gates.json<br>tests/test_public_harness_package.py | This is verified for the local policy implementation, not every private deployment path. |
| `CLM-FLOW-EVIDENCE` The demo binds candidate, policy, jury, rework, evidence, event-log, and trace bytes for read-only reinspection. | `experimental` | docs/open-alpha-demo.md<br>schemas/open-alpha-demo-trace.schema.json<br>src/flowness_oss_harness/open_alpha_demo.py<br>tests/test_public_harness_package.py | Content binding does not by itself prove semantic correctness, production durability, or external reproducibility. |
| `CLM-FLOW-LEDGER` Ledger Core is the narrowest runnable Open Alpha slice for proposal visibility, terminal decisions, projection freshness, verdict readback, and bounded crash-tail recovery. | `experimental` | README.md<br>docs/mechanism-card-ledger-candidate-v0.md<br>registries/mechanism-cards-v0.json | The public mechanism remains experimental. Linux aarch64 / CPython 3.12 is a pre-release clean-room target, not an independently accepted coordinate in this mutable candidate; distributed behavior, production reliability, and a broader platform matrix are also unproven. |
| `CLM-FLOW-CONTENT` Flowness includes experimental mechanism, Claim, Unknown, Drift, Content Graph, and propagation contracts for tracing public statements back to evidence. | `experimental` | docs/content-graph.md<br>docs/drift-atlas-seed-v0.md<br>registries/mechanism-registry-seed-v0.json<br>config/content-graph.json | Contract presence and static propagation checks do not prove every private mechanism or channel is covered. |
| `CLM-FLOW-ARCH` The Architecture Atlas presents layered D0–D9 views and distinguishes current, experimental, target, and unknown material. | `experimental` | docs/architecture-atlas.md<br>config/architecture-atlas.json<br>registries/architecture-cross-layer-edges-local-v0.json<br>registries/mechanism-cards-v0.json<br>assets/architecture-atlas/open-alpha-v1/D0.mmd<br>assets/architecture-atlas/open-alpha-v1/D0.svg<br>assets/architecture-atlas/open-alpha-v1/D1.mmd<br>assets/architecture-atlas/open-alpha-v1/D1.svg<br>assets/architecture-atlas/open-alpha-v1/D2.mmd<br>assets/architecture-atlas/open-alpha-v1/D2.svg<br>assets/architecture-atlas/open-alpha-v1/D3.mmd<br>assets/architecture-atlas/open-alpha-v1/D3.svg<br>assets/architecture-atlas/open-alpha-v1/D4.mmd<br>assets/architecture-atlas/open-alpha-v1/D4.svg<br>assets/architecture-atlas/open-alpha-v1/D5.mmd<br>assets/architecture-atlas/open-alpha-v1/D5.svg<br>assets/architecture-atlas/open-alpha-v1/D6.mmd<br>assets/architecture-atlas/open-alpha-v1/D6.svg<br>assets/architecture-atlas/open-alpha-v1/D7.mmd<br>assets/architecture-atlas/open-alpha-v1/D7.svg<br>assets/architecture-atlas/open-alpha-v1/D8.mmd<br>assets/architecture-atlas/open-alpha-v1/D8.svg<br>assets/architecture-atlas/open-alpha-v1/D9.mmd<br>assets/architecture-atlas/open-alpha-v1/D9.svg | The public static candidate binds Mechanism Registry `registry_hash` sha256:1e8466c845398079139d3d6c250b857a4da43141f267f520a578e8633365ab8e, the included semantic-edge registry, and exact included D0–D9 Mermaid/SVG bytes. These static identities do not prove runtime reachability, renderer equivalence, or reliability. |
| `CLM-FLOW-BOUNDARY` The Open Alpha explicitly excludes private operational and sensitive surfaces and keeps repository and publication effects behind an owner-controlled release sequence. | `experimental` | README.md<br>docs/open-alpha-package-scope-v0.md<br>config/open-alpha-package-scope.json<br>docs/open-alpha-release-audit-v0.md | Local packaging and sensitive-content checks are inspectable, but the exact sealed export, non-author clean-room, fresh jury, repository transition, GitHub security setup, publication, and successor identity remain pre-release responsibilities. |
| `CLM-DEEPSEEK-OPPORTUNITY` The owner supplied an unverified hypothesis that a DeepSeek-related open-source Harness discovery opportunity may exist. | `unknown` | None — external hypothesis remains Unknown | User-supplied discovery context only; no primary-source criteria, endorsement, selection process, or deadline was verified in this offline pass. |

## Static boundary

Old Wow-Harness stars do not validate Flowness. They are continuity and historical-interest signals only; the rewritten implementation needs its own install, runtime, acceptance, and adoption evidence.

Every named external project, article, discussion, and paper is only an unverified search seed collected on 2026-08-02. A name or snippet may enqueue inspection; it cannot establish stars, architecture, behavior, maintenance, adoption, or comparative superiority.
