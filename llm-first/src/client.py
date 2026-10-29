"""DeepSeek 调用：JSON 模式 + 一次重试 + 全量留痕。

用标准库 urllib，不引额外依赖——这个产品要能在一台只装了 ortools 的机器上跑起来。

刻意不做的事：
  - 不做 tool call，不做流式。要的是一次成型的 JSON，流式只会让失败更难定位。
  - 不在客户端里解析业务语义。客户端只负责"把字符串拿回来"，
    解析、归一化、验收都在别处，这样换模型时只需要换这一个文件。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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
    key = api_key()
    if not key:
        raise LLMUnavailable("未配置 DEEPSEEK_API_KEY")

    base = (os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE).rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    url = f"{base}/v1/chat/completions"

    payload: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
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
            text = ""
            if choices:
                text = (choices[0].get("message") or {}).get("content") or ""
            if text.strip():
                return Reply(text=text, latency_ms=int((time.monotonic() - started) * 1000),
                             model=data.get("model", model), usage=data.get("usage") or {},
                             attempts=attempt)
            last = "返回内容为空"
        time.sleep(0.4 * attempt)

    raise LLMUnavailable(f"{retries + 1} 次都没拿到内容：{last}")
