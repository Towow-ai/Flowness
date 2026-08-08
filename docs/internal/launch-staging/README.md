# Launch staging (internal)

Files here are **staged for a future release** and are not part of the current
public surface:

- `README.after-flow-demo.md` / `.zh-CN.md` — the README pair that replaces the
  root pair only after the Work Outlives Agents Hero Demo passes its release
  gate (see `docs/demos/HERO_DEMO_SPEC.md` §9). Their links are root-relative
  **by design**; do not "fix" them here — they resolve when the files are
  promoted back to the repository root.
- `GITHUB_ABOUT.txt` — the GitHub About description source of record.

CI link checks intentionally skip this directory.
