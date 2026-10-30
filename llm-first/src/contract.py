"""产物契约：形状、枚举、归一化。

契约先于判据。判据（verify.py）只判语义，不判"字段名叫什么"——
名字对不对由这里一次性归一化掉。否则每条检查都要写一遍 `if "claimId" in fill`，
迟早漏一条。

归一化是**有边界的**：只改键名写法、把单对象包成数组、去掉 markdown 围栏，
不改值、不补内容、不猜模型想说什么。补内容就叫"代它作答"，
那会让后面的判据自欺。
"""
from __future__ import annotations

import json
import re
from typing import Any

ROUTES: tuple[str, ...] = ("dsl", "not_change", "reject", "clarify")
KINDS: tuple[str, ...] = ("action", "ask", "noise", "unsure")
ASK_ABOUT: tuple[str, ...] = ("fact", "reason", "diff")
FROM: tuple[str, ...] = ("literal", "convert", "enum", "default")
REASONS: tuple[str, ...] = ("concept_not_modeled", "row_not_wired", "need_info")
SECTIONS: tuple[str, ...] = ("parameters", "constraints", "objectives", "data")

# 只做键名容错的别名，不做语义代填
_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "claim_id", "claimId", "no"),
    "text": ("text", "claim", "content", "utterance"),
    "kind": ("kind", "type", "claim_kind"),
    "about": ("about", "ask_about", "sub_kind"),
    "claim": ("claim", "claim_id", "claimId", "id"),
    "row": ("row", "row_id", "rowId", "line"),
    "span": ("span", "evidence", "quote", "source"),
    "from": ("from", "source_kind", "origin"),
    "value": ("value", "val", "to"),
    "op": ("op", "operator"),
    "why": ("why", "reason_note", "note"),
    "calc": ("calc", "calculation", "expr"),
    "reason": ("reason", "why_not", "cause"),
}


def strip_fence(text: str) -> str:
    """去掉模型爱加的 ```json 围栏。"""
    body = (text or "").strip()
    body = re.sub(r"^```(?:json)?\s*", "", body)
    body = re.sub(r"\s*```$", "", body)
    return body.strip()


def _tail_is_prose(tail: str) -> bool:
    """JSON 后面剩下的那段，是不是"人话"而不是第二份产物。

    模型经常这么干：```json {...} ``` 之后接一段「说明：…」。这段说明对机器
    没用，是它的解释，丢掉不影响任何语义。但**第二份 JSON 不能丢**——
    那是数据，丢掉会让后面的判据在少了一半的产物上判"通过"。
    所以只放行"去掉空白和围栏标记后不是 { 或 [ 开头"的尾巴。
    """
    probe = re.sub(r"^[\s`]*(?:json)?[\s`]*", "", tail or "")
    return not probe.startswith(("{", "["))


def parse_object(text: str) -> dict[str, Any]:
    """把模型返回解析成一个 dict；做不到就抛 ValueError。

    **只有这一份实现。** 曾经在 pipeline 里另写了一版"够用的判据"，
    用 rsplit("```") 切尾部，结果它把「JSON + 说明」判成合法、这里却抛
    Extra data——同一段文字两个模块两个结论，白白多烧几十次修复轮。
    """
    body = strip_fence(text)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        if exc.msg != "Extra data":
            raise
        head = body[: exc.pos]
        payload = json.loads(head)  # head 不合法就让原来的错抛出去
        if not _tail_is_prose(body[exc.pos :]):
            raise ValueError("产物后面还跟着第二段 JSON，不猜哪个算数") from exc
    if not isinstance(payload, dict):
        raise ValueError("产物顶层不是对象")
    return payload


def load(text: str) -> dict[str, Any]:
    """从模型返回的字符串里拿产物；拿不到就抛 ValueError（由调用方转澄清）。"""
    return parse_object(text)


def _pick(item: dict[str, Any], key: str) -> Any:
    for alias in _ALIASES.get(key, (key,)):
        if alias in item:
            return item[alias]
    return None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """把产物归一化成固定形状。不补语义，只对齐键名与容器类型。"""
    claims = []
    for index, item in enumerate(_as_list(raw.get("claims") or raw.get("claim_list")), start=1):
        if not isinstance(item, dict):
            claims.append({"id": index, "text": str(item), "kind": None})
            continue
        kind = _pick(item, "kind")
        claims.append({
            "id": _pick(item, "id") if _pick(item, "id") is not None else index,
            "text": _pick(item, "text") or "",
            "kind": kind if isinstance(kind, str) else None,
            "about": _pick(item, "about"),
        })

    fills = []
    for item in _as_list(raw.get("fills")):
        if not isinstance(item, dict):
            continue
        fills.append({
            "claim": _pick(item, "claim"),
            "row": _pick(item, "row"),
            "span": _pick(item, "span"),
            "from": _pick(item, "from"),
            "op": _pick(item, "op"),
            "value": _pick(item, "value"),
            "why": _pick(item, "why"),
            "calc": _pick(item, "calc"),
        })

    unassigned = []
    for item in _as_list(raw.get("unassigned") or raw.get("unassigned_claims")):
        if not isinstance(item, dict):
            continue
        unassigned.append({
            "claim": _pick(item, "claim"),
            "reason": _pick(item, "reason"),
            "note": _pick(item, "why") or _pick(item, "text") or "",
        })

    dsl_raw = raw.get("dsl")
    dsl: dict[str, list] = {name: [] for name in SECTIONS}
    if isinstance(dsl_raw, dict):
        for name in SECTIONS:
            dsl[name] = [x for x in _as_list(dsl_raw.get(name)) if isinstance(x, dict)]

    route = raw.get("route")
    route = route if isinstance(route, str) else None

    return {
        "route": route,
        "claims": claims,
        "fills": fills,
        "unassigned": unassigned,
        "dsl": dsl,
        "solver": dsl_raw.get("solver") if isinstance(dsl_raw, dict) else None,
    }


def as_dsl(product: dict[str, Any]) -> dict[str, Any]:
    """只取四段，交给 validator / 编译器。"""
    dsl: dict[str, Any] = {name: list(product["dsl"].get(name) or []) for name in SECTIONS}
    if isinstance(product.get("solver"), dict):
        dsl["solver"] = product["solver"]
    return {key: value for key, value in dsl.items() if value}


def shape_issues(raw: dict[str, Any]) -> list[str]:
    """A9：形状不合法的地方。这里只挑"结构错"，枚举错留给 A5/A6/A10。"""
    issues: list[str] = []
    if not isinstance(raw, dict):
        return ["顶层不是对象"]
    for key in ("claims", "fills", "unassigned"):
        value = raw.get(key)
        if value is not None and not isinstance(value, list):
            issues.append(f"{key} 不是数组（是 {type(value).__name__}）")
    dsl = raw.get("dsl")
    if dsl is not None:
        if not isinstance(dsl, dict):
            issues.append(f"dsl 不是对象（是 {type(dsl).__name__}）")
        else:
            for name in SECTIONS:
                if name in dsl and not isinstance(dsl[name], list):
                    issues.append(f"dsl.{name} 不是数组")
            unknown = set(dsl) - set(SECTIONS) - {"solver"}
            if unknown:
                issues.append(f"dsl 有未登记的段：{sorted(unknown)}")
    route = raw.get("route")
    if route is not None and route not in ROUTES:
        issues.append(f"route {route!r} 不在 {ROUTES} 之内")
    return issues
