<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flowness license matrix

This matrix explains which license applies to each public line and asset type.
A more specific SPDX header or release file manifest takes precedence for an
individual path.

| Scope | License | Complete terms |
| --- | --- | --- |
| Flowness v1 source code, tests, schemas, executable configuration, scripts, and operational agent commands/skills/knowledge shipped with the software | Apache License 2.0 (`Apache-2.0`) | [Repository license text](LICENSE) |
| Flowness v1 explanatory documentation, diagrams, examples, public narrative, and media that are not executable operational assets | Creative Commons Attribution 4.0 International (`CC-BY-4.0`) | [Repository copy of the complete legal code](public-core/flowness-ledger-core/LICENSES/CC-BY-4.0.txt) |
| Wow-Harness `v0.x` historical bytes | MIT (`MIT`) | The `LICENSE` file in the applicable v0 tag or `legacy/wow-harness-v0` branch; [SPDX reference text](https://spdx.org/licenses/MIT.html) |
| Hosted, enterprise, credential, Transcript, customer, account/quota/fleet, server-operational, and other explicitly private surfaces | Proprietary; excluded from this public distribution | Not granted by this repository |

Flowness v1 does not retroactively relicense Wow-Harness v0.x. Conversely, the
historic MIT license does not replace the licenses assigned to new Flowness v1
bytes. No trademark rights are granted by the software or documentation
licenses.

See [NOTICE](NOTICE) for attribution context. Release SBOMs, package metadata,
and third-party notices describe dependencies and upstream obligations; they
do not change this first-party matrix.
