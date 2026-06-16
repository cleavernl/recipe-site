from __future__ import annotations

from django.apps import AppConfig


class RecipesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "recipes"

    def ready(self) -> None:
        import recipes.signals  # noqa: F401
