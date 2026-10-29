"""LLM 主导版服务（端口 8011）。

只做三件事：把一句话读成产物、把产物送进验收、把两边并排摆出来。
求解与执行侧共用 `backend/`，一行没改——否则两边执行侧不同，
对比出来的差异就说不清是谁造成的。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import client  # noqa: E402
import pipeline  # noqa: E402
import prompt  # noqa: E402
import rulebench  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI(title="LLM 主导版 · 意图理解", version="0.1.0")

WEB = ROOT / "web"

EXAMPLES = [
    "把船租降到8万，每天最多干10小时",
    "任务001和任务002不要同一天做，钱不超过300万",
    "把船租降到8万，为什么这么贵",
    "经济优先和风险优先差多少",
    "成本压到最低，但高风险的必须全修",
    "只排风机003的任务",
    "人工单价涨到200，任务003最晚11月9日修完",
    "船租翻倍了",
]


class Ask(BaseModel):
    utterance: str
    allow_repair: bool = True


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "llm_ready": client.configured(),
        "model": __import__("os").environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "prompt_chars": len(prompt.build()),
        "examples": EXAMPLES,
    }


@app.post("/api/understand")
def understand(ask: Ask) -> JSONResponse:
    outcome = pipeline.understand(ask.utterance, allow_repair=ask.allow_repair)
    return JSONResponse(outcome.as_dict())


@app.post("/api/understand-rule")
def understand_rule(ask: Ask) -> JSONResponse:
    outcome = rulebench.understand_rule(ask.utterance)
    return JSONResponse(outcome.as_dict())


@app.post("/api/compare")
def compare(ask: Ask) -> JSONResponse:
    """一次把两边都算完。左右两栏各自也能单独调，这里只是省一次往返。"""
    rule = rulebench.understand_rule(ask.utterance)
    llm = pipeline.understand(ask.utterance, allow_repair=ask.allow_repair)
    return JSONResponse({
        "utterance": ask.utterance,
        "rule": rule.as_dict(),
        "llm": llm.as_dict(),
    })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8011, log_level="warning")
