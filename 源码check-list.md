# 源码检查清单（Source Check List）

> 用途：对源码做系统性走查，逐条记录发现的问题。每条问题带状态、严重度、证据定位（file:line）与**问题来源**，最终**依据本清单做整体修改**。
>
> 维护规则：
> - 新问题按序号追加，不做匿名问题——每条必须落到具体文件与行号，写清「现象 / 根因 / 建议方向」。
> - **必须记录问题来源**：哪个文档、哪块源码、以什么方式引出了这个问题（例如「读系统提示词 18 条时对照 agent_tools.py 的 description 发现重复」）。问题是按阅读顺序浮现的，来源要还原这条线索，方便日后复盘「为什么想到这」。
> - 每次修改后更新「版本历史」，并把对应问题状态改为「已修复 / 已验证」。
> - 状态流转：`待修改 → 修改中 → 已修复 → 已验证`。整体修改阶段按本清单逐条推进，改完一条勾一条。

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.4 | 2026-11-01 | #3 结构分离落地：抽 `dsl_core/` 中立共享包，两产品解耦，两侧测试全绿 |
| v0.3 | 2026-11-01 | 记录问题 #3（llm-first 与 rule-first 两套理解层并存，产线未切换） |
| v0.2 | 2026-11-01 | 记录问题 #2（工具调用规则没有单一事实源）；为问题清单新增「问题来源」字段 |
| v0.1 | 2026-11-01 | 建立清单，记录问题 #1（关键词路由是补丁不是设计） |

## 问题列表

### #1 关键词路由是补丁，不是设计

- **状态**：待修改
- **严重度**：中（不碰正确性底线，但存在系统性漏判 / 误判，且词表会持续腐烂）
- **位置**：
  - `backend/app/core/conversation_agent.py:33-45` — `DATA_QUESTION` / `OPS_DATA` / `MATH_PASSTHROUGH` 三个关键词正则
  - `backend/app/core/conversation_agent.py:124` — `force_tool` 用 `DATA_QUESTION.search(message)` 做第 0 轮强制工具调用
  - `backend/app/core/conversation_agent.py:176` — `MATH_PASSTHROUGH.search(message)` 决定是否原样透传公式/参数表
- **问题来源**：在 `conversation_agent.py` 里选中第 33-45 行的三个关键词正则，追问「这三行是干吗的」，顺着 `force_tool` 的用法往下挖，发现关键词匹配承担了本该由工具决策完成的「何时调工具」判断。

- **现象**：用一行关键词 `search()` 判断「这句话是不是在问数据」，在第 0 轮把 `tool_choice` 从 `auto` 顶成 `any`，逼模型先去读工具而不是凭记忆编数字。

- **根因**：`tool_choice="auto"` 允许模型「选择不调工具」。叠加三个因素——system prompt 里塞了大量「产品事实」（`conversation_agent.py:320-330`）、模型是 deepseek-flash + temperature 0.2 偏懒、`max_rounds=4` 上限让第 0 轮被浪费代价高——使数据类问题特别容易在第 0 轮被模型凭记忆/常识直接回答，产出无来源的数字。

- **三个固有缺陷**：
  1. **会漏**：不命中词表的问法（如「这台风机要修吗」「还剩几个窗口」）不触发强制，该漏还是漏。
  2. **会误伤**：命中词表但本应直接答概念的问题（如「什么是目标函数」，规则 9/12 明确该直接回答）被强行拉去调工具，多此一举。
  3. **会腐烂**：词表随业务词汇扩张而漂移，永远追不上真实问法分布。

- **建议方向**（整体修改阶段二选一或组合）：
  - **A. 反向校验**（推荐）：信任 `tool_choice="auto"` 让模型自决，把防线从「猜用户意图」挪到「查模型输出」——数据类回答里出现的数字，校验是否来自某次工具调用结果；不是则判为「未经工具的事实」拒掉或强制重跑。
  - **B. 显式意图分类**：把关键词正则替换为一次轻量意图分类（LLM 或分类器），输出「问数据 / 问概念 / 要公式原文」三态，替代现在的词表启发式。

- **决策备注**：当前词表是「缓解」不是「解决」。保留它有价值（挡住 flash 第 0 轮偷懒编数字这个已被观测的故障），但要认清它注定有漏网之鱼，不应作为长期方案。

### #2 工具调用规则没有单一事实源（系统提示词与工具 description 重复）

- **状态**：待修改
- **严重度**：中（不影响正确性，但规则漂移、重复维护、系统提示词臃肿挤占注意力）
- **位置**：
  - `backend/app/core/conversation_agent.py:332-384` — 系统提示词里的 18 条「工作方式」规则，其中 2/3/5/6/7 条是在复述「何时调用哪个工具」
  - `backend/app/core/agent_tools.py:191-535` — `tool_specs()` 里每个工具已经写了详尽的 `description`（read_jobs 写了「用户问『算完了吗』时用它」、submit_change 写了「必须先调过 solve」等）
- **问题来源**：读系统提示词的 18 条「工作方式」时发现绝大多数是「什么时候调用什么工具」，对照 `agent_tools.py` 的 `tool_specs()`，发现 description 里写得比系统提示词更细，遂追问「这不重复吗、Claude Code 怎么教的」。

- **现象**：同一份「何时调工具」的知识，同时写在两处——工具的 `description`（模型每轮都能看到、和工具强绑定）和系统提示词的规则条目里。两处会漂移，改了一处漏一处。

