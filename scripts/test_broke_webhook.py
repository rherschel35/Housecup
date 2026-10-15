#!/usr/bin/env python3
"""Filed /gonsomethingbroke tickets POST to a webhook without changing the command.

The player-facing command name, option, and messages stay as they are.
Webhook failures are logged and do not raise. The webhook key is never logged.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-broke-"))
os.environ.setdefault("DISCORD_TOKEN", "")
for _name in (
    "TICKET_WEBHOOK_URL",
    "TICKET_WEBHOOK_KEY",
    "TICKET_WEBHOOK_KEY_HEADER",
    "RAILWAY_GIT_COMMIT_SHA",
):
    os.environ.pop(_name, None)

import discord  # noqa: E402
from aiohttp import web  # noqa: E402

from cogs.broke import (  # noqa: E402
    WEBHOOK_TIMEOUT_SECONDS,
    Broke,
    _build_broke_ticket_payload,
    _commit_sha,
    _post_ticket_webhook,
    _ticket_webhook_auth_headers,
    log,
)

KEY = "test-ticket-webhook-key-9f3a"
_ENV = (
    "TICKET_WEBHOOK_URL",
    "TICKET_WEBHOOK_KEY",
    "TICKET_WEBHOOK_KEY_HEADER",
    "RAILWAY_GIT_COMMIT_SHA",
)


class _Grab(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
        if record.exc_info:
            self.messages.append(logging.Formatter().formatException(record.exc_info))


grab = _Grab()


def _use_env(**values: str) -> None:
    for name in _ENV:
        os.environ.pop(name, None)
    for name, value in values.items():
        os.environ[name] = value


def _logs() -> str:
    return "\n".join(grab.messages)


def _assert_key_hidden() -> None:
    blob = _logs()
    assert KEY not in blob, blob
    assert f"Bearer {KEY}" not in blob, blob


class _User:
    def __init__(self) -> None:
        self.id = 555141900802457630
        self.display_name = "Renée"

    def __str__(self) -> str:
        return "renee"


class _Channel(discord.TextChannel):
    def __init__(self) -> None:
        self.id = 1555383179802579005
        self.name = "great-hall"


class _Response:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send_message(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _Followup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _Interaction:
    def __init__(self, channel) -> None:
        self.user = _User()
        self.channel = channel
        self.guild_id = 123456789012345678
        self.response = _Response()
        self.followup = _Followup()

    async def original_response(self):
        raise discord.HTTPException(
            SimpleNamespace(status=400, reason="Bad Request"),
            "missing",
        )


class _Bot:
    def add_view(self, view, *, message_id=None) -> None:
        return None


def _command_unchanged() -> None:
    cmd = Broke.gonsomethingbroke
    assert cmd.name == "gonsomethingbroke"
    assert cmd.description == (
        "Tell Gon something's broken — staff get a jump link to your ticket."
    )
    params = list(cmd.parameters)
    assert [p.name for p in params] == ["what"]
    assert params[0].description == "What's broken? Be as specific as you can."
    assert params[0].required is True

    src = inspect.getsource(cmd.callback)
    receipt_at = src.index("embed=receipt")
    forward_at = src.index("_forward_ticket")
    assert receipt_at < forward_at
    assert "await self._forward_ticket" not in src
    assert "await _post_ticket_webhook" not in src
    for snippet in (
        "Tell me what's broken — add a short description.",
        "Keep it under",
        "You just sent one — try again in",
        "Run this in a text channel so staff can jump back to your ticket.",
    ):
        assert src.index(snippet) < receipt_at
    for snippet in (
        "Your ticket is posted here, but the staff inbox couldn't be reached.",
        "Ping Gon if it's urgent.",
    ):
        assert snippet in src


def _headers_and_payload() -> None:
    assert _ticket_webhook_auth_headers("", "") == {}
    assert _ticket_webhook_auth_headers("  ", "X-Api-Key") == {}
    assert _ticket_webhook_auth_headers(KEY, "") == {"Authorization": f"Bearer {KEY}"}
    assert _ticket_webhook_auth_headers(KEY, "   ") == {"Authorization": f"Bearer {KEY}"}
    assert _ticket_webhook_auth_headers(KEY, "X-Api-Key") == {"X-Api-Key": KEY}
    # An explicit header name sends the raw key, including when the name is Authorization.
    assert _ticket_webhook_auth_headers(KEY, "Authorization") == {"Authorization": KEY}
    try:
        _ticket_webhook_auth_headers(KEY, f"Bad {KEY}")
    except ValueError as exc:
        assert KEY not in str(exc)
    else:
        raise AssertionError("invalid header name was accepted")

    _use_env(RAILWAY_GIT_COMMIT_SHA="deadbeefcafebabe")
    assert _commit_sha() == "deadbeefcafebabe"
    stamped = _build_broke_ticket_payload(
        text="the staircase disappeared",
        reporter_id=555141900802457630,
        reporter_name="Renée",
        channel_id=1555383179802579005,
        channel_name="great-hall",
        guild_id=123456789012345678,
        ticket_id="abc123def4",
        timestamp="2026-10-15T22:45:00Z",
        commit_sha=_commit_sha(),
    )
    assert stamped == {
        "text": "the staircase disappeared",
        "reporter_id": "555141900802457630",
        "reporter_display_name": "Renée",
        "channel_id": "1555383179802579005",
        "channel_name": "great-hall",
        "guild_id": "123456789012345678",
        "timestamp": "2026-10-15T22:45:00Z",
        "ticket_id": "abc123def4",
        "commit_sha": "deadbeefcafebabe",
    }
    assert "category" not in stamped

    _use_env()
    assert _commit_sha() is None
    bare = _build_broke_ticket_payload(
        text="x",
        reporter_id=1,
        reporter_name="n",
        channel_id=2,
        channel_name="c",
        guild_id=None,
        ticket_id="id",
        timestamp="2026-10-15T00:00:00Z",
        commit_sha=_commit_sha(),
    )
    assert bare["guild_id"] is None
    assert "commit_sha" not in bare
    assert "category" not in bare

    _use_env(RAILWAY_GIT_COMMIT_SHA="   ")
    assert _commit_sha() is None


async def _listen() -> tuple[web.AppRunner, str, list[dict]]:
    received: list[dict] = []

    async def take(request: web.Request) -> web.Response:
        raw = await request.text()
        headers = {k.lower(): v for k, v in request.headers.items()}
        received.append({"path": request.path, "headers": headers, "raw": raw})
        if request.path == "/fail":
            auth = request.headers.get("Authorization", "")
            custom = request.headers.get("X-Api-Key", "")
            return web.Response(status=500, text=f"denied {auth} {custom}")
        if request.path == "/redirect":
            return web.Response(status=302, headers={"Location": "http://127.0.0.1:9/nope"})
        if request.path == "/slow":
            await asyncio.sleep(30)
            return web.Response(status=204)
        return web.Response(status=204)

    app = web.Application()
    app.router.add_post("/{tail:.*}", take)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, f"http://127.0.0.1:{port}", received


async def _drain(cog: Broke) -> None:
    pending = [task for task in list(cog._webhook_tasks) if not task.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), timeout=5)


def _player_text(interaction: _Interaction) -> str:
    parts: list[str] = []
    for args, kwargs in interaction.response.sent:
        parts.extend(str(arg) for arg in args)
        parts.extend(str(value) for value in kwargs.values())
    for args, kwargs in interaction.followup.sent:
        parts.extend(str(arg) for arg in args)
        parts.extend(str(value) for value in kwargs.values())
    return "\n".join(parts)


async def _run() -> None:
    print("command unchanged")
    _command_unchanged()
    print("headers and payload")
    _headers_and_payload()

    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(grab)

    runner, base, received = await _listen()
    try:
        cog = Broke(_Bot())
        callback = Broke.gonsomethingbroke.callback
        channel = _Channel()

        async def early(what: str, *, channel_obj, prepare=None) -> _Interaction:
            received.clear()
            grab.messages.clear()
            _use_env(TICKET_WEBHOOK_URL=base + "/ok", TICKET_WEBHOOK_KEY=KEY)
            interaction = _Interaction(channel_obj)
            cog._last.clear()
            if prepare:
                prepare(cog, interaction)
            await callback(cog, interaction, what)
            await asyncio.sleep(0.1)
            await _drain(cog)
            assert received == []
            assert cog._webhook_tasks == set()
            assert KEY not in _player_text(interaction)
            _assert_key_hidden()
            return interaction

        print("rejects do not forward")
        empty = await early("   ", channel_obj=channel)
        assert empty.response.sent[0][0][0] == (
            "Tell me what's broken — add a short description."
        )
        assert empty.response.sent[0][1]["ephemeral"] is True

        long = await early("x" * 1001, channel_obj=channel)
        assert long.response.sent[0][0][0] == "Keep it under 1000 characters."

        cooled = await early(
            "the staircase disappeared",
            channel_obj=channel,
            prepare=lambda c, i: c._last.__setitem__(i.user.id, time.time()),
        )
        assert cooled.response.sent[0][0][0].startswith("You just sent one — try again in")

        wrong = await early("the staircase disappeared", channel_obj=None)
        assert wrong.response.sent[0][0][0] == (
            "Run this in a text channel so staff can jump back to your ticket."
        )

        print("filed ticket is posted once")
        received.clear()
        grab.messages.clear()
        cog._last.clear()
        _use_env(
            TICKET_WEBHOOK_URL=base + "/ok",
            TICKET_WEBHOOK_KEY=KEY,
            RAILWAY_GIT_COMMIT_SHA="abc123deadbeef",
        )
        filed = _Interaction(channel)
        await callback(cog, filed, "the staircase disappeared")
        await _drain(cog)
        assert len(received) == 1
        item = received[0]
        assert item["path"] == "/ok"
        assert item["headers"].get("authorization") == f"Bearer {KEY}"
        assert '"reporter_id": "555141900802457630"' in item["raw"]
        assert '"channel_id": "1555383179802579005"' in item["raw"]
        assert '"guild_id": "123456789012345678"' in item["raw"]
        body = json.loads(item["raw"])
        assert body["text"] == "the staircase disappeared"
        assert body["reporter_display_name"] == "Renée"
        assert body["channel_name"] == "great-hall"
        assert body["commit_sha"] == "abc123deadbeef"
        assert len(body["ticket_id"]) == 10
        assert "category" not in body
        assert body["timestamp"].endswith("Z")
        embed = filed.response.sent[0][1]["embed"]
        assert embed.title == "🛠️ Something broke"
        assert embed.description == "the staircase disappeared"
        assert embed.footer.text == (
            "Gon has been told. Staff can jump here from the report channel."
        )
        assert filed.response.sent[0][1].get("ephemeral") is not True
        assert filed.followup.sent == []
        player = _player_text(filed)
        assert "webhook" not in player.lower()
        assert KEY not in player
        _assert_key_hidden()
        assert f"Forwarded broke ticket {body['ticket_id']}" in _logs()

        print("raw key header")
        received.clear()
        grab.messages.clear()
        _use_env(
            TICKET_WEBHOOK_URL=base + "/ok",
            TICKET_WEBHOOK_KEY=KEY,
            TICKET_WEBHOOK_KEY_HEADER="X-Api-Key",
        )
        await _post_ticket_webhook(base + "/ok", {"ticket_id": "raw1", "text": "x"})
        assert received[-1]["headers"].get("x-api-key") == KEY
        assert "authorization" not in received[-1]["headers"]
        _assert_key_hidden()

        print("explicit Authorization header is the raw key")
        received.clear()
        grab.messages.clear()
        _use_env(
            TICKET_WEBHOOK_URL=base + "/ok",
            TICKET_WEBHOOK_KEY=KEY,
            TICKET_WEBHOOK_KEY_HEADER="Authorization",
        )
        await _post_ticket_webhook(base + "/ok", {"ticket_id": "authraw", "text": "x"})
        assert received[-1]["headers"].get("authorization") == KEY
        _assert_key_hidden()

        print("no key sends no auth header")
        received.clear()
        grab.messages.clear()
        _use_env(TICKET_WEBHOOK_URL=base + "/ok")
        await _post_ticket_webhook(base + "/ok", {"ticket_id": "nokey", "text": "x"})
        assert "authorization" not in received[-1]["headers"]
        assert "x-api-key" not in received[-1]["headers"]

        print("invalid header name still posts, without the key")
        received.clear()
        grab.messages.clear()
        _use_env(
            TICKET_WEBHOOK_URL=base + "/ok",
            TICKET_WEBHOOK_KEY=KEY,
            TICKET_WEBHOOK_KEY_HEADER=f"Bad {KEY}",
        )
        await _post_ticket_webhook(base + "/ok", {"ticket_id": "badhdr", "text": "x"})
        blob = " ".join(
            f"{k}:{v}" for k, v in received[-1]["headers"].items()
        )
        assert KEY not in blob
        assert "authorization" not in received[-1]["headers"]
        assert "not a valid HTTP header name" in _logs()
        _assert_key_hidden()

        print("error body is logged without the key")
        received.clear()
        grab.messages.clear()
        _use_env(TICKET_WEBHOOK_URL=base + "/fail", TICKET_WEBHOOK_KEY=KEY)
        await _post_ticket_webhook(base + "/fail", {"ticket_id": "err1", "text": "x"})
        assert "HTTP 500" in _logs()
        assert "[redacted]" in _logs()
        _assert_key_hidden()

        print("redirect is not followed")
        grab.messages.clear()
        _use_env(TICKET_WEBHOOK_URL=base + "/redirect", TICKET_WEBHOOK_KEY=KEY)
        await _post_ticket_webhook(
            base + "/redirect", {"ticket_id": "redir", "text": "x"}
        )
        assert "HTTP 302" in _logs()
        _assert_key_hidden()

        print("feature off")
        grab.messages.clear()
        _use_env(TICKET_WEBHOOK_URL="   ", TICKET_WEBHOOK_KEY=KEY)
        before = len(received)
        cog._forward_ticket(filed, "the staircase disappeared", "off1")
        await asyncio.sleep(0.05)
        assert len(received) == before
        assert cog._webhook_tasks == set()

        print("schedule does not block")
        grab.messages.clear()
        _use_env(TICKET_WEBHOOK_URL=base + "/slow", TICKET_WEBHOOK_KEY=KEY)
        started = time.perf_counter()
        cog._forward_ticket(filed, "the staircase disappeared", "slowsched")
        elapsed = time.perf_counter() - started
        assert elapsed < 0.5, elapsed
        inflight = list(cog._webhook_tasks)
        assert inflight and any(not task.done() for task in inflight)
        for task in inflight:
            task.cancel()
        await asyncio.gather(*inflight, return_exceptions=True)

        print("timeout is 10s and the key stays out of the log")
        assert WEBHOOK_TIMEOUT_SECONDS == 10
        grab.messages.clear()
        _use_env(TICKET_WEBHOOK_KEY=KEY)
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
        sock.close()
        started = time.perf_counter()
        await _post_ticket_webhook(
            f"http://127.0.0.1:{closed_port}/hook?token={KEY}",
            {"ticket_id": "closed", "text": "x"},
        )
        refused = time.perf_counter() - started
        assert refused < 5, refused
        assert "failed" in _logs()
        _assert_key_hidden()

        grab.messages.clear()
        _use_env(TICKET_WEBHOOK_KEY=KEY)
        started = time.perf_counter()
        await asyncio.wait_for(
            _post_ticket_webhook(
                base + "/slow", {"ticket_id": "slow1", "text": "x"}
            ),
            timeout=20,
        )
        elapsed = time.perf_counter() - started
        assert 8 <= elapsed < 16, elapsed
        assert "failed" in _logs()
        _assert_key_hidden()
    finally:
        await runner.cleanup()
        _use_env()

    print("OK")


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
