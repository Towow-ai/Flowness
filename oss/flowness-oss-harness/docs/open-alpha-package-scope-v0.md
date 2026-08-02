# Broadened Flowness Open Alpha package scope

`required_include_paths` are presence assertions. `required_exclude_paths` are
fail-closed exclusion assertions: when a named path exists in private staging it
must classify as `exclude`; after sealed export or replacement-tree import, its
absence is the expected sanitized state and must not make public CI fail.

The Open Alpha shows a real Harness, not only one small library. Its selected
public scope has four visible truth lanes:

| Lane | Proposed public surface | Meaning |
|---|---|---|
| Runnable Open Alpha | Runnable Flowness Ledger source, CLI, recovery/projection behavior, tests, quickstart, and bounded case material | Experimental product slice; exact successor identities and external release state live in the immutable release record |
| Experimental — canonical engine | Selected `harness/src/towow` EventLog, envelope, commit gate, projection, task/dependency, claim/fencing, dispatch/orchestration, review/finding/closure, recovery, lock and worktree source, plus matched tests | The real Flowness engine is inspectable; static first-party dependency closure is closed, without claiming the complete private runtime or production behavior |
| Experimental — OSS machine | Multi-agent evidence excavation and role runner; immutable evidence/candidate flow; independent jury and targeted rework; Mechanism, Unknown and Drift registries; Content Graph and propagation machinery; schemas, policies, tests, and examples | Inspectable working release/research machine; it is not presented as the product engine |
| Experimental — portable agent entry | The tracked `.agents/skills/work` entry plus the narrow planning, execution, review, fix, retest, handoff, recovery and meta-review command/skill/glue set from canonical `.claude` and packaged glue trees | Shows how an agent enters and follows Flowness without exporting the whole historical skill/evaluation workspace |
| Design Target | D0-D9 Architecture Atlas, public/private boundary, module route, Alpha/Beta/broad-release roadmap, and whitepaper evidence outline | The intended whole-system model with current, unknown, and target labels preserved; target nodes are not implementation claims |
| Private-Excluded | Deployment and server configuration, runtime ledgers and observations, transcript content, credentials/tokens, customer material, private jury/Pro packets, private channel drafts, and rights-unknown imported/reference assets | Never copied into the Open Alpha export by this scope |

The canonical expansion is selective. Account/rotation modules,
Transcript-backed supervision, real-spawn helpers, private `.claude` workspaces,
server/client settings, runtime ledgers, customer data and credentials remain
explicit `exclude`; other unselected engine and test files are `hold`, not
silently copied. Required path assertions prevent the EventLog, envelope,
commit gate, projection, orchestrator, review/fix/retest, recovery, locks, worktree and
portable entry anchors from falling out of the package unnoticed.

The first closure pass found 41 cross-boundary imports. They were resolved by
three explicit choices rather than copying the whole private runtime:

- portable contracts and direct kernel dependencies were reviewed into the
  include set;
- host maintenance entrypoints that are not needed by the canonical Alpha E2E
  were downgraded to `hold`; and
- account rotation, private Claude background spawning and live-session revival
  remain excluded behind `towow.l2.portable_runtime`.

The portable seam keeps mock orchestration runnable, shares only an existing
canonical `.towow` ledger, denies new dispatch when no resource governor is
installed, and fails closed for real spawn or maintenance emit paths that need
a separately authorized adapter. It contains no account, token, Transcript,
server or live-session implementation.

The manifest also performs a static closure check over every included Python
file under `harness/src/towow`. If selected code imports a first-party module
classified as `hold`, `exclude`, or cannot resolve it in the tracked tree, the
edge is emitted as a stable `DEP-*` blocker and closure remains `blocked`.
That is a packaging stop signal: the dependency must be replaced with a public
adapter or separately reviewed for inclusion before a clean-room claim. The
current canonical selection has zero such static blockers. This
static check does not replace runtime reachability, packaging, external
dependency, or clean-room tests.

The machine policy is
[`config/open-alpha-package-scope.json`](../config/open-alpha-package-scope.json).
The builder expands its ordered rules over every tracked file in the Ledger and
OSS-Harness roots. The resulting manifest therefore records an exact path,
Git blob, SHA-256, byte count, maturity, disposition, component, reason, and
claim boundary for each candidate file.

`include` means selected membership in the Open Alpha sealed-export process. It
does not by itself authorize distribution or prove production behavior.
`hold` means useful material is not assembled until its stated condition is
resolved. `exclude` is a hard package boundary. Any unclassified private
program record fails closed to the final exclusion rule.

This broader Alpha can truthfully introduce Flowness as:

> An evidence-first multi-agent engineering Harness with a stable decision
> ledger, selected canonical event-sourced Flowness engine code, an
> experimental orchestration/jury/content-maintenance system, and an explicitly
> labelled D0-D9 target.

It cannot truthfully claim that the complete private Flowness runtime, server
fleet, worktree/account/quota machinery, production security boundary, or
external effectiveness has been released or independently proven.

For each successor, file-level origin/license mapping, sensitive-content
scanning, sealing, independent clean-room installation, successor jury review,
and owner authorization must be rebound to the exact bytes in a retained
external release record before publication. Earlier predecessor evidence
cannot silently promote a later successor.
