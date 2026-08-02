"""K4a · 判活决策核 (层①, 实现 rc-liveness-realtime-evidence-primary@v1)。

设计依据: 10-layer1-awareness.md §判活 / 故障检测 FLP+phi-accrual / owner 实战#2(卡死被误杀→孪生)。

## 本质 (owner #1 实战痛点的根治判据)
对"一个 agent 此刻活没活", FLP 证明慢/死理论判不准 → 不求判准, 求**误判往代价小处错**:
判活为死=杀+孪生(最坏) >> 判死为活=多等(可恢复)。所以**偏判活**。

## 决策表 (分层偏序, 不是投票; 顺序=优先级 —— 严格对齐 §T1.2 权威决策表 = classify_vitality 同序)
每类信号只在它"不假阳"的方向当权威。**本表序与 l2.session_vitality.classify_vitality 逐条对齐**
(T-RMD-S3-FLP 合一: 同一会话状态喂两核必得同裁决, 跨核等价测试钉死, 见 tests/integration/test_s3_flp.py):
1. has_canonical_product → DONE         (§T1.2 规则1: canonical 产物=DONE 最高, 优先于一切 DEAD 来路)
2. emitting_tokens 或 transcript_fresh → WORKING  (§T1.2 规则2: transcript 近期在动=强活, 优先于死亡来路)
3. holds_live_lock(且进程未确死) → PARKED  (§T1.2 规则3: 持锁恒不死=认领着工作=强活, 优先于 aborted/pid没了)
4. task_run_aborted → DEAD              (§T1.2 规则4: 显式 abort = 正向死亡证据)
5. process_alive==False → DEAD          (§T1.2 规则6: pid 确没了 = 正向死亡证据; 持锁则附带回收锁=zombie lock 回收)
6. revive_count>=budget → DEAD          (§T1.2 规则7=§T1.3: 复活预算耗尽=OTP max-restart-intensity, 仅冷时熔断)
7. 其余(沉默/探针失败) → UNKNOWN        (判不准保守不收割, 绝不当 DEAD —— owner #2 根治)

## 为什么这个序 (FLP 偏判活 + 跨核合一; T-RMD-S3-FLP 对齐了三处旧序差)
- **DONE / 在动 / 持锁 全部优先于 DEAD 来路** (规则 1/2/3 排在 4/5 前): 一个会话只要 ①已出 canonical
  产物(DONE) 或 ②transcript 近期在动(WORKING) 或 ③仍持非 stale 活锁(PARKED), 就**偏判活**——误判活为死
  (杀+孪生)代价远大于多等一轮(可恢复)。这正是 §T1.2 权威表把 has_success(规则1)/is_active(规则2)/
  holds_live_lock(规则3) 全排在 task_run_aborted(规则4)/process_alive(规则6) 之前的理由。
- **finding f-sub-flp-decision-tables-conflict 根治的三处旧序差**(旧 liveness 把正向死亡证据排太前 →
  与 classify_vitality 相反裁决): ① holds_live_lock vs task_run_aborted(finding 点名): 持锁+本 run
  aborted 旧判 DEAD、新判 PARKED(对齐); ② transcript_fresh 旧是"滞后弱佐证"排在死亡证据后(规则7),
  与权威表"transcript 在动=强活"(规则2)矛盾 → 在动+abort/pid探针死/复活耗尽旧判 DEAD、新判 WORKING
  (在动会话绝不因历史 abort / 滞后探针被误杀, = vitality test_active_beats_aborted 的保护); ③
  has_canonical_product 旧排规则5(死亡证据后), 新提规则1 DONE 最高(出货+abort 旧判 DEAD、新判 DONE,
  = vitality test_success_beats_aborted)。
- **zombie lock 仍回收** (规则3 带"进程未确死"守卫): liveness 的 holds_live_lock 是 heartbeat 原始
  信号(与 process_alive 独立采集), 故规则3 显式守 `process_alive is not False` —— 持锁但进程**确凿**
  没了(os.kill 探到 pid 死) = 死会话的 zombie lock, 不命中规则3, 落规则5 DEAD 回收锁, 绝不让"持锁
  恒不死"变成"废 run 永占执行位"(守 F-R11 / T-LRF-04 不回退)。
- **revive 熔断仅冷时生效** (规则6 排在规则2 后): 复活预算耗尽是 OTP 熔断, 但若 transcript 同时在动
  (规则2先命中→WORKING)说明这次复活成功了, 不熔断; 只有"耗尽且 transcript 冷"才落规则6 DEAD。

## 可证伪
对因为: DEAD 只由正向证据(abort/pid没/预算耗尽且冷)判, "冷+无产物"这种沉默降级 UNKNOWN 而非 DEAD →
休眠/泊车/限流卡住的会话不再被杀+孪生(owner 实战#2)。塌于 Y: 真死但持锁的会话(进程没了但锁还在),
靠 process_alive==False→DEAD(规则5, 规则3 守卫排除它) 回收, 不靠"冷"回收; 若 process_alive 探针在
隔离/远程查不到(None, 非 False) → 落规则7 UNKNOWN 保守(绝不因探针失败反推死, 也不破规则3 PARKED)。
"""

