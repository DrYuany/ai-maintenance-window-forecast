"""LLM 主导版的系统提示词（岗位手册体）。

与规则主导版 `backend/.../registry/schema_prompt.py` 的三处结构差异：

  1. 顺序
     旧：先给表，最后才说格式。模型拿到一堆零件，不知道能拼出什么。
     新：先讲"你手上两类工具分别能干嘛"，再给表。表降为附录。
     顺序变了，因为**先给零件再给用途，模型只会照抄零件**。

  2. 原语的定位（本次改造的核心，旧版完全没有）
     旧：11 个原语并列成清单，每条只写"必填哪几个字段"。
         读完只知道要素，不知道这堆东西合起来意味着"能用它们拼出来的都能改"。
     新：明确"表 A 是旋钮、表 C 是积木"，用 9 个例子证明"表 A 没有的行也能拼"，
         再给出边界（量目录里没有的、要"全部"的、非线性的），让模型知道该拒什么。

  3. 产物
     旧：一份 dsl。
     新：claims（切句）+ fills（认领，带原文片段）+ dsl（结果），三者互相背书。
     机器按 A1~A9 逐条核，核不过整份退回。

表 A~F 全部由注册表生成（param_reg / prim_reg / qty_reg / validator），不手抄。
`self_check()` 断言覆盖完整——防的正是历史上那次"51 项宣称 / 16 项生效"。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python src/prompt.py` 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402,F401  （副作用：把 ../backend 挂进 sys.path）

import context as session  # noqa: E402  （会话基准日期；与 verify 共用同一份换算）
import segment as seg  # noqa: E402  （连接词表与切段逻辑同源）

from app.core.dsl import validator as dsl_validator  # noqa: E402
from app.core.dsl.registry import parameters as param_reg  # noqa: E402
from app.core.dsl.registry import primitives as prim_reg  # noqa: E402
from app.core.dsl.registry import quantities as qty_reg  # noqa: E402

# ── 表 A 的分组与顺序 ──────────────────────────────────────────────────────
# 按业务语义分组，不按 CSV 行序：模型找"海况"时四个连着，比散在 15 行里好找。
# 分组是手写的，所以 self_check() 会断言它覆盖了所有有落点的参数——
# 漏一项就报错，不许静默漂移。
_TABLE_A_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("班次与粒度", ("每日有效维修上限", "每天开始维修", "每天结束维修", "候选开工粒度")),
    ("人手与船", ("最大作业线", "可调动人员总数", "风机间转移", "允许夜间转移", "允许跨天暂停")),
    ("海况阈值", ("常态风速上限", "常态浪高上限", "安全风速上限", "安全浪高上限")),
    ("成本单价", ("船舶日租金", "维修人工单价")),
)

# CSV 的"取值约束"是给人看的散文（"当地时刻""是或否"），对模型不够具体。
# 只覆盖这几项；其余直接用 CSV 原文，不重写。
#
# 跨字段关系里**不要写行号**（写成 {安全风速上限} 这样的名字，生成时替换）。
# 原因：这里第一版手写的是 "≤ P11"，实测错位了——海况组从 P10 开始，
# 写 P11 指的是"常态浪高上限"，不是"安全风速上限"。手抄行号必错，交给机器解析。
_DOMAIN_OVERRIDE: dict[str, str] = {
    "每天开始维修": "HH:MM",
    "每天结束维修": "HH:MM",
    "允许夜间转移": '"是" 或 "否"',
    "允许跨天暂停": '"是" 或 "否"',
    "候选开工粒度": "正整数（分钟）",
    "常态风速上限": "≥0 且 ≤ {安全风速上限}",
    "常态浪高上限": "≥0 且 ≤ {安全浪高上限}",
    "安全风速上限": "≥0 且 ≥ {常态风速上限}",
    "安全浪高上限": "≥0 且 ≥ {常态浪高上限}",
}

# 表 C 的展示顺序与业务注解（只列表 C 允许出现的原语，self_check 会核）
_TABLE_C_ORDER: tuple[tuple[str, str], ...] = (
    ("must", "点名必须修。"),
    ("exclude", "点名不许修。"),
    ("precedence", '"先修A再修B"，gap 是天数间隔。'),
    ("co_occur", 'mode 只有 same_day / diff_day 两种。'),
    ("fix", "钉死在某条船/作业线，line 填 1~3。"),
    ("cardinality", "至少/至多 N 个。没有「全部」，见边界段。"),
    ("deadline", '"任务018不能晚于某日"用这条；也可按 severity 批量设。'),
    ("earliest", '"任务018不能早于某日"。'),
    ("forbidden_dates", '"这几天别开工"。'),
    ("budget", '口径写 quantity："现金支出" 或 "总经济成本"。'),
    ("linear", "加权和 ≤/≥/== 常数。天花板，也是能力的边界；terms 写法见下一节。"),
)

# 表 E：kind 集合与必填字段都由 validator 决定，这里只提供排序与"可选字段"说明。
# 第一版把必填写成手抄的 "part, at, quantity"——validator 实际只强制 part+at，
# 提示词比校验器严，模型多填一个不存在的必填项，反而被自己的 A7 判错。
_TABLE_E_ORDER: tuple[tuple[str, str, str], ...] = (
    ("part_hours", "—", '"齿轮箱工时按1.2倍算"'),
    ("part_arrival", "quantity（到几套）、blockBefore", '"叶片11月5日到2套，到货前不能开工"'),
    ("task_deadline", "task 或 severity（都不给=改全部任务）", '"高风险任务截止提前到11月8日"'),
    ("weather", "wind / wave / hours / state 至少给一个", '"明天风15米每秒" / "明天不可作业"'),
    ("hourly", "price / wind / power 至少给一个", "电价、功率只能按小时改，没有全局倍率"),
    ("window", "start / end 至少给一个", '"窗口02推迟到11月1日开始"'),
)

# 表 B 的 4 行（X01~X04）。写成常量而不是整段字符串，
# 是为了让 audit_block 里的行号范围能算出来，不用手抄 "X01~X04"。
_TABLE_B_ROWS: tuple[str, ...] = (
    '| X01 | 标准目标策略 | objectives | `{"type":"strategy","name":"<四选一>"}` |',
    '| X02 | 费用预算上限 | constraints | `{"type":"budget","quantity":"现金支出","op":"<=","value":<元>}` |',
    '| X03 | 共同最低完成任务数 | constraints | `{"type":"cardinality","filter":{},"op":">=","value":<N>}` |',
    '| X04 | 强制维修任务 | constraints | `{"type":"must","task":"任务018"}`，多个任务写多条 |',
)

# 五个目标原语全部可执行。
#
# `weighted` 曾经被从这里拿掉：`stages_builder` 不认它，于是它一路绿灯走到求解
# 再抛错。后来查清那不是"编译器没实现"——`strategy` 和 `weighted` 是**求解器模式**
# （落到 mode / weights / strategy 三个字段），压根不该进分层阶段；
# `planning_bridge` 一直这么处理，`runner.run` 漏了这一步，导致同一份 DSL
# 走变更流程能跑、走 runner 抛错。补齐 runner 之后五个都能跑（端到端出过解）。
#
# 教训记在这儿：当时的归因（"编译器没实现"）是错的，但症状是真的。归因错了，
# 修法就会修错地方——我们围着编译器转了半天，真正缺的是 runner 里的四行分派。
# 顺序有讲究：`weighted` 排在最后，不要插在中间。
# 表 D 的行号 O01~O05 是**按这个顺序算出来的**，模型在 fills[].row 里引用的就是它。
# 往中间插一个，后面所有行号都会挪——`lexmin` 从 O04 变 O05，那些写死 O04 的
# 测试和已缓存的产物会集体错位。加新原语请一律追加到末尾。
_TABLE_D_ORDER: tuple[str, ...] = ("strategy", "minimize", "maximize", "lexmin", "weighted")

_TABLE_F_NOTE: dict[str, str] = {
    "完成任务数": "选中的任务个数",
    "高严重完成数": "只看严重等级「高」的",
    "中严重完成数": "只看严重等级「中」的",
    "低严重完成数": "只看严重等级「低」的",
    "完工时间和": "各任务完工时刻相加；**不是**整体收工时间",
    "高严重完工时间和": "只看严重等级「高」的完工时刻之和",
    "中严重完工时间和": "只看严重等级「中」的完工时刻之和",
    "低严重完工时间和": "只看严重等级「低」的完工时刻之和",
    "最后完工时间": "最晚那个任务的完工时刻；说「整体最早收工」用这个",
    "总经济成本": "含发电损失的综合成本",
    "现金支出": "不含发电损失的掏钱部分（预算默认口径）",
}


def _short(text: str, limit: int = 22) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table_a_rows() -> list[tuple[str, str, str, str]]:
    """→ [(行号, 名称, 单位, 取值域)]，行号 P01.. 与分组顺序一致。"""
    numbered: list[tuple[str, str]] = []
    for _group, names in _TABLE_A_GROUPS:
        for name in names:
            numbered.append((f"P{len(numbered) + 1:02d}", name))
    row_of = {name: row_id for row_id, name in numbered}
    rows: list[tuple[str, str, str, str]] = []
    for row_id, name in numbered:
        meta = param_reg.lookup(name) or {}
        unit = meta.get("单位") or "-"
        if unit == "无":  # CSV 里开关/时刻类写"无"，对模型是噪音，统一成占位符
            unit = "-"
        domain = _DOMAIN_OVERRIDE.get(name) or _short(meta.get("取值约束", "")) or "-"
        # 跨字段引用按名字解析成行号，避免手抄错位
        for other, other_id in row_of.items():
            domain = domain.replace("{" + other + "}", other_id)
        rows.append((row_id, name, unit, domain))
    return rows


def table_a() -> str:
    rows = _table_a_rows()
    lines = ["| 行号 | 栏目 | 单位 | 取值域 |", "|---|---|---|---|"]
    lines += [f"| {r} | {n} | {u} | {d} |" for r, n, u, d in rows]
    return "\n".join(lines)


def table_b() -> str:
    return "\n".join(["| 行号 | 栏目 | 写到哪 | 形状 |", "|---|---|---|---|", *_TABLE_B_ROWS])


def table_c() -> str:
    lines = ["| 行号 | 原语 | 必填 | 说明 |", "|---|---|---|---|"]
    for i, (name, note) in enumerate(_TABLE_C_ORDER, start=1):
        required = "、".join(prim_reg.CONSTRAINT_PRIMITIVES[name].get("required", []))
        lines.append(f"| C{i:02d} | {name} | {required} | {note} |")
    return "\n".join(lines)


def table_d() -> str:
    lines = ["| 行号 | 原语 | 必填 |", "|---|---|---|"]
    for i, name in enumerate(_TABLE_D_ORDER, start=1):
        required = "、".join(prim_reg.OBJECTIVE_PRIMITIVES[name].get("required", []))
        lines.append(f"| O{i:02d} | {name} | {required} |")
    return "\n".join(lines)


def _row_ranges() -> str:
    """验收单里的行号范围，全部算出来，不手抄。"""
    return (
        f"P01~P{len(_table_a_rows()):02d}"
        f" / X01~X{len(_TABLE_B_ROWS):02d}"
        f" / C01~C{len(_TABLE_C_ORDER):02d}"
        f" / O01~O{len(_TABLE_D_ORDER):02d}"
        f" / D01~D{len(_TABLE_E_ORDER):02d}"
    )


def table_e() -> str:
    """必填列从 validator 取，可选列手写——必填抄错会直接冤枉模型。"""
    lines = ["| 行号 | kind | 必填 | 可选 | 用户会怎么说 |", "|---|---|---|---|---|"]
    for i, (kind, optional, said) in enumerate(_TABLE_E_ORDER, start=1):
        required = "、".join(dsl_validator._DATA_PATCH_REQUIRED.get(kind, ()))
        lines.append(f"| D{i:02d} | `{kind}` | {required} | {optional} | {said} |")
    return "\n".join(lines)


def table_f() -> str:
    lines = ["| 行号 | 量 | 是什么 |", "|---|---|---|"]
    for i, name in enumerate(qty_reg.NAMED_QUANTITIES, start=1):
        lines.append(f"| F{i:02d} | {name} | {_TABLE_F_NOTE.get(name, '')} |")
    return "\n".join(lines)


def capability_block() -> str:
    """本次改造的核心：把"原语清单"讲成"能力说明"。

    旧版只有一张表，每条写"必填哪些字段"。模型读完只知道要素，
    不知道这堆东西合起来意味着"能用它们拼出来的都能改"。
    这一段先用 9 个例子把"表 A 没有也能拼"这件事立起来，再给边界。

    两个数字从注册表算，不手抄——否则改注册表时这里会静默过期。
    """
    knobs = sum(len(names) for _group, names in _TABLE_A_GROUPS)
    bricks = len(_TABLE_C_ORDER)
    return f"""# 你手上两类工具，性质不一样

