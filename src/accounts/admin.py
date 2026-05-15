from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone

from accounts.models import InviteCode, InviteRedemption, UserSiteActivity, generate_invite_code

_US_CENTRAL = ZoneInfo("America/Chicago")


def _format_last_seen_us_central(dt) -> str:
    if not timezone.is_aware(dt):
        dt = timezone.make_aware(dt, datetime.timezone.utc)
    local = dt.astimezone(_US_CENTRAL)
    hour12 = local.hour % 12 or 12
    suffix = "a.m." if local.hour < 12 else "p.m."
    tz = local.strftime("%Z")
    return (
        f"{local.strftime('%b')} {local.day}, {local.year}, "
        f"{hour12}:{local.minute:02d} {suffix} {tz}"
    )


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (*BaseUserAdmin.list_display, "last_site_visit")
    readonly_fields = (*BaseUserAdmin.readonly_fields, "last_site_visit")
    fieldsets = (
        *BaseUserAdmin.fieldsets,
        ("Site usage", {"fields": ("last_site_visit",)}),
    )

    @admin.display(
        description="Last site visit (US Central)",
        ordering="site_activity__last_seen_at",
    )
    def last_site_visit(self, obj):
        if not obj.pk:
            return "—"
        try:
            activity = obj.site_activity
        except UserSiteActivity.DoesNotExist:
            return "—"
        if activity.last_seen_at:
            return _format_last_seen_us_central(activity.last_seen_at)
        return "—"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("site_activity")


@admin.register(InviteCode)
class InviteCodeAdmin(admin.ModelAdmin):
    change_list_template = "admin/accounts/invitecode/change_list.html"
    list_display = (
        "code",
        "label",
        "is_active",
        "use_count",
        "max_uses",
        "expires_at",
        "created_at",
    )
    list_filter = ("is_active", "created_at", "expires_at")
    search_fields = ("code", "label")
    readonly_fields = ("created_by", "use_count", "created_at")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "quick-24h/",
                self.admin_site.admin_view(self.create_24_hour_invite),
                name="accounts_invitecode_quick_24h",
            ),
        ]
        return custom_urls + urls

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def create_24_hour_invite(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_add_permission(request):
            raise PermissionDenied

        invite = InviteCode.objects.create(
            code=self.generate_unique_code(),
            label="24-hour invite",
            max_uses=1,
            expires_at=timezone.now() + timedelta(hours=24),
            is_active=True,
            created_by=request.user,
        )
        self.message_user(
            request,
            f"Created 24-hour invite code {invite.code}.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:accounts_invitecode_changelist"))

    def generate_unique_code(self) -> str:
        while True:
            code = generate_invite_code()
            if not InviteCode.objects.filter(code=code).exists():
                return code

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if request.GET.get("quick") == "24h":
            initial.update(
                {
                    "code": generate_invite_code(),
                    "label": "24-hour invite",
                    "max_uses": 1,
                    "expires_at": timezone.now() + timedelta(hours=24),
                    "is_active": True,
                    "created_by": request.user,
                }
            )
        return initial


@admin.register(InviteRedemption)
class InviteRedemptionAdmin(admin.ModelAdmin):
    list_display = ("invite_code", "user", "redeemed_at")
    list_filter = ("redeemed_at",)
    search_fields = ("invite_code__code", "user__username", "user__email")
    readonly_fields = ("invite_code", "user", "redeemed_at")
