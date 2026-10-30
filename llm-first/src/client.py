"""DeepSeek 调用：JSON 模式 + 工具调用 + 一次重试 + 全量留痕。

用标准库 urllib，不引额外依赖——这个产品要能在一台只装了 ortools 的机器上跑起来。

两条通路，按需选：
  - 单趟：`chat(...)`，要一次成型的 JSON。不带工具，改参数类请求走这条。
  - 循环：`chat_messages(...)`，带 `tools`，模型可以调工具、拿结果、再决定下一步。

刻意不做的事：
  - 不做流式。要的是一次成型的结构，流式只会让失败更难定位。
  - 不在客户端里解析业务语义。客户端只负责"把字符串拿回来"，
    解析、归一化、验收都在别处，这样换模型时只需要换这一个文件。

踩过的坑（别踩回去）：`response_format={"type":"json_object"}` 要求 prompt 里
出现 "json" 字样，否则直接 HTTP 400。那跟 tools 无关，是 json_object 自己的
前置条件。实测本模型上 tools 与 json_object **可以共存**（见 scripts/probe_tools.py），
但 json_object 模式下模型的 content 会被压成空——它不说话了，直接调工具。
所以工具轮一律不开 json_mode，终轮才开。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def _load_dotenv() -> None:
    """读 llm-first/.env（若存在）。只为本地跑起来方便——
    key 不进仓库，.gitignore 已经挡了。"""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and value and not os.environ.get(name):
            os.environ[name] = value


_load_dotenv()


class LLMUnavailable(RuntimeError):
    """没有 key、网络不通、或两次都拿不到合法 JSON。"""


@dataclass
class Reply:
    text: str
    latency_ms: int
    model: str
    usage: dict
    attempts: int
    # 模型这一轮打算调的工具。每项形如
    #   {"id": "call_x", "type": "function", "function": {"name": ..., "arguments": "..."}}
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = ""

    @property
    def wants_tools(self) -> bool:
        """这一轮是「要调工具」还是「说完了」。

        唯一的判据是 tool_calls 非空——不看 finish_reason。
        `finish_reason == "stop"` 与非空 tool_calls 同时出现是见过的，
        以 tool_calls 为准（这点跟 Anthropic 那边一致）。
        """
        return bool(self.tool_calls)

    def call_list(self) -> list[tuple[str, dict]]:
        """把 tool_calls 拆成 (工具名, 参数字典)。

        `arguments` 是 JSON 字符串，可能是坏的（模型偶尔吐半截）。
        解析不了就给空字典——**不要抛**：让工具那边按"必填参数缺失"报
        invalid_arguments，模型看得懂，还能自己改一次。
        """
        out: list[tuple[str, dict]] = []
        for call in self.tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            raw = fn.get("arguments")
            if isinstance(raw, dict):
                args = raw
            else:
                try:
                    args = json.loads(raw) if raw else {}
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
            if name:
                out.append((name, args))
        return out


def api_key() -> str:
    return (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def _post(url: str, payload: dict, key: str, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def chat(system: str, user: str, *, temperature: float = 0.0,
         max_tokens: int = 2000, timeout: float = 90.0,
         json_mode: bool = True, retries: int = 1) -> Reply:
    """单趟调用：不给工具，要一次成型的 JSON。行为与改造前完全一致。"""
    return chat_messages(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        json_mode=json_mode, retries=retries,
    )


def chat_messages(messages: list[dict], *, tools: list[dict] | None = None,
                  tool_choice: str | None = None,
                  temperature: float = 0.0, max_tokens: int = 2000,
                  timeout: float = 90.0, json_mode: bool = False,
                  retries: int = 1) -> Reply:
    """完整 messages 调用，可带工具。工具循环每一轮都走这里。

    `tools` 非空时**强制关掉 json_mode**：实测两者能共存，但 json_object 模式下
    模型的 content 被压成空，它不解释、直接调工具。修复轮次需要它「看着结果说
    自己改了哪儿」，所以工具轮必须让它说话。终轮想要结构，调用方显式传
    `json_mode=True` 且不传 tools 即可。

    返回空 content 且无工具调用，才判失败重试——**只看 content 会把正常的
    工具调用误判成失败**（工具轮 content 常常就是空的）。
    """
    key = api_key()
    if not key:
        raise LLMUnavailable("未配置 DEEPSEEK_API_KEY")

    base = (os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE).rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    url = f"{base}/v1/chat/completions"

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
    elif json_mode:
        payload["response_format"] = {"type": "json_object"}

    last = ""
    started = time.monotonic()
    for attempt in range(1, retries + 2):
        try:
            data = _post(url, payload, key, timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            last = f"HTTP {exc.code}: {detail}"
            if exc.code in (401, 403):
                raise LLMUnavailable(f"鉴权失败：{last}") from exc
        except Exception as exc:  # 网络超时、DNS、连接被重置
            last = f"{type(exc).__name__}: {exc}"
        else:
            choices = data.get("choices") or []
            message = (choices[0].get("message") or {}) if choices else {}
            text = message.get("content") or ""
            calls = message.get("tool_calls") or []
            finish = (choices[0].get("finish_reason") or "") if choices else ""
            if text.strip() or calls:
                return Reply(
                    text=text,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    model=data.get("model", model),
                    usage=data.get("usage") or {},
                    attempts=attempt,
                    tool_calls=list(calls),
                    finish_reason=finish,
                )
            last = "返回内容为空，也没有工具调用"
        time.sleep(0.4 * attempt)

    raise LLMUnavailable(f"{retries + 1} 次都没拿到内容：{last}")
