import json

import httpx
from fastapi import HTTPException

from ..config import get_settings


def translate(transcript: str, summary: dict, recent_turns: list[dict]) -> tuple[str, dict, int]:
    settings = get_settings()
    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="尚未配置 LLM_API_KEY")
    system = "You translate the user's Chinese monologue into natural spoken English. Do not answer, advise, or add information. Preserve intent, uncertainty, names, and tone. Use conversation context only to resolve references and keep terminology consistent. Return only JSON with keys translation and summary; summary must contain topic, entities_and_glossary, translation_preferences, unresolved_references."
    context = {"summary": summary, "recent_turns": recent_turns[-6:], "current_chinese_monologue": transcript}
    try:
        response = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={"model": settings.llm_model, "temperature": 0.2, "max_tokens": settings.llm_max_output_tokens, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]},
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        decoded = json.loads(content)
        translation = str(decoded.get("translation", "")).strip()
        updated_summary = decoded.get("summary") or summary
        usage = payload.get("usage", {})
        tokens = int(usage.get("total_tokens") or usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
        if not translation:
            raise ValueError("empty translation")
        return translation, updated_summary, tokens
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"大模型服务返回错误：{exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="大模型翻译失败，请稍后重试") from exc
