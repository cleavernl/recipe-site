from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import include, path, reverse_lazy
from django.views.generic import RedirectView

from config.views import serve_protected_media

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="recipes:list", permanent=False), name="home"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "accounts/password/",
        auth_views.PasswordChangeView.as_view(
            template_name="registration/password_change_form.html",
            success_url=reverse_lazy("accounts:profile"),
        ),
        name="password_change",
    ),
    path("recipes/", include("recipes.urls")),
    path(
        "media/<path:path>",
        login_required(serve_protected_media),
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
