from __future__ import annotations

from datetime import timedelta

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone

from accounts.models import InviteCode, InviteRedemption, generate_invite_code


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
