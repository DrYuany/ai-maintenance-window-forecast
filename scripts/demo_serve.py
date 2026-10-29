"""演示模式启动器：一个端口同时出前端页面和 API，口令保护。

    python scripts/demo_serve.py
    python scripts/demo_serve.py --code demo2026 --port 8010

没设口令时后端行为和平时完全一样，所以这个脚本也能当普通开发服务器用。
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BUILD_INDEX = ROOT / "build" / "index.html"
ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"


def fail(message: str) -> None:
    print(f"[x] {message}")
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动受口令保护的演示站点")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", default="127.0.0.1", help="默认只听本机，走隧道即可对外")
    parser.add_argument("--code", default="", help="访问口令；省略则读环境变量或自动生成")
    args = parser.parse_args()

    if not BUILD_INDEX.exists():
        fail("找不到 build/index.html，先执行：npm run build")

    try:
        import ortools  # noqa: F401
    except ImportError:
        fail("当前解释器没有 ortools，请用装了 OR-Tools 的 Python 3.12 运行")

    code = args.code.strip() or os.environ.get("DEMO_ACCESS_CODE", "").strip()
    if not code:
        code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    os.environ["DEMO_ACCESS_CODE"] = code

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    import uvicorn

    base = f"http://127.0.0.1:{args.port}"
    print()
    print("  演示站点已就绪")
    print(f"  本机打开   {base}/?k={code}")
    print(f"  访问口令   {code}")
    print(f"  健康检查   {base}/api/health")
    print()
    print("  要给别人看，另开一个终端跑：")
    print(f"      cloudflared tunnel --url {base}")
    print("  把隧道给出的公网地址接上 /?k=口令，就是邀请链接。")
    print()
    print("  换下一批人体验之前，先停服务，再跑：python scripts/demo_reset.py")
    print()

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
