# Review Casebook — 12 例：VoI 判断 + Falsification 精神

> 用途：review skill 的 case study——遇到类似情况时拿这些例子做参考。
> 归属：M-1.5 review skill 知识库
> 核心精神：不是"X 风险面 → 跑 Y 维度"标签化，是讲故事——reviewer 怎么基于 differentiated context 判断 + Apply VoI

---

## 例 1: Demo + 长期发展上下文——同一观察两种 VoI（Nature 给的核心例）

**Task**：实现一个 demo 系统让客户看效果。

### Scenario A: VoI = 0（假问题）

**Reviewer fork**（执行路径）："这个 demo 没有并发——多个用户同时操作会有 race condition。"

**VoI 测试**：如果 author 加并发处理，任务完成度改善多少？
- Task spec: "demo 给客户看效果"
- demo 不需要并发——客户看一次操作的演示
- VoI = 0

**判断**：finding 丢弃。这是漫游找问题。

### Scenario B: VoI > 0（真问题）

**同样的 reviewer fork**："这个 demo 没有并发。**结合 brief 里用户明确说'demo 验证完后长期发展'**——如果现在不预留并发接口，后续改造代价远大于现在预留。"

**VoI 测试**：如果 author 现在预留并发接口，任务完成度改善多少？
- Task spec: "demo + 长期发展铺路"
- 现在预留 vs 后续重做：现在预留成本 5%，后续重做成本 80%
- VoI > 0

**判断**：finding 上报。voi_rationale 必须含"长期发展"这个具体 context。

**核心**：同一事实观察，**voi_rationale 由 context-aware reasoning 决定**——reviewer 看到 brief 含"长期发展"才能 produce 有效 finding。

---

## 例 2: 一行代码 critical bug（Anthropic 真实 case）

**Task**：修改 auth 服务的一行代码（routine 看起来）。

**Reviewer fork**（执行路径模拟）：

```
mental simulation:
  - patch diff: 一行——将 `check_auth_token(token)` 改为 `check_auth_token(token, optional=true)`
  - 跟踪 caller: 这个函数有 3 个 caller
  - 跟踪 caller 1: AuthMiddleware → 之前依赖 strict check → 现在变 optional 会让未认证请求通过
  - falsification attempt: 构造 input=unauthenticated_user → middleware 让其通过 → 严重 break authentication
  - confirmed
```

**Finding**：
```yaml
voi_rationale: "auth middleware 依赖 strict check——本 patch 让 unauthenticated 请求通过认证，直接破坏 task 隐含的 'auth 仍然 enforce' 约束"
severity: critical
target.location: "src/auth/auth.py:42"
suggested_fix_layer:
  primary: code
  rationale: "需要 caller 显式 opt-in optional 而不是默认 optional"
falsification_evidence:
  attempt: "构造 unauthenticated request → middleware call chain → confirmed bypass"
  result: confirmed
review_dimension: method-execution-path
```

**核心**：看起来 routine 的一行 patch 可能是 critical——**执行路径模拟（追踪 caller chain）抓到 author skim read miss 的问题**。这是 reviewer 不同 lens 的核心价值。

---

## 例 3: Preexisting bug in adjacent code（TrueNAS 真实 case + purple severity）

**Task**：refactor ZFS encryption module。

**Reviewer fork**（内部一致性）：

发现 patch 没改 adjacent code，但 adjacent code 含 latent type mismatch（不是 PR 引入的）——`encryption_key_cache` 在 sync 时被 silently wipe，影响整个 system。

**判别尺——这算不算 author scope creep？**

- Finding 跟 task 相关吗？相关——同 module，sync 是 task 涉及的操作
- 如果 author 不处理：bug 仍然 latent，但 author 至少需要知道
- VoI > 0：author 知道这个 bug 后可以决定（修 / 留作 known risk / 提 issue）

**Finding**：
```yaml
voi_rationale: "adjacent code 的 type mismatch 在 sync 时 wipe encryption key cache——跟本 task 同 module + 同 operation，author 需要知道这个 latent bug 才能决定修复策略"
severity: purple                       # 关键——preexisting，跟 PR 不直接相关
is_preexisting: true                   # 标记不算 author scope creep
target.location: "src/zfs/encryption.py:128 (not in PR diff)"
suggested_fix_layer:
  primary: code
  rationale: "修 type cast"
review_dimension: method-consistency
```

**核心**：reviewer 的 lens 可以扩到 adjacent code，**只要跟 task 相关 + VoI > 0**——不是漫游。purple severity + is_preexisting=true 让 author 知道这不算 scope creep。

