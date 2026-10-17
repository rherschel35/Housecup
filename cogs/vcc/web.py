"""aiohttp table for Velmora Curse & Crown.

Runs on the bot's event loop and binds ``0.0.0.0:$PORT`` (8080 if PORT
is unset). Tokens travel in the URL path. Responses send
``Referrer-Policy: no-referrer`` so the token is not leaked to font hosts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

from aiohttp import web

from cogs.vcc.book import Book

log = logging.getLogger("velmora.vcc.web")

ROOT = Path(__file__).resolve().parent
ART_DIR = ROOT / "art"
PAGE_PATH = ROOT / "page.html"
STATIC_DIR = ROOT / "static"
ART_NAME = re.compile(r"^[a-z0-9-]+\.jpg$")


class Hub:
    """Websockets keyed by token hash. The hash is not the raw token."""

    def __init__(self) -> None:
        self.subs: dict[str, set] = {}

    def add(self, token_hash: str, socket) -> None:
        self.subs.setdefault(token_hash, set()).add(socket)

    def discard(self, token_hash: str, socket) -> None:
        group = self.subs.get(token_hash)
        if not group:
            return
        group.discard(socket)
        if not group:
            self.subs.pop(token_hash, None)

    async def push(self, pairs: list[tuple[str, dict]]) -> None:
        for token_hash, payload in pairs:
            dead = []
            for socket in list(self.subs.get(token_hash, ())):
                try:
                    await socket.send_json(payload)
                except Exception:
                    dead.append(socket)
            for socket in dead:
                self.discard(token_hash, socket)

    async def close(self, token_hashes: list[str]) -> None:
        for token_hash in token_hashes:
            for socket in list(self.subs.get(token_hash, ())):
                try:
                    await socket.close()
                except Exception:
                    pass
            self.subs.pop(token_hash, None)


def create_app(book: Book, hub: Hub, lock: asyncio.Lock, on_end=None, *, bot=None) -> web.Application:
    app = web.Application()
    app["book"] = book
    app["hub"] = hub
    app["lock"] = lock
    app["on_end"] = on_end
    app["bot"] = bot
    app.router.add_get("/", home)
    app.router.add_get("/health", health)
    app.router.add_get("/play/{token}", play_page)
    app.router.add_get("/api/{token}/state", state)
    app.router.add_post("/api/{token}/play", act_play)
    app.router.add_post("/api/{token}/end", act_end)
    app.router.add_post("/api/{token}/discard", act_discard)
    app.router.add_post("/api/{token}/choose", act_choose)
    app.router.add_post("/api/{token}/forfeit", act_forfeit)
    app.router.add_get("/ws/{token}", websocket)
    app.router.add_get("/art/{name}", art)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    # Read-only website routes. A failure here must not take the table down.
    try:
        from cogs.web_api import mount_readonly_api

        mount_readonly_api(app)
    except Exception:
        log.exception("Read-only website API was not mounted")
    app.middlewares.append(privacy_headers)
    return app


@web.middleware
async def privacy_headers(request: web.Request, handler):
    response = await handler(request)
    if isinstance(response, web.WebSocketResponse):
        return response
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not request.path.startswith("/art/"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def home(_request: web.Request) -> web.Response:
    body = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Velmora Curse &amp; Crown</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #100c1c; color: #f3ead4; font: 18px/1.5 Georgia, serif; }
  main { max-width: 34rem; padding: 2rem; text-align: center; }
  h1 { font-weight: 600; letter-spacing: 0.12em; color: #e6c878; font-size: 1.4rem; }
  p { color: #cbbfa6; }
</style></head>
<body><main>
  <h1>✦ VELMORA CURSE &amp; CROWN ✦</h1>
  <p>Tables are dealt in Discord. Challenge someone with <strong>/vcc challenge</strong>
  and open the private link the bot hands you. That link is yours alone.</p>
</main></body></html>"""
    return web.Response(text=body, content_type="text/html")


