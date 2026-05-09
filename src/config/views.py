from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.static import serve


def serve_protected_media(request: HttpRequest, path: str) -> HttpResponse:
    return serve(request, path, document_root=settings.MEDIA_ROOT, show_indexes=False)

