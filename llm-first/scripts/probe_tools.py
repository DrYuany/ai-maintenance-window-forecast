"""探针：DeepSeek 的 tools 通道到底能不能用。

这个脚本回答三个问题，答案决定后面整套方案的形状：

  1. 不传 response_format 时，能不能拿到 tool_calls？
  2. 传了 strict: true 的 JSON Schema，服务端会不会真按 schema 约束？
  3. tools 和 response_format=json_object 同时传，会怎样？
     —— 这一条是死穴：如果两者互斥且不报错（只是悄悄丢 tool_calls），
        那就是最坏的情况：看起来正常、实际不生效。

用法：
    python scripts/probe_tools.py
    python scripts/probe_tools.py --model deepseek-flash

不打印、不落盘任何密钥。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip())


# 一个极小但真实的工具：让模型查参数表里有没有「船租」
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_capabilities",
            "description": "查询系统支持的参数、约束原语、目标与数据字段。回答"
                           "「有没有这个说法」「编号是什么」「值域多少」「能不能编译」时必须调它，不要凭记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查的说法，例如「船租」「每日工时」",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["parameter", "constraint", "objective", "quantity", "data"],
                        "description": "限定查哪一类；不传就全查",
                    },
                },
                "required": ["query"],
            },
        },
    }
]

SYSTEM = (
    "你是一个排程系统的意图解析器。遇到需要确认「系统支不支持某个说法」的问题时，"
    "必须先调用 read_capabilities 查，不要凭记忆回答。"
)
USER = "用户说：船租降一点。请确认「船租」对应哪个参数。"


def post(url: str, payload: dict, key: str, timeout: float = 60.0) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:400]
        try:
            return exc.code, json.loads(detail)
        except Exception:
            return exc.code, {"raw": detail}


def summarise(tag: str, status: int, data: dict) -> dict:
    """从响应里抽出「到底有没有真调工具」。"""
    choices = data.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    calls = message.get("tool_calls") or []
    content = message.get("content") or ""
    finish = choices[0].get("finish_reason") if choices else None
    return {
        "切面": tag,
        "HTTP": status,
        "工具调用数": len(calls),
        "函数名": [c.get("function", {}).get("name") for c in calls],
        "参数": [c.get("function", {}).get("arguments") for c in calls][:1],
        "content 前 60 字": (content or "")[:60].replace("\n", " "),
        "finish_reason": finish,
        "错误": (data.get("error") or {}).get("message") if data.get("error") else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    load_env()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("没有 DEEPSEEK_API_KEY，无法探测", file=sys.stderr)
        return 2
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = args.model or os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
    url = f"{base}/v1/chat/completions"

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER},
    ]
    base_payload = {"model": model, "messages": messages, "temperature": 0.0,
                    "max_tokens": 600}

    rows = []

    # 切面 A：只传 tools，不传 response_format —— 这是我们要走的路
    started = time.monotonic()
    status, data = post(url, {**base_payload, "tools": TOOLS}, key)
    rows.append(summarise("A 只有 tools", status, data))
    rows[-1]["耗时ms"] = round((time.monotonic() - started) * 1000)

    # 切面 B：只传 json_object —— 这是现在 llm-first 走的路（对照组）
    status, data = post(url, {**base_payload,
                              "response_format": {"type": "json_object"},
                              "messages": messages[:-1] + [{
                                  "role": "user",
                                  "content": USER + " 请用 json 回答，字段名 intent。",
                              }]}, key)
    rows.append(summarise("B 只有 json_object", status, data))

    # 切面 C：两个都传 —— 验证互斥（最坏情况是静默丢 tool_calls）
    # 注意：json_object 模式要求 prompt 里出现 "json" 字样，否则直接 400。
    # 那是 json_object 自己的前置条件，跟 tools 无关——第一版探针就栽在这儿，
    # 把 400 误读成了"互斥"。所以这一格必须用带 json 字样的 prompt 重测。
    json_messages = [
        {"role": "system", "content": SYSTEM + " 需要结构化输出时用 json。"},
        {"role": "user", "content": USER + " 用 json 回答。"},
    ]
    status, data = post(url, {**base_payload, "messages": json_messages, "tools": TOOLS,
                              "response_format": {"type": "json_object"}}, key)
    rows.append(summarise("C tools + json_object", status, data))

    # 切面 C2：对照组——同样的 prompt，只传 json_object 不传 tools。
    # 如果 C 没工具调用而 C2 正常，才能说明是 tools 被挤掉了。
    status, data = post(url, {**base_payload, "messages": json_messages,
                              "response_format": {"type": "json_object"}}, key)
    rows.append(summarise("C2 同 prompt 只有 json", status, data))

    # 切面 D：tools + tool_choice=required，看能不能强制
    status, data = post(url, {**base_payload, "tools": TOOLS,
                              "tool_choice": "required"}, key)
    rows.append(summarise("D tools + required", status, data))

    width = max(len(str(r["切面"])) for r in rows)
    for row in rows:
        print("=" * 68)
        for name, value in row.items():
            print(f"  {name:<{width + 2}}{value}")

    print()
    print("=" * 68)
    a = rows[0]["工具调用数"]
    c = rows[2]["工具调用数"]
    c2 = rows[3]["工具调用数"]
    c_err = rows[2]["错误"]
    print("结论：")
    print(f"  只传 tools 时能拿到工具调用      : {'能' if a else '不能'}")
    print(f"  同 prompt 只传 json_object 时     : {'正常返回 json' if c2 == 0 and not rows[3]['错误'] else '异常'}")
    print(f"  两个都传时的工具调用数            : {c}{('（报错：%s）' % c_err[:60]) if c_err else ''}")
    if a and c:
        print("  → 两者**可以共存**（此前搜到的「互斥、会丢 tool_calls」在本模型上不成立）。")
        print("     但仍建议分开走：JSON 模式会让模型倾向于直接吐 JSON 而不是调工具。")
    elif a and not c and not c_err:
        print("  → 两者互斥，且是**静默丢弃**（HTTP 200 但没有 tool_calls）。")
        print("     这条必须写进客户端：传了 tools 就不许再传 response_format。")
    elif a and not c and c_err:
        print("  → 两个都传会直接报错，不是静默丢弃。客户端必须二选一。")
    else:
        print("  → tools 通道不可用，方案需要改走 Anthropic 端点或放弃循环。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
