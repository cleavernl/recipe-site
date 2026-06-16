from __future__ import annotations

import io
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from recipes.models import Recipe, RecipePhoto
from recipes.thumbnails import (
    TILE_IMAGE_LIST_WIDTH,
    TILE_IMAGE_MAX_WIDTH,
    ensure_thumbnail,
    thumbnail_url,
    warm_tile_thumbnails,
)


def make_test_jpeg(*, width: int = 1600, height: int = 1200) -> bytes:
    image = Image.new("RGB", (width, height), color=(220, 120, 80))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


@override_settings(DEBUG=False)
class RecipeThumbnailTests(TestCase):
    def setUp(self):
        self.temp_media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_media_root, ignore_errors=True))
        self.override = override_settings(MEDIA_ROOT=self.temp_media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.user = User.objects.create_user(username="thumb-user", password="password-123")
        self.recipe = Recipe.objects.create(owner=self.user, title="Thumb Test")
        self.recipe.photo.save(
            "recipes/photos/thumb-test.jpg",
            SimpleUploadedFile("thumb-test.jpg", make_test_jpeg(), content_type="image/jpeg"),
            save=True,
        )

    def test_thumbnail_url_builds_media_path(self):
        self.assertEqual(
            thumbnail_url(self.recipe.photo.name),
            f"/media/thumb/{TILE_IMAGE_MAX_WIDTH}/{self.recipe.photo.name}",
        )

    def test_ensure_thumbnail_creates_smaller_cached_file(self):
        cache_path = ensure_thumbnail(self.recipe.photo.name, TILE_IMAGE_MAX_WIDTH)

        self.assertIsNotNone(cache_path)
        assert cache_path is not None
        self.assertTrue(cache_path.is_file())
        with Image.open(cache_path) as cached:
            self.assertLessEqual(cached.width, TILE_IMAGE_MAX_WIDTH)

    def test_thumbnail_endpoint_requires_login(self):
        response = self.client.get(thumbnail_url(self.recipe.photo.name))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_authenticated_user_can_fetch_thumbnail(self):
        self.client.force_login(self.user)

        response = self.client.get(thumbnail_url(self.recipe.photo.name))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        payload = b"".join(response.streaming_content)
        with Image.open(io.BytesIO(payload)) as served:
            self.assertLessEqual(served.width, TILE_IMAGE_MAX_WIDTH)

    def test_warm_tile_thumbnails_creates_both_sizes(self):
        warmed = warm_tile_thumbnails(self.recipe.photo.name)

        self.assertEqual(warmed, 2)
        self.assertIsNotNone(ensure_thumbnail(self.recipe.photo.name, TILE_IMAGE_LIST_WIDTH))
        self.assertIsNotNone(ensure_thumbnail(self.recipe.photo.name, TILE_IMAGE_MAX_WIDTH))

    def test_recipe_list_uses_thumbnail_urls(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("recipes:list"))

        self.assertContains(
            response,
            f'/media/thumb/{TILE_IMAGE_LIST_WIDTH}/{self.recipe.photo.name}"',
        )
        self.assertNotContains(response, f'src="/media/{self.recipe.photo.name}"')

    def test_recipe_list_prefers_gallery_over_legacy_photo(self):
        RecipePhoto.objects.create(
            recipe=self.recipe,
            image=SimpleUploadedFile(
                "gallery-only.jpg",
                make_test_jpeg(width=800, height=600),
                content_type="image/jpeg",
            ),
            order=1,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("recipes:list"))

        self.assertEqual(response.content.count(b"data-carousel-slide"), 1)
        self.assertContains(
            response,
            f'/media/thumb/{TILE_IMAGE_LIST_WIDTH}/recipes/photos/gallery-only.jpg"',
        )
        self.assertNotContains(
            response,
            f'/media/thumb/{TILE_IMAGE_MAX_WIDTH}/{self.recipe.photo.name}"',
        )

    def test_recipe_detail_uses_full_size_image_urls(self):
        RecipePhoto.objects.create(
            recipe=self.recipe,
            image=SimpleUploadedFile(
                "detail-gallery.jpg",
                make_test_jpeg(width=1200, height=900),
                content_type="image/jpeg",
            ),
            order=1,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, 'src="/media/recipes/photos/detail-gallery.jpg"')
        self.assertNotContains(response, "/media/thumb/")
