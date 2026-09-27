"""
Bootstrap Mirror sprite packs onto the persistent volume.

Licensed Tainara-P packs stay out of git. On Railway they live under
STATE_DIR/wizard_assets/{male,female}. If that tree is missing or empty,
we download the Dropbox zips from env vars once and unpack them.

Env (all optional; skip download if unset):
    WIZARD_PACK_FEMALE
    WIZARD_PACK_FEMALE_UPDATE
    WIZARD_PACK_MALE
    WIZARD_PACK_MALE_UPDATE
    WIZARD_ASSETS_DIR   - override destination (default STATE_DIR/wizard_assets)

Safe to call on every boot: a populated assets dir is left alone.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("velmora.wizard_assets")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))

PACK_ENVS = (
    ("female", "WIZARD_PACK_FEMALE"),
    ("female", "WIZARD_PACK_FEMALE_UPDATE"),
    ("male", "WIZARD_PACK_MALE"),
    ("male", "WIZARD_PACK_MALE_UPDATE"),
)


def assets_root() -> Path:
    override = os.getenv("WIZARD_ASSETS_DIR", "").strip()
    if override:
        return Path(override)
    return STATE_DIR / "wizard_assets"


def crests_root() -> Path:
    override = os.getenv("HOUSE_CRESTS_DIR", "").strip()
    if override:
        return Path(override)
    # Crests ship in the repo.
    return DATA_DIR / "house_crests"


def assets_ready(root: Path | None = None) -> bool:
    root = root or assets_root()
    for gender in ("male", "female"):
        body = root / gender / "Body" / "Body"
        if not body.is_dir() or not any(body.glob("*.png")):
            return False
    return True


def _looks_like_pack(path: Path) -> bool:
    return (path / "Body").is_dir() and (path / "Head").is_dir()


def _find_pack_root(extracted: Path) -> Path | None:
    """Zip may unwrap to nested folders; find the one that has Body/Head."""
    if _looks_like_pack(extracted):
        return extracted
    for child in extracted.rglob("*"):
        if child.is_dir() and _looks_like_pack(child):
            return child
    return None


def _merge_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            _merge_tree(item, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _download(url: str, dest: Path) -> None:
    log.info("Downloading wizard pack → %s", dest.name)
    req = urllib.request.Request(url, headers={"User-Agent": "VelmoraHouseCup/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _install_zip(url: str, gender: str, root: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="wizard_pack_") as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "pack.zip"
        try:
            _download(url, zip_path)
        except Exception:
            log.exception("Failed to download wizard pack for %s", gender)
            return False
        extract_to = tmp_path / "out"
        extract_to.mkdir()
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_to)
        except zipfile.BadZipFile:
            log.exception("Wizard pack for %s is not a valid zip", gender)
            return False
        pack = _find_pack_root(extract_to)
        if pack is None:
            log.error("Wizard pack for %s had no Body/Head folders after extract", gender)
            return False
        _merge_tree(pack, root / gender)
        log.info("Installed wizard pack into %s/%s", root, gender)
        return True


def ensure_wizard_assets() -> bool:
    """Make sure STATE_DIR (or override) has male+female packs. Returns ready?"""
    root = assets_root()
    if assets_ready(root):
        log.info("Wizard assets already present at %s", root)
        return True

    # Prefer copying from the repo checkout if a dev machine has them locally.
    repo = DATA_DIR / "wizard_assets"
    if assets_ready(repo):
        log.info("Copying wizard assets from repo %s → %s", repo, root)
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(repo, root)
        return True

    urls = [(gender, os.getenv(env, "").strip()) for gender, env in PACK_ENVS]
    urls = [(g, u) for g, u in urls if u]
    if not urls:
        log.warning(
            "Wizard assets missing at %s and no WIZARD_PACK_* env vars set. "
            "/wizard and /mirror portraits will fail until packs are installed.",
            root,
        )
        return False

    root.mkdir(parents=True, exist_ok=True)
    ok = True
    for gender, url in urls:
        if not _install_zip(url, gender, root):
            ok = False
    if ok and assets_ready(root):
        log.info("Wizard assets ready at %s", root)
        return True
    log.error("Wizard assets still incomplete after download at %s", root)
    return False
