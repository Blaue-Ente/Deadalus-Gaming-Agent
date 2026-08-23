"""CLI: ``daedalus serve`` / ``python -m app``."""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    from app.envload import load_env_file

    load_env_file()
    parser = argparse.ArgumentParser(prog="daedalus", description="Daedalus coding agent")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="Run the standalone HTTP service")
    serve.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    serve.add_argument("--reload", action="store_true")

    pub = sub.add_parser("publish", help="Export this product to an empty GitHub/GitLab remote")
    pub.add_argument("remote", help="git remote URL, e.g. git@gitlab.com:Blaue-Ente/Daedalus.git")
    pub.add_argument(
        "--force",
        action="store_true",
        help="Replace a GitLab/GitHub starter README (git push -uf)",
    )

    ping = sub.add_parser("ping", help="Call the configured LLM once (no Chroma / no ADN)")
    ping.add_argument(
        "--message",
        default="Reply with the single word pong.",
        help="Prompt to send",
    )

    args = parser.parse_args(argv)
    if args.cmd == "publish":
        import subprocess
        from pathlib import Path
        script = Path(__file__).resolve().parent.parent / "scripts" / "publish-standalone-repo.sh"
        cmd = [str(script), args.remote]
        if getattr(args, "force", False):
            cmd.append("--force")
        raise SystemExit(subprocess.call(cmd))
    if args.cmd == "ping":
        os.environ.setdefault("DAEDALUS_STANDALONE", "1")
        os.environ.setdefault("DAEDALUS_USE_MESH_ROUTER", "0")
        import asyncio
        from daedalus.llm_client import complete
        from daedalus.llm_config import get_config

        cfg = get_config()
        print(
            f"provider={cfg.get('provider')} model={cfg.get('model')} "
            f"has_key={bool(cfg.get('api_key'))} source={cfg.get('source')}"
        )
        if not cfg.get("api_key"):
            print("missing API key", file=sys.stderr)
            raise SystemExit(1)

        async def _go() -> dict:
            return await complete(
                user=args.message,
                max_tokens=64,
                temperature=0,
                client_id="daedalus/ping",
            )

        out = asyncio.run(_go())
        print(out.get("reply") or "")
        return
    if args.cmd in (None, "serve"):
        os.environ.setdefault("DAEDALUS_STANDALONE", "1")
        import uvicorn
        host = getattr(args, "host", os.environ.get("HOST", "0.0.0.0"))
        port = getattr(args, "port", int(os.environ.get("PORT", "8787")))
        reload = bool(getattr(args, "reload", False))
        uvicorn.run("app.main:app", host=host, port=port, reload=reload)
        return
    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
