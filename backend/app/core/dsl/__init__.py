"""dsl_prototype：受控 DSL 原型包。

第一阶段目标（不碰 LLM）：手写 DSL JSON → 注册表校验 → 生成器 → CP-SAT 编译 → 真求解，
用 golden 用例证明"新约束真的生效"。LLM 解析（llm_parser）是第二阶段。

依赖主项目求解内核：maintenance-milp-modeler/scripts（product_data/product_planner）。
"""
