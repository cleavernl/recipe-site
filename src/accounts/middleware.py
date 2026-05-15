from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from accounts.models import UserSiteActivity

# Avoid writing to the database on every request for active users.
_LAST_SEEN_WRITE_INTERVAL = timedelta(minutes=1)


def _should_skip_path(path: str) -> bool:
    if path.startswith(settings.STATIC_URL):
        return True
    if path.startswith(settings.MEDIA_URL):
        return True
    return False


class UpdateLastSiteVisitMiddleware:
    """Records throttled last-seen timestamps for authenticated users."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated and not _should_skip_path(request.path):
            self._maybe_touch_last_seen(request.user)
        return response

    @staticmethod
    def _maybe_touch_last_seen(user) -> None:
        now = timezone.now()
        activity, created = UserSiteActivity.objects.get_or_create(user=user)
        stale = (
            created
            or activity.last_seen_at is None
            or (now - activity.last_seen_at) >= _LAST_SEEN_WRITE_INTERVAL
        )
        if stale:
            UserSiteActivity.objects.filter(pk=activity.pk).update(last_seen_at=now)
