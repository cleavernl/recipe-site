from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase

from recipes.models import Ingredient, Recipe, RecipePhoto
from recipes.yaml_import import RecipeImportError, import_recipes_from_directory


class ImportRecipesTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            username="cleavernl",
            password="password-123",
        )
        self.other = User.objects.create_user(
            username="other",
            password="password-123",
        )

    def _write_recipe_dir(self, tmp: Path, *, extra_yaml: str = "") -> Path:
        recipe = tmp / "sample.recipe.yaml"
        recipe.write_text(
            """
title: Sample Import Soup
description: |
  A test recipe.
prep_time_minutes: 10
cook_time_minutes: 20
servings: 4
source_url: ""
tags:
  - soup
  - dessert
ingredients:
  - quantity: "1 cup"
    name: broth
    notes: ""
steps:
  - Simmer and serve.
photos: []
""".strip()
            + extra_yaml,
            encoding="utf-8",
        )
        return tmp

    def test_dry_run_does_not_create_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            directory = self._write_recipe_dir(Path(tmp_name))
            results = import_recipes_from_directory(
                owner=self.owner,
                directory=directory,
                dry_run=True,
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "would_import")
        self.assertEqual(Recipe.objects.count(), 0)

    def test_import_creates_recipe_with_related_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            directory = self._write_recipe_dir(Path(tmp_name))
            results = import_recipes_from_directory(
                owner=self.owner,
                directory=directory,
            )
        self.assertEqual(results[0].action, "imported")
        recipe = Recipe.objects.get(title="Sample Import Soup")
        self.assertEqual(recipe.owner, self.owner)
        self.assertEqual(recipe.prep_time_minutes, 10)
        self.assertEqual(recipe.ingredients.count(), 1)
        self.assertEqual(recipe.steps.count(), 1)
        tag_slugs = set(recipe.tags.values_list("slug", flat=True))
        self.assertEqual(tag_slugs, {"soup", "dessert"})

    def test_skip_existing_by_title(self) -> None:
        Recipe.objects.create(owner=self.owner, title="Sample Import Soup")
        with tempfile.TemporaryDirectory() as tmp_name:
            directory = self._write_recipe_dir(Path(tmp_name))
            results = import_recipes_from_directory(
                owner=self.owner,
                directory=directory,
                skip_existing=True,
            )
        self.assertEqual(results[0].action, "skipped")
        self.assertEqual(Recipe.objects.count(), 1)

    def test_import_attaches_photo_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            directory = Path(tmp_name)
            images = directory / "images"
            images.mkdir()
            tiny_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
                b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            (images / "sample-1.png").write_bytes(tiny_png)
            recipe = directory / "photo.recipe.yaml"
            recipe.write_text(
                """
title: Photo Sample
description: ""
tags: []
ingredients:
  - quantity: ""
    name: salt
    notes: ""
steps:
  - Mix.
photos:
  - path: images/sample-1.png
    caption: Test shot
""".strip(),
                encoding="utf-8",
            )
            import_recipes_from_directory(owner=self.owner, directory=directory)

        recipe = Recipe.objects.get(title="Photo Sample")
        self.assertEqual(recipe.photos.count(), 1)
        photo = RecipePhoto.objects.get(recipe=recipe)
        self.assertTrue(photo.image.name.endswith(".png"))
        self.assertEqual(photo.caption, "Test shot")

    def test_missing_photo_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            directory = Path(tmp_name)
            recipe = directory / "bad-photo.recipe.yaml"
            recipe.write_text(
                """
title: Missing Photo
description: ""
tags: []
ingredients: []
steps:
  - Step.
photos:
  - path: images/nope.png
    caption: ""
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaises(RecipeImportError):
                import_recipes_from_directory(owner=self.owner, directory=directory)

    def test_management_command_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            directory = self._write_recipe_dir(Path(tmp_name))
            self.assertEqual(
                Ingredient.objects.count(),
                0,
            )
            from django.core.management import call_command

            call_command(
                "import_recipes",
                "--owner",
                "cleavernl",
                "--dir",
                str(directory),
            )
        self.assertTrue(Recipe.objects.filter(title="Sample Import Soup").exists())
