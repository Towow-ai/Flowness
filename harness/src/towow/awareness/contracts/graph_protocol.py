"""图协议接口契约 (贯穿层) —— 统一节点/边模型 + 跨域导航。

设计依据: rc-graph-protocol-unified-navigation / rc-graph-protocol-data-model-property-graph-event-reification@v1
/ 50-graph-protocol.md。

## 本质 (它必须做什么)
把现在 7 张各搞各的图 (concept/task/information_need/impact/at_reference/consumer/ownership)
+ 三类新节点 (session/lock/escalation) 统一成**一张可跨类型导航的图**, 让 investigate 能一句话查
"这个 session 占哪些锁、派生哪些子、提了哪个 escalation、碰了哪些 concept"。

## 关键设计判断 (可证伪)
- **数据模型 = 开放类型属性图 (labeled property graph), 不是 RDF**。对因为: 节点/边都要带 payload +
  provenance, RDF triple 要 reification 才能给边加属性 = 笨重。塌于 Y: 若未来某关系既非 n 元事实、
  非分层、非嵌套、非时间 (举不出例子), 此模型装不下。
- **"立体" = 事件作一等事实节点 (n 元关系 reification) + layer + 时间, 不是字面 3D 拓扑**。canonical
  事件天生就是这个事实层 (一个事件碰多概念 = 一条超边)。
- **承重边白名单 (slice_bearing)**: 通用导航不能冲掉概念图 CIA 承重语义。reference/dependency 是
  forward_slice 承重边, 必须标 slice_bearing=True, 改它要意识到在喂 CIA 传播。
- **迁移走 facade (路 B)**: 7 张图 reducer 零改, facade 只读映射成统一 node/edge; concept_graph
  永不重构存储 (它是生产真在拦的承重墙)。impact_graph(projection.py:1418-1501) 已是 facade 活样本。
- **完整性**: 边的 to 必须 ∈ 图内节点 ∪ 合法 external_anchor (指 repo 内 spec 路径/url 的合法外部引用),
  否则导航撞空 = 完整性破 (现有 20 条悬空边)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    """统一节点。type 开放可扩 (concept/task/session/lock/escalation/decision/finding/...)。"""

    id: str
    type: str  # 开放字符串, 不锁死枚举 —— 未来新图作新 type 插入不改核心
    payload: dict[str, Any] = field(default_factory=dict)  # domain-specific, 不压平各域语义
    provenance: str | None = None  # 物化它的 canonical event_id
    layer: str | None = None  # multiplex 层标签 (concept 层 / task 层 / session 层 ...)
    is_active: bool = True  # 时间维: 经 supersede/失活后为 False, 可重建任意历史时刻


@dataclass(frozen=True)
class ExternalAnchor:
    """悬空边的合法第二形态: 指向图外的 spec 文件/url, 显式标记、导航终点而非撞空。"""

    ref: str  # repo 内 spec 路径 或 合法 url
    validated: bool = False


@dataclass(frozen=True)
class Edge:
    """统一边。edge_type 跨域可扩 (reference/dependency/contains/supersedes/spawned/ordered/
    sibling/held-by/raised-by/answered-by/blocks/...)。"""

    from_id: str
    to: str | ExternalAnchor  # 图内 node.id 或合法外部锚点
    edge_type: str
    direction: str = "unidirectional"  # unidirectional | bidirectional
    is_active: bool = True
    slice_bearing: bool = False  # 是否 CIA 影响传播承重边 (reference/dependency=True)
    provenance: str | None = None


@dataclass(frozen=True)
class NavStep:
    """一跳导航: 沿某 edge_type 走, 可限方向 (出边/入边)。"""

    edge_type: str
    inbound: bool = False  # False=沿出边 (from→to), True=沿入边 (to→from)


class GraphProtocol(ABC):
    """统一图协议接口。investigate (层①) + 看板 (图的视图) + CIA 传播 都吃它。

    实现侧 (当前): facade 适配 7 张现有图 (只读映射) + 三类新节点原生统一存储; 事件溯源一致
    (所有图变更经 canonical 事件 + commit gate 物化)。
    """

    @abstractmethod
    def get_node(self, node_id: str) -> Node | None:
        """取一个节点 (跨任意域)。不存在返回 None。"""

    @abstractmethod
    def edges_of(self, node_id: str, *, inbound: bool = False) -> list[Edge]:
        """取一个节点的出边 (默认) 或入边。"""

    @abstractmethod
    def navigate(self, start_id: str, path: list[NavStep]) -> list[Node]:
        """跨类型导航: 从 start 沿 path 逐跳走, 返回终点节点集。

        这是图协议的全部价值所在 —— 一次查询跨域。例: 从 session 走
        [held-by(出)] 得它占的锁; [spawned(出)] 得子会话; [touched(出)] 得碰的概念。
        塌于 Y: 若某承重边没投影到统一模型 (slice_bearing 漏标), navigate 走不到/走错向。
        """

    @abstractmethod
    def integrity_violations(self) -> list[Edge]:
        """返回所有完整性破的边 (to 既不是图内节点也不是合法 external_anchor)。

        commit gate 用它在加边时校验: 新边 to 必须 ∈ 节点 ∪ 合法 external_anchor。
        """
