"""LLM 解析器（第二阶段，占位）。

第一阶段（当前）用手写 DSL JSON 验证"注册表 → 校验 → 编译 → 求解"的确定性链路。
第二阶段在此接 LLM：自然语言 → DSL JSON。

接口契约（将来实现时遵守）：
    parse(message: str, context: dict) -> dict
        context 含会话方案、选中版本、能力目录；返回 DSL JSON 草案。

风险提示（设计文档 §7.1）：LLM 解析是非确定性的，解析结果必须落库进证据链；
"可复现"承诺从"自然语言 → 模型"降级为"DSL 契约 → 模型"。
"""
from __future__ import annotations

from typing import Any


def parse(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    raise NotImplementedError(
        "LLM 解析是第二阶段。第一阶段请手写 DSL JSON 喂给 runner.run()。"
    )
