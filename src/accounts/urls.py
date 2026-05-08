from __future__ import annotations

from django.urls import path

from accounts.views import InviteSignupView, ProfileView

app_name = "accounts"

urlpatterns = [
    path("signup/", InviteSignupView.as_view(), name="signup"),
    path("profile/", ProfileView.as_view(), name="profile"),
]
