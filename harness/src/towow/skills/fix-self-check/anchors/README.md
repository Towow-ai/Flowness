# fix-self-check 回归锚（anchors）

来源：skill-optimization wave-3 诊断（`workspace/diagnoses/fix-self-check.md` ②节），2026-07-07 建。
用法：改本 skill 文本前后做双向核对——**正锚不许退化**（新文本下 fork verdict 仍长范本的形状），
**负锚必须被治住**（冷读新文本的 fork/fixer 在锚定场景产出正确行为）。

- `p1-fork-verdict-exemplars.md` — 正锚 P1：三份 ★ 依从范本 verdict 原文（EP-B / EP-C / EP-D）。
  判据：修订不得削弱 ★ 段任何一条（真 grep 账本 / 引文件+行号 / 拒摘要 / 真跑复算 / 看真 diff）；
  新文本下的 fork verdict 仍长这个形状。
- `n1-ep-b-unilateral-flag-flip.md` — 负锚 N1：EP-B 单方换旗时间线（fork disprove 29 秒后被被审者
  换 inline 推翻、attestation 审计痕消失），附 EP-C / EP-D 的正面对照（从坏到好的协议演化线）。
  判据：冷读修订后文本，fork 的 verdict 不给"换旗放行"解释空间；fixer 的下一步落在合法出路之内。
- 结构锚 A-2：三部署面（根 `.claude/skills/` = `harness/.claude/skills/` = `harness/src/towow/skills/`）
  cmp byte-identical；CLI `--self-check-mode` 默认 `fork` 不动。

正锚 P2（fail-closed 纪律 + EP-C 诚实降 outcome=needs_further_review）、负锚 N2/N3、结构锚 A-1
（attestation 基线扫描）的完整判据在诊断报告 ②节，此处不重抄。
