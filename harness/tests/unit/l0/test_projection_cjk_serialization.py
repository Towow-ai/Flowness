"""防回归 guard — finding-projection-cjk-ascii-escape-grep-false-negative。

投影 (concept_graph.json 等) 是 canonical events.log 的派生 grep 面。若用 json.dumps 默认
ensure_ascii=True 序列化, 中文概念名会被转义成 \\uXXXX, 任何按中文名 grep 该文件的
agent/工具/owner 都得假阴性 (节点存在却 0 命中)。本测试钉死: ProjectionStore.write_atomic
必须把中文落盘为字面 UTF-8 (ensure_ascii=False), 让派生图可被 grep。回归 (有人改回默认转义)
即此处变红。
"""

from towow.l0.projection.projection import ProjectionStore


def test_projection_serializes_cjk_literal_not_ascii_escaped(tmp_path):
    store = ProjectionStore(tmp_path)
    # name 含 owner 实际会用来 grep 的中文概念名 (菠萝包) — 真实场景复刻
    store.write_atomic(
        "concept_graph",
        {"concepts": [{"concept_id": "bolobao@v1", "name": "菠萝包 (pineapple-bun)"}]},
    )

    raw = (tmp_path / "concept_graph.json").read_text(encoding="utf-8")

    # ① 字面中文可被 grep 直接命中 (修复目标)
    assert "菠萝包" in raw, "投影应存字面中文, 否则按中文名 grep 假阴性"

    # ② 无 CJK \\uXXXX ascii 转义 (回归即此处再现: 默认 ensure_ascii=True 会让本断言失败)
    assert "\\u" not in raw, (
        "投影含 \\uXXXX ascii 转义 = ensure_ascii=True 回归 — grep 中文概念名将假阴性"
    )