**表 A 是拧好的旋钮**：{knobs} 个，每个都有业务名字。用户说"船租""工时"，你直接拧。
拧到表外就是没有——这是旋钮的性质，不用纠结。

**表 C 是积木**：{bricks} 种，没有业务名字，只有形状。它们能拼出表 A 拼不出的东西。

一句话区分：

- 表 A 回答「这个词有没有对应的参数」
- 表 C 回答「这句话能不能被表达」

## 表 A 里没有，但积木能拼的

| 用户说 | 怎么拼 |
|---|---|
| 任务001和任务002必须同一天做 | C04 `co_occur{{mode:"same_day",tasks:[…]}}` |
| 任务001和任务002别排同一天 | C04 `co_occur{{mode:"diff_day",tasks:[…]}}` |
| 先修任务001，隔两天再修任务002 | C03 `precedence{{before,after,gap:2}}` |
| 任务018不能晚于11月8日 | C07 `deadline{{task,time}}` |
| 11月3日到5日别开工 | C09 `forbidden_dates{{dates:[…]}}` |
| 把任务001固定到2号船 | C05 `fix{{task,line:2}}` |
| 至少要修完5个 | C06 `cardinality{{filter:{{}},op:">=",value:5}}` |
| 整体越早收工越好 | O02 `minimize{{q:"最后完工时间"}}` |
| 大家都早点干完 / 别拖到最后 | O02 `minimize{{q:"完工时间和"}}`（求和，不是最后那个） |
| 总成本压到最低 | O02 `minimize{{q:"总经济成本"}}` |
| 先修严重的 / 优先修高风险的 | O01 `strategy{{name:"高风险优先抢修"}}` |

