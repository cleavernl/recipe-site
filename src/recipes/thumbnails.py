from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

TILE_IMAGE_MAX_WIDTH = 520
TILE_IMAGE_LIST_WIDTH = 320
ALLOWED_THUMBNAIL_WIDTHS = frozenset({TILE_IMAGE_LIST_WIDTH, TILE_IMAGE_MAX_WIDTH})


def thumbnail_url(relative_path: str, *, max_width: int = TILE_IMAGE_MAX_WIDTH) -> str:
    if not relative_path or max_width not in ALLOWED_THUMBNAIL_WIDTHS:
        return ""
    return f"{settings.MEDIA_URL}thumb/{max_width}/{relative_path}"


def _media_root() -> Path:
    return Path(settings.MEDIA_ROOT)


def _safe_source_path(relative_path: str) -> Path | None:
    source_path = (_media_root() / relative_path).resolve()
    try:
        source_path.relative_to(_media_root().resolve())
    except ValueError:
        return None
    if not source_path.is_file():
        return None
    return source_path


def _thumbnail_cache_path(source_path: Path, max_width: int) -> Path:
    relative = source_path.relative_to(_media_root())
    cache_key = hashlib.sha256(f"{max_width}:{relative.as_posix()}".encode()).hexdigest()[:20]
    return _media_root() / ".thumbnails" / str(max_width) / f"{cache_key}.jpg"


def _prepare_image(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA", "P"}:
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "P":
            image = image.convert("RGBA")
        alpha = image.split()[-1] if image.mode in {"RGBA", "LA"} else None
        background.paste(image, mask=alpha)
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def ensure_thumbnail(relative_path: str, max_width: int) -> Path | None:
    if max_width not in ALLOWED_THUMBNAIL_WIDTHS:
        return None

    source_path = _safe_source_path(relative_path)
    if source_path is None:
        return None

    cache_path = _thumbnail_cache_path(source_path, max_width)
    if cache_path.is_file():
        source_mtime = source_path.stat().st_mtime
        if cache_path.stat().st_mtime >= source_mtime:
            return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(source_path) as opened:
            image = _prepare_image(opened)
            if image.width > max_width:
                resized = image.copy()
                resized.thumbnail((max_width, max_width * 3), Image.Resampling.LANCZOS)
                image = resized
            image.save(cache_path, format="JPEG", quality=82, optimize=True)
    except (OSError, UnidentifiedImageError):
        return None

    return cache_path


def warm_tile_thumbnails(relative_path: str) -> int:
    """Pre-generate cached thumbnails for all allowed tile widths."""
    if not relative_path:
        return 0
    warmed = 0
    for max_width in ALLOWED_THUMBNAIL_WIDTHS:
        if ensure_thumbnail(relative_path, max_width) is not None:
            warmed += 1
    return warmed
