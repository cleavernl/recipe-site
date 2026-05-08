from __future__ import annotations

import secrets
import string

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


def generate_invite_code(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class InviteCode(models.Model):
    code = models.CharField(max_length=32, unique=True, default=generate_invite_code)
    label = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_invites",
    )
    max_uses = models.PositiveIntegerField(default=1)
    use_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.label or self.code

    def is_usable(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return self.use_count < self.max_uses

    @transaction.atomic
    def redeem_for(self, user) -> InviteRedemption:
        invite = InviteCode.objects.select_for_update().get(pk=self.pk)
        if not invite.is_usable():
            raise ValueError("Invite code is not usable.")
        invite.use_count += 1
        invite.save(update_fields=["use_count"])
        self.use_count = invite.use_count
        return InviteRedemption.objects.create(invite_code=invite, user=user)


class InviteRedemption(models.Model):
    invite_code = models.ForeignKey(
        InviteCode,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="invite_redemption",
    )
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-redeemed_at"]

    def __str__(self) -> str:
        return f"{self.user} redeemed {self.invite_code.code}"
