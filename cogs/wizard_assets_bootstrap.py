"""
Bootstrap Mirror sprite packs onto the persistent volume.

Licensed packs stay out of git. On Railway they live under
STATE_DIR/wizard_assets/{male,female}. If that tree is missing or empty,
we download the Dropbox zips from env vars once and unpack them.

Env (all optional; skip download if unset):
    WIZARD_PACK_FEMALE
    WIZARD_PACK_FEMALE_UPDATE
    WIZARD_PACK_MALE
    WIZARD_PACK_MALE_UPDATE
    WIZARD_ASSETS_DIR   - override destination (default STATE_DIR/wizard_assets)

Safe to call on every boot:
  - populated female pack is left alone
  - male is (re)installed when WIZARD_PACK_MALE* is set and the on-disk
    male tree is not yet the MALE1 muscular format (hair_top/ + body/)
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


def _is_tainara(gender_root: Path) -> bool:
    body = gender_root / "Body" / "Body"
    return body.is_dir() and any(body.glob("*.png"))


def _is_male1(gender_root: Path) -> bool:
    return (gender_root / "hair_top").is_dir() and (gender_root / "body").is_dir()


def assets_ready(root: Path | None = None) -> bool:
    root = root or assets_root()
    female_ok = _is_tainara(root / "female")
    male_ok = _is_male1(root / "male") or _is_tainara(root / "male")
    return female_ok and male_ok


def _looks_like_pack(path: Path) -> bool:
    # Tainara-P (capitalised folders)
    if (path / "Body").is_dir() and (path / "Head").is_dir():
        return True
    # MALE1 flat pack
    if (path / "body").is_dir() and (path / "hair_top").is_dir():
        return True
    return False


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
    with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as out:
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
            log.error("Wizard pack for %s had no recognised folders after extract", gender)
            return False
        dest = root / gender
        if dest.exists():
            shutil.rmtree(dest)
        _merge_tree(pack, dest)
        fmt = "male1" if _is_male1(dest) else "tainara"
        (dest / ".pack_format").write_text(fmt + "\n", encoding="utf-8")
        log.info("Installed wizard pack (%s) into %s/%s", fmt, root, gender)
        return True


def _env_urls_for(gender: str) -> list[str]:
    urls = []
    for g, env in PACK_ENVS:
        if g != gender:
            continue
        u = os.getenv(env, "").strip()
        if u:
            urls.append(u)
    return urls


def _ensure_male1_if_configured(root: Path) -> None:
    """If WIZARD_PACK_MALE* is set and male isn't MALE1 yet, replace it."""
    urls = _env_urls_for("male")
    if not urls:
        return
    male = root / "male"
    if _is_male1(male):
        return
    log.info("Male pack is missing or not MALE1 — installing from WIZARD_PACK_MALE*")
    root.mkdir(parents=True, exist_ok=True)
    for url in urls:
        if _install_zip(url, "male", root):
            return


def ensure_wizard_assets() -> bool:
    """Make sure STATE_DIR (or override) has male+female packs. Returns ready?"""
    root = assets_root()

    # Prefer copying from the repo checkout if a dev machine has them locally
    # (and male is already MALE1 when present).
    repo = DATA_DIR / "wizard_assets"
    if not assets_ready(root) and assets_ready(repo):
        log.info("Copying wizard assets from repo %s → %s", repo, root)
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(repo, root)

    # Upgrade/replace male when the new Dropbox URL is configured.
    _ensure_male1_if_configured(root)

    if assets_ready(root):
        log.info("Wizard assets ready at %s", root)
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
    seen = set()
    for gender, url in urls:
        if gender in seen and (root / gender).exists():
            continue
        if not _install_zip(url, gender, root):
            ok = False
        else:
            seen.add(gender)
    if ok and assets_ready(root):
        log.info("Wizard assets ready at %s", root)
        return True
    log.error("Wizard assets still incomplete after download at %s", root)
    return False