- **根因**：误把「何时调工具」当成系统级策略，实际它是工具级契约，Anthropic 的共识是放在 `description` / 参数 `description`。系统提示词真正该装的是三类 description 装不下的东西：**跨工具顺序/状态机**（solve→submit_change、dispatch→approve→feedback）、**产品级事实**（求解器是 CP-SAT、成本口径、四态）、**输出规范**（不倾倒 JSON、数字必须来自工具、公式原样保留）。

- **分类（18 条规则切分）**：
  - **纯复述 description，可删**：2（read_alarms/read_site）、3（read_versions）、5（模型 include=model）、6（成本/参数/状态 include）、7（not_available/not_found 处理）。
  - **跨工具状态机，必须留**：8（solve/submit_change 确认流程）。
  - **一半重复一半规范，拆**：4（「差异已算好」是 description 已有，删；「先结论后差异」是解读规范，留）。
  - **概览/输出规范/产品事实，留**：1（压成 3 行）、9、10、11、12、13、14-18。

- **建议方向**：
  - 把 2/3/5/6/7 从系统提示词删掉，让 description 成为「何时调工具」的唯一权威来源。
  - 只保留：六工具地图（3 行）、跨工具状态机（solve→submit_change、ledger 链路）、产品事实、输出规范。
  - 小模型（deepseek-flash）会欠读 description，「数字必须来自工具、先读再答」这类**底线**规则可保留一句重述作为强调——这是「强调」不是「重复」，但不应扩展到整段规则。

- **决策备注**：这个问题与 #1 同根——**「何时调工具」的逻辑没有单一权威来源**，一部分靠关键词正则、一部分靠系统提示词、一部分靠 description，三处各说各话。整体修改阶段应把这条统一收口：工具决策归模型 + description，跨工具顺序归系统提示词，兜底校验归后端。

### #3 llm-first 与 rule-first 两套理解层并存，产线仍走 rule-first，未切换

- **状态**：部分完成（执行侧已抽成中立共享包 `dsl_core/`，两产品解耦并验证；「产线切到 llm-first」未做）
- **严重度**：高（方向性问题——「LLM 优先」这个目标还没落地；且「两个独立产品」的架构愿望尚未达成）
- **位置**：
  - `backend/app/core/dsl/llm_parser.py:855-931` — 产线解析链顺序：批量正则 → `parse_by_rules` → not_change 规则 → legacy 桥接 → 非变更分类 → 向量检索 → **LLM（兜底，不是第一道）**
  - `llm-first/`（整目录）— LLM 主导版原型，独立目录；README 明说「新产品，不是升级」「并排对比」
  - `llm-first/src/paths.py` — 把 `../backend` 挂进 sys.path，复用 `app.core.dsl.*`（这是「混」的根源）
  - `backend/eval_cases/probe_llm_first.py` — 验证 llm-first 八路路由契约的探针，不接入产线
- **问题来源**：看 `backend/eval_cases/_probe_llm_first_result.json` 里 route/confidence 字段、追问字段含义 → 引出「probe 的八路词表（modify_plan/compare/…）与产线 llm_parser 的六路词表（dsl/batch/not_change/…）并存」→ 进一步确认 llm-first 是否接入产线 → 发现 llm-first 是独立原型、产线仍 rule-first。

- **现象**：用户目标「现在希望 llm 优先」，但产线仍跑 rule-first；llm-first 作为独立原型并排对比、未切换。两套路由词表并存且未对齐。

- **根因**：这是**刻意的「并排对比」阶段**（README §1-2 有意不合并、不设规则兜底、共用执行侧），所以 llm-first 依赖 backend（sys.path 复用），backend 对 llm-first 零引用。但这与用户「两个独立产品、不要混在一起」的最终愿望冲突。

- **依赖面（llm-first 伸手进 backend import 的模块）**：
  1. **执行侧/共享内核**（「其他都一样」该共享的部分）：`app.core.dsl.registry`（parameters/primitives/quantities）、`validator`、`compiler`、`runner`、`planning_bridge`
  2. **规则侧理解层**（仅 `rulebench.py` 对照用）：`llm_parser`、`planning_intent`、`model_change`

- **建议方向**（三选一，待用户定）：
  - **A. 复制进 llm-first**：registry/validator/compiler/runner 复制一份 → 真正自包含、独立版本；代价是执行侧两份会漂移，违背「其他都一样」。
  - **B. 抽出中立共享包**（倾向）：把执行侧抽成一个第三方目录（顶层 `dsl-core/` 或并入 `maintenance-milp-modeler/`），backend 与 llm-first 都 import 它、互不 import → 单一事实源 + 两个独立产品；代价是动 backend 自己的 import。
  - **C. 维持 sys.path 复用**（现状）：适合「并排对比」阶段，但不是「独立产品」。

- **决策备注**：这是 #2 的延续且更严重——不止「规则重复」，而是「两个产品 / 两套理解层 / 两套路由词表并存，且共享执行侧被 backend 独占」。切换前必须先定：执行侧怎么共享（复制 / 中立共享包 / 维持）。

- **已做（2026-11-01，方案 B）**：抽出 `dsl_core/` 中立共享包（registry、validator、compiler、generator、runner、planning_bridge、objective_catalog、part_catalog、planning_request）；`llm_parser.py` 留 backend（规则侧理解层）；`PlanningRequest`/`json_key`/`part_catalog` 抽进 dsl_core；两产品 import 全部改指 dsl_core，互不 import。验证：backend pytest 373 过、llm-first pytest 168 过。
- **未做**：产线从 rule-first 切到 llm-first（这是另一场迁移；rulebench 的规则侧对照 import 仍在 llm-first 内，见 README「遗留」）。
