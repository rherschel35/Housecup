#!/usr/bin/env python3
"""Smoke tests for patronus uniqueness, reset flags, and art coverage."""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _stub_discord() -> None:
    discord = types.ModuleType("discord")

    class _Emb:
        def __init__(self, **k):
            self.__dict__.update(k)
            self.fields = []

        def add_field(self, **k):
            self.fields.append(k)

        def set_image(self, **k):
            pass

        def set_footer(self, **k):
            pass

    class _File:
        def __init__(self, *a, **k):
            pass

    class _Modal:
        def __init_subclass__(cls, **kwargs):
            kwargs.pop("title", None)
            super().__init_subclass__(**kwargs)

        def __init__(self, *a, **k):
            pass

    class _TextInput:
        def __init__(self, *a, **k):
            pass

    discord.Embed = _Emb
    discord.File = _File
    discord.Interaction = object
    discord.Member = object
    discord.ui = types.ModuleType("discord.ui")
    discord.ui.Modal = _Modal
    discord.ui.TextInput = _TextInput

    app_commands = types.ModuleType("discord.app_commands")

    def command(**k):
        def deco(fn):
            return fn

        return deco

    def describe(**k):
        def deco(fn):
            return fn

        return deco

    app_commands.command = command
    app_commands.describe = describe
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
    sys.modules["discord.ui"] = discord.ui
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands


def main() -> None:
    _stub_discord()
    td = tempfile.mkdtemp()
    os.environ["STATE_DIR"] = td

    import cogs.patronus as P

    assert len(P.ANIMALS) == 190, len(P.ANIMALS)
    assert len(set(P.ANIMALS)) == 190
    for reserved in ("Siren", "Griffin", "Margarita", "Chimera", "Kraken"):
        assert reserved not in P.ANIMALS

    class FakeBot:
        def get_cog(self, name):
            return None

    cog = P.Patronus(FakeBot())
    used: set[str] = set()
    for i in range(80):
        words = f"alpha beta gamma{i}"
        animal = cog.assign_animal(words, user_id=i)
        assert animal, i
        assert animal not in used, (animal, i)
        used.add(animal)
        cog.owners[str(i)] = P.fallback_patronus(words, animal)
    cog.save()

    words0 = "alpha beta gamma0"
    a0 = cog.owners["0"]["animal"]
    a_other = cog.assign_animal(words0, user_id=9999)
    assert a_other != a0

    assert cog.release(929771936408547328) is False  # custom sealed
    assert not cog.reset_used_by(1)
    cog.mark_reset_used(1)
    assert cog.reset_used_by(1)

    missing = [a for a in P.ANIMALS if not (P.ASSETS_DIR / P.image_name(a)).is_file()]
    assert not missing, f"missing art: {missing[:10]}"

    # customs still on disk
    for name in (
        "Siren_Rowie.png",
        "Griffin_Pod.jpg",
        "Margarita_Mara.png",
        "Chimera_Saro.jpg",
        "Kraken_Lumi.jpg",
    ):
        assert (P.ASSETS_DIR / name).is_file(), name

    print("patronus smoke OK", len(used), "unique claims;", len(P.ANIMALS), "forms")


if __name__ == "__main__":
    main()
