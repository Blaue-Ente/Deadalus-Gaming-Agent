"""``python -m daedalus.harness.play_worker`` — serve the play API on 127.0.0.1 (token from DAEDALUS_PLAY_TOKEN)."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="daedalus.harness.play_worker")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port and print it")
    ap.add_argument("--idle-reap", type=float, default=60.0, help="seconds between idle-session sweeps")
    args = ap.parse_args(argv)
    token = (os.environ.get("DAEDALUS_PLAY_TOKEN") or "").strip()   # never via argv: it would show in `ps`
    if len(token) < 16:
        sys.stderr.write("DAEDALUS_PLAY_TOKEN (>=16 chars) is required\n")
        return 2
    import uvicorn

    from .manager import PlayManager
    from .server import create_app

    mgr = PlayManager()
    app = create_app(token, mgr)
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")  # loopback only, always
    server = uvicorn.Server(config)

    def reaper() -> None:
        while not server.should_exit:
            threading.Event().wait(args.idle_reap)
            try:
                mgr.reap_idle()
            except Exception:
                pass

    threading.Thread(target=reaper, daemon=True).start()

    def term(*_a) -> None:        # a stopped worker leaves no game or display behind
        server.should_exit = True

    signal.signal(signal.SIGTERM, term)
    signal.signal(signal.SIGINT, term)
    import socket

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]

    def announce() -> None:      # only once the server really accepts connections
        import time
        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.started:
            sys.stdout.write(f"PLAY_WORKER_READY {port}\n")
            sys.stdout.flush()

    threading.Thread(target=announce, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        mgr.stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
