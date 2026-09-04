"""CLI: ``daedalus serve`` / ``python -m app``."""
from __future__ import annotations

import argparse
import os
import sys


def _desktop_ui(args: argparse.Namespace) -> str:
    ui = str(getattr(args, "ui", "auto") or "auto")
    if getattr(args, "browser", False):
        ui = "browser"
    if getattr(args, "gui", False):
        ui = "auto"
    if getattr(args, "no_browser", False) or getattr(args, "no_gui", False):
        if ui == "auto" and not getattr(args, "gui", False) and not getattr(args, "browser", False):
            ui = "none"
    return ui


def _desktop_open_browser(args: argparse.Namespace) -> bool:
    return _desktop_ui(args) != "none"


def main(argv: list[str] | None = None) -> None:
    from app.envload import load_env_file

    load_env_file()
    parser = argparse.ArgumentParser(prog="daedalus", description="Daedalus coding agent")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="Run the standalone HTTP service")
    serve.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Bind address (default 127.0.0.1). Public bind requires DAEDALUS_TOKEN.",
    )
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    serve.add_argument("--reload", action="store_true")

    pub = sub.add_parser("publish", help="Export this product to an empty GitHub/GitLab remote")
    pub.add_argument("remote", help="git remote URL, e.g. git@gitlab.com:Blaue-Ente/Daedalus.git")
    pub.add_argument(
        "--force",
        action="store_true",
        help="Replace a GitLab/GitHub starter README (git push -uf)",
    )

    ping = sub.add_parser("ping", help="Call the configured LLM once (no Studio, no memory)")
    ping.add_argument(
        "--message",
        default="Reply with the single word pong.",
        help="Prompt to send",
    )

    models = sub.add_parser("models", help="Show configured LLM + probe local backends")
    sub.add_parser("computer", help="Show Play Lab / SmolVM computer backends")

    desk = sub.add_parser(
        "desktop",
        help="Start Studio as a desktop window (WebView2 / Tk) plus local server",
    )
    desk.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    desk.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    desk.add_argument(
        "--ui",
        choices=["auto", "webview", "tk", "browser", "none"],
        default="auto",
        help="Desktop surface (default: auto = webview → tk → browser)",
    )
    desk.add_argument("--gui", action="store_true", help="Open a desktop window (default)")
    desk.add_argument("--no-gui", action="store_true", help="Start the server only")
    desk.add_argument(
        "--browser",
        action="store_true",
        help="Open the default browser instead of a window",
    )
    desk.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the server only (same as --no-gui)",
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
    if args.cmd == "models":
        os.environ.setdefault("DAEDALUS_STANDALONE", "1")
        from daedalus.local_llm import probe_all
        from daedalus.llm_config import get_config
        try:
            cfg = get_config()
            print(
                f"effective provider={cfg.get('provider')} model={cfg.get('model')} "
                f"source={cfg.get('source')} local={bool(cfg.get('local'))} "
                f"reachable={cfg.get('reachable', 'n/a')}"
            )
            if cfg.get("warning"):
                print(f"warning: {cfg['warning']}")
        except Exception as exc:
            print(f"effective: {exc}")
        for row in probe_all():
            flag = "up" if row.get("ok") else "down"
            ids = ",".join((row.get("models") or [])[:6]) or "-"
            print(f"  [{flag}] {row.get('provider')} {row.get('base_url')} models={ids}")
            if row.get("error") and not row.get("ok"):
                print(f"         {row['error']}")
        try:
            from daedalus.llm_wardrobe import status as wardrobe_status
            w = wardrobe_status()
            p = w.get("profile") or {}
            print(
                f"machine class={p.get('class')} ram={p.get('ram_gb')}G "
                f"cpu={p.get('cpu_cores')} disk={p.get('disk_free_gb')}G "
                f"gpu={(p.get('gpu') or {}).get('name') or 'none'}"
            )
            print(f"  {p.get('note') or ''}")
            print("recommended:")
            for row in w.get("recommended") or []:
                print(f"  - {row.get('role')}: {row.get('ollama')}  ({row.get('label')})")
            if w.get("wardrobe", {}).get("active"):
                print(f"wardrobe active={w['wardrobe']['active']} backend={w['wardrobe'].get('backend')}")
        except Exception as extra:
            print(f"wardrobe: {extra}")
        return
    if args.cmd == "computer":
        os.environ.setdefault("DAEDALUS_STANDALONE", "1")
        from daedalus.play_lab import probe_host
        from daedalus.computer import live_computers
        host = probe_host()
        print(f"backend={host.get('backend_requested')} local_http={host.get('local_http')}")
        docker = host.get("docker") or {}
        print(
            f"docker ready={docker.get('ready')} image={docker.get('image')} "
            f"present={docker.get('image_present')}"
        )
        smol = host.get("smolvm") or {}
        print(
            f"smolvm ok={smol.get('ok')} import={smol.get('import')} "
            f"bin={smol.get('bin') or '-'} kvm={smol.get('kvm')}"
        )
        if smol.get("note"):
            print(f"  {smol['note']}")
        live = live_computers()
        if not live:
            print("live: none")
        for row in live:
            print(
                f"live {row.get('lab_id')} url={row.get('url') or '-'} "
                f"viewer={row.get('viewer_url') or '-'}"
            )
        return
    if args.cmd == "ping":
        os.environ.setdefault("DAEDALUS_STANDALONE", "1")
        os.environ.setdefault("DAEDALUS_USE_MESH_ROUTER", "0")
        import asyncio
        from daedalus.llm_client import complete
        from daedalus.llm_config import get_config
        from daedalus.local_llm import is_local_provider

        cfg = get_config()
        print(
            f"provider={cfg.get('provider')} model={cfg.get('model')} "
            f"has_key={bool(cfg.get('api_key'))} source={cfg.get('source')} "
            f"local={bool(cfg.get('local'))}"
        )
        if not cfg.get("api_key") and not is_local_provider(str(cfg.get("provider") or "")):
            print("missing API key", file=sys.stderr)
            raise SystemExit(1)
        if cfg.get("warning"):
            print(cfg["warning"], file=sys.stderr)

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
    if args.cmd == "desktop":
        from app.desktop import run_desktop
        raise SystemExit(run_desktop(
            host=getattr(args, "host", None),
            port=getattr(args, "port", None),
            open_browser=_desktop_open_browser(args),
            ui=_desktop_ui(args),
        ))
    if args.cmd in (None, "serve"):
        os.environ.setdefault("DAEDALUS_STANDALONE", "1")
        import uvicorn
        from daedalus.owner_auth import assert_bind_allowed, default_serve_host
        host = getattr(args, "host", default_serve_host())
        port = getattr(args, "port", int(os.environ.get("PORT", "8787")))
        reload = bool(getattr(args, "reload", False))
        os.environ["DAEDALUS_BIND_HOST"] = str(host)
        assert_bind_allowed(str(host))
        uvicorn.run("app.main:app", host=host, port=port, reload=reload)
        return
    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
