from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.views.static import serve

from recipes.thumbnails import ALLOWED_THUMBNAIL_WIDTHS, ensure_thumbnail


def serve_protected_media(request: HttpRequest, path: str) -> HttpResponse:
    return serve(request, path, document_root=settings.MEDIA_ROOT, show_indexes=False)


def serve_protected_thumbnail(
    request: HttpRequest,
    max_width: int,
    path: str,
) -> HttpResponse:
    if max_width not in ALLOWED_THUMBNAIL_WIDTHS:
        return HttpResponseBadRequest("Unsupported thumbnail width.")

    source_path = Path(settings.MEDIA_ROOT) / path
    try:
        source_path.resolve().relative_to(Path(settings.MEDIA_ROOT).resolve())
    except ValueError as exc:
        raise Http404("Invalid media path.") from exc

    thumbnail_path = ensure_thumbnail(path, max_width)
    if thumbnail_path is None:
        raise Http404("Image not found.")

    return FileResponse(
        thumbnail_path.open("rb"),
        content_type="image/jpeg",
        as_attachment=False,
        filename=thumbnail_path.name,
    )

