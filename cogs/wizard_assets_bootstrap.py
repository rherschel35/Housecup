"""
Bootstrap Mirror sprite packs onto the persistent volume.

Licensed packs stay out of git. On Railway they live under
STATE_DIR/wizard_assets/{male,female,female_full}. If that tree is missing
or empty, we download the Dropbox zips from env vars once and unpack them.

Env (all optional; skip download if unset):
    WIZARD_PACK_FEMALE
    WIZARD_PACK_FEMALE_UPDATE
    WIZARD_PACK_FEMALE_FULL         - Girl Sprites Premium zip (PSD); exported on boot
    WIZARD_PACK_FEMALE_FULL_UPDATE
    WIZARD_PACK_MALE          - overrides the default MALE1 muscular pack URL
    WIZARD_PACK_MALE_UPDATE
    WIZARD_ASSETS_DIR         - override destination (default STATE_DIR/wizard_assets)

On every boot:
  - female is left alone if already present
  - female_full is built from the Premium Girl Sprites PSD when missing
  - male is replaced whenever it is not the MALE1 muscular format
    (hair_top/ + body/), using WIZARD_PACK_MALE* or the built-in Dropbox URL
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("velmora.wizard_assets")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))

# Default masculine pack (muscular MALE1). Env WIZARD_PACK_MALE overrides.
DEFAULT_MALE1_URL = (
    "https://www.dropbox.com/scl/fi/wek73vd4h4vyts80ewnwi/MALE1_assets.zip"
    "?rlkey=3ybjz1332k00a8b01eiokqa3s&dl=1"
)

# Girl Sprites Premium 1 — full-body feminine pack (PSD → PNG export).
DEFAULT_FEMALE_FULL_URL = (
    "https://www.dropbox.com/scl/fi/lmtvapy4h19h1acpikexe/Girl-Sprites-Premium-1.zip"
    "?rlkey=wkelzyhllz750s6058za0oexf&dl=1"
)

PACK_ENVS = (
    ("female", "WIZARD_PACK_FEMALE"),
    ("female", "WIZARD_PACK_FEMALE_UPDATE"),
    ("female_full", "WIZARD_PACK_FEMALE_FULL"),
    ("female_full", "WIZARD_PACK_FEMALE_FULL_UPDATE"),
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


def _is_female_full(gender_root: Path) -> bool:
    try:
        from cogs.mirror_girl_premium import is_girl_premium_root
        return is_girl_premium_root(gender_root)
    except Exception:
        return (gender_root / "body" / "body.png").is_file() and (gender_root / "clothes").is_dir()


def female_full_ready(root: Path | None = None) -> bool:
    root = root or assets_root()
    return _is_female_full(root / "female_full")


def assets_ready(root: Path | None = None) -> bool:
    """Female = Tainara; male must be MALE1 (old soft male pack counts as not ready).

    female_full is optional — Mirror hides that presentation if missing.
    """
    root = root or assets_root()
    female_ok = _is_tainara(root / "female")
    male_ok = _is_male1(root / "male")
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
    """Zip may unwrap to nested folders; find the one with pack folders."""
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


def _normalize_dropbox(url: str) -> str:
    """Force Dropbox direct-download (dl=1)."""
    if "dropbox.com" not in url:
        return url
    if "dl=0" in url:
        return url.replace("dl=0", "dl=1")
    if "dl=" not in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}dl=1"
    return url


def _download(url: str, dest: Path) -> None:
    url = _normalize_dropbox(url)
    log.info("Downloading wizard pack → %s (%s…)", dest.name, url[:80])
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VelmoraHouseCup/1.0"})
            with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            size = dest.stat().st_size
            log.info("Downloaded %s (%s bytes)", dest.name, f"{size:,}")
            if size < 1_000_000:
                raise RuntimeError(f"Pack download too small ({size} bytes) — check Dropbox link")
            return
        except Exception as e:
            last_err = e
            log.warning("Pack download attempt %s failed: %s", attempt, e)
            time.sleep(2 * attempt)
    raise last_err or RuntimeError("download failed")


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
            urls.append(_normalize_dropbox(u))
    return urls


def _male_urls() -> list[str]:
    """Env overrides first, then the built-in MALE1 Dropbox URL."""
    urls = _env_urls_for("male")
    default = _normalize_dropbox(DEFAULT_MALE1_URL)
    if default not in urls:
        urls.append(default)
    return urls


def _ensure_male1(root: Path) -> bool:
    """Replace soft/old male pack with MALE1 whenever needed."""
    male = root / "male"
    if _is_male1(male):
        log.info("Male pack is MALE1 at %s", male)
        return True
    if _is_tainara(male):
        log.warning(
            "Male pack at %s is the old soft Tainara set — replacing with MALE1 muscular pack",
            male,
        )
    else:
        log.info("Male pack missing — installing MALE1 muscular pack")
    root.mkdir(parents=True, exist_ok=True)
    for url in _male_urls():
        if _install_zip(url, "male", root) and _is_male1(root / "male"):
            return True
    log.error("Could not install MALE1 male pack")
    return False


def _female_full_urls() -> list[str]:
    urls = _env_urls_for("female_full")
    default = _normalize_dropbox(DEFAULT_FEMALE_FULL_URL)
    if default not in urls:
        urls.append(default)
    return urls


def _export_female_full_from_psd(psd_path: Path, dest: Path) -> bool:
    try:
        from scripts.export_girl_premium import export_psd
    except ImportError:
        # Boot path: scripts/ may not be a package — load by file.
        import importlib.util
        script = Path(__file__).resolve().parent.parent / "scripts" / "export_girl_premium.py"
        spec = importlib.util.spec_from_file_location("export_girl_premium", script)
        if spec is None or spec.loader is None:
            log.error("Could not load export_girl_premium.py")
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        export_psd = mod.export_psd
    try:
        export_psd(psd_path, dest)
    except Exception:
        log.exception("Failed exporting Girl Sprites Premium → %s", dest)
        return False
    return _is_female_full(dest)


def _ensure_female_full(root: Path) -> bool:
    """Install Girl Sprites Premium as wizard_assets/female_full (PNG parts)."""
    dest = root / "female_full"
    if _is_female_full(dest):
        log.info("Full-body feminine pack ready at %s", dest)
        return True

    # Stale exports (pre girl_premium_v2) placed hair/clothes at (0,0). Wipe
    # so we re-copy or re-export with corrected layer offsets.
    if dest.exists():
        log.info("Refreshing female_full pack (stale/incomplete) at %s", dest)
        shutil.rmtree(dest)

    repo = DATA_DIR / "wizard_assets" / "female_full"
    if _is_female_full(repo):
        log.info("Copying female_full wizard assets from repo → %s", dest)
        shutil.copytree(repo, dest)
        return _is_female_full(dest)

    root.mkdir(parents=True, exist_ok=True)
    for url in _female_full_urls():
        with tempfile.TemporaryDirectory(prefix="girl_premium_") as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "pack.zip"
            try:
                _download(url, zip_path)
            except Exception:
                log.exception("Failed to download female_full pack")
                continue
            extract_to = tmp_path / "out"
            extract_to.mkdir()
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_to)
            except zipfile.BadZipFile:
                log.exception("female_full pack is not a valid zip")
                continue
            psds = list(extract_to.rglob("Premium Girl Sprites.psd"))
            if not psds:
                psds = list(extract_to.rglob("*.psd"))
            if not psds:
                log.error("No PSD found in female_full zip")
                continue
            if _export_female_full_from_psd(psds[0], dest):
                return True
    log.warning("Full-body feminine pack unavailable — /wizard will hide that option")
    return False


def ensure_wizard_assets() -> bool:
    """Make sure STATE_DIR (or override) has male+female packs. Returns ready?"""
    root = assets_root()

    # Dev machines: seed female from repo checkout if volume is empty.
    repo = DATA_DIR / "wizard_assets"
    if not (root / "female").exists() and _is_tainara(repo / "female"):
        log.info("Copying female wizard assets from repo %s → %s", repo, root)
        root.mkdir(parents=True, exist_ok=True)
        dest = root / "female"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(repo / "female", dest)

    # Always enforce muscular male pack (even if an old soft male is present).
    _ensure_male1(root)

    # Female from env if still missing.
    if not _is_tainara(root / "female"):
        for url in _env_urls_for("female"):
            if _install_zip(url, "female", root):
                break

    _ensure_female_full(root)

    if assets_ready(root):
        log.info("Wizard assets ready at %s (male=MALE1 female_full=%s)",
                 root, _is_female_full(root / "female_full"))
        return True

    log.error(
        "Wizard assets incomplete at %s (female=%s male1=%s)",
        root,
        _is_tainara(root / "female"),
        _is_male1(root / "male"),
    )
    return False
