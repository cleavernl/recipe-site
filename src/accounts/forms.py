from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db import transaction

from accounts.models import InviteCode


class InviteSignupForm(UserCreationForm):
    invite_code = forms.CharField(
        max_length=32,
        help_text="Use the invite code shared by your host.",
    )
    email = forms.EmailField(required=False)
    first_name = forms.CharField(required=False, max_length=150)
    last_name = forms.CharField(required=False, max_length=150)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def clean_invite_code(self) -> str:
        code = self.cleaned_data["invite_code"].strip().upper()
        try:
            invite = InviteCode.objects.get(code__iexact=code)
        except InviteCode.DoesNotExist as error:
            raise forms.ValidationError("This invite code was not found.") from error

        if not invite.is_usable():
            raise forms.ValidationError("This invite code is no longer available.")

        self.invite = invite
        return invite.code

    @transaction.atomic
    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        if commit:
            user.save()
            self.save_m2m()
            self.invite.redeem_for(user)
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
