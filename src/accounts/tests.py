from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import InviteCode, InviteRedemption


class InviteSignupTests(TestCase):
    def test_valid_invite_creates_user_and_redeems_code(self):
        InviteCode.objects.create(code="FAMILY123", label="Family")

        response = self.client.post(
            reverse("accounts:signup"),
            {
                "invite_code": "family123",
                "username": "sam",
                "email": "sam@example.com",
                "password1": "a-strong-test-password-123",
                "password2": "a-strong-test-password-123",
            },
        )

        self.assertRedirects(response, reverse("recipes:list"))
        user = User.objects.get(username="sam")
        invite = InviteCode.objects.get(code="FAMILY123")
        self.assertEqual(invite.use_count, 1)
        self.assertTrue(InviteRedemption.objects.filter(invite_code=invite, user=user).exists())
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(user.pk))

    def test_exhausted_invite_code_is_rejected(self):
        invite = InviteCode.objects.create(code="USEDUP", max_uses=1, use_count=1)

        response = self.client.post(
            reverse("accounts:signup"),
            {
                "invite_code": invite.code,
                "username": "sam",
                "password1": "a-strong-test-password-123",
                "password2": "a-strong-test-password-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="sam").exists())
        self.assertContains(response, "no longer available")


class InviteAdminTests(TestCase):
    def test_staff_can_create_multiple_24_hour_invites_with_same_label(self):
        staff = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password-123",
        )
        self.client.force_login(staff)

        changelist_response = self.client.get(reverse("admin:accounts_invitecode_changelist"))
        quick_invite_url = reverse("admin:accounts_invitecode_quick_24h")
        first_response = self.client.post(quick_invite_url)
        second_response = self.client.post(quick_invite_url)
        invites = InviteCode.objects.filter(label="24-hour invite")

        self.assertContains(changelist_response, "New 24-hour invite")
        self.assertRedirects(first_response, reverse("admin:accounts_invitecode_changelist"))
        self.assertRedirects(second_response, reverse("admin:accounts_invitecode_changelist"))
        self.assertEqual(invites.count(), 2)
        self.assertEqual(invites.values("code").distinct().count(), 2)
        self.assertTrue(all(invite.max_uses == 1 for invite in invites))
        self.assertTrue(all(invite.expires_at is not None for invite in invites))

    def test_quick_24_hour_invite_requires_post(self):
        staff = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password-123",
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("admin:accounts_invitecode_quick_24h"))

        self.assertEqual(response.status_code, 405)
        self.assertFalse(InviteCode.objects.exists())


class ProfileTests(TestCase):
    def test_profile_requires_login(self):
        response = self.client.get(reverse("accounts:profile"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_authenticated_user_can_update_profile(self):
        user = User.objects.create_user(
            username="sam",
            password="password-123",
            first_name="Sam",
            last_name="Old",
            email="old@example.com",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("accounts:profile"),
            {
                "first_name": "Sam",
                "last_name": "Cook",
                "email": "sam@example.com",
            },
        )

        user.refresh_from_db()
        self.assertRedirects(response, reverse("accounts:profile"))
        self.assertEqual(user.last_name, "Cook")
        self.assertEqual(user.email, "sam@example.com")

    def test_profile_has_change_password_link(self):
        user = User.objects.create_user(username="sam", password="password-123")
        self.client.force_login(user)

        response = self.client.get(reverse("accounts:profile"))

        self.assertContains(response, reverse("password_change"))

    def test_authenticated_user_can_change_password(self):
        user = User.objects.create_user(username="sam", password="old-password-123")
        self.client.force_login(user)

        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "old-password-123",
                "new_password1": "new-password-123-strong",
                "new_password2": "new-password-123-strong",
            },
        )

        self.assertRedirects(response, reverse("accounts:profile"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("new-password-123-strong"))
