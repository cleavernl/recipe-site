from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from recipes.models import Comment, Ingredient, InstructionStep, Rating, Recipe, RecipePhoto
from recipes.views import purge_expired_deleted_recipes


class RecipeWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            first_name="Pat",
            last_name="Baker",
            password="password-123",
        )
        self.other_user = User.objects.create_user(
            username="cousin",
            first_name="Sam",
            last_name="Cook",
            password="password-123",
        )
        self.recipe = Recipe.objects.create(
            owner=self.owner,
            title="Sunday Pancakes",
            description="Fluffy pancakes for slow mornings.",
            prep_time_minutes=10,
            cook_time_minutes=15,
            servings=4,
        )
        Ingredient.objects.create(recipe=self.recipe, quantity="2 cups", name="flour", order=1)
        InstructionStep.objects.create(recipe=self.recipe, text="Mix and cook.", order=1)

    def test_recipe_list_requires_login(self):
        response = self.client.get(reverse("recipes:list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_random_recipe_requires_login(self):
        response = self.client.get(reverse("recipes:random"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_random_recipe_redirects_to_an_active_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:random"), follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recipe.get_absolute_url())

    def test_random_recipe_when_no_active_recipes_redirects_to_list(self):
        self.recipe.deleted_at = timezone.now()
        self.recipe.save(update_fields=["deleted_at"])
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:random"), follow=True)

        self.assertRedirects(response, reverse("recipes:list"))
        message_text = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("no recipes", message_text.lower())

    def test_recipe_list_has_back_to_top_link(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:list"))

        self.assertContains(response, 'id="top"')
        self.assertContains(response, 'href="#top"')
        self.assertContains(response, reverse("recipes:random"))
        self.assertContains(response, "Pick Something!")

    def test_recipe_list_has_no_search_submit_button_and_live_search_hooks(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:list"))

        self.assertNotContains(response, 'type="submit">Search<')
        self.assertContains(response, "data-recipe-list-search")
        self.assertContains(response, "data-recipe-list-dynamic")

    def test_recipe_list_partial_returns_results_only(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:list"), {"partial": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "recipe-grid")
        self.assertNotContains(response, "Friends and family cookbook")

    def test_authenticated_user_can_create_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.post(
            reverse("recipes:create"),
            {
                "title": "Tomato Soup",
                "description": "A simple family soup.",
                "prep_time_minutes": "15",
                "cook_time_minutes": "30",
                "servings": "6",
                "source_url": "https://example.com/soup",
                "ingredients-TOTAL_FORMS": "1",
                "ingredients-INITIAL_FORMS": "0",
                "ingredients-MIN_NUM_FORMS": "0",
                "ingredients-MAX_NUM_FORMS": "1000",
                "ingredients-0-quantity": "4",
                "ingredients-0-name": "tomatoes",
                "ingredients-0-notes": "large",
                "ingredients-0-order": "1",
                "steps-TOTAL_FORMS": "1",
                "steps-INITIAL_FORMS": "0",
                "steps-MIN_NUM_FORMS": "0",
                "steps-MAX_NUM_FORMS": "1000",
                "steps-0-text": "Simmer everything until soft.",
                "steps-0-order": "1",
                "photos-TOTAL_FORMS": "1",
                "photos-INITIAL_FORMS": "0",
                "photos-MIN_NUM_FORMS": "0",
                "photos-MAX_NUM_FORMS": "1000",
            },
        )

        recipe = Recipe.objects.get(title="Tomato Soup")
        self.assertRedirects(response, recipe.get_absolute_url())
        self.assertEqual(recipe.owner, self.other_user)
        self.assertEqual(recipe.ingredients.count(), 1)
        self.assertEqual(recipe.steps.count(), 1)

    def test_recipe_form_has_dynamic_formset_hooks_without_extra_delete_boxes(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:create"))
        content = response.content.decode()

        self.assertContains(response, "data-formset-template", count=3)
        self.assertContains(response, "data-unsaved-warning")
        self.assertContains(response, "data-discard-changes", count=1)
        self.assertContains(response, "Back to recipes")
        self.assertNotContains(response, 'type="checkbox" name="ingredients-0-DELETE"')
        self.assertNotContains(response, 'data-form-row draggable="true"')
        self.assertContains(response, 'data-drag-handle aria-label="Drag to reorder ingredient"')
        self.assertLess(content.index("<h2>Photos</h2>"), content.index("<h2>Ingredients</h2>"))

    def test_non_owner_cannot_edit_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("recipes:update", kwargs={"slug": self.recipe.slug}),
        )

        self.assertEqual(response.status_code, 403)

    def test_edit_recipe_shows_single_extra_rows_for_existing_items(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("recipes:update", kwargs={"slug": self.recipe.slug}))

        ingredient_formset = response.context["ingredient_formset"]
        step_formset = response.context["step_formset"]
        self.assertContains(response, self.recipe.get_absolute_url())
        self.assertContains(response, "Back to recipe")
        self.assertEqual(ingredient_formset.initial_form_count(), 1)
        self.assertEqual(step_formset.initial_form_count(), 1)
        self.assertEqual(ingredient_formset.total_form_count(), 2)
        self.assertEqual(step_formset.total_form_count(), 2)

    def test_order_only_blank_extra_rows_are_ignored_on_edit(self):
        self.client.force_login(self.owner)
        ingredient = self.recipe.ingredients.get()
        step = self.recipe.steps.get()

        response = self.client.post(
            reverse("recipes:update", kwargs={"slug": self.recipe.slug}),
            {
                "title": self.recipe.title,
                "description": self.recipe.description,
                "prep_time_minutes": str(self.recipe.prep_time_minutes),
                "cook_time_minutes": str(self.recipe.cook_time_minutes),
                "servings": str(self.recipe.servings),
                "source_url": "",
                "ingredients-TOTAL_FORMS": "2",
                "ingredients-INITIAL_FORMS": "1",
                "ingredients-MIN_NUM_FORMS": "0",
                "ingredients-MAX_NUM_FORMS": "1000",
                "ingredients-0-id": str(ingredient.id),
                "ingredients-0-quantity": ingredient.quantity,
                "ingredients-0-name": ingredient.name,
                "ingredients-0-notes": ingredient.notes,
                "ingredients-0-order": "1",
                "ingredients-1-order": "2",
                "steps-TOTAL_FORMS": "2",
                "steps-INITIAL_FORMS": "1",
                "steps-MIN_NUM_FORMS": "0",
                "steps-MAX_NUM_FORMS": "1000",
                "steps-0-id": str(step.id),
                "steps-0-text": step.text,
                "steps-0-order": "1",
                "steps-1-order": "2",
                "photos-TOTAL_FORMS": "1",
                "photos-INITIAL_FORMS": "0",
                "photos-MIN_NUM_FORMS": "0",
                "photos-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertRedirects(response, self.recipe.get_absolute_url())
        self.assertEqual(self.recipe.ingredients.count(), 1)
        self.assertEqual(self.recipe.steps.count(), 1)

    def test_new_ingredient_without_order_sorts_after_existing_ingredients(self):
        self.client.force_login(self.owner)
        ingredient = self.recipe.ingredients.get()
        step = self.recipe.steps.get()

        response = self.client.post(
            reverse("recipes:update", kwargs={"slug": self.recipe.slug}),
            {
                "title": self.recipe.title,
                "description": self.recipe.description,
                "prep_time_minutes": str(self.recipe.prep_time_minutes),
                "cook_time_minutes": str(self.recipe.cook_time_minutes),
                "servings": str(self.recipe.servings),
                "source_url": "",
                "ingredients-TOTAL_FORMS": "2",
                "ingredients-INITIAL_FORMS": "1",
                "ingredients-MIN_NUM_FORMS": "0",
                "ingredients-MAX_NUM_FORMS": "1000",
                "ingredients-0-id": str(ingredient.id),
                "ingredients-0-quantity": ingredient.quantity,
                "ingredients-0-name": ingredient.name,
                "ingredients-0-notes": ingredient.notes,
                "ingredients-0-order": "1",
                "ingredients-1-quantity": "1 tbsp",
                "ingredients-1-name": "sugar",
                "ingredients-1-notes": "",
                "steps-TOTAL_FORMS": "2",
                "steps-INITIAL_FORMS": "1",
                "steps-MIN_NUM_FORMS": "0",
                "steps-MAX_NUM_FORMS": "1000",
                "steps-0-id": str(step.id),
                "steps-0-text": step.text,
                "steps-0-order": "1",
                "photos-TOTAL_FORMS": "1",
                "photos-INITIAL_FORMS": "0",
                "photos-MIN_NUM_FORMS": "0",
                "photos-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertRedirects(response, self.recipe.get_absolute_url())
        self.assertEqual(
            list(self.recipe.ingredients.values_list("name", flat=True)),
            ["flour", "sugar"],
        )

    def test_recipe_detail_renders_star_rating_form(self):
        Ingredient.objects.create(
            recipe=self.recipe,
            quantity="2",
            name="lemons",
            notes="zested",
            order=2,
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))
        content = response.content.decode()

        self.assertContains(response, "recipe-cook-layout")
        self.assertContains(response, "ingredient-card")
        self.assertContains(response, "ingredient-quantity")
        self.assertContains(response, "ingredient-name")
        self.assertContains(response, "ingredient-line")
        self.assertContains(response, "ingredient-meta")
        self.assertContains(response, "ingredient-note")
        self.assertContains(response, "instruction-card")
        self.assertContains(response, "star-rating-field")
        self.assertContains(response, "5 stars")
        self.assertLess(content.index('value="1"'), content.index('value="5"'))

    def test_authenticated_user_can_comment_and_rate(self):
        self.client.force_login(self.other_user)

        comment_response = self.client.post(
            reverse("recipes:comment", kwargs={"slug": self.recipe.slug}),
            {"body": "This worked perfectly."},
        )
        rating_response = self.client.post(
            reverse("recipes:rate", kwargs={"slug": self.recipe.slug}),
            {"value": "5"},
        )

        self.assertRedirects(comment_response, f"{self.recipe.get_absolute_url()}#comment-form")
        self.assertRedirects(rating_response, f"{self.recipe.get_absolute_url()}#discussion")
        self.assertTrue(Comment.objects.filter(recipe=self.recipe, author=self.other_user).exists())
        self.assertEqual(Rating.objects.get(recipe=self.recipe, user=self.other_user).value, 5)

        detail_response = self.client.get(
            reverse("recipes:detail", kwargs={"slug": self.recipe.slug})
        )
        self.assertContains(detail_response, "Sam C.")
        self.assertContains(detail_response, "1 review")

    def test_recipe_detail_can_reverse_sort_comments(self):
        older_comment = Comment.objects.create(
            recipe=self.recipe,
            author=self.owner,
            body="This is the older note.",
        )
        newer_comment = Comment.objects.create(
            recipe=self.recipe,
            author=self.other_user,
            body="This is the newer note.",
        )
        Comment.objects.filter(id=older_comment.id).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        Comment.objects.filter(id=newer_comment.id).update(created_at=timezone.now())
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("recipes:detail", kwargs={"slug": self.recipe.slug}),
            {"comments": "newest"},
        )
        content = response.content.decode()

        self.assertContains(response, "Newest first")
        self.assertContains(response, "comment-meta")
        self.assertContains(response, 'data-comment-sort="newest"')
        self.assertContains(response, "data-comments-list")
        self.assertContains(response, "data-comment-created-at")
        self.assertLess(
            content.index("This is the newer note."),
            content.index("This is the older note."),
        )

    def test_recipe_detail_shows_average_and_recent_reviewers(self):
        older_rating = Rating.objects.create(recipe=self.recipe, user=self.owner, value=4)
        newer_rating = Rating.objects.create(recipe=self.recipe, user=self.other_user, value=5)
        Rating.objects.filter(id=older_rating.id).update(
            updated_at=timezone.now() - timedelta(days=1)
        )
        Rating.objects.filter(id=newer_rating.id).update(updated_at=timezone.now())
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))
        content = response.content.decode()

        self.assertContains(response, "4.5 out of 5")
        self.assertContains(response, "--rating-percent: 90.0%;")
        self.assertContains(response, "(you)")
        self.assertContains(response, "data-rating-reviewer-name")
        self.assertLess(content.index("Sam C."), content.index("Pat B."))
        self.assertNotContains(response, "Show reviewers")

    def test_ajax_rating_saves_without_redirect(self):
        self.client.force_login(self.other_user)

        response = self.client.post(
            reverse("recipes:rate", kwargs={"slug": self.recipe.slug}),
            {"value": "4"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "message": "Rating saved.",
                "rating": 4,
                "average": 4.0,
                "average_percent": 80.0,
                "count": 1,
                "reviewer_label": "Sam C. (you)",
                "user_id": self.other_user.id,
                "user_name": "Sam C.",
            },
        )
        self.assertEqual(Rating.objects.get(recipe=self.recipe, user=self.other_user).value, 4)

    def test_recipe_list_tile_shows_average_rating(self):
        Rating.objects.create(recipe=self.recipe, user=self.owner, value=4)
        Rating.objects.create(recipe=self.recipe, user=self.other_user, value=5)
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:list"))

        self.assertContains(response, "4.5 ★ (2)")

    def test_deleting_owner_preserves_recipe(self):
        owner_id = self.owner.id

        self.owner.delete()
        self.recipe.refresh_from_db()

        self.assertIsNone(self.recipe.owner)
        self.assertFalse(User.objects.filter(id=owner_id).exists())
        self.assertTrue(Recipe.objects.filter(id=self.recipe.id).exists())

    def test_any_user_can_soft_delete_and_restore_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.post(reverse("recipes:delete", kwargs={"slug": self.recipe.slug}))
        self.recipe.refresh_from_db()

        self.assertRedirects(response, reverse("recipes:list"))
        self.assertIsNotNone(self.recipe.deleted_at)
        self.assertContains(self.client.get(reverse("recipes:list")), "Recently deleted")
        self.assertContains(self.client.get(reverse("recipes:recently_deleted")), self.recipe.title)

        restore_response = self.client.post(
            reverse("recipes:restore", kwargs={"slug": self.recipe.slug})
        )
        self.recipe.refresh_from_db()

        self.assertRedirects(restore_response, self.recipe.get_absolute_url())
        self.assertIsNone(self.recipe.deleted_at)

    def test_recipe_list_uses_same_tiles_for_active_and_recently_deleted_recipes(self):
        deleted_recipe = Recipe.objects.create(
            owner=self.owner,
            title="Recently Deleted Soup",
            description="A deleted recipe with the normal tile layout.",
            deleted_at=timezone.now(),
        )
        active_recipe = Recipe.objects.create(
            owner=self.owner,
            title="Active Soup",
            description="An active recipe with the normal tile layout.",
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:list"))

        self.assertContains(response, '<article class="recipe-card">', count=3)
        self.assertContains(response, active_recipe.title)
        self.assertContains(response, deleted_recipe.title)
        self.assertNotContains(response, "deleted-card")
        self.assertNotContains(response, "restore-card-form")

    def test_recently_deleted_page_uses_recipe_cards(self):
        deleted_recipe = Recipe.objects.create(
            owner=self.owner,
            title="Deleted Card Recipe",
            deleted_at=timezone.now(),
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:recently_deleted"))

        self.assertContains(response, "recipe-card")
        self.assertContains(response, deleted_recipe.title)

    def test_recipe_detail_shows_additional_photos(self):
        RecipePhoto.objects.create(
            recipe=self.recipe,
            image="recipes/photos/test.jpg",
            caption="Finished",
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, "Finished")

    def test_expired_deleted_recipe_is_purged(self):
        self.recipe.deleted_at = timezone.now() - timedelta(days=8)
        self.recipe.save(update_fields=["deleted_at"])

        deleted_count = purge_expired_deleted_recipes()

        self.assertGreaterEqual(deleted_count, 1)
        self.assertFalse(Recipe.objects.filter(id=self.recipe.id).exists())

    def test_print_view_requires_login(self):
        response = self.client.get(reverse("recipes:print", kwargs={"slug": self.recipe.slug}))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


@override_settings(DEBUG=False)
class ProtectedMediaServingTests(TestCase):
    def setUp(self):
        self.temp_media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_media_root, ignore_errors=True))
        self.override = override_settings(MEDIA_ROOT=self.temp_media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.user = User.objects.create_user(username="media-user", password="password-123")
        self.recipe = Recipe.objects.create(owner=self.user, title="Photo Test")
        self.recipe.photo.save(
            "media-test.jpg",
            SimpleUploadedFile("media-test.jpg", b"fake-image-content", content_type="image/jpeg"),
            save=True,
        )

    def test_media_file_requires_login_when_debug_false(self):
        response = self.client.get(self.recipe.photo.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_authenticated_user_can_access_media_file_when_debug_false(self):
        self.client.force_login(self.user)

        response = self.client.get(self.recipe.photo.url)

        self.assertEqual(response.status_code, 200)
