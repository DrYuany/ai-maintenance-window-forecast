"""模型可以调的工具。第一版只注册一个：`read_capabilities`。

## 为什么先做这一个

backend 221 条评测里有 22 条 route 分歧。逐条看下来，**没有一条是
"编造参数被抓住"**，全是"不知道自己有什么"——

    Q114  人手砍一半                 → clarify（该 dsl）：不知道哪个参数是"人手"
    Q123  齿轮箱修起来更费时了，加20%  → clarify（该 dsl）：不知道有 part_hours 补丁
    Q174  改成加权，高严重完成数0.6    → clarify（该 dsl）：不知道 weighted 不能编译
    Q204  让任务018和022的距离最大     → clarify（该 reject）：不知道没有"距离"这种量

它们保守地回 clarify，是因为提示词把这些表全塞给它背（14718 字符 / 66 个概念），
背不住就不敢动。工具的意义是**把背换成查**。

## 为什么是"给整张表"而不是"精确匹配"

第一版按子串匹配，实测直接翻车：

    query="船租"  → 查不到「船舶日租金」（表里叫"船舶日租金"，没有连续的"船租"二字）
    query="加权"  → 查不到 weighted（中文和英文名零重叠）
    query="人手"  → 查不到「可调动人员总数」

中文口语和表里的正式名之间隔着一道同义词鸿沟，**靠字符匹配永远填不平**。
而这道鸿沟恰好是模型最擅长的：给它看 15 个参数名，它一眼就知道"船租"是哪一个。

所以工具的职责不是"猜它要什么"，而是**把可选集合按相关度排好递过去**，
让模型自己判断。字面匹配只用来排序，不用来取舍——猜错反而害它。

顺带一个收益：整表也就 41 项（15 参数 + 15 原语 + 11 量），一次查询约 1.5 KB，
比在提示词里背 14718 字符便宜一个数量级，而且是按需加载的。

## 注解从哪来

约束原语、数据补丁的中文注解复用 `prompt._TABLE_C_ORDER` / `_TABLE_E_ORDER`，
具名量的复用 `prompt._TABLE_F_NOTE`——**不抄第二份**。提示词哪天瘦身了，
这些注解还在，工具还是准的。
"""
from __future__ import annotations

from typing import Any

import paths  # noqa: F401  （副作用：把 ../backend 挂进 sys.path）

import prompt
import verify

_PARAM_ROUTE = "parameters"   # 参数表 122 行里，只有这个 route 才是真参数
_MAX_ITEMS = 40
_QUERY_MAX = 40

_KINDS = ("parameter", "constraint", "objective", "quantity", "data")

# 目标原语的中文注解。这不是"真话"（可执行性才是），只是解释；
# 表 D 那边只列了名字，注解写在这儿，同源于 prompt 的口径。
#
# 注解里**必须写用户会说的中文词**（"权重""成本最低""多修几个"），
# 因为原语名是英文的：查询「加权」和 `weighted` 字符零重叠，
# 纯字面匹配永远命不中。注解是唯一能把这道中英鸿沟填上的地方。
_OBJECTIVE_NOTE: dict[str, str] = {
    "strategy": "四选一的策略模板（分层字典序）。用户说「按成本优先」「按风险优先」时用它。",
    "minimize": "把某个具名量压到最小。用户说「成本最低」「工期尽量短」「少花钱」时用它。",
    "maximize": "把某个具名量抬到最大。用户说「多修几个」「完成数最多」时用它。",
    "lexmin": "分阶段依次最优，用来表达「先 A 再 B」。用户说「先保安全再省钱」时用它。",
    "weighted": "多目标加权折中。用户说「权重」「加权」「按比例折中」时需要它——但它不能编译。",
}


def _registries():
    """延迟导入注册表：只有真要查的时候才加载，import 本模块不拖慢启动。"""
    from app.core.dsl.registry import parameters as params
    from app.core.dsl.registry import primitives as prims
    from app.core.dsl.registry import quantities as qtys
    return params, prims, qtys


def executable_objectives(prims) -> set[str]:
    """真正能被执行侧跑起来的目标原语。

    ## 曾经有个分叉，现在没有了

    `weighted`（和 `strategy` 一样）不是"分层目标"，是求解器模式：
    该落到 PlanningRequest 的 mode / weights / strategy 三个字段。
    `planning_bridge.dsl_to_request` 一直是这么处理的，但 `runner.run`
    把整段 objectives 直接丢给 `compiler.stages_builder`，于是同一份 DSL
    走变更流程能跑、走 runner（评测与执行侧）抛「已登记但原型未实现编译」。

    补齐 runner → 先过 `split_objectives` 把模式字段摘出来 → 五个目标原语
    全部可执行。所以这个函数现在等于全体，`_UNCOMPILED_OBJECTIVES` 是空表。

    **仍然不读 `prims.COMPILED_OBJECTIVES`。** 那个集合是 validator 的放行名单，
    它跟"真有代码接住它"是两件事——上面那个 bug 正是两者不一致造成的。
    真话来源保持唯一：`verify._UNCOMPILED_OBJECTIVES`，由
    `test_every_objective_is_handled_somewhere` 盯着"有没有人管"。
    """
    return set(prims.OBJECTIVE_PRIMITIVES) - set(verify._UNCOMPILED_OBJECTIVES)