async def play_page(request: web.Request) -> web.Response:
    # The token stays in the path. The page reads it from location and
    # never puts it on another origin (referrer policy is no-referrer).
    if not PAGE_PATH.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(PAGE_PATH)


async def art(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not ART_NAME.match(name):
        raise web.HTTPNotFound()
    path = ART_DIR / name
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def state(request: web.Request) -> web.Response:
    book: Book = request.app["book"]
    async with request.app["lock"]:
        action = book.view_action(request.match_info["token"], now=time.time())
    return _json(action)


async def act_play(request: web.Request) -> web.Response:
    body = await _body(request)
    card_id = str(body.get("card") or "")
    return await _mutate(request, lambda book, token, now: book.play(token, card_id, now=now))


async def act_end(request: web.Request) -> web.Response:
    return await _mutate(request, lambda book, token, now: book.end_turn(token, now=now))


async def act_discard(request: web.Request) -> web.Response:
    body = await _body(request)
    cards = body.get("cards") or []
    if not isinstance(cards, list):
        return web.json_response({"error": "Name the cards to discard."}, status=400)
    ids = [str(c) for c in cards]
    return await _mutate(request, lambda book, token, now: book.discard(token, ids, now=now))


async def act_choose(request: web.Request) -> web.Response:
    body = await _body(request)
    keep = str(body.get("keep") or "")
    return await _mutate(request, lambda book, token, now: book.choose(token, keep, now=now))


async def act_forfeit(request: web.Request) -> web.Response:
    return await _mutate(request, lambda book, token, now: book.forfeit(token, now=now))


async def websocket(request: web.Request) -> web.WebSocketResponse:
    socket = web.WebSocketResponse(heartbeat=20)
    await socket.prepare(request)
    book: Book = request.app["book"]
    hub: Hub = request.app["hub"]
    raw = request.match_info["token"]
    from cogs.vcc.book import digest as token_digest

    token_hash = token_digest(raw)
    async with request.app["lock"]:
        action = book.view_action(raw, now=time.time())
    if action.status == 404:
        await socket.send_json({"error": action.error or "This table link doesn't work."})
        await socket.close()
        return socket
    hub.add(token_hash, socket)
    try:
        if action.view is not None:
            await socket.send_json(action.view)
        async for _msg in socket:
            # The page doesn't send moves on the socket. Receiving (and
            # ignoring) keeps proxies from treating the connection as idle.
            pass
    finally:
        hub.discard(token_hash, socket)
    return socket


async def _mutate(request: web.Request, fn) -> web.Response:
    book: Book = request.app["book"]
    hub: Hub = request.app["hub"]
    raw = request.match_info["token"]
    async with request.app["lock"]:
        action = fn(book, raw, time.time())
    if action.pushes:
        await hub.push(action.pushes)
    if action.just_ended:
        on_end = request.app.get("on_end")
        if on_end is not None and action.game_id:
            task = asyncio.create_task(_safe_end(on_end, action.game_id))
            request.app.setdefault("tasks", set()).add(task)
            task.add_done_callback(request.app["tasks"].discard)
    return _json(action)


async def _safe_end(on_end, game_id: str) -> None:
    try:
        await on_end(game_id)
    except Exception:
        log.exception("VCC settle failed for a finished duel")


def _json(action) -> web.Response:
    if action.status == 404:
        return web.json_response({"error": action.error}, status=404)
    if action.error and action.status != 200:
        payload = {"error": action.error}
        if action.view is not None:
            payload["state"] = action.view
        return web.json_response(payload, status=action.status)
    payload = dict(action.view or {})
    if action.notice:
        payload["notice"] = action.notice
    return web.json_response(payload)


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def start_site(app: web.Application, *, host: str = "0.0.0.0", port: int | None = None):
    if port is None:
        port = int(os.environ.get("PORT", "8080"))
    # access_log=None: request paths contain per-player table tokens.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Curse & Crown table listening on %s:%s", host, port)
    return runner
