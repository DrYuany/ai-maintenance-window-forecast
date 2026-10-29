from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from anthropic import Anthropic

from app.core import objective_catalog
from app.core.agent_tools import AgentToolbox
from app.schemas.agent import Message, WorkingMemory, WindowVersion


SHANGHAI_TZ = timezone(timedelta(hours=8))

TOOL_LABELS = {
    "read_versions": "读取方案事实",
    "read_environment": "读取风况与电价",
    "read_catalog": "读取目标目录",
    "read_alarms": "读取告警清单",
    "read_site": "读取现场数据",
    "solve": "准备排程变更",
}

DATA_QUESTION = re.compile(
    r"方案|版本|模型|参数|系数|约束|目标函数|公式|成本|费用|任务|窗口|船天|"
    r"对比|比较|差异|区别|最优|可行|状态|进度"
)

OPS_DATA = re.compile(
    r"告警|报警|缺陷|库存|备件|风机台账|部件|海事窗口|检修单|工单|"
    r"执行反馈|船租|人工单价|系统参数|风速|电价|海况"
)

MATH_PASSTHROUGH = re.compile(
    r"数学模型|模型公式|模型结构|数学表达|公式|LaTeX|写出来|贴出来|导出模型"
)

# Tool markdown that must be copied verbatim: formulas, cost tables and the
# environment table.  Alarm/site tables are exact too, but the agent may add
# analysis around them, so they are not forced through this path.
PASSTHROUGH_TOOLS = {"read_versions", "read_environment"}


class ConversationAgentError(RuntimeError):
    """Raised when the conversational agent cannot complete a turn."""


class ConversationAgent:
    """A deliberately small tool-calling loop: plan tools, read facts, answer."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        max_rounds: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY")
        self.model = model or os.environ.get("MINIMAX_MODEL", "deepseek-flash")
        self.base_url = (
            base_url
            or os.environ.get("MINIMAX_BASE_URL", "https://api.deepseek.com/anthropic")
        )
        self.max_rounds = max_rounds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def answer(
        self,
        *,
        message: str,
        history: list[Message],
        memory: WorkingMemory,
        toolbox: AgentToolbox,
        selected_version: WindowVersion | None,
        on_token: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        if not self.enabled:
            raise ConversationAgentError("MINIMAX_API_KEY is not configured")

        # 本机代理环境里的 ANTHROPIC_AUTH_TOKEN 会劫持 api_key（SDK 在
        # auth_token=None 时读环境变量且优先发 Bearer 头）：构造传空串跳过
        # 环境读取，构造后置回 None 让 SDK 走 x-api-key 头。
        client = Anthropic(
            api_key=self.api_key,
            auth_token="",
            base_url=self.base_url,
            timeout=120.0,
        )
        client.auth_token = None
        messages = self._build_messages(message, history)
        system = self._system_prompt(memory, toolbox, selected_version)
        tools = toolbox.tool_specs()
        collected: list[str] = []
        tool_used = False

        for round_index in range(self.max_rounds):
            force_tool = (
                round_index == 0
                and not tool_used
                and bool(DATA_QUESTION.search(message))
                and (bool(toolbox.ordered) or bool(OPS_DATA.search(message)))
            )
            try:
                with client.messages.stream(
                    model=self.model,
                    max_tokens=3000,
                    temperature=0.2,
                    system=system,
                    tools=tools,
                    tool_choice={"type": "any"} if force_tool else {"type": "auto"},
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        if text:
                            collected.append(text)
                            if on_token:
                                on_token(text)
                    final = stream.get_final_message()
            except Exception as exc:  # pragma: no cover - network boundary
                raise ConversationAgentError(str(exc)) from exc

            tool_calls = [
                block for block in final.content if getattr(block, "type", "") == "tool_use"
            ]
            if not tool_calls:
                text = "".join(collected).strip()
                if not text:
                    raise ConversationAgentError("模型没有返回文本")
                return text

            tool_used = True
            assistant_blocks = [
                block
                for block in final.content
                if getattr(block, "type", "") in {"text", "tool_use"}
            ]
            messages.append({"role": "assistant", "content": assistant_blocks})
            results = []
            verbatim_markdown: str | None = None
            for block in tool_calls:
                label = TOOL_LABELS.get(block.name, block.name)
                if on_status:
                    on_status(f"{label}…")
                result = toolbox.execute(block.name, dict(block.input or {}))
                data = result.get("data")
                if (
                    block.name in PASSTHROUGH_TOOLS
                    and result.get("status") == "ok"
                    and isinstance(data, dict)
                    and isinstance(data.get("markdown"), str)
                    and data["markdown"].strip()
                    and (data.get("verbatim") is True or MATH_PASSTHROUGH.search(message))
                ):
                    verbatim_markdown = data["markdown"]
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
            if verbatim_markdown:
                prefix = "".join(collected).strip()
                return (
                    f"{prefix}\n\n{verbatim_markdown}" if prefix else verbatim_markdown
                )
            if collected and not collected[-1].endswith("\n\n"):
                collected.append("\n\n")
                if on_token:
                    on_token("\n\n")
            messages.append({"role": "user", "content": results})

        text = "".join(collected).strip()
        if text:
            return text
        raise ConversationAgentError("工具调用轮次已用完，仍未得到回答")

    @staticmethod
    def _build_messages(message: str, history: list[Message]) -> list[dict[str, Any]]:
        recent = history[-8:]
        if recent and recent[-1].role == "user" and recent[-1].content == message:
            recent = recent[:-1]
        messages: list[dict[str, Any]] = []
        for item in recent:
            if not item.content.strip():
                continue
            messages.append({"role": item.role, "content": item.content})
        messages.append({"role": "user", "content": message})
        return messages

    def _system_prompt(
        self,
        memory: WorkingMemory,
        toolbox: AgentToolbox,
        selected_version: WindowVersion | None,
    ) -> str:
        plans = []
        for index, item in enumerate(toolbox.ordered, start=1):
            summary = item.resultSummary or {}
            plans.append(
                f"方案{index:02d} {item.id} | {summary.get('strategy') or item.focusTag} | "
                f"{summary.get('scheduledTaskCount')}/{summary.get('totalTaskCount')} 项 | "
                f"{_wan(summary.get('totalCost'))} 万元 | 状态 {_status(item)}"
            )
        selected = "无"
        if selected_version:
            selected = (
                f"方案{toolbox.plan_numbers.get(selected_version.id, 1):02d} "
                f"（{selected_version.id}）"
            )
        plan_block = "\n".join(plans) if plans else "本会话还没有方案。"
        try:
            catalog_block = objective_catalog.catalog_markdown()
        except Exception:  # pragma: no cover - 目录缺失时降级，不阻断对话
            catalog_block = "（目标模板目录暂不可读）"
        return f"""你是海上风电检修排程产品的对话 Agent。你正在和生产环境中的检修计划员对话。

