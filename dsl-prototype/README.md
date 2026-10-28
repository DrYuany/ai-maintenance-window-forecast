# dsl-prototype：受控 DSL 原型

设计文档：[docs/2026-10-29-milp-controlled-dsl-design.md](../docs/2026-10-29-milp-controlled-dsl-design.md)（v0.3）
状态：**第一、二阶段完成**——校验/生成/编译全链 + 三层解析器（规则→向量→LLM），golden 判分可跑。

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
    runner.py           端到端：DSL JSON → 校验 → 注入求解 → 报告（支持部分排程）
    llm_parser.py       三层解析器：规则(正则) → 向量(模板检索) → LLM(生成+校验)
  eval/
    golden_cases.jsonl  golden 用例（手写 DSL）
    validated_library.jsonl  已验证 DSL 模板库（向量层检索源 + LLM few-shot）
    run_golden.py       判分脚本：断言求解结果真的满足约束
  tests/
    test_validator.py   校验器单测（纯 Python）
    test_generator.py   生成器单测（纯 Python）
    test_llm_parser.py  解析器单测：规则层/向量层/LLM 层(mock)
    test_end_to_end.py        端到端基础（真求解，3 原语）
    test_end_to_end_extended.py  端到端扩展（8 原语 + 自定义目标 + 全链路 NL→求解）
```

## 跑法

```powershell
cd dsl-prototype

# 1. 纯 Python 单测（快）
py -m pytest tests/test_validator.py tests/test_generator.py tests/test_llm_parser.py -q

# 2. 端到端（真求解，每用例约 1 分钟）
py -m pytest tests/test_end_to_end.py tests/test_end_to_end_extended.py -v

# 3. golden 判分（exit 0 = 全部通过）
py -m eval.run_golden
```

## 三层解析器（规则 → 向量 → LLM，不是反的）

```
规则层（正则直出）      高频确定性话术，slot 抽取直出 DSL，零成本零延迟
向量层（模板检索）      本地字符 n-gram 检索"已验证 DSL 模板库"，
                         作为 few-shot 喂给 LLM——降误判、保一致
LLM 层（生成 + 校验）   新话术兜底，产物必须过确定性校验器
```

- 向量层不做"分类"（我们是开集结构化生成，不是闭集意图识别），做"检索"。
- 确定性边界：解析层可能非确定（LLM），"可复现"承诺从"NL→模型"降级为"DSL→模型"。
- LLM 未配置（无 MINIMAX_API_KEY）时抛 `ParseUnavailable`，调用方回退澄清提问。

## 设计要点（与主项目的关系）

- **求解内核零复制**：`sys.path` 挂载主项目 `maintenance-milp-modeler/scripts`。
- **唯一集成点**：`product_planner.build_model/solve` 新增两个可选闭包参数
  （`extra_constraints` / `custom_stages`），默认 None 行为不变，主项目 178 个测试零回归。
- **路由判据**：`registry/parameters.py` 的 `consumption_point()` 查表。
- **确定性分层**：校验器/生成器/编译器全部确定性。

## 原型限制（明确报错，不静默给错数）

- max/derived 形式未实现（最后完工时间、总经济成本、现金支出引用会报"原型未实现"）
- co_occur 只实现 same_day/diff_day；同/不同窗口、同/不同线登记在册未实现
- linear 表达式不允许混合时间量与成本量（量纲不同）
- 已下发检修单锁定、部分排程兜底（完整→部分自动降级）未处理（正式合并走 recommend 路径）

## 毕业标准进度

1. ✅ 3 个原语打通（co_occur diff_day / precedence / earliest）——实际已打通 9 个
   （另加 deadline/fix/forbidden_dates/budget/exclude/must/cardinality + lexmin 自定义目标）
2. ✅ golden 判分证明约束真的生效（断言求解结果满足约束，非"能跑通"）
3. ✅ 与主项目测试同跑全绿（backend 178 个 + 本包全部）
