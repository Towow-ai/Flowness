# Localization Strategy

Flowness should be bilingual at launch and translation-ready thereafter. Translation quality matters because core terms carry architectural claims.

## 1. Canonical languages

### English

Primary language for source code, public API, research distribution, HN, Product Hunt, Reddit, and international contributor communication.

### Simplified Chinese

Equal first-class language for project philosophy, Chinese developer communities, WeChat, Zhihu, Juejin, V2EX/LinuxDo, and founder voice.

Both READMEs should be reviewed together at every release. Neither should become an outdated summary of the other.

## 2. Next languages

Add only when a named maintainer owns terminology and release sync.

Recommended order:

1. Japanese — active AI/software-engineering audience and strong technical documentation culture;
2. Korean — active agent tooling community;
3. Spanish — broad open-source reach;
4. Traditional Chinese — low translation distance but still requires terminology review.

Do not publish machine-translated full READMEs without a maintainer label. A clearly marked community draft is better than false parity.

## 3. Canonical term table

| English | Simplified Chinese | Translation rule |
|---|---|---|
| Work | 工作对象 / 工作（context-dependent） | Do not translate as “任务” everywhere; Work can contain many tasks |
| Flow | Flow / 工作流动 | Keep `Flow` in category and product language; explain once |
| Flow Engineering | Flow 工程 | Avoid ordinary “流程工程” |
| Harness | Harness / 执行脚手架 | Keep industry term in technical text |
| Loop | Loop / 反馈循环 | Do not reduce to “重试” |
| Graph | Graph / 执行图 | Distinguish from knowledge graph |
| Reflow | 重流 | Define as invalidation + impact + recompilation, not rerun |
| Context Capsule | 上下文胶囊 | Keep capitalization in API references |
| Human Constitution | 人类宪法层 / 人定义的系统宪法 | Avoid implying state law |
| Cognitive Exoskeleton | 认知外骨骼 | Canonical Chinese phrase |
| Finding | Finding / 发现项 | Do not flatten into generic error |
| Activation | 激活 / 真实使用激活 | Distinguish from deployment |
| Acceptance | 接受 / 验收接受 | Authority-bearing, not model verdict |

## 4. Translation workflow

1. English and Chinese canonical source changes are drafted together.
2. Update `GLOSSARY.csv` first for new terms.
3. Translator works from a release branch and exact source commit.
4. Automated checks validate links, code blocks, status labels, and command parity.
5. Human reviewer compares claim strength, negation, uncertainty, and authority words.
6. Release manifest binds every language artifact.

## 5. What requires human review

- first/only/superiority claims;
- `must`, `may`, `cannot`, `should`;
- safety vs liveness distinctions;
- formal theorem boundaries;
- human authority and waiver language;
- runnable vs designed status;
- “activation,” “acceptance,” and “closure”;
- poetic lines and cultural metaphors.

## 6. Localization status header

Every non-canonical translation should begin with:

```text
Translation status: community draft
Source language: English
Source commit: <sha>
Last synchronized: <date>
Maintainer: <name or unowned>
Claim parity reviewed: yes/no
```

## 7. Short hero translations

### Japanese draft

> **仕事は残り、Agent はその周りに組み上がる。**  
> Flowness は、変化する Agent、コンテキスト、計画を越えて仕事を持続させる、仕事中心の Agentic Software Engineering Runtime です。

### Spanish draft

> **El trabajo persiste. Los agentes se organizan a su alrededor.**  
> Flowness es un runtime centrado en el trabajo para la ingeniería de software con agentes.

These are launch-card drafts, not full supported documentation.