---

## 例 4: 假阳性 verify-step 过滤掉

**Reviewer fork**（红队）："输入 `username='\'; DROP TABLE users--'` 会触发 SQL injection"

**Verify-step fork**：
```
disprove attempt:
  - 看实际 SQL query construction: 用的是 parameterized query (psycopg2 `cursor.execute("...", params=(username,))`)
  - SQL injection 在 parameterized query 下不可触发
  - finding 描述的失败场景不存在
  - able_to_disprove
```

**Finding**：verification_state = `rejected_false_positive`，丢弃，不进入 author resolution。

**核心**：verify-step 独立物理 verification 把 noise 压下来——这是 Anthropic < 1% FP rate 的秘诀。

---

## 例 5: 假阳性但 verify-step 不确定（unverified_inconclusive）

**Reviewer fork**（执行路径）："并发场景下 `cache.get(key)` 跟 `cache.set(key)` 间可能 race"

**Verify-step fork**：
```
disprove attempt:
  - 看 cache 实现: 用的是 threadsafe Lock
  - 但 patch 引入了 async wrapper，async 跟 thread lock 交互不确定
  - 物理上无法独立验证不能 race
  - inconclusive
```

**Finding**：verification_state = `unverified_inconclusive`——给 author 自己判断（author 比 verify-step fork 更了解 async wrapper 的具体实现）。

**核心**：三态输出（不只 verified/rejected）——诚实的 "I don't know" 比强行二分更有用。

---

## 例 6: Author 反驳带 novelty 收敛

**Finding**："SQL query 没用 prepared statement"

**Author dispute**：
```yaml
novelty_type: new_interpretation
detail: "这段代码是从可信 internal source 读 query template，user input 不会到达 SQL；threat model 在本仓不成立——只有 admin 能改 template。"
```

**Reviewer fork 看 dispute**：
- 检查 dispute 的 claim 是否成立——查 caller chain，确认 template 来源
- 找到 template 来自 config file（admin 可改）→ dispute 成立
- Reviewer withdraws finding → FindingResolved(retracted)

**核心**：author 反驳权是合法的——但必须带 novelty（new_interpretation 含具体内容），不是"我不同意"了之。Reviewer 看到合理 novelty 后 withdraw 是健康的，不是失败。

---

## 例 7: Author 反驳不带 novelty 被 commit gate 拒

**Finding**："这段没做 input validation"

**Author dispute attempt**：
```yaml
novelty_type: new_interpretation
detail: "我不觉得需要 validate"
```

**M-0.5 NoveltyCheck Substantive audit** 抽样到这条 dispute：
- 物理 check 通过（novelty_type 不为空）
- 但 audit 判定：detail "我不觉得需要" 没具体内容——not substantive novelty
- audit verdict: reject (audit_failed, novelty_substance_insufficient)

**dispute supersede 被 commit gate 拒**——author 必须补具体 novelty 或 accept / escalate。

**核心**：novelty 不是仪式——必须有 substance。Substantive audit 防"novelty 字段填了但是假 novelty"。

---

## 例 8: Escalate Nature

**Finding round 3**：reviewer "input X 会破坏 invariant Y"
**Author dispute round 3**：novelty = "我有 fallback Z 处理 X"
**Reviewer round 4**：novelty = "Z 在 corner case W 不生效"
**Author dispute round 4**：novelty_type = no_new_information=true

**dispute loop 终止**：no_new_information=true 触发 M-0.5 commit gate 拒 supersede——
- Reviewer + Author 都没新东西
- → escalate Nature → FindingResolved(escalated)

**Nature 仲裁后** capture 一个 judgment（NatureJudgmentCaptured event），可能升级为 obligation（M-0.6）。

**核心**：escalate 不是失败——是合理的最后一步。novelty-gated 让收敛有明确终止条件，不需要 arbitrary "最多 N 轮"。

---

## 例 9: Meta-review 抓 review_plan 缺陷

**Review_plan**：dimensions = [method-execution-path, method-consistency]（漏了 method-red-team）

**Meta-review fork**：
```
disprove attempt:
  - 读 task: 改 auth/login.py
  - 风险面识别: 安全风险面（auth/* file pattern 触发）
  - 安全风险面 → 强制 red-team 维度（F-08f）
  - review_plan 漏了 red-team → critical gap
```

