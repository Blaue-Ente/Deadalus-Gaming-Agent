"""HTTP and WebSocket reverse-proxy helpers for Playground preview."""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import unquote

from fastapi import Request, WebSocket
from fastapi.responses import HTMLResponse, Response

from .playground_runtime import PlaygroundRuntime

log = logging.getLogger(__name__)

_PROXY_DROP_HEADERS = frozenset({
    "host", "connection", "cookie", "authorization",
    "x-forwarded-for", "x-replit-user-id", "x-replit-user-name",
})
_PROXY_STRIP_RESPONSE = frozenset({
    "content-encoding", "transfer-encoding", "content-length", "connection",
})


def rewrite_preview_paths(text: str, proxy_base: str, *, kind: str = "") -> str:
    """Prefix bare-root URLs so assets resolve through the preview proxy."""
    base = proxy_base.rstrip("/")
    for needle in ('src="/@', "src='/@", 'href="/@', "href='/@",
                   'from "/@', "from '/@", '"/@vite/', "'/@vite/",
                   '"/@react-refresh', "'/@react-refresh"):
        text = text.replace(needle, needle.replace("/@", f"{base}/@", 1))

    rewrite_kinds = ("expo", "vite", "vite-react", "node", "streamlit")
    if kind in rewrite_kinds:
        text = re.sub(
            r'(\b(?:src|href|action)=(["\']))/(?!/|#)',
            rf"\1{base}/",
            text,
        )
        text = re.sub(
            r'(\bfrom\s+)(["\'])/(?!/)',
            rf"\1\2{base}/",
            text,
        )

    if kind == "expo":
        text = re.sub(
            r'(["\'])/(?!/)(index\.bundle|assets/|node_modules/|_expo/|static/)',
            rf"\1{base}/\2",
            text,
        )
        text = re.sub(
            r'url\((["\']?)/(?!/)(assets/|_expo/|static/)',
            rf"url(\1{base}/\2",
            text,
        )

    if kind == "streamlit":
        # Streamlit core websocket + static assets
        text = re.sub(
            r'(["\'])/(?!/)(_stcore/|static/)',
            rf"\1{base}/",
            text,
        )

    return text


def _should_rewrite_content(content_type: str, kind: str) -> bool:
    if "text/html" in content_type:
        return True
    if kind == "expo" and "javascript" in content_type:
        return True
    if kind == "streamlit" and ("javascript" in content_type or "json" in content_type):
        return True
    return False


async def proxy_http(
    port: int,
    path: str,
    request: Request,
    slug: str = "",
    *,
    kind: str = "",
    proxy_base: str = "",
) -> Response:
    """Reverse-proxy HTTP to a local dev-runner process."""
    import httpx

    decoded_path = unquote(path)
    url = f"http://127.0.0.1:{port}/{decoded_path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _PROXY_DROP_HEADERS
    }
    if not proxy_base and slug:
        proxy_base = PlaygroundRuntime.admin_preview_base(slug)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as cx:
            body = await request.body()
            resp = await cx.request(request.method, url, headers=headers, content=body)
        out_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in _PROXY_STRIP_RESPONSE
        }
        content = resp.content
        content_type = resp.headers.get("content-type", "")
        if slug and proxy_base and _should_rewrite_content(content_type, kind):
            try:
                text = content.decode("utf-8", errors="replace")
                text = rewrite_preview_paths(text, proxy_base, kind=kind)
                content = text.encode("utf-8")
            except Exception:
                pass
        return Response(
            content=content,
            status_code=resp.status_code,
            headers=out_headers,
            media_type=content_type or resp.headers.get("content-type"),
        )
    except Exception as exc:
        return HTMLResponse(
            f"<h1>502</h1><p>Runner proxy error: {exc}</p>",
            status_code=502,
        )


async def proxy_websocket(
    client_ws: WebSocket,
    port: int,
    path: str,
) -> None:
    """Bidirectional WebSocket relay to a local dev-runner (Streamlit _stcore)."""
    import aiohttp

    decoded = unquote(path or "").lstrip("/")
    backend_url = f"ws://127.0.0.1:{port}/{decoded}"
    await client_ws.accept()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(backend_url, heartbeat=20.0) as backend_ws:

                async def client_to_backend() -> None:
                    try:
                        while True:
                            msg = await client_ws.receive()
                            if msg["type"] == "websocket.disconnect":
                                break
                            text = msg.get("text")
                            data = msg.get("bytes")
                            if text is not None:
                                await backend_ws.send_str(text)
                            elif data is not None:
                                await backend_ws.send_bytes(data)
                    except Exception:
                        pass

                async def backend_to_client() -> None:
                    try:
                        async for msg in backend_ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await client_ws.send_text(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await client_ws.send_bytes(msg.data)
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
                    except Exception:
                        pass

                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(client_to_backend()),
                        asyncio.create_task(backend_to_client()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    try:
                        await task
                    except Exception:
                        pass
    except Exception as exc:
        log.debug("playground ws proxy closed: %s", exc)
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass


# Backward-compatible aliases used by playground_router / admin.
_rewrite_preview_paths = rewrite_preview_paths


async def _proxy(
    port: int,
    path: str,
    request: Request,
    slug: str = "",
    *,
    kind: str = "",
    proxy_base: str = "",
) -> Response:
    return await proxy_http(
        port, path, request, slug, kind=kind, proxy_base=proxy_base,
    )