from __future__ import annotations

from .contracts.heartbeat import LivenessEvidence
from .contracts.per_form_adapter import AgentState, Vitality

DEFAULT_REVIVE_BUDGET = 3  # OTP max-restart-intensity 映射


def classify_liveness(
    ev: LivenessEvidence,
    *,
    revive_count: int = 0,
    revive_budget: int = DEFAULT_REVIVE_BUDGET,
    holds_locks: tuple[str, ...] = (),
) -> AgentState:
    """多源证据 → 裁决。FLP-非对称: 偏判活; DEAD 只由正向死亡证据; 沉默→UNKNOWN 保守。"""

    def st(verdict: Vitality, reason: str, revivable: bool) -> AgentState:
        return AgentState(verdict=verdict, reason=reason, revivable=revivable,
                          holds_locks=holds_locks, revive_count=revive_count)

    # 序与 §T1.2 权威表 / classify_vitality 逐条对齐 (T-RMD-S3-FLP 合一; 跨核等价测试钉死)。
    # 1. canonical 产物 → DONE (§T1.2 规则1: DONE 最高, 优先于一切 DEAD 来路)
    if ev.has_canonical_product:
        return st(Vitality.DONE, "has canonical product (DONE-authoritative)", False)
    # 2. transcript 近期在动 = 强活 (§T1.2 规则2, 优先于死亡来路): 实时出 token(最强) 或 transcript 新鲜。
    #    在动会话绝不因历史 abort / 滞后 pid 探针 / 复活耗尽被误杀 (= vitality test_active_beats_aborted)。
    if ev.emitting_tokens is True:
        return st(Vitality.WORKING, "emitting tokens (realtime)", True)
    if ev.transcript_fresh is True:
        return st(Vitality.WORKING, "transcript fresh (recent activity)", True)
    # 3. 持锁 = 认领着工作 = 强活 → PARKED (§T1.2 规则3: 持锁恒不死, 优先于 aborted/pid没了)。
    #    带"进程未确死"守卫 (process_alive is not False): 持锁但 pid 确凿没了 = zombie lock, 不命中此条,
    #    落规则5 DEAD 回收锁 (liveness 的 holds_live_lock 是原始信号, 须自守 zombie; vitality 上游已过滤
    #    stale 锁故其守卫是 no-op, 两核同序)。None(探针失败) is not False → True → 仍 PARKED 保守偏判活。
    if ev.holds_live_lock and ev.process_alive is not False:
        return st(Vitality.PARKED, "holds live lock (claiming work) → revive, not reap", True)
    # 4. 显式 abort = 正向死亡证据 (§T1.2 规则4)
    if ev.task_run_aborted is True:
        return st(Vitality.DEAD, "explicit task_run_aborted", False)
    # 5. pid 确没了 = 正向死亡证据 (§T1.2 规则6); 持锁则附带回收锁 (zombie lock 回收)
    if ev.process_alive is False:  # 严格 False, 非 None(探针失败)
        why = "process gone (pid dead)" + (" + holds lock→reclaim" if ev.holds_live_lock else "")
        return st(Vitality.DEAD, why, False)
    # 6. 复活预算耗尽 → 降级 DEAD (OTP; 仅冷时生效——transcript 在动已在规则2 判 WORKING 不熔断)
    if revive_count >= revive_budget:
        return st(Vitality.DEAD, f"revive budget exhausted ({revive_count}/{revive_budget})", False)
    # 7. 沉默/探针失败 → UNKNOWN 保守不收割 (owner #2 根治: 绝不把卡住当 dead)
    return st(Vitality.UNKNOWN, "silent / probe inconclusive → conservative, do NOT reap", True)
