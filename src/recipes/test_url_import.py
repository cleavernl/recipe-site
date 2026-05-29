from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from recipes.models import Recipe, RecipePhoto
from recipes.test_url_import_parsing import SAMPLE_RECIPE_HTML
from recipes.url_import import (
    RECIPE_URL_IMPORT_SESSION_KEY,
    draft_form_initial_from_document,
    fetch_and_parse_recipe_url,
    new_staging_token,
    parse_recipe_html,
    stage_recipe_photos,
    validate_staged_photo_path,
)


class RecipeImageStagingTests(TestCase):
    @patch("recipes.url_import.fetch_url_bytes")
    def test_stage_recipe_photos_writes_preview_files(self, fetch_mock) -> None:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (12, 12), color=(240, 120, 60)).save(buffer, format="JPEG")
        fetch_mock.return_value = buffer.getvalue()

        recipe = {"image": ["https://example.com/hero.jpg"]}
        token = new_staging_token()
        staged = stage_recipe_photos(recipe, referer="https://example.com/recipe", token=token)

        self.assertEqual(len(staged), 1)
        full_path = validate_staged_photo_path(staged[0]["storage_path"])
        self.assertTrue(full_path.is_file())
        self.assertTrue(staged[0]["preview_url"].endswith(staged[0]["storage_path"]))


class StagedPhotoSaveTests(TestCase):
    @patch("recipes.url_import.fetch_url_bytes")
    def test_save_skips_removed_staged_photos(self, fetch_mock) -> None:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
        fetch_mock.return_value = buffer.getvalue()

        user = User.objects.create_user(username="staged-del", password="password-123")
        self.client.force_login(user)

        doc = parse_recipe_html(SAMPLE_RECIPE_HTML, source_url="https://example.com/chili")
        token = new_staging_token()
        doc["staged_photos"] = stage_recipe_photos(
            {"image": ["https://example.com/hero.jpg"]},
            referer="https://example.com/chili",
            token=token,
        )
        staged = doc["staged_photos"]
        self.assertEqual(len(staged), 1)
        staged_path = staged[0]["storage_path"]

        post_data = {
            "title": "No Import Photos",
            "description": "Test",
            "prep_time_minutes": "15",
            "cook_time_minutes": "45",
            "servings": "6",
            "source_url": "https://example.com/chili",
            "ingredients-TOTAL_FORMS": "2",
            "ingredients-INITIAL_FORMS": "0",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "1000",
            "ingredients-0-quantity": "1 tbsp",
            "ingredients-0-name": "oil",
            "ingredients-0-order": "1",
            "ingredients-1-quantity": "2",
            "ingredients-1-name": "beans",
            "ingredients-1-order": "2",
            "steps-TOTAL_FORMS": "2",
            "steps-INITIAL_FORMS": "0",
            "steps-MIN_NUM_FORMS": "0",
            "steps-MAX_NUM_FORMS": "1000",
            "steps-0-text": "Heat oil.",
            "steps-0-order": "1",
            "steps-1-text": "Add beans.",
            "steps-1-order": "2",
            "tags-TOTAL_FORMS": "2",
            "tags-INITIAL_FORMS": "0",
            "tags-MIN_NUM_FORMS": "0",
            "tags-MAX_NUM_FORMS": "1000",
            "tags-0-tag_name": "dinner",
            "tags-1-tag_name": "soup",
            "photos-TOTAL_FORMS": "2",
            "photos-INITIAL_FORMS": "0",
            "photos-MIN_NUM_FORMS": "0",
            "photos-MAX_NUM_FORMS": "1000",
            "photos-0-DELETE": "on",
            "photos-0-staged_path": staged_path,
            "photos-1-DELETE": "",
            "photos-1-order": "",
            "photos-1-caption": "",
        }

        response = self.client.post(reverse("recipes:create"), post_data)
        recipe = Recipe.objects.get(title="No Import Photos")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RecipePhoto.objects.filter(recipe=recipe).count(), 0)


class RecipeImportFromUrlViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="importer", password="password-123")

    @patch("recipes.views.fetch_and_parse_recipe_url")
    def test_import_stores_draft_and_redirects_to_create(self, fetch_mock) -> None:
        fetch_mock.return_value = parse_recipe_html(SAMPLE_RECIPE_HTML, source_url="https://example.com/chili")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("recipes:import_url"),
            {"url": "https://example.com/chili"},
        )

        self.assertRedirects(
            response,
            reverse("recipes:create"),
            fetch_redirect_response=False,
        )
        session = self.client.session
        draft = session.get(RECIPE_URL_IMPORT_SESSION_KEY)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["recipe"]["title"], "Weeknight Chili")

    @patch("recipes.views.fetch_and_parse_recipe_url")
    def test_create_page_prefills_form_from_import_draft(self, fetch_mock) -> None:
        fetch_mock.return_value = parse_recipe_html(SAMPLE_RECIPE_HTML, source_url="https://example.com/chili")
        self.client.force_login(self.user)
        self.client.post(reverse("recipes:import_url"), {"url": "https://example.com/chili"})

        response = self.client.get(reverse("recipes:create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Weeknight Chili")
        self.assertContains(response, "olive oil")
        self.assertContains(response, "kidney beans")
        self.assertContains(response, "Heat oil in a pot.")
        self.assertContains(response, "Add beans and simmer 30 minutes.")
        ingredient_formset = response.context["ingredient_formset"]
        step_formset = response.context["step_formset"]
        self.assertEqual(ingredient_formset.total_form_count(), 3)
        self.assertEqual(step_formset.total_form_count(), 3)
        self.assertNotIn(RECIPE_URL_IMPORT_SESSION_KEY, self.client.session)

    @patch("recipes.views.fetch_and_parse_recipe_url")
    def test_import_failure_shows_message(self, fetch_mock) -> None:
        from recipes.yaml_import import RecipeImportError

        fetch_mock.side_effect = RecipeImportError("No recipe data was found on that page.")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("recipes:import_url"),
            {"url": "https://example.com/empty"},
            follow=True,
        )

        self.assertContains(response, "No recipe data was found on that page.")

    def test_import_requires_login(self) -> None:
        response = self.client.post(reverse("recipes:import_url"), {"url": "https://example.com/chili"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    @patch("recipes.url_import.fetch_url_text")
    def test_fetch_and_parse_recipe_url_uses_html_parser(self, fetch_mock) -> None:
        fetch_mock.return_value = SAMPLE_RECIPE_HTML
        document = fetch_and_parse_recipe_url("https://example.com/chili")
        self.assertEqual(document["title"], "Weeknight Chili")
        fetch_mock.assert_called_once_with("https://example.com/chili")

    @patch("recipes.url_import.stage_recipe_photos")
    @patch("recipes.url_import.fetch_url_text")
    def test_fetch_and_parse_can_stage_photos(self, fetch_mock, stage_mock) -> None:
        fetch_mock.return_value = SAMPLE_RECIPE_HTML
        stage_mock.return_value = [
            {
                "storage_path": "url_import/staging/t/photo-0.jpg",
                "preview_url": "/media/url_import/staging/t/photo-0.jpg",
                "caption": "",
                "order": "0",
            },
        ]
        document = fetch_and_parse_recipe_url("https://example.com/chili", stage_photos_token="t")
        self.assertEqual(len(document["staged_photos"]), 1)
        stage_mock.assert_called_once()