这几条在表 A 里一行都没有。**不要因为找不到对应参数，就说做不到。**

「先修严重的」这句话要看**有没有点名任务**：没点名，是问"整体先修哪一类"，
落 O01 策略（高风险优先抢修），不要因为"没说是哪两个任务"就反问你想要谁先谁后；
点了两个号说"先修001再修002"，才是 C03 `precedence`。

判断标准只有一句：

> 这句话是不是在限制「选哪些任务、什么时候做、怎么排、花多少钱」？
> 是 → 一定能落到某块积木上，或者几块的组合。
> 不是 → 才叫做不到。

## 积木也有边界，边界在「量目录」不在「线性」

C11 `linear` 能装任意线性式，可式子里的每一项都必须来自表 F。
所以真正表达不了的是下面四类。遇到它们要直接拒，**不要拿近似的东西顶**：

1. **「全部」这种量词。** 只有 C06「至少 N 个」，没有「全部」。
   用户说"高风险的必须全修完"，现在做不到——不要用 `cardinality{{">=",1}}` 顶替，
   那是"至少一个"，不是"全部"。这两者差得很远。
2. **量目录里没有的单项。** 有"现金支出""总经济成本"这种合账，没有"船租"这个单项。
   用户说"船租和人工加起来不超300万"，拆不开，现在做不到。
3. **非线性的东西。** 比值、平均数、最大值、方差、"均衡"。
   这些不是线性式，C11 也装不下。
4. **只在某一天生效的临时限制。** "明天只有3个人能出海""2号船周三别排"——
   表 A 的人员、船、作业线都是**全程**参数，没有"某天不同"的表达。
   这类要进 `unassigned` + `row_not_wired`，**绝对不要拿全局参数顶**：
   那会把一天的事变成整月的事，而且求解器会老老实实照做——
   用户第二天发现整月只剩 3 个人，比"没听懂"严重得多。
