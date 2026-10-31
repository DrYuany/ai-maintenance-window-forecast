"""规则盘点：把"我们到底有多少规则、分几层"算成可复跑的数字。

背景：讨论"要不要把规则改成文档注入"时，得先回答两个更前置的问题——
  1. 规则现在长什么样、有多少、在哪些文件里
  2. 哪些层是"文档"（能搬的），哪些层是"代码"（搬不走的）

跑法：
    cd llm-first && python scripts/rules_census.py

改完规则重跑，看哪一层涨了——涨得最快的那层，就是后来者觉得最顺手的地方。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]  # 仓库根
SRC = pathlib.Path(__file__).resolve().parents[1]  # llm-first
sys.path.insert(0, str(SRC / "src"))

import paths  # noqa: E402,F401  （副作用：把 ../backend 挂进 sys.path）

from app.core.dsl.registry import parameters as param_reg  # noqa: E402
from app.core.dsl.registry import primitives as prim_reg  # noqa: E402
from app.core.dsl.registry import quantities as qty_reg  # noqa: E402


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def layer_front() -> list[tuple[str, str]]:
    """L1：写在 Python 里的硬规则。模型看不见，先于模型执行。"""
    import segment
    guard = _text("llm-first/src/guardrail.py")
    ctx = _text("llm-first/src/context.py")
    return [
        ("护栏正则", f"{len(re.findall(r're\.compile', guard))} 条"),
        ("相对时间换算",
         f"{len(re.findall(r'[\"今明后昨前大][^\"]{0,2}\"\s*:\s*-?\d', ctx, re.M))} 项"),
        ("切句连接词", f"{len(segment.CONNECTIVES)} 个"),
    ]


def layer_registry() -> list[tuple[str, str]]:
    """L2：CSV / 注册表驱动。本来就是数据文件，不是规则代码。"""
    import context as session  # noqa: F401  （保证 products 侧路径一致）
    params = sum(1 for r in param_reg._rows()
                 if param_reg.route_of(r["参数名称"]) == "parameters")
    return [
        ("表 A 参数", f"{params} 项"),
        ("表 C 约束原语", f"{len(prim_reg.CONSTRAINT_PRIMITIVES)} 登记 / "
                          f"{len(prim_reg.COMPILED_CONSTRAINTS)} 能编译"),
        ("表 D 目标原语", f"{len(prim_reg.OBJECTIVE_PRIMITIVES)} 登记 / "
                          f"{len(prim_reg.COMPILED_OBJECTIVES)} 能编译"),
        ("表 F 具名量", f"{len(qty_reg.NAMED_QUANTITIES)} 个 / coeff "
                        f"{len(qty_reg.COEFFS)} / filter {len(qty_reg.FILTERS)}"),
    ]


def layer_prompt() -> tuple[list[tuple[str, str]], int]:
    """L3：提示词里的自然语言规则。改文档注入只能动这一层。"""
    import context as session
    import prompt
    ctx = session.with_params_for(session.default())
    rows = [(name, f"{size} 字符") for name, size in prompt.stats(ctx)]
    return rows, len(prompt.build(ctx))


def layer_machine() -> tuple[list[tuple[str, str]], int]:
    """L4+L5：真正的裁判。是代码，搬不成文档。"""
    verify_lines = _text("llm-first/src/verify.py").splitlines()
    validator = _text("backend/app/core/dsl/validator.py")
    compiler = _text("backend/app/core/dsl/compiler.py")
    rows = [
        ("verify.py 判据", f"{len(verify_lines)} 行"),
        ("validator.py", f"{len(validator.splitlines())} 行 / "
                         f"{len(re.findall('errors.append', validator))} 处判错"),
        ("compiler.py", f"{len(compiler.splitlines())} 行 / "
                        f"{len(re.findall('errors.append', compiler))} 处报错"),
    ]
    return rows, len(verify_lines) + len(validator.splitlines()) + len(compiler.splitlines())


def main() -> int:
    print("=== L1 硬规则（Python，模型看不见，先于模型跑） ===")
    for name, value in layer_front():
        print(f"  {name:<16} {value}")

    print("\n=== L2 注册表（CSV 驱动，本来就是数据） ===")
    for name, value in layer_registry():
        print(f"  {name:<16} {value}")

    print("\n=== L3 提示词里的自然语言规则（唯一能改成文档注入的一层） ===")
    rows, prompt_chars = layer_prompt()
    for name, value in rows:
        print(f"  {value:>10}  {name}")
    print(f"  {prompt_chars:>10}  合计")

    print("\n=== L4+L5 机器裁判（代码，搬不走） ===")
    rows, code_lines = layer_machine()
    for name, value in rows:
        print(f"  {name:<16} {value}")

    parser = _text("backend/app/core/dsl/llm_parser.py")
    print("\n=== 参照：规则主导版 llm_parser.py ===")
    print(f"  正则表           {len(parser.splitlines())} 行 / "
          f"{len(re.findall(r'^\s*\(rf?\"', parser, re.M))} 条")

    print(f"\n提示词 {prompt_chars} 字符  vs  机器裁判 {code_lines} 行代码")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
