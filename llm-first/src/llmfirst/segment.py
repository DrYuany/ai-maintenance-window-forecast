"""系统切段：A1「覆盖」的分母。

为什么单独一个模块：分母必须由系统算，不能由模型报。
模型说"我切了三条"，那三条就是它自己划的范围——漏掉的东西永远不会出现在分母里。
所以系统按标点和连接词独立切一遍，模型交的 claims 必须覆盖它切出的每一段。

这个表也是提示词里那张"连接词含：…"的唯一来源。两边共用一份，
否则提示词说七个、切段只认五个，A1 就成了随机判据。
"""
from __future__ import annotations

import re

# 连接词：命中就当作断句点。只收不会引起歧义的。
#
# 长词在前，短词在后——"但是"要先被替换掉，否则只替换"但"会留下"是"。
#
# 故意不收"再"：它在"再修任务002"里是连接词，在"再算一遍"里是副词，
# 切错了会把一句话劈成两段，反而制造假警报。宁可少切——
# 少切只是让分母变小（更容易通过），不会冤枉模型。
CONNECTIVES: tuple[str, ...] = (
    "但是", "不过", "而且", "然后", "于是",
    "并且", "同时", "顺便", "另外", "还要", "以及",
    "但",
)

# 段落首尾的虚词残留。切完"但高风险的必须全修"，模型抄 claim 时通常会去掉"但"，
# 于是 A1 会因为一个字判它漏话——这是假警报。首尾各剥一层再比。
_LEAD = "但而且并又还则就那嗯好但"
_TAIL = "吧呢啊呀嘛哦"

# 断句标点
BREAKS = "，,。；;！!？?、\n\r\t"

# 比较用的归一化：去掉空白、标点、引号括号，留下实字
_DROP = set(" \t\n\r\u3000，,。；;！!？?、：:“”\"'‘’（）()【】[]《》<>…—－-·")


def norm(text: str) -> str:
    """归一化成"只剩实字"的形式，用于逐字比对。"""
    return "".join(ch for ch in (text or "") if ch not in _DROP)


def segments(text: str) -> list[str]:
    """按标点 + 连接词切段，返回归一化后非空的片段（保留原文形态）。"""
    work = text or ""
    for word in CONNECTIVES:
        work = work.replace(word, "，")
    parts = re.split("[" + re.escape(BREAKS) + "]", work)

    out: list[str] = []
    for part in parts:
        piece = part.strip().lstrip(_LEAD).rstrip(_TAIL).strip()
        if not norm(piece):
            continue
        # 太短的残片（“和”“的”）并回上一段，避免一个虚词单独成段
        if out and len(norm(piece)) <= 1:
            out[-1] = out[-1] + piece
            continue
        out.append(piece)
    return out


def connective_phrase() -> str:
    """给提示词用的一句话，保证与切段逻辑同源。"""
    return "、".join(CONNECTIVES)
