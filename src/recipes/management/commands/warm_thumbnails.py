from __future__ import annotations

from django.core.management.base import BaseCommand

from recipes.models import Recipe, RecipePhoto
from recipes.thumbnails import warm_tile_thumbnails


class Command(BaseCommand):
    help = "Pre-generate cached tile thumbnails for all existing recipe photos."

    def handle(self, *args, **options) -> None:
        paths: set[str] = set()
        for name in RecipePhoto.objects.exclude(image="").values_list("image", flat=True):
            if name:
                paths.add(name)
        for name in Recipe.objects.exclude(photo="").values_list("photo", flat=True):
            if name:
                paths.add(name)

        if not paths:
            self.stdout.write("No recipe photos found.")
            return

        warmed_files = 0
        for relative_path in sorted(paths):
            warmed_files += warm_tile_thumbnails(relative_path)

        self.stdout.write(
            self.style.SUCCESS(
                f"Warmed thumbnails for {len(paths)} photo(s) "
                f"({warmed_files} cached file(s) ensured).",
            ),
        )