5. **删除/新增任务、改风机台账、把天气改成"晴天"。** 这些不是"改排程模型"，
   是改输入台账或不存在的能力。`exclude` 是"这次不修"，**不等于"删掉"**——
   把"删掉任务012"写成 `exclude 任务012`，是把一件数据变更伪装成一个排程约束。
6. **多个目标按权重折中——现在可以了。** 用 `weighted`
   （`{{"type":"weighted","weights":{{"高严重完成数":0.6,"总经济成本":0.4}}}}`）。
   它曾经写不出来，因为执行侧漏了一步分派；补齐后能跑。
   **但「先 A 再 B」不是加权，那是 `lexmin`**——先后关系用加权表达等于把
   两件事压成一个数，结果会两边都不像。
7. **限定排程范围。** "只排风机003的任务""除了任务001，别的都别动"——
   没有一块积木专门表示"这份计划只看某个子集"。`filter` 是在某块积木**内部**筛量，
   不是筛整份计划。这类要 unassigned + `row_not_wired`，
   **不要用 `count==1` 顶替**：那是"正好完工一个任务"，不是"只排这一台风机"。
8. **作业之间的歇工/准备/清理时间。** "同一条船干完一个活要歇2小时""每修完一台要清场半天"——
   模型里只有一个"风机间转移"（P07），它指的是**换风机的路上**要多久，不是"活儿与活儿之间
   必须空多久"。两者长得像，数值和含义都不同：把"歇2小时"写成 P07=2，等于把
   转移时间从 6 小时改成 2 小时，求解器会照做，出来的是另一份计划。
   这类要 unassigned + `concept_not_modeled`。真正能表达的是**点名的两个任务之间**留几天
   （C03 `precedence{{gap}}`），不是"同一条船的所有活儿之间"。

这八类要给 `unassigned`，理由写 `concept_not_modeled` 或 `row_not_wired`。
**不要交一个"差不多"的约束上去**——那比说做不到更糟，因为它会真的被执行。"""


def job_block() -> str:
    return """# 你的岗位

这是个海上风电检修排程系统：给定一批故障任务、几条船、若干人、天气预报和海事窗口，
求解器算出一份"哪个任务哪天由谁在哪条船上修"的计划。

你的活是**把人的话，翻译成求解器能吃的改动**。每一格改动都要能对回原话。

你交三样东西：

| 产物 | 是什么 | 干什么用 |
|---|---|---|
| `claims` | 把这句话切成几条诉求，逐条抄原文 | 给出"应该有几句"的分母，防漏 |
| `fills` | 每条诉求认领到哪一行，原文哪一段是依据 | 让"填得对不对"可以被机器核对 |
| `dsl` | 最终执行的改动 | 交给编译器 |

机器会核这三样之间对不对得上。**对不上整份退回，不给部分通过。**
所以宁可少填一条并说明原因，也不要填一条自己都对不上的。"""


def _date_rule_block(ctx: "session.Context") -> str:
    """日期口径的**静态**半截。留在前面，因为它逐字不变。"""
    return ctx.rule_block()


def _today_values_block(ctx: "session.Context") -> str:
    """本轮基准 + 现值 + 窗口起止。**排在最后**，因为它每轮都可能变。"""
    return ctx.values_block()


def step_block() -> str:
    return f"""# 你怎么干

## 第一步：切句（claims）

按标点和连接词切开，一句一条。连接词含：{seg.connective_phrase()}。

- 一个数字一条。同一诉求里出现两个数也拆两条（"从8万降到7万"→ 两条，两个值）
- `text` 照抄原文，不要改写、不要合并、不要润色

每条 claim 必带一个 `kind`，只有四种：

| kind | 什么算 | 要不要归宿 |
|---|---|---|
| `action` | 要改模型（改参数、加约束、换目标） | **必须**进 fills 或 unassigned |
| `ask` | 问事实、问原因、要比两版 | 不用，但必须写 `about` |
| `noise` | 寒暄、感叹、口头语 | 不用 |
| `unsure` | 整段读不懂 | 不用 |

`ask` 的 `about` 只有三种：`fact`（问现在是什么）/ `reason`（问为什么）/ `diff`（比两版差多少）。

**改一半、问一半，就切两条。** 例："把船租降到8万，为什么这么贵"
→ 一条 `action`（8万）+ 一条 `ask`（为什么）。只标一条，就是把另一半静默丢了。

系统会独立按标点再切一遍。你的 `claims` 要覆盖它切出的每一段。

## 第二步：认领（fills）

每条 claim 要么进 `fills`，要么进 `unassigned`，二选一，**不许两边都没有**。

每个 fill 交五样：claim 编号、`row`、`span`、`from`、`value`。

**`value` 是最容易漏的一样，而且漏了必判死。** 它是这一栏最终落成的值（数字、日期、或整段对象）。
"我把值写在 `dsl` 里了，fill 就不用重复"——不行：`dsl` 是结论，`value` 是这个结论的出处账本，
两者对不上就分不清哪一项是你要的、哪一项是顺手写的。A8 会拿 `dsl` 的每一项去 `value` 里找对应，
找不到就判"多出"。

`span` 是你从原文抄下来的那段原话——它会拿去和原文逐字比对，抄错一个字就算错。

`from` 只有四种：

| from | 什么时候用 | 要求 |
|---|---|---|
| `literal` | 原文有字面值 | `span` 里必须能找到这个值（数字、日期、编号） |
| `convert` | 原文给的是说法 | `span` 里必须能找到那个说法，`calc` 写算式 |
| `enum` | 说法要落成固定词 | `span` 里必须能找到那个说法 |
| `default` | 原文没给值，你自己补 | 必须写 `why`；这条会被单独计数 |

