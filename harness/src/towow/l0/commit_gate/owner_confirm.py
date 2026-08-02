"""owner-confirm@v1 — 不可伪造 owner 授权的验证原语 (组件6: 红线 enforcement + owner-confirm)。

WHY (anti-fake-done 的最后一块 — R2 留的 stub 要接的地方)
----------------------------------------------------------
红队实证: agent 有【文件系统 + import + 自报 provenance】三样权限, 系统里所有"owner-only"锚
(admin-bypass.flag / --owner-confirm 布尔 flag / provenance actor_type=nature / interview 的
owner_confirmation NJ) 都可被单人一手伪造 —— 它们校验的是"事件长得像 owner 发的", 而"像不像"
全是 agent 自报的。要拦住"autopilot 自助退役冻结 task""autopilot 自助绕 4 类不可逆红线", 信任
必须从【谁 emit 了事件】(可伪造) 挪到【事件里带了一个 emitter 算不出来的东西】—— 一个签名。

不可伪造性的唯一真锚 (诚实边界, 见模块尾 residual)
--------------------------------------------------
本模块只做【验签侧】: 用一把【钉死在源码常量里的公钥】验证 owner 对【具体动作挑战】做的 Ed25519
detached 签名。agent 造得出 OwnerConfirmationGranted 事件, 造不出有效签名 —— 除非它拿到私钥。
所以真不可伪造 ⟺ (a) 私钥在 agent 的工具信封【外】, 且 (b) 钉死的公钥常量不能被换。两条都
【不是代码能兜的】: (a) 是 owner 的私钥托管选型, (b) 靠"改这个常量 == 改门逻辑本身 ==
monkeypatch, 划在威胁模型外"来保。
⚠️ 托管选型注记 (2026-07-03 红队实测): macOS Keychain `-T ""` 形态【不成立】—— security CLI
后续读取无弹窗, agent 起的 pytest 子进程当场读走私钥并签出有效签名。已就位形态 = owner 自持
(密码管理器) + `towow owner-confirm grant` 交互式隐藏输入, 私钥只存在于 owner 的密码管理器
与签名瞬间的进程内存 (产生侧见 cli/main.py owner-confirm 命令组模块头)。

- 公钥【钉在源码常量】(非 keys/*.pub 数据文件): 数据文件可能落进某 task 的 write-set、且不受源码
  review 力度审视; 源码常量与门逻辑同一保护级 —— 换它 = 改门 = monkeypatch (威胁模型外)。
- 私钥托管未就位时 (``_PINNED_OWNER_CONFIRM_PUBKEY_HEX is None``) 一律 fail-closed —— 与 R2 留的
  stub 行为一致 (agent 自助退役/绕红线全拒), 直到 owner 把真公钥填进常量并托管好私钥。

这两处 (retire 闭合门 + 4 红线物理门) 共用本模块的同一把公钥/同一验签核, 一处漏则处处漏、一处硬则
处处硬 —— 单一信任根, 不是两套。

⚠️ 残险 3 (replay / standing-authorization, 未闭合 — 诚实登记):
本模块【只验签、不消费 nonce】。一条验签通过的 owner-confirm 对它绑定的目标【永久】有效, 不是单次。
- 物理路 (真实缺口): action_fingerprint = sha256(command + write_set), owner 对 `rm -rf /data/X`
  签一次 → 之后任何 `rm -rf /data/X` 都验得过。这是"对该确切命令的常驻授权", 不是单次批准。
  要单次必须记 nonce 消费 (一条 consume-event, 见过即拒)。
- retire 路 (大概率被构造边界兜住, 未完全核实): 绑 (task_id, finding_id), 只在"已关 task 可再关 /
  task_id 会复现"时才有 replay 面; 若"closed 是终态"成立则 retire 的 replay 由构造边界兜死, 与 nonce
  消费无关 —— 该前提本轮未逐字核实, 登记为待验。
不可伪造 3 前提 (缺一即塌回可伪造类): (a) 私钥在 agent 信封外, (b) 钉死公钥常量不被换, (c) replay
有界 (单次消费 / 构造边界)。(c) 本轮未落地, 是登记的残险, 不是已达成。

姊妹模块威胁模型口径 (与 event_log.py 的 done_elsewhere 写边界锁收敛, 别读成同一保护级)
--------------------------------------------------------------------------------
本模块 (retire 闭合门 + 4 红线物理门) 的不可伪造性建立在【import-proof 的密码学签名】上: agent
即便有 filesystem + import 权限也造不出有效 Ed25519 签名 (前提是私钥在信封外、公钥常量不被换)。
event_log.py 的 done_elsewhere 写边界锁 (_AUDIT_PRODUCER_ONLY_TYPES) 【不是】同级保护: 它只在
CLI/envelope 面机械预防伪造 sanctioned 审计链 (两写口封死), import/原始-append 面 (segments.
append_raw_event_line) 不在其机械预防内, 靠 audit_integrity 的 record_hash 事后侦测兜底。两模块
都诚实登记各自边界——别把 done_elsewhere 的写边界读成"import 面也被机械封死"。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from towow.l0.event_log.index import EventIndex

# ── 红线类别 (challenge 绑定的第一字段 —— 一个 owner 签名只对它签的那类动作有效) ──
RED_LINE_RETIRE_TASK = "retire_task"  # 退役一个冻结 task 的前提判定 (Class A owner 决定)
RED_LINE_DELETE_DATA = "delete_data"
RED_LINE_OUTBOUND_PUBLISH = "outbound_publish"
RED_LINE_FUNDS = "funds"
RED_LINE_PUBLIC_COMMITMENT = "public_commitment"

# canonical OwnerConfirmationGranted 的 event_type 字面 (本模块自包含, 不 import EventType 枚举:
# 门读账本时 record.event_type.value == 本字面, 与 R1 精神一致——只认原始扁平事件、不吃 stub-rewrap)。
OWNER_CONFIRMATION_GRANTED_TYPE = "OwnerConfirmationGranted"

# ── 钉死公钥 (32-byte raw Ed25519 public key, hex) ──────────────────────────────
# None = 私钥托管未就位 → 一律 fail-closed (安全默认, 等同 R2 stub)。owner 就位私钥托管后, 把对应
# 公钥的 64-hex 填进这里 (这是一次【核心改动】: 改它 = 改信任根, 应视同碰门逻辑, 走 owner/canary)。
# 2026-07-03: Nature 本人跑 `towow owner-confirm keygen` 就位, 私钥自持 (密码管理器)。
# 首对密钥 (5578ce98…) 因私钥经会话内 `!` 命令 stdout 进入 agent 对话上下文而作废轮换 —
# 本常量为轮换后的第二对公钥; 私钥仅存于 owner 密码管理器, 从未出现在任何 agent 可读通道。
_PINNED_OWNER_CONFIRM_PUBKEY_HEX: str | None = (
    "fca3ab025779f8f2c05f8bd2bdde303458cf487bcc8ce74976432c601ababaad"
)


def _pinned_pubkey_hex() -> str | None:
    """call-time 读钉死公钥常量 (不在函数默认参绑定, 好让 owner 就位/测试注入生效)。"""
    return _PINNED_OWNER_CONFIRM_PUBKEY_HEX


def action_fingerprint(command_or_payload: str, write_set: tuple[str, ...] | list[str]) -> str:
    """4 红线动作的稳定指纹 (owner 签的是这个具体动作, 不是一句泛权限)。

    与物理门 IrreversibleActionBlocked 的 per-instance 签名同构 (command_or_payload + write_set),
    使 owner 对"删 A"的签名换不到"删 B"。sha256 全长 hex (碰撞面无实际意义, 不截断)。
    """
    blob = "\x00".join([command_or_payload, *write_set])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def canonical_challenge(
    red_line_class: str,
    *,
    task_id: str | None,
    finding_id: str | None,
    action_fingerprint: str | None,
    nonce: str,
) -> bytes:
    """确定性重建被签的挑战字节 (owner 签的、门验的, 必须逐字节一致)。

    所有字段【只从被授权动作的事实取】(task_id/finding_id 从 task 自身、action_fingerprint 从动作
    write_set/command 的 hash)——绝不从 agent 自由文本取, 否则 agent 会签一个更便宜的挑战再改绑。
    canonical JSON (sort_keys + 紧凑分隔 + ascii) 保证签/验两侧序列化一致。
    """
    payload = {
        "v": "owner-confirm@v1",
        "red_line_class": red_line_class,
        "task_id": task_id,
        "finding_id": finding_id,
        "action_fingerprint": action_fingerprint,
        "nonce": nonce,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def verify_detached_signature(
    challenge: bytes, signature_hex: str, *, pinned_pubkey_hex: str | None
) -> bool:
    """用钉死公钥验 Ed25519 detached 签名。任何失败 (无公钥/坏签/坏 hex/无 crypto) → False (fail-closed)。

    pinned_pubkey_hex is None → owner 私钥托管未就位, 一律拒 (安全默认)。
    """
    if not pinned_pubkey_hex or not isinstance(signature_hex, str) or not signature_hex:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pinned_pubkey_hex))
        pub.verify(bytes.fromhex(signature_hex), challenge)
    except InvalidSignature:
        return False
    except (ValueError, ImportError, TypeError):
        # 坏 hex / 坏公钥长度 / crypto 缺失 → fail-closed (绝不 fail-open 放过未验证动作)。
        return False
    return True


def _grant_payloads(index: EventIndex) -> list[dict[str, object]]:
    """账本里所有【原始扁平】OwnerConfirmationGranted 的 payload (拒 stub-rewrap 冒充)。

    OwnerConfirmationGranted 是 commit-category guard observation (同 GuardAdminBypass), payload
    扁平 —— red_line_class / task_id / finding_id / action_fingerprint / nonce / signature 直接在
    payload 顶层, 无 after_state 包裹。
    """
    out: list[dict[str, object]] = []
    for rec in index.lookup_type(OWNER_CONFIRMATION_GRANTED_TYPE):
        # R1 精神: 只认原始扁平事件, event_type.value 必须真等于本字面 (lookup_type 已按此建桶,
        # 二次断言防未来索引语义漂移把 stub-rewrap 归进同桶)。
        if rec.event_type.value != OWNER_CONFIRMATION_GRANTED_TYPE:
            continue
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        out.append(payload)
    return out


def _grant_matches_and_verifies(
    payload: dict[str, object],
    *,
    red_line_class: str,
    task_id: str | None,
    finding_id: str | None,
    action_fingerprint: str | None,
    pinned_pubkey_hex: str | None,
) -> bool:
    if payload.get("red_line_class") != red_line_class:
        return False
    if payload.get("task_id") != task_id:
        return False
    if payload.get("finding_id") != finding_id:
        return False
    if payload.get("action_fingerprint") != action_fingerprint:
        return False
    nonce = payload.get("nonce")
    signature = payload.get("signature")
    if not isinstance(nonce, str) or not nonce:
        return False
    if not isinstance(signature, str) or not signature:
        return False
    challenge = canonical_challenge(
        red_line_class,
        task_id=task_id,
        finding_id=finding_id,
        action_fingerprint=action_fingerprint,
        nonce=nonce,
    )
    return verify_detached_signature(
        challenge, signature, pinned_pubkey_hex=pinned_pubkey_hex
    )


def owner_confirm_present_for_retire(
    index: EventIndex,
    task_id: str,
    finding_id: str,
    *,
    pinned_pubkey_hex: str | None = None,
) -> bool:
    """账本里是否有一条【验签通过、锚定本 (task, finding)】的 retire owner-confirm。

    绑定 red_line_class=retire_task + task_id + finding_id: owner 对 A 的退役签名, 换不到 B 的退役
    (挑战不同 → 验签必失败), 这就是【重放绑定】—— 一个 owner 签名不是万能钥。

    pinned_pubkey_hex=None (默认) → 读模块钉死公钥常量 (call-time, 见 _pinned_pubkey_hex)。
    """
    if pinned_pubkey_hex is None:
        pinned_pubkey_hex = _pinned_pubkey_hex()
    for payload in _grant_payloads(index):
        if _grant_matches_and_verifies(
            payload,
            red_line_class=RED_LINE_RETIRE_TASK,
            task_id=task_id,
            finding_id=finding_id,
            action_fingerprint=None,
            pinned_pubkey_hex=pinned_pubkey_hex,
        ):
            return True
    return False


def owner_confirm_present_for_action(
    index: EventIndex,
    red_line_class: str,
    action_fingerprint: str,
    *,
    pinned_pubkey_hex: str | None = None,
) -> bool:
    """账本里是否有一条【验签通过、锚定本具体动作指纹】的 4 红线 owner-confirm。

    action_fingerprint = 该动作 write_set + command 的稳定 hash: owner 对"删 A 库"的签名换不到
    "删 B 库"(指纹不同 → 挑战不同 → 验签失败)。

    pinned_pubkey_hex=None (默认) → 读模块钉死公钥常量 (call-time, 见 _pinned_pubkey_hex)。
    """
    if pinned_pubkey_hex is None:
        pinned_pubkey_hex = _pinned_pubkey_hex()
    for payload in _grant_payloads(index):
        if _grant_matches_and_verifies(
            payload,
            red_line_class=red_line_class,
            task_id=None,
            finding_id=None,
            action_fingerprint=action_fingerprint,
            pinned_pubkey_hex=pinned_pubkey_hex,
        ):
            return True
    return False