def tool_specs() -> list[dict[str, Any]]:
    """给模型看的工具定义（OpenAI function calling 格式）。"""
    return [{
        "type": "function",
        "function": {
            "name": "read_capabilities",
            "description": (
                "查询本系统支持什么：参数（名称/编号/单位/取值约束）、约束原语"
                "（哪些能编译、需要什么参数）、目标原语、具名量、数据补丁类型。\n"
                "**在把任何业务说法落成 DSL 之前先查它**：确认这个词有没有对应的"
                "参数或原语、编号是什么、能不能编译。不要凭记忆——你对这套私有"
                "词汇表没有可靠记忆。\n"
                "注意：查询是字面排序，不是语义搜索。「船租」不一定字面命中"
                "「船舶日租金」，所以你拿到的是**按相关度排好的候选清单**，"
                "要自己从上往下看，判断哪一个才是用户说的东西。\n"
                "清单里没有对应项，就说明系统确实不支持——该 reject 或 clarify，"
                "不要硬凑一个近似的原语。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "用户原话里的关键词，例如「船租」「人手」「加权」「同一天」。"
                            "传空字符串则只返回各类能力有多少项。"
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(_KINDS),
                        "description": "限定查哪一类。不传则每类都递前几条。",
                    },
                },
                "required": ["query"],
            },
        },
    }]


# ── 相关度 ────────────────────────────────────────────────────────────────────

def _score(query: str, *texts: str) -> int:
    """字面相近程度。**只用于排序，不用于取舍。**

    宁可把"不太相关"的也排在后面递过去，也不要因为分数低就藏起来——
    藏起来等于替模型做了它更擅长的判断。
    """
    if not query:
        return 0
    joined = " ".join(t for t in texts if t)
    if query == joined:
        return 1000
    if query in joined:
        return 500
    qs = set(query)
    # 单字查询不做字符重叠：`set("zzzz") == {"z"}` 会命中任何含 z 的英文名
    # （minimize / maximize），把噪声当成相关。单字仍然走上面的子串匹配。
    if len(qs) < 2:
        return 0
    overlap = len(qs & set(joined))
    return int(100 * overlap / len(qs))


# ── 各类清单 ──────────────────────────────────────────────────────────────────

def _param_items(params) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in params._rows():
        name = row.get("参数名称") or ""
        if params.route_of(name) != _PARAM_ROUTE:
            continue
        item = {
            "kind": "parameter",
            "name": name,
            "code": row.get("参数编号"),
            "unit": row.get("单位"),
            "default": row.get("默认值"),
            "domain": row.get("取值约束"),
        }
        out.append({k: v for k, v in item.items() if v not in (None, "", "无")})
    return out


def _alias_items(params) -> list[dict[str, Any]]:
    """参数表里 route 不是 parameters 的行——它们是「说法 → 原语」的映射。

    「预算」「至多」「全部」这类词，模型不知道该落成哪个原语，但表里其实写了。
    不吐出来，模型就只能猜。
    """
    out: list[dict[str, Any]] = []
    for row in params._rows():
        name = row.get("参数名称") or ""
        route = params.route_of(name)
        if route == _PARAM_ROUTE:
            continue
        out.append({"kind": "alias", "name": name, "maps_to": route,
                    "note": row.get("建模影响") or ""})
    return out


def _constraint_items(prims) -> list[dict[str, Any]]:
    notes = dict(prompt._TABLE_C_ORDER)
    compiled = set(prims.COMPILED_CONSTRAINTS)
    out: list[dict[str, Any]] = []
    for name, spec in prims.CONSTRAINT_PRIMITIVES.items():
        item = {
            "kind": "constraint",
            "name": name,
            "note": notes.get(name, ""),
            "required": list((spec or {}).get("required") or []),
        }
        if name not in compiled:
            item["executable"] = False
            item["why"] = "已登记但编译器没有实现分支，写了会在校验阶段被拒"
        out.append(item)
    return out


def _objective_items(prims) -> list[dict[str, Any]]:
    executable = executable_objectives(prims)
    out: list[dict[str, Any]] = []
    for name in prims.OBJECTIVE_PRIMITIVES:
        item = {"kind": "objective", "name": name,
                "note": _OBJECTIVE_NOTE.get(name, "")}
        if name not in executable:
            item["executable"] = False
            item["why"] = ("已登记、校验器也放行，但编译器没有实现分支——"
                           "写它会在求解前抛错。要改权重请改用 lexmin 分阶段。")
        out.append(item)
    return out


