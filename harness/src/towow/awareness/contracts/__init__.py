"""K0 · 六份接口契约 (接口先行).

owner 2026-06-20 令: 先定接口 (本质 = 它必须做什么), 再做实现 (当前 Claude Code 绑定);
CC 更新或迁移别的工具时, 知道本质和接口、按新情况换实现。每份契约的 docstring 把设计里的关键
判断 (可证伪的 "对因为 X / 塌于 Y") 焊进来, 让实现者照着建不掉进没导航的岔口。

- graph_protocol : 统一节点/边模型 + 跨域导航 (贯穿层, 是其余的真相图地基)
- per_form_adapter: 任何 agent 形态的统一适配器 {detect_state, revive, harvest, lineage} (层①)
- investigate    : 只读、不唤醒、带 recommended_action 的统一调查工具 (层①, 吃图协议)
- heartbeat      : 判活 = 实时存活证据为主 (层①)
- usage_governor : Usage 感知节流 (层⑤)
- physical_gate  : 不可逆动作的物理 fail-closed 闸位 (层④)
"""