`convert` 的例子：`span="涨两成"` → `calc="12000*1.2=14400"`，`value=14400`。
`enum` 的例子：`span="优先抢修高风险的"` → `value="高风险优先抢修"`。

**"没给数"不等于"不能填"。** 原文只说了现象、但明确要求停工的
（"明天风大，先别出海"），用 `from=default` + `state="不可作业"` 落表，
`why` 写"原文未给风速，按不可作业处理"。这不是编造：
编造是给自己造一个原文没有的数，这里是在两种合法取值里选了用户表达的那一个。
反过来，原文只描述天气（"明天风大"）却**没说要停工**，那才该问一句。

**日期字段只收日期。** `day` / `dates` / `start` / `finish` / `deadline` / `at` 这些键，
要么写 `2026-11-09`、`2026-11-09T08:00:00+08:00`，要么写 `11月9日`；
把"明天""下周一""月底"原样写进去等于没落地——那不是"没编造"，那是把没定的事说成定了。
能算的按文末「本轮的基准」那段换算成具体日期再写，`calc` 里说明怎么算的；算不出来的按"日期怎么定"那节处理。

`unassigned` 每条交三样：claim 编号、`reason`、`note`。`reason` 只有三种：

| reason | 什么时候用 |
|---|---|
| `concept_not_modeled` | 用户说的概念没建模（风险评分、可靠性、满意度、界面颜色） |
| `row_not_wired` | 表里有这个栏目但没接通（候选风机、候选部件、电价全局倍率） |
| `need_info` | 缺基准（"顺延两天"没说从哪天算；引用的任务或风机不存在） |

## 第三步：交卷

`route` 只有四种，且必须和 claims/fills 自洽：

| route | 什么时候 | 约束 |
|---|---|---|
| `dsl` | 这句话要改模型 | 必须有 fills |
| `not_change` | **看懂了是什么问题**的问句、解释、闲聊 | 没有 fills；claims 里**不许有** `action` |
| `reject` | 整句都在表外 | claims 全部进 `unassigned`（`concept_not_modeled` 或 `row_not_wired`） |
| `clarify` | 缺基准、有冲突，或**说不出要改什么** | claims 里有 need_info，或同一栏两个值打架 |

**`not_change` 不等于"没听懂"。** 它是"我听懂了，这句话本来就不该改模型"。
"尽量安排得合理一些"这种模糊诉求、`如果…怎么办`这种假设句，都不是 `not_change`：
前者你说不出要改哪一行，后者要换一组条件重算一遍——两者都缺信息，写 `clarify`，
把"要你补哪一句"说清楚。用 `not_change` 兜住模糊诉求，用户会以为系统认为
"现状就是合理的"。

**`reject` 是最容易用错的一个。** 只要有一段能落到表里，就不能写 `reject`——
能落的那段用 `dsl`，落不了的那段进 `unassigned`。整句拒掉是在偷懒，
用户会以为整件事都做不到。

例："成本压到最低，但高风险的必须全修"
→ `route=dsl`，O02 做成本那半，第二条进 `unassigned` 写 `concept_not_modeled`。
**不是** `reject`。

有 fills 却写 `not_change`、claims 全在表外却写 `dsl`，都按 A10 不通过。"""


def syntax_block() -> str:
    """写法的硬约束。每一条都对应 validator 的一条确定报错——
    不写就是让模型去猜，猜错一次就白跑一轮（实测 `op:"="` 与 `terms` 形状都踩过）。
    """
    return """# 几个必须照抄的写法

这些不是风格问题，写错编译器当场报错，整份退回。

**`op` 分两套，不能混：**

| 写在哪 | 允许的 op |
|---|---|
| `parameters` | `=`（绝对值）或 `*`（倍率） |
| `constraints` 里所有带 op 的原语 | `<=` / `>=` / `==`（**注意是双等号，没有单个 `=`**） |

**`linear` 的 terms 是「系数 + 量」的二元组列表：**

```json
{"type": "linear", "terms": [[1, "完成任务数"]], "op": "<=", "value": 5}
```

系数是数字，量有三种写法：

| 量的写法 | 例子 |
|---|---|
| 表 F 的具名量 | `[1, "完成任务数"]` |
| 计数形式 | `[1, {"form": "count", "filter": {"severity": "高"}}]` |
| 加权形式 | `[2, {"form": "weighted_sum", "coeff": "labor", "filter": {"turbine": "风机003"}}]` |

`filter` 只能用这几个键：task / turbine / part / severity / window / day / line。
`coeff` 只能是：finish / start / day / work_hours / labor / material / other_fee。

**量名不许自己造。** 表 F 之外、又不能写成上面三种形式的，就是做不到——写进 `unassigned`。

# 口径：把不标准的说法落到标准值

用户的写法不会照你的表来。下面这些要**你自己归一**，不能因为写法不标准就说做不到。

**严重等级只有 高 / 中 / 低。** 用户说「严重故障」「高危」「紧急」「重大」→ 高；
「中等」「一般」→ 中；「轻微」「小毛病」「低严重」「高严重」（注意这个）→ 按字面：高严重=高。
碰到"高严重""中严重""低严重"这种别扭写法，认里面的"高/中/低"。

**编号可能写得很随意。** `T01`、`001`、`任务1`、`任务 001` 都是 **任务001**；
`F03`、`风机3` 都是 **风机003**；`W02`、`窗口2` 都是 **窗口02**。
**但 `claim.text` 仍然照抄原文**（原文写 T01 就抄 T01）——归一只发生在 `value` 里。

