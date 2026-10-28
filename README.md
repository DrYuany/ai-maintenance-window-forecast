# AI窗口期预测产品

当前版本的核心形态是独立的“检修排程 Agent”：

- 左侧保存和切换规划会话
- 中间通过自然语言持续调整检修目标与约束；每次求解结果作为一条助手回答进入消息流，历史窗口卡不会被新结果覆盖
- 窗口卡只展示时间段、总成本、完成量、船天和基准差异；任务明细、求解证据与模型指纹可在方案详情中查看
- 右侧按生成顺序保存本会话的全部方案，点击任意记录可回看概览、检修排程和审计证据
- 方案详情新增“数学模型”，展示本次 CP-SAT 模型的变量组、有限上下界、目标层、约束组、参数来源和原始模型文件
- 对话发送时会绑定当前选中的方案版本；模型公式、参数来源、方案解释和求解状态从已保存证据读取
- 参数、约束和目标调整先生成变更预览，确认后才启动独立沙盘求解，不覆盖原方案
- 旧会话缺少消息与方案的关联字段时，前端会按生成时间兼容配对，仍可回看历史窗口
- 后端为每次执行生成规范化决策产物和求解证据
- `UNKNOWN`、`FEASIBLE`、`INFEASIBLE`、`OPTIMAL` 使用独立状态，不把超时描述为无解
- 对话 Agent 先调用只读工具读取方案事实，再组织回答；不再凭历史消息编造数字
- “对比方案 01 和 02”由确定性工具计算三层差异：事实与差值、差异归因、下钻索引；解读由模型完成
- 回答通过 SSE 流式输出，前端用 KaTeX 渲染 LaTeX 公式，支持 Markdown 表格
- 数学模型问题原样输出模型产物生成的公式，不改写系数、变量或约束
- 方案可以下发生成检修单和出海清单，任务状态随之下沉；现场回填完工后任务才真正关闭、退出待安排池

Agent 下一阶段的场景、工具契约、验收用例和优先级见
[`docs/2026-10-27-agent-scenario-requirement-matrix.md`](docs/2026-10-27-agent-scenario-requirement-matrix.md)。
本版工具循环与对比契约见
[`docs/2026-10-27-tool-agent-and-compare-contract.md`](docs/2026-10-27-tool-agent-and-compare-contract.md)。
全项目文件地图（每个文件管什么、从哪看起）见
[`docs/2026-10-28-项目全景与学习路线.md`](docs/2026-10-28-项目全景与学习路线.md)。

## 安装后端依赖

```powershell
python -m pip install -r backend\requirements.txt
```

## 配置 DeepSeek Flash

后端会优先读取项目根目录或 `backend` 目录下的 `.env.local`。当前默认使用 DeepSeek 的 Anthropic 兼容接口。

示例：

```env
MINIMAX_API_KEY=你的 DeepSeek API Key
MINIMAX_MODEL=deepseek-flash
MINIMAX_BASE_URL=https://api.deepseek.com/anthropic
```

如果未配置 `MINIMAX_API_KEY`，系统会退回到本地规则解析模式，不会调用真实大模型。

## 启动后端

```powershell
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

接口文档：

`http://127.0.0.1:8000/docs`

## 启动前端

```powershell
npm run dev
```

默认地址：

`http://127.0.0.1:3000`

## 当前体验重点

- `检修窗口原型.html` 是无外链依赖的单文件，可直接双击查看产品界面
- 本地开发仍使用 `npm run dev`，真实对话与求解需要同时启动 FastAPI 后端
- 模型指纹由完整上下文、决策契约和求解模型分别计算
- 新运行会额外保存 `model_artifact.json` 和原始 `solver_model.pbtxt`；旧版本没有完整产物时会明确提示，而不是事后重建冒充原模型
- 3 分钟是软提醒阈值，不是“无解”阈值；等待、后台运行、使用 incumbent、停止是独立动作

## 确定性评测

本地程序判分是发布闸门，Langfuse 只负责 trace、数据集、实验对比和人工标注。

```powershell
$env:PYTHONPATH='backend'
python -m app.evals.run_eval backend\eval_cases\compile_cases.jsonl
```

当前 evaluator 已覆盖状态诚实性、模型指纹重复性和证据链完整性。持久化异步求解任务、独立解复核器和真实 `maintenance-milp-modeler` adapter 仍属于 MVP 后续必做项，完成前不应把当前版本描述为生产完成。
