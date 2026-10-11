#!/usr/bin/env python3
"""Smoke tests for familiar looks catalog and art coverage."""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _stub_discord() -> None:
    discord = types.ModuleType("discord")

    class _Emb:
        def __init__(self, **k):
            self.__dict__.update(k)

        def set_image(self, **k):
            pass

        def set_footer(self, **k):
            pass

    class _File:
        def __init__(self, *a, **k):
            pass

    class _ChoiceMeta(type):
        def __getitem__(cls, item):
            return cls

    class _Choice(metaclass=_ChoiceMeta):
        def __init__(self, name=None, value=None):
            self.name = name
            self.value = value

    discord.Embed = _Emb
    discord.File = _File
    discord.Interaction = object
    discord.Member = object
    app_commands = types.ModuleType("discord.app_commands")

    def command(**k):
        def deco(fn):
            fn.autocomplete = lambda *a, **k: (lambda f: f)
            return fn

        return deco

    def describe(**k):
        def deco(fn):
            return fn

        return deco

    def choices(**k):
        def deco(fn):
            return fn

        return deco

    class Group:
        def __init__(self, **k):
            pass

        def command(self, **k):
            def deco(fn):
                fn.autocomplete = lambda *a, **k: (lambda f: f)
                return fn

            return deco

    app_commands.command = command
    app_commands.describe = describe
    app_commands.choices = choices
    app_commands.Choice = _Choice
    app_commands.Group = Group
    discord.app_commands = app_commands
    commands = types.ModuleType("discord.ext.commands")

    class Cog:
        pass

    class Bot:
        pass

    commands.Cog = Cog
    commands.Bot = Bot
    ext = types.ModuleType("discord.ext")
    ext.commands = commands
    sys.modules["discord"] = discord
    sys.modules["discord.app_commands"] = app_commands
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands


def main() -> None:
    _stub_discord()
    # stub world_engine.today
    we = types.ModuleType("cogs.world_engine")
    we.today = lambda: "2026-10-11"
    sys.modules["cogs.world_engine"] = we

    td = tempfile.mkdtemp()
    os.environ["STATE_DIR"] = td

    import cogs.familiars as F

    assert set(F.LOOKS) == {"salamander", "raven", "fox"}
    for sp, looks in F.LOOKS.items():
        assert len(looks) == 30, (sp, len(looks))
        assert F.default_look(sp) in looks

    class FakeBot:
        def get_cog(self, name):
            return None

    with patch.object(F, "today", lambda: "2026-10-11"):
        cog = F.Familiars(FakeBot())
        rec = cog.record(1)
        assert rec["species"] is None
        rec["species"] = "fox"
        rec["look"] = None
        rec2 = cog.record(1)
        assert rec2["look"] == F.default_look("fox")
        rec2["look"] = "rust_tail"
        assert F.look_meta("fox", "rust_tail")["label"] == "Rust Tail"
        path = F.art_path("fox", "rust_tail")
        assert path is None or path.is_file()

    missing = []
    for sp, looks in F.LOOKS.items():
        for kid in looks:
            p = F.ASSETS_DIR / sp / f"{kid}.png"
            if not p.is_file():
                missing.append(f"{sp}/{kid}")
    assert not missing, missing[:10]
    print("familiar looks OK", sum(len(v) for v in F.LOOKS.values()), "looks")


if __name__ == "__main__":
    main()