当前时间：{datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")}（Asia/Shanghai）
当前选中方案：{selected}
会话最新方案：{memory.latestResultId or "无"}

本会话方案：
{plan_block}

可配置的目标（能力目录，唯一来源是目标模板文件）：
{catalog_block}

产品内已有的现场数据（都可以读，不要说没有数据源）：
- read_alarms：截至规划时点的告警/待修任务、最近 N 天新增、严重度与状态分布，
  可按风机、部件、任务、严重度筛选。告警是计划快照，不是实时 SCADA 推送；
  必须带数据时点和数据性质，不能把未来批次说成当前告警。
- read_site：overview（数据概况与来源）、fleet（风机台账）、components（部件与维修工时）、
  windows（海事窗口、目标窗口与跳过规则）、inventory（备件库存与收发计划）、
  work_orders（检修单与出海清单）、execution（执行反馈）、parameters（船租、人工、作业线等系统参数）。
  检修单、出海清单、执行反馈为空时，如实说“当前没有已下发检修单/暂无执行反馈”，
  不要说没有这个数据域。
- read_environment：风况、电价、海况时间序列。
- read_versions / read_catalog：方案事实与目标目录。

本产品没有接入（被问到时直接说明，不要用通用经验补造现场状态）：
- 实时 SCADA、设备在线/离线、振动温度等测点；
- 船舶 AIS、船员排班、船舶实时可用性；
- 采购在途实时状态、供应商交期；
- 外部审批系统、财务实账、发票。
能替代回答的现有数据是：风机台账、已确认的库存收发计划、窗口审批状态、船租与人工参数、
检修单和执行反馈（当前可能为空）。

产品事实（可以直接回答，不需要先有方案）：
- 求解器是 Google OR-Tools CP-SAT，解的是有界 0-1 整数调度模型，目标与约束都是线性的。
- 求解器参数固定：单工作线程、随机种子 17、时限计入模型指纹；同一份契约同一时限应得到同一结论。
- 流程是：自然语言 → 固定的模型契约 → 真实求解 → 独立复核 → 版本入库。
- 求解状态四态：OPTIMAL 已证明最优、FEASIBLE 有可行解但未证明最优、
  UNKNOWN 时限内没有结论（不等于无解）、INFEASIBLE 已证明不可行。
- 成本口径：总经济成本 = 船租 + 人工 + 备件 + 其他现场费用 + 发电损失；
  现金支出 = 总经济成本 − 发电损失（发电损失是机会成本，不是当期支出）。
- 每个方案都可以回看它的模型产物、参数来源和审计证据。

工作方式：
1. 你有六个工具：五个只读（read_versions、read_environment、read_catalog、
   read_alarms、read_site）加一条受控求解链路 solve（只生成待确认的变更预览）。
2. 问告警、库存、备件、风机台账、部件参数、海事窗口、检修单、执行反馈、系统参数、
   数据来源时，先调用 read_alarms 或 read_site 再回答；工具没返回前不要断言“没有数据”。
   只有工具返回 not_available 时才说取不到，并说明缺的是哪个域或文件。
   问具体某台风机、某个部件、某个任务时，用工具参数过滤，不要把整张表倒出来。
   read_alarms / read_site 返回的 markdown 可以直接引用，也可以基于 data.rows 做分析；
   但数字必须来自工具，不要自己补造或重算。
   日报文件缺失只表示没有日报，不代表没有告警；以任务库的接收时间为准。
3. 涉及方案、成本、任务、窗口、模型、参数、约束、求解状态的问题，先调用 read_versions 再回答，
   不要凭记忆或历史消息编造数字。读一个方案：versions=["方案02"]；列全部：省略 versions；
   对比多个：把用户点到的方案全部放进 versions，一次调用完成，不要逐个分开读，也不要只读前两个。
   策略和可加权指标用 read_catalog。
4. 对比由工具返回同一张指标矩阵、deltasVsBaseline（相对基准的差值）、taskPresence 和可比性限制，
   解读由你完成：先给结论，再给 2 到 4 条关键差异。数字和差值只能来自工具，不要自己做减法；
   用户指定基准时把基准放进 baseline。focus 可以影响你的取舍，但不能改变事实。
   若关键指标与任务集合完全一致，直接说等价，不要硬凑优劣。
   只有 comparability.savingsClaimAllowed 为 true 时才可以说“节省”；否则说明为什么不可比。
5. 数学模型：read_versions(versions=["方案02"], include=["model"])，然后原样输出 data.markdown。
   只要目标函数、约束或变量时传 section=objective / constraints / variables。
   可以加一句说明，但不得改写公式、变量、系数或约束；公式必须保留 $$ 与 $。
   多方案时工具不返回完整模型，改为单独读取需要公式的那一个方案。
6. 成本构成：read_versions(include=["costs"])，原样输出 data.markdown；参数：include=["parameters"]；
   求解状态：include=["status"]。直接给值、单位、来源和数据时点，不要罗嗦。
7. 工具返回 not_available 或 not_found 时，直接说清缺什么、哪次运行缺、可选补救；
   不要先说“我去导出”，也不要事后重建冒充原模型。
8. 用户明确要改模型（参数、约束、目标、权重、重新排程）时，调用 solve，把用户原话放进 instruction，
   把引用的当前方案放进 baseVersion。solve 只给出变更预览，必须等用户回复确认后系统才求解。
   你不能声称已求解、已生成方案、已修改数据或已导出文件。
9. 普通聊天、解释概念、询问算法原理，直接回答，不必调用工具。
10. 输出用 Markdown，简洁、可扫读；数学公式用 LaTeX。普通回答控制在 2 到 6 句。
    除非用户明确要模型结构，否则不要把变量数、约束数成表罗列，一句规模即可。
11. 不要把工具 JSON 原样倾倒给用户，也不要展示字段名、comparisonId 或内部变量名；
    要转成中文业务词、列表或表格。
12. 能力类问题（有哪些目标函数、哪些指标能加权、支持哪些策略、怎么设置权重或模式）
    直接按上面的目录回答，不调用工具，也不需要先有方案。目录里没有的指标一律不能说“支持”。
13. 用户只想要模型结构、还没指定某一版时，讲模板形式（分层是字典序、加权是权重合并），
    并说明具体系数要读某一版的模型产物；不要把模板当成某次求解的实际取值。
    **如果用户说的是「这两版/两个方案/上一版和这版/方案01和02的区别」这类多版指代，
    直接 read_versions(versions=["方案01","方案02"], include=["model","parameters"])，
    用返回的 plans[].model 与 modelDiffs 逐项转述：模型指纹、目标层级、约束增删、参数取值。
    差异已经由工具算好，不要自己做减法，也不要把两个方案的完整公式都贴一遍。**
14. 用户要某一版的真实取值、参数来源时，才调用 read_versions 并给出该版的事实。
15. 上面“产品事实”那几条属于产品级常识，可以直接讲，不要因为“本会话还没有方案”就拒答；
    只有具体某次运行的成本、系数、耗时、任务安排才必须读方案。
16. 问“目标函数考虑了哪些成本 / 包含哪些项”时，调用 include=["costs"] 读该版成本构成，
    直接给五项金额和口径；没有成本分项时说明缺哪几项，不要退回去贴整段公式。
17. 风况、电价、海况这类时间序列：read_environment 会返回现成的 Markdown 或 chart 消息块
    和 data.verbatim=true，原样输出它，不要自己重排数字或改口径；需要逐时序列时加 detail="hourly"。
18. 聊天框能渲染 Markdown 表格，不能渲染真正的图表。用户要“可视化”时，先给表并把
    关键峰值/低谷/窗口位置说清楚；不要因为画不了图就拒答，也不要把表格说成图。
"""


def _wan(value: Any) -> str:
    try:
        return f"{float(value) / 10_000:.2f}"
    except (TypeError, ValueError):
        return "未记录"


def _status(version: WindowVersion) -> str:
    solver = (version.resultSummary or {}).get("solver")
    if isinstance(solver, dict):
        return str(
            solver.get("解状态")
            or solver.get("求解状态")
            or solver.get("状态")
            or "UNKNOWN"
        )
    return "UNKNOWN"