def _quantity_items(qtys) -> list[dict[str, Any]]:
    notes = prompt._TABLE_F_NOTE
    out: list[dict[str, Any]] = []
    for name in qtys.NAMED_QUANTITIES:
        out.append({"kind": "quantity", "name": name,
                    "note": notes.get(name, "")})
    out.append({"kind": "quantity_group", "name": "系数 coeff",
                "members": list(qtys.COEFFS)})
    out.append({"kind": "quantity_group", "name": "筛选 filter",
                "members": list(qtys.FILTERS)})
    return out


def _data_items() -> list[dict[str, Any]]:
    notes = {name: note for name, _opt, note in prompt._TABLE_E_ORDER}
    required = {name: opt for name, opt, _note in prompt._TABLE_E_ORDER}
    return [{"kind": "data", "name": name, "note": notes.get(name, ""),
             "fields": required.get(name, "")}
            for name in notes]


# ── 对外入口 ──────────────────────────────────────────────────────────────────

def read_capabilities(arguments: dict[str, Any]) -> dict[str, Any]:
    """查能力表。返回信封，`status` 用与 backend 工具相同的词表。"""
    raw_query = arguments.get("query")
    if not isinstance(raw_query, str):
        return {"status": "invalid_arguments",
                "message": "query 必须是字符串；查全部请传空字符串"}
    query = raw_query.strip()[:_QUERY_MAX]

    kind = arguments.get("kind")
    if kind is not None and kind not in _KINDS:
        return {"status": "invalid_arguments",
                "message": f"kind 只能是 {'/'.join(_KINDS)}"}

    params, prims, qtys = _registries()

    # 第三项是"是不是辅助组"。alias 永远排最后：它们字面特别容易命中
    # （"船租计费单位"含"船租"，500 分），混进来会把真正的「船舶日租金」（100 分）
    # 挤下去。它们是补充信息，不该抢头条。
    groups: list[tuple[str, list[dict[str, Any]], bool]] = []
    aliases = _alias_items(params)
    if kind in (None, "parameter"):
        groups.append(("parameter", _param_items(params), False))
    if kind in (None, "constraint"):
        groups.append(("constraint", _constraint_items(prims), False))
    if kind in (None, "objective"):
        groups.append(("objective", _objective_items(prims), False))
    if kind in (None, "quantity"):
        groups.append(("quantity", _quantity_items(qtys), False))
    if kind in (None, "data"):
        groups.append(("data", _data_items(), False))
    if kind in (None, "parameter"):
        groups.append(("alias", aliases, True))

    if not query:
        # 空查询只给计数。铺全表是几千字，模型反而看不清重点。
        return {"status": "ok",
                "data": {"summary": {name: len(items) for name, items, _ in groups},
                         "hint": "用 query 查具体说法，例如 query='船租'、query='加权'"}}

    # 有查询：先按字面相关度、命中优先、辅助组垫底
    scored_rows: list[tuple[bool, int, int, dict[str, Any]]] = []
    hits = 0
    for group_index, (_name, items, is_aux) in enumerate(groups):
        for item in items:
            score = _score(query, item.get("name") or "",
                           item.get("note") or "",
                           item.get("unit") or "",
                           item.get("domain") or "")
            if score > 0:
                hits += 1
            scored_rows.append((is_aux, -score, group_index, item))

    scored_rows.sort(key=lambda row: (row[0], row[1], row[2]))
    picked: list[dict[str, Any]] = []
    for is_aux, neg_score, _gi, item in scored_rows:
        kept = dict(item)
        if neg_score < 0:
            kept["matched"] = True
        picked.append(kept)
        if len(picked) >= _MAX_ITEMS:
            break

    if not picked:
        return {"status": "not_found", "message": "能力表是空的，这本身是个异常"}

    data: dict[str, Any] = {
        "query": query,
        "matched": hits,
        "items": picked[:_MAX_ITEMS],
    }
    if hits == 0:
        # 关键：把"没有字面命中"这件事明说，但同时把候选递过去。
        # 模型需要知道「没命中」不等于「不支持」——"船租"永远命不中"船舶日租金"。
        data["hint"] = (
            f"没有字面命中「{query}」的条目。下面是按相关度排的候选，"
            "请自己判断有没有同义项；如果确实一个都对不上，再判不支持。"
        )
    return {"status": "ok", "data": data}


_HANDLERS = {"read_capabilities": read_capabilities}


def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """执行一次工具调用。未知工具名不抛，返回 not_found 让模型自己改。

    抛异常会让整轮作废，模型连"我调错名字了"都学不到。
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"status": "not_found",
                "message": f"没有叫 {name} 的工具；可用的是：{sorted(_HANDLERS)}"}
    try:
        return handler(arguments or {})
    except Exception as exc:  # noqa: BLE001 - 工具内部错误不该炸掉整轮
        return {"status": "internal", "message": f"{type(exc).__name__}: {exc}"}
