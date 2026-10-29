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
| 总成本压到最低 | O02 `minimize{{q:"总经济成本"}}` |

这几条在表 A 里一行都没有。**不要因为找不到对应参数，就说做不到。**

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
4. **限定排程范围。** "只排风机003的任务""除了任务001，别的都别动"——
   没有一块积木专门表示"这份计划只看某个子集"。`filter` 是在某块积木**内部**筛量，
   不是筛整份计划。这类要 unassigned + `row_not_wired`，
   **不要用 `count==1` 顶替**：那是"正好完工一个任务"，不是"只排这一台风机"。

这四类要给 `unassigned`，理由写 `concept_not_modeled` 或 `row_not_wired`。
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


def today_block(ctx: "session.Context") -> str:
    """基准日期。放在岗位之后、步骤之前——它影响"值填多少"，必须在动手前就知道。"""
    return "# 今天几号\n\n" + ctx.explain()


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
"明天"这类说法按上一节的基准换算成具体日期再写，`calc` 里说明怎么算的。

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
| `not_change` | 问句、解释、闲聊 | 没有 fills；claims 里**不许有** `action` |
| `reject` | 整句都在表外 | claims 全部进 `unassigned`（`concept_not_modeled` 或 `row_not_wired`） |
| `clarify` | 缺基准或有冲突 | claims 里有 need_info，或同一栏两个值打架 |

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

**量名不许自己造。** 表 F 之外、又不能写成上面三种形式的，就是做不到——写进 `unassigned`。"""


def audit_block() -> str:
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


SECTIONS: tuple[tuple[str, callable], ...] = (
    ("岗位", lambda _ctx: job_block()),
    ("今天", today_block),
    ("工具性质（积木段）", lambda _ctx: capability_block()),
    ("干活步骤", lambda _ctx: step_block()),
    ("验收单", lambda _ctx: audit_block()),
    ("交卷格式", lambda _ctx: output_block()),
    ("写法", lambda _ctx: syntax_block()),
    ("参考表 A~F", lambda _ctx: reference_block()),
)


def build(ctx: "session.Context | None" = None) -> str:
    """`ctx` 是会话基准（今天几号）。不给就用系统日期——**但调用方应当显式给**：
    同一句"明天"在不同日期该落成不同日期，提示词里不写清楚，模型的换算就是瞎猜。"""
    ctx = ctx or session.default()
    return "\n\n".join(fn(ctx) for _name, fn in SECTIONS)


def stats(ctx: "session.Context | None" = None) -> list[tuple[str, int]]:
    ctx = ctx or session.default()
    return [(name, len(fn(ctx))) for name, fn in SECTIONS]


# ── 自检：防"手抄清单漂移" ──────────────────────────────────────────────────
# 历史上出过事：注册表宣称 51 项可改，执行侧白名单只认 16 项，提示词按 51 项对外宣称，
# 用户改剩下 35 项必然报错。根因是三份手抄清单之间没有测试比对。
# 这里的断言就是那道比对。
def self_check() -> list[str]:
    problems: list[str] = []

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

    d_names = {"strategy", "minimize", "maximize", "lexmin", "weighted"}
    for name in sorted(d_names - prim_reg.COMPILED_OBJECTIVES):
        problems.append(f"表 D 列了未实现的目标原语：{name}")

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
        for name, size in stats():
            print(f"{size:>6}  {name}")
        print(f"{len(build()):>6}  合计")
        return 0
    print(build())
    print(f"\n=== 合计 {len(build())} 字符 / 自检通过 ===", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