**只说"几号"没给月份**：按「日期怎么定」那节取基准日之后**最近**的那个，别反问是几月。

**时刻可能是 12 小时制**：「下午6点」→ `18:00`；「晚上8点」→ `20:00`；
「中午12点」→ `12:00`；「早上7点」→ `07:00`。直接写 6:00 会把收工时间提前 12 小时。

**"算两个场景""做敏感性分析"这类批量**：本产品一次只处理一个场景。
不要只做其中一半就交上来——写 `clarify`，说明"需要分两次算"，并把两个场景都复述一遍。

**相对增减分两种，别混：**

| 原文 | 怎么办 | 写什么 |
|---|---|---|
| 少一条、多两个人、砍掉5万 | 用现值加减，算成绝对值 | `op:"="`，`value` 写算出来的数 |
| 涨20%、跌一成、砍半、翻倍 | 保持比例，不要乘出现值 | `op:"*"`，`value` 写 1.2 / 0.9 / 0.5 / 2 |

「现在是多少」表里给了现值，加减时用它；**比例不要用它**——`*1.2` 已经在求解器里乘过了，
你自己再乘一遍等于涨两次，而且原话里的 20 会被判成"丢了一个数"。

**"看看""试试""算一下"是让你跑一遍，不是让你确认。** 这类话出现在相对增减后面
（"船少一条看看"），意思就是"按这个改动算一份给我看"，直接出 `dsl`。
只有**改哪个东西本身有两种以上可能**时才 `clarify`——比如"船"既可以指作业线数、
也可以指船租单价，那才要问。"""


def audit_block() -> str:
    """验收单只列**模型需要照着做**的判据。

    A14（可执行性）故意不列：它守的是"目标原语有没有人接住"。
    模型看不见那份名单，也就不会绕着它写；真写了，退回消息里会告诉它。
    写进验收单反而变成"嘱咐"，而这套提示词的原则是：不在表里才是定义。

    A14 的名单现在是空的——五个目标原语全部可执行。它保留着是为了守"将来"：
    谁再往注册表加一个没实现的原语，A14 会亮。这份保留本身有代价（一条永远
    pass 的判据），所以配了一条测试专门证明"名单非空时它真的会拦"。
    """
    return f"""# 什么算合格（这一节是验收单，不是建议）

| 编号 | 判据 | 不通过会怎样 |
|---|---|---|
| A1 覆盖 | 系统按标点切出的每一段，都被至少一条 claim 覆盖 | 漏一段 → 整份退回 |
| A2 闭合 | 每条 `action` claim 都有归宿（fills 或 unassigned）；`ask`/`noise`/`unsure` 不需要 | 有 action claim 没归宿 → 整份退回 |
| A3 出处 | 每个 `span` 是原文的连续子串，且落在 claim 指向的那一段里 | 对不上 → 该条作废，其余仍采信 |
| A4 来源 | `from≠default` 时，`value` 能从 `span` 直接得到或由 `calc` 算出；相对时间要按基准换算，**换算出来对不上也算错** | 推不出 → 判编造，该条作废 |
| A5 行号 | `row` 必须在 {_row_ranges()} 之内 | 越界 → 整份退回 |
| A6 单值 | 同一 claim 里同一行只能一个值；跨 claim 冲突要写 `conflict` | 冲突未声明 → 退回澄清 |
| A7 必填 | 每行的必填字段都要有（见各表） | 缺 → 该条作废 |
| A8 一致 | dsl 每项都有 fill 背书；每个 fill 在 dsl 里都有对应项 | 双向对不上 → 整份退回 |
| A9 形状 | 顶层是对象；四段是数组；字段名照抄 | 不合法 → 先归一化再重判，不算你的错 |
| A10 不改 | `not_change` 时不许有 `action` claim；`reject` 时每条 claim 都要进 unassigned | 出现 action → 改判 dsl，或退回 |
| A11 方向 | claim 里写「不超过/最多」→ op 必须是 `<=`；「至少/不低于」→ `>=` | 方向反了 → 整份退回 |
| A12 不轻拒 | `reject` 时不许放过表内有落点的段（这条只记线索，不拦你） | 只提示，不改判 |
| A13 量词 | claim 里说「全部/所有/全修」时，不许写成 `cardinality{">=",1}` | 拿近似顶替 → 整份退回 |

三档后果：**整份退回 > 该条作废 > 归一化重判**。

A11 单独说明：「钱不超过300万」写成 `op: ">="` 也能编译、也能求解，只是**结论反了**——
原本要压预算，变成至少花这么多。这是唯一一类"全过还更糟"的错，所以判死。
前两档会告诉你具体哪一条、为什么。

下面这些是格式，不是提醒：

- 任务写"任务018"；风机写"风机003"；窗口写"窗口02"；部件用名称"齿轮箱"
- 时间写 ISO 带时区 `2026-11-05T08:00:00+08:00`；只说某日 → 该日 08:00
- 金额一律元："500万"→5000000
- 值形态：开关写 `"是"`/`"否"`；时刻写 `"07:00"`；数值写数字，不要写"八小时\""""


