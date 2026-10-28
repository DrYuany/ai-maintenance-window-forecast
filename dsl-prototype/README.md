# dsl-prototype：受控 DSL 原型（第一阶段：不碰 LLM）

设计文档：[docs/2026-10-29-milp-controlled-dsl-design.md](../docs/2026-10-29-milp-controlled-dsl-design.md)（v0.3）
状态：骨架 + 校验器/生成器/编译器已实现，golden 判分可跑。

## 目录结构

```
dsl-prototype/
  dsl_prototype/
    registry/           四张注册表（第一性三元骨架的数据面）
      entities.py       7 实体 + 引用解析（任务/风机/部件/窗口/作业线/人员/时间）
      quantities.py     可引用量生成器 schema：4 形式 × 7 系数 × 7 过滤 + 具名量
      primitives.py     原语 schema：约束七维度 + linear；目标 3 个（本质 2 个）
      parameters.py     参数注册表：122 项 CSV + 消费点（路由判据的数据面）
    validator.py        确定性校验器（纯 Python）：结构/原语/实体/量/线性/界
    generator.py        可引用量生成器（纯 Python）：形式×系数×过滤 → 符号项
    compiler.py         编译器：符号项 → CP-SAT 约束/目标（物化 + 量纲缩放）
    runner.py           端到端：DSL JSON → 校验 → 注入求解 → 报告
    llm_parser.py       第二阶段占位（NL → DSL JSON）
  eval/
    golden_cases.jsonl  golden 用例（手写 DSL）
    run_golden.py       判分脚本：断言求解结果真的满足约束
  tests/
    test_validator.py   校验器单测（纯 Python）
    test_generator.py   生成器单测（纯 Python）
    test_end_to_end.py  端到端（真求解，毕业证据）
```

## 跑法

```powershell
cd dsl-prototype

# 1. 纯 Python 单测（快）
py -m pytest tests/test_validator.py tests/test_generator.py -q

# 2. 端到端（真求解，每个用例 10~60 秒）
py -m pytest tests/test_end_to_end.py -v

# 3. golden 判分（exit 0 = 全部通过）
py -m eval.run_golden
```

## 设计要点（与主项目的关系）

- **求解内核零复制**：`sys.path` 挂载主项目 `maintenance-milp-modeler/scripts`，
  复用 `load_project / candidates / build_model / solve`。
- **唯一集成点**：`product_planner.build_model/solve` 新增两个可选闭包参数
  （`extra_constraints` / `custom_stages`），默认 None 行为不变，主项目 178 个测试零回归。
- **路由判据**：`registry/parameters.py` 的 `consumption_point()` 查表
  （candidates = 重建候选，build_model = 复用候选）。
- **确定性分层**：校验器/生成器/编译器全部确定性；LLM 解析（第二阶段）产生的 DSL
  必须过校验器，且解析结果落库进证据链——"可复现"承诺从"NL→模型"降级为"DSL→模型"。

## 原型限制（明确报错，不静默给错数）

- max/derived 形式未实现（最后完工时间、总经济成本、现金支出引用会报"原型未实现"）
- co_occur 只实现 same_day/diff_day；同/不同窗口、同/不同线登记在册未实现
- linear 表达式不允许混合时间量与成本量（量纲不同）
- 已下发检修单锁定、部分排程兜底未处理（正式合并走 recommend 路径）

## 毕业标准（满足才合并回主项目）

1. 3 个新原语打通（co_occur diff_day / precedence / earliest）✅ 已完成
2. golden 判分证明约束真的生效（非"能跑通"）——见 eval/
3. 与主项目测试同跑全绿（backend 178 个 + 本包全部）
