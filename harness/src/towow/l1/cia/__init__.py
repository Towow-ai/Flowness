"""R01 §2 — CIA (Change Impact Analysis) 概念图自愈与影响集计算包.

阶段2 起把 SIS (seed impact set) → CIS (candidate impact set) → AIS (actual
impact set) 的影响分析做成可度量的库层能力。本包件:
  - evolutionary_coupling.py (T-R01-2): 从变更历史挖共变 (演化耦合) → 概念级耦合边。
  - candidate_impact_set.py  (T-R01-3): SIS 沿结构闭包 ∪ 共变 → CIS。
  - actual_impact_set.py     (T-R01-4): 从后续真实事件派生 AIS + 精确召回度量。
"""
