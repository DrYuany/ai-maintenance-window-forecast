"""探针：两轮工具往返能不能真的走通。

`probe_tools.py` 只验证了"能返回 tool_calls"。真正要用的是**往返**：

    模型 → tool_calls → 我们执行 → 把结果塞回 messages → 模型 → 最终 JSON

这一圈里有三个地方最容易不通：
  1. assistant 消息带 tool_calls 时，content 该传空串还是 None
  2. tool 消息的 tool_call_id 必须逐字对上，对不上就被 400
  3. 塞完结果之后，模型会不会真的用它（而不是继续瞎猜）

第 3 条最重要：前两条不通会报错，能看见；第 3 条不通是**静默失效**——
模型拿到了参数表还是编一个编号出来，表面一切正常。

用法：
    python scripts/probe_loop.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import client  # noqa: E402
import tools  # noqa: E402

SYSTEM = """你是排程系统的意图解析器。把用户的话落成 DSL。

需要确认某个说法对应哪个参数时，**必须调用 read_capabilities**，不要凭记忆。

最终输出必须是 JSON，形如：
{"route": "dsl", "objectives": [], "constraints": [], "parameters": [], "note": ""}
"""

# 这句的关键：「船租」和表里的「船舶日租金」字面不同，
# 不查就一定写错编号（P14 才对）。这是检验"工具结果有没有被用上"的试金石。
USER = "把船租降到8万"


def main() -> int:
    specs = tools.tool_specs()
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER},
    ]

    print("=" * 70)
    print("第 1 轮：带工具，看它会不会主动查")
    reply = client.chat_messages(messages, tools=specs, tool_choice="auto")
    print(f"  wants_tools = {reply.wants_tools}")
    print(f"  content     = {reply.text[:80]!r}")
    calls = reply.call_list()
    print(f"  调用        = {calls}")

    if not reply.wants_tools:
        print()
        print("  ** 模型没调工具。要么它已经知道，要么它打算瞎猜。")
        print("     下面用同样的提示词再问一次，但强制 required 试试：")
        reply = client.chat_messages(messages, tools=specs, tool_choice="required")
        calls = reply.call_list()
        print(f"     强制后调用 = {calls}")
        if not reply.wants_tools:
            print("     ** required 也没用，工具通道走不通")
            return 1

    print()
    print("=" * 70)
    print("第 2 轮：执行工具，把结果塞回去")

    # OpenAI 格式：assistant 带 tool_calls 时 content 用空串最稳（None 有的实现会拒）
    messages.append({
        "role": "assistant",
        "content": reply.text or "",
        "tool_calls": reply.tool_calls,
    })

    for call in reply.tool_calls:
        name = (call.get("function") or {}).get("name") or ""
        args = next((a for n, a in reply.call_list() if n == name), {})
        result = tools.execute(name, args)
        body = json.dumps(result, ensure_ascii=False)
        print(f"  执行 {name}({args})")
        print(f"    -> {body[:150]}...  （{len(body)} 字符）")
        messages.append({
            "role": "tool",
            "tool_call_id": call.get("id"),
            "content": body,
        })

    print()
    print("=" * 70)
    print("第 3 轮：要最终 JSON（不带工具）")
    messages.append({"role": "user", "content": "现在给出最终 JSON。"})
    final = client.chat_messages(messages, json_mode=True)
    print(f"  {final.text[:400]}")

    print()
    print("=" * 70)
    print("判定：")
    ok_json = False
    try:
        parsed = json.loads(final.text)
        ok_json = True
    except Exception:
        parsed = {}
    print(f"  最终输出是合法 JSON          : {'是' if ok_json else '否'}")
    text_all = final.text
    print(f"  用上了「船舶日租金」这个正式名 : {'是' if '船舶日租金' in text_all else '否'}")
    print(f"  写的是 P14 还是费用01         : "
          f"{'P14' if 'P14' in text_all else ('费用01' if '费用01' in text_all else '都不是')}")
    if ok_json:
        print(f"  parameters = {parsed.get('parameters')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