def output_block() -> str:
    """示例里的行号按名字解析，不手抄——手写过一次，P13 指错了字段。"""
    row_of = {name: row_id for row_id, name, _unit, _domain in _table_a_rows()}
    template = """# 交卷格式

```json
{
  "route": "dsl",
  "claims": [
    {"id": 1, "text": "把船租降到8万", "kind": "action"},
    {"id": 2, "text": "每天最多干10小时", "kind": "action"}
  ],
  "fills": [
    {"claim": 1, "row": "@RENT@", "span": "8万", "from": "literal", "op": "=", "value": 80000},
    {"claim": 2, "row": "@HOURS@", "span": "10小时", "from": "literal", "op": "=", "value": 10}
  ],
  "unassigned": [],
  "dsl": {
    "parameters": [
      {"field": "船舶日租金", "op": "=", "value": 80000},
      {"field": "每日有效维修上限", "op": "=", "value": 10}
    ]
  }
}
```

`dsl` 的四段：`parameters`（数组）/ `constraints`（数组）/ `objectives`（数组）/ `data`（数组）。
另外 `solver.seconds` 可以设求解时限（30~1800 秒）。"""
    return template.replace("@RENT@", row_of["船舶日租金"]).replace("@HOURS@", row_of["每日有效维修上限"])


def reference_block() -> str:
    blocks = [
        "# 参考表",
        "## 表 A　旋钮：直接写进 dsl.parameters（每行必填 `op` 和 `value`）",
        table_a(),
        '`op` 只有两种：`"="` 绝对值；`"*"` 相对倍率（"涨两成"→1.2，"砍半"→0.5）。',
        "## 表 B　不能写 parameters，换节写",
        table_b(),
        "标准目标策略四选一：高风险优先抢修／成本最低优先／最短完成优先／检修最多器件优先。\n"
        '预算默认口径是"现金支出"（不含发电损失）；用户说"连发电损失一起算"才写 `"总经济成本"`。',
        "## 表 C　积木：约束原语（写进 dsl.constraints）",
        table_c(),
        "## 表 D　目标原语（写进 dsl.objectives）",
        table_d(),
        "要「先 A 再 B」用 `lexmin`，`stages` 写成 "
        "`[{\"dir\":\"minimize\",\"q\":\"完工时间和\"}, {\"dir\":\"minimize\",\"q\":\"总经济成本\"}]`。",
        "要「按权重折中多个目标」用 `weighted`，`weights` 写成 "
        "`{\"总经济成本\":0.6,\"高严重完成数\":0.4}`——键是表 F 的量名，权重不必凑成 1。",
        "『先保安全再省钱』是先后，用 `lexmin`；『安全和成本各占一半』是折中，用 `weighted`。"
        "两者别混：前者一句一句比，后者一次加总。",
        "`q`、`terms`、`weights` 里的量只能用表 F 的名字或三元组。",
        f"## 表 E　数据补丁（写进 dsl.data，每项必须有 `kind`，只能是这 {len(_TABLE_E_ORDER)} 个）",
        table_e(),
        "## 表 F　可引用的量",
        table_f(),
        '也可以写三元组：`{"form":"count" 或 "weighted_sum","coeff":<系数>,"filter":<过滤>}`\n'
        f"　系数（{len(qty_reg.COEFFS)} 种）：{'／'.join(qty_reg.COEFFS)}\n"
        f"　过滤（{len(qty_reg.FILTERS)} 种）：{'／'.join(qty_reg.FILTERS)}",
    ]
    return "\n\n".join(blocks)


# ── 段落顺序 ────────────────────────────────────────────────────────────────
# 这条顺序有一条硬规矩（跟 Codex 那边的做法一样）：
#
#     **逐字不变的内容排前面，会变的排最后。**
#
# LLM 的提示词缓存**只认前缀**：开头第 N 个字一变，后面全部重新读一遍、重新计费。
# "今天几号""现在是多少"每轮都不一样，原先它排第 2 位，
# 后面一万两千多字全被它拖着变——多轮追问时缓存命中率几乎是零。
# 挪到最后，静态前缀就能跨轮复用。
#
# 第二列就是这条规矩：`False` 表示这段必须逐字不变，`True` 表示它每轮可能变。
# 做成数据而不是靠人记，是为了让 `prefix_check()` 能一直盯着——
# 谁往静态段里塞一个今天，测试立刻红。
SECTIONS: tuple[tuple[str, bool, callable], ...] = (
    # ── 静态前缀段（可缓存区）──────────────────────────────────────────
    ("岗位", False, lambda _ctx: job_block()),
    ("日期怎么定", False, _date_rule_block),
    ("工具性质（积木段）", False, lambda _ctx: capability_block()),
    ("干活步骤", False, lambda _ctx: step_block()),
    ("验收单", False, lambda _ctx: audit_block()),
    ("交卷格式", False, lambda _ctx: output_block()),
    ("写法", False, lambda _ctx: syntax_block()),
    ("参考表 A~F", False, lambda _ctx: reference_block()),
    # ── 变量后缀段（每轮都会变，必须在最后）───────────────────────────
    ("本轮基准与现值", True, _today_values_block),
)


def build(ctx: "session.Context | None" = None) -> str:
    """`ctx` 是会话基准（今天几号）。不给就用系统日期——**但调用方应当显式给**：
    同一句"明天"在不同日期该落成不同日期，提示词里不写清楚，模型的换算就是瞎猜。"""
    ctx = ctx or session.with_params_for(session.default())
    return "\n\n".join(fn(ctx) for _name, _varies, fn in SECTIONS)


def stats(ctx: "session.Context | None" = None) -> list[tuple[str, int]]:
    ctx = ctx or session.with_params_for(session.default())
    return [(name, len(fn(ctx))) for name, _varies, fn in SECTIONS]