**Meta-finding**：
```yaml
voi_rationale: "review_plan 漏了 red-team 维度——安全风险面 schema-level 强制，本次 review 不跑 red-team 就不能 surface auth bypass / injection 类问题"
target: "review_plan.dimensions"
suggested_fix_layer:
  primary: spec
  rationale: "更新 review_plan 加 method-red-team dimension"
review_dimension: meta-review
```

**核心**：meta-review 审 review_plan 本身——是 review 的元层 falsification。

---

## 例 10: Multi-perspective 同根因去重

**3 个方法论 fork 同时报**：

**Fork 1（执行路径）**："`update_user(id)` 在 id 来自 user input 时构造 SQL 直接拼接 → SQL injection"
**Fork 2（红队）**："构造 input id=`1 OR 1=1` 让 `update_user` SQL 变成 `WHERE id=1 OR 1=1` 影响所有 user"
**Fork 3（一致性）**："API doc 说 'id is sanitized' 但 code 没 sanitize"

**Aggregation fork**：
- target.location 都指向 `src/user_service.py:42`
- root cause 都是"SQL injection due to direct string interpolation"
- merge 成一个 finding，多 review_dimension 字段记录被几个 fork 发现

**Merged finding**：
```yaml
voi_rationale: "SQL injection at update_user(id)——3 个 fork 独立确认"
target.location: "src/user_service.py:42"
severity: critical                       # 多 fork 同根因 → 高信心 + 高 severity
review_dimension: [method-execution-path, method-red-team, method-consistency]
falsification_evidence:
  attempt: "三个独立尝试都 confirmed"
  result: confirmed (high confidence)
```

**核心**：multi-fork 发现同根因是好 signal（不是通胀）——aggregation 去重保留高信心。通胀失败模式是"3 个 fork 同同方向 lens 重复发现同 finding"——v3.1 按方法论正交分就是为了避免这件事。

---

## 例 11: Adjacent code finding scope creep（反例）

**Reviewer fork**（执行路径）：在 task 的 caller chain 上发现 adjacent module 的 logging 代码风格不一致——"这个 logger 应该用 structured logging 而不是 printf 风格"

**VoI 测试**：如果 author 改 adjacent module 的 logging style，本 task 完成度改善多少？
- Task 是改 auth/login.py，不是 logging refactor
- Logging style 跟当前 task 无直接关系
- VoI = 0

**判断**：finding 丢弃。这是 scope creep——reviewer 看到了什么"应该改进的"就提，跟当前 task 无关。

**判别尺（vs 例 3 TrueNAS）**：例 3 的 adjacent code finding 跟 task 同 module + 同 operation + impact 跟 task 相关 → VoI > 0；本例 adjacent code 跟 task scope 无相关性 → VoI = 0。

**核心**：adjacent code 是合法的探查范围（例 3）——但仍受 VoI 限制（本例）。

---

## 例 12: Reviewer 想越权改代码（V-02 防线）

**Reviewer fork**（执行路径）：发现 patch 有明显 typo——`user_namee` 应该是 `user_name`。

**诱惑**：reviewer 想"我直接改了 commit 一下，比写 finding 快"

**V-02 物理拦截**：reviewer fork 的工具白名单只含 Read/Grep/Glob/Bash——Edit / Write 物理不可用。

**Reviewer 只能产 finding**：
```yaml
voi_rationale: "typo `user_namee` 会让运行时报 attribute error——直接影响 task done_criteria 'service can be called'"
severity: major
target.location: "src/user_service.py:42"
suggested_fix_layer:
  primary: code
  rationale: "改成 user_name"
```

→ author 看到 finding → accept → M-1.6 fix 接 → 修复。

**核心**：V-02 不只是字面"不让 reviewer 改代码"——是**保持 reviewer 评估者 lens 干净**。reviewer 想"我改一下"是 context 污染信号；物理隔离让这件事不可能。

---

## 12 例的共同精神

1. **VoI 是灵魂判据**（例 1）—— 同事实不同 context 不同 VoI
2. **不同 lens 抓不同问题**（例 2 执行路径 / 例 3 一致性 / 例 9 meta-review）
3. **Verify-step 独立性 = noise 压制**（例 4 / 例 5）
4. **Author 反驳权 + novelty 收敛**（例 6 / 例 7 / 例 8）
5. **Multi-fork 同根因是好 signal**（例 10）
6. **adjacent code 合法 + VoI 限制**（例 3 vs 例 11）
7. **V-02 保护 context 干净**（例 12）
8. **Falsification 精神贯穿**（每例都尝试 disprove 而不是证明）