def cache_stats(ctx: "session.Context | None" = None) -> dict[str, int]:
    """静态前缀占整份提示词的比例。这是唯一能证明"缓存有没有用"的数字。

    光说一句"缓存命中了"容易糊弄——改个顺序就说优化了，谁也没量过。
    前缀占比是能称的：改前改后各取一次，涨没涨一目了然。
    """
    ctx = ctx or session.with_params_for(session.default())
    prefix = 0
    for _name, varies, fn in SECTIONS:
        if varies:
            break
        prefix += len(fn(ctx)) + len("\n\n")
    total = len(build(ctx))
    return {"静态前缀": prefix, "合计": total,
            "前缀占比": round(prefix * 100 / total) if total else 0}


def prefix_check() -> list[str]:
    """守住"静态段不许变"这条规矩。两件事都不靠自觉：

      1. 标了 False 的段，换成别的基准日生成必须逐字相同
      2. 变量段必须是最后一段——后面再插静态段，前缀就断在那儿了

    历史上这类规矩全是靠注释提醒的，没人看得住；这里让它进 `self_check()`，
    跟"51 项宣称 / 16 项生效"用的是同一道闸门。
    """
    problems: list[str] = []
    days = (_dt.date(2026, 10, 30), _dt.date(2026, 11, 15), _dt.date(2027, 3, 1))
    contexts = [session.Context(today=d) for d in days]

    for name, varies, fn in SECTIONS:
        if varies:
            continue
        texts = {fn(c) for c in contexts}
        if len(texts) > 1:
            problems.append(f"「{name}」段标的是静态，但换基准日就变了——"
                            "里面混进了跟着日期走的内容")

    varying = [i for i, (_n, v, _f) in enumerate(SECTIONS) if v]
    static_tail = [i for i in range((varying[-1] if varying else -1) + 1, len(SECTIONS))]
    if static_tail:
        names = "、".join(SECTIONS[i][0] for i in static_tail)
        problems.append(f"静态段「{names}」排在变量段后面，前缀缓存会在那里断掉")

    # 反过来也不能漏：变量段绝不能在静态段之前就该报错（上面已查），
    # 这里再核一次"前缀是不是真的能用"——纯静态（无参数）时也不能出岔子。
    try:
        ratio = cache_stats(contexts[0])["前缀占比"]
        if ratio >= 100:
            problems.append("整份提示词都标成了静态——多半是哪段忘了标 True")
    except Exception as exc:
        problems.append(f"算前缀占比失败：{exc}")
    return problems


# ── 自检：防"手抄清单漂移" ──────────────────────────────────────────────────
# 历史上出过事：注册表宣称 51 项可改，执行侧白名单只认 16 项，提示词按 51 项对外宣称，
# 用户改剩下 35 项必然报错。根因是三份手抄清单之间没有测试比对。
# 这里的断言就是那道比对。
def self_check() -> list[str]:
    problems: list[str] = []
    problems += prefix_check()

    listed = {name for _g, names in _TABLE_A_GROUPS for name in names}
    actual = {row["参数名称"] for row in param_reg._rows()
              if param_reg.route_of(row["参数名称"]) == "parameters"}
    for name in sorted(actual - listed):
        problems.append(f"表 A 漏了有落点的参数：{name}")
    for name in sorted(listed - actual):
        problems.append(f"表 A 列了没有落点的参数：{name}")

    c_names = [name for name, _note in _TABLE_C_ORDER]
    for name in c_names:
        if name not in prim_reg.CONSTRAINT_PRIMITIVES:
            problems.append(f"表 C 列了未注册的原语：{name}")
        elif name not in prim_reg.COMPILED_CONSTRAINTS:
            problems.append(f"表 C 列了未实现的原语：{name}")
    for name in sorted(prim_reg.COMPILED_CONSTRAINTS - set(c_names)):
        problems.append(f"表 C 漏了已实现的原语：{name}")

    d_names = set(_TABLE_D_ORDER)
    for name in sorted(d_names - prim_reg.COMPILED_OBJECTIVES):
        problems.append(f"表 D 列了未实现的目标原语：{name}")
    # 反过来也要查：能编译却没列出来的，会被A14当成"不存在"而拒掉正常请求。
    if not d_names <= prim_reg.COMPILED_OBJECTIVES:
        problems.append(f"表 D 与编译器不一致：{sorted(d_names - prim_reg.COMPILED_OBJECTIVES)}")

    e_kinds = {kind for kind, _f, _s in _TABLE_E_ORDER}
    no = set(dsl_validator._DATA_PATCH_REQUIRED)
    for kind in sorted(no - e_kinds):
        problems.append(f"表 E 漏了校验器支持的 kind：{kind}")
    for kind in sorted(e_kinds - no):
        problems.append(f"表 E 列了校验器不认的 kind：{kind}")

    f_names = set(qty_reg.NAMED_QUANTITIES)
    if set(_TABLE_F_NOTE) - f_names:
        problems.append(f"表 F 注解里有未注册的量：{set(_TABLE_F_NOTE) - f_names}")

    return problems


def main(argv: list[str]) -> int:
    problems = self_check()
    if problems:
        print("自检不通过，提示词与注册表不一致：", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1
    if "--stats" in argv:
        ctx = session.with_params_for(session.default())
        for name, size in stats(ctx):
            print(f"{size:>6}  {name}")
        print(f"{len(build(ctx)):>6}  合计")
        cache = cache_stats(ctx)
        print(f"\n静态前缀 {cache['静态前缀']}/{cache['合计']} 字符"
              f"（{cache['前缀占比']}%）——这一段跨轮可被缓存复用")
        return 0
    text = build()
    print(text)
    print(f"\n=== 合计 {len(text)} 字符 / 自检通过 ===", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
