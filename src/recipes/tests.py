from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from recipes.forms import RecipeTagLineFormSet
from recipes.models import (
    Comment,
    Ingredient,
    InstructionStep,
    Rating,
    Recipe,
    RecipeMade,
    RecipePhoto,
    Tag,
    sync_recipe_tags,
)
from recipes.views import RecipeListView, purge_expired_deleted_recipes


def tag_formset_post_data(names: list[str]) -> dict[str, str]:
    """POST keys for the tags line formset (prefix ``tags``)."""
    n = len(names)
    data: dict[str, str] = {
        "tags-TOTAL_FORMS": str(n),
        "tags-INITIAL_FORMS": "0",
        "tags-MIN_NUM_FORMS": "0",
        "tags-MAX_NUM_FORMS": "40",
    }
    for i, name in enumerate(names):
        data[f"tags-{i}-tag_name"] = name
    return data


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

    def test_random_recipe_respects_search_query(self):
        other = Recipe.objects.create(
            owner=self.owner,
            title="Zesty Limeade",
            description="Cool and tart.",
        )
        self.client.force_login(self.other_user)

        response = self.client.get(f"{reverse('recipes:random')}?q=Limeade", follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], other.get_absolute_url())

    def test_random_recipe_no_match_for_search_redirects_to_list(self):
        self.client.force_login(self.other_user)

        response = self.client.get(f"{reverse('recipes:random')}?q=nonexistent", follow=True)

        self.assertRedirects(response, f"{reverse('recipes:list')}?q=nonexistent")
        message_text = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("match", message_text.lower())

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
        self.assertContains(response, "data-recipe-search-panel")
        self.assertContains(response, "data-recipe-random-pick")
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
        self.assertNotContains(response, 'aria-label="Recipe pages"')

    def test_recipe_list_shows_infinite_scroll_sentinel_when_more_pages(self):
        self.client.force_login(self.other_user)
        with patch.object(RecipeListView, "paginate_by", 2):
            for index in range(3):
                Recipe.objects.create(
                    owner=self.owner,
                    title=f"Scroll Recipe {index}",
                    description="",
                )
            response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, "data-recipe-list-sentinel")
        self.assertContains(response, 'data-next-page="2"')
        self.assertNotContains(response, 'aria-label="Recipe pages"')

    def test_recipe_list_partial_append_returns_next_cards_only(self):
        self.client.force_login(self.other_user)
        with patch.object(RecipeListView, "paginate_by", 2):
            for index in range(3):
                Recipe.objects.create(
                    owner=self.owner,
                    title=f"Append Recipe {index}",
                    description="",
                )
            response = self.client.get(
                reverse("recipes:list"),
                {"partial": "append", "page": "2"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-recipe-list-append-chunk")
        self.assertContains(response, "Append Recipe 2")
        self.assertNotContains(response, "data-recipe-list-sync-extras")
        self.assertNotContains(response, "Friends and family cookbook")

    def test_recipe_list_sorts_by_rating_high_first(self):
        self.client.force_login(self.other_user)
        low = Recipe.objects.create(owner=self.owner, title="Low Rated Soup")
        high = Recipe.objects.create(owner=self.owner, title="High Rated Pie")
        Rating.objects.create(recipe=low, user=self.owner, value=2)
        Rating.objects.create(recipe=high, user=self.owner, value=5)

        response = self.client.get(reverse("recipes:list"), {"sort": "rating"})
        body = response.content.decode()

        self.assertLess(body.index("High Rated Pie"), body.index("Low Rated Soup"))

    def test_recipe_list_sorts_by_rating_asc_low_first(self):
        self.client.force_login(self.other_user)
        low = Recipe.objects.create(owner=self.owner, title="Low Rated Soup")
        high = Recipe.objects.create(owner=self.owner, title="High Rated Pie")
        Rating.objects.create(recipe=low, user=self.owner, value=2)
        Rating.objects.create(recipe=high, user=self.owner, value=5)

        response = self.client.get(reverse("recipes:list"), {"sort": "rating", "sort_dir": "asc"})
        body = response.content.decode()

        self.assertLess(body.index("Low Rated Soup"), body.index("High Rated Pie"))

    def test_recipe_list_invalid_sort_dir_uses_default(self):
        self.client.force_login(self.other_user)
        low = Recipe.objects.create(owner=self.owner, title="Low Rated Soup")
        high = Recipe.objects.create(owner=self.owner, title="High Rated Pie")
        Rating.objects.create(recipe=low, user=self.owner, value=2)
        Rating.objects.create(recipe=high, user=self.owner, value=5)

        response = self.client.get(
            reverse("recipes:list"),
            {"sort": "rating", "sort_dir": "not-a-direction"},
        )
        body = response.content.decode()

        self.assertLess(body.index("High Rated Pie"), body.index("Low Rated Soup"))

    def test_recipe_list_sorts_by_ease_fewer_work_first(self):
        self.client.force_login(self.other_user)
        simple = Recipe.objects.create(owner=self.owner, title="AAA Simple Snack")
        Ingredient.objects.create(recipe=simple, name="salt", order=1)
        InstructionStep.objects.create(recipe=simple, text="Eat.", order=1)
        heavy = Recipe.objects.create(owner=self.owner, title="ZZZ Heavy Feast")
        for i in range(5):
            Ingredient.objects.create(recipe=heavy, name=f"ingredient{i}", order=i)
        for i in range(5):
            InstructionStep.objects.create(recipe=heavy, text=f"step {i}", order=i)

        response = self.client.get(reverse("recipes:list"), {"sort": "ease"})
        body = response.content.decode()

        self.assertLess(body.index("AAA Simple Snack"), body.index("ZZZ Heavy Feast"))

    def test_recipe_list_invalid_sort_is_ignored(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:list"), {"sort": "not-a-real-sort"})

        self.assertEqual(response.status_code, 200)

    def test_recipe_list_sorts_title_case_insensitive(self):
        self.client.force_login(self.other_user)
        Recipe.objects.create(owner=self.owner, title="Zebra Cake")
        Recipe.objects.create(owner=self.owner, title="apple Tart")
        Recipe.objects.create(owner=self.owner, title="Banana Bread")

        response = self.client.get(reverse("recipes:list"))
        body = response.content.decode()

        self.assertLess(body.index("apple Tart"), body.index("Banana Bread"))
        self.assertLess(body.index("Banana Bread"), body.index("Zebra Cake"))

    def test_infinite_scroll_preserves_sort_in_search_form(self):
        self.client.force_login(self.other_user)
        for index in range(24):
            Recipe.objects.create(owner=self.owner, title=f"Bulk Recipe {index:02d}")

        response = self.client.get(reverse("recipes:list"), {"sort": "rating"})

        self.assertContains(response, 'name="sort" value="rating"')
        self.assertContains(response, "data-recipe-list-sentinel")

    def test_infinite_scroll_preserves_sort_dir_in_search_form(self):
        self.client.force_login(self.other_user)
        for index in range(24):
            Recipe.objects.create(owner=self.owner, title=f"Bulk Recipe {index:02d}")

        response = self.client.get(
            reverse("recipes:list"),
            {"sort": "rating", "sort_dir": "asc"},
        )

        self.assertContains(response, 'name="sort" value="rating"')
        self.assertContains(response, 'name="sort_dir" value="asc"')

    def test_sync_recipe_tags_sets_many_to_many(self):
        recipe = Recipe.objects.create(owner=self.owner, title="Tagged Dish")
        sync_recipe_tags(recipe, "Breakfast, Quick, Comfort Food")
        self.assertEqual(recipe.tags.count(), 3)
        self.assertCountEqual(
            list(recipe.tags.values_list("slug", flat=True)),
            ["breakfast", "quick", "comfort-food"],
        )

    def test_sync_recipe_tags_case_insensitive_reuses_single_tag(self):
        Tag.objects.create(name="Vegan", slug="vegan")
        recipe = Recipe.objects.create(owner=self.owner, title="Tagged Dish")
        sync_recipe_tags(recipe, "vegan, VEGAN, Vegan")
        self.assertEqual(recipe.tags.count(), 1)
        self.assertEqual(recipe.tags.get().name, "Vegan")

    def test_recipe_tag_formset_similar_tag_adds_recommendation_note(self):
        Tag.objects.create(name="Dessert", slug="dessert")
        fs = RecipeTagLineFormSet(
            {
                "tags-TOTAL_FORMS": "1",
                "tags-INITIAL_FORMS": "0",
                "tags-MIN_NUM_FORMS": "0",
                "tags-MAX_NUM_FORMS": "40",
                "tags-0-tag_name": "desser",
            },
            prefix="tags",
        )
        self.assertTrue(fs.is_valid())
        self.assertTrue(any("Dessert" in note for note in fs.similar_tag_notes))

    def test_recipe_tag_formset_similar_broring_when_typo_boring(self):
        Tag.objects.create(name="Broring", slug="broring")
        fs = RecipeTagLineFormSet(
            {
                "tags-TOTAL_FORMS": "1",
                "tags-INITIAL_FORMS": "0",
                "tags-MIN_NUM_FORMS": "0",
                "tags-MAX_NUM_FORMS": "40",
                "tags-0-tag_name": "boring",
            },
            prefix="tags",
        )
        self.assertTrue(fs.is_valid())
        self.assertTrue(any("Broring" in note for note in fs.similar_tag_notes))

    def test_owner_can_quick_add_tag_from_detail(self):
        self.client.force_login(self.owner)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(url, {"tag": "  Brunch  "}, follow=True)
        self.assertRedirects(response, self.recipe.get_absolute_url())
        self.recipe.refresh_from_db()
        self.assertTrue(self.recipe.tags.filter(name__iexact="Brunch").exists())

    def test_quick_add_similar_tag_no_ack_shows_warning(self):
        Tag.objects.create(name="Broring", slug="broring")
        self.client.force_login(self.owner)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(url, {"tag": "boring"}, follow=True)
        self.assertRedirects(response, self.recipe.get_absolute_url())
        message_text = " ".join(str(m) for m in response.context["messages"]).lower()
        self.assertIn("broring", message_text)
        self.assertIn("existing tag", message_text)

    def test_quick_add_similar_tag_preflight_json(self):
        Tag.objects.create(name="Broring", slug="broring")
        self.client.force_login(self.owner)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(url, {"tag": "boring"}, HTTP_X_RECIPE_SIMILAR_TAG_CHECK="1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["need_confirm"])
        self.assertEqual(len(data["pairs"]), 1)
        self.assertEqual(data["pairs"][0]["typed"], "boring")
        self.assertEqual(data["pairs"][0]["suggested"], "Broring")

    def test_quick_add_similar_tag_accepted_uses_suggested(self):
        Tag.objects.create(name="Broring", slug="broring")
        self.client.force_login(self.owner)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(
            url,
            {"tag": "boring", "similar_tag_ack": "accepted"},
            follow=True,
        )
        self.assertRedirects(response, self.recipe.get_absolute_url())
        self.recipe.refresh_from_db()
        self.assertTrue(self.recipe.tags.filter(name__iexact="Broring").exists())

    def test_quick_add_similar_tag_skipped_keeps_typed_name(self):
        Tag.objects.create(name="Broring", slug="broring")
        self.client.force_login(self.owner)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(
            url,
            {"tag": "boring", "similar_tag_ack": "skipped"},
            follow=True,
        )
        self.assertRedirects(response, self.recipe.get_absolute_url())
        self.recipe.refresh_from_db()
        self.assertTrue(self.recipe.tags.filter(name__iexact="boring").exists())
        self.assertFalse(self.recipe.tags.filter(name__iexact="Broring").exists())
        self.assertTrue(Tag.objects.filter(name__iexact="Broring").exists())

    def test_quick_add_duplicate_tag_shows_message(self):
        tag = Tag.objects.create(name="Brunch", slug="brunch")
        self.recipe.tags.add(tag)
        self.client.force_login(self.owner)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(url, {"tag": "brunch"}, follow=True)
        message_text = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("already", message_text.lower())

    def test_quick_add_tag_requires_permission(self):
        self.client.force_login(self.other_user)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(url, {"tag": "Nope"})
        self.assertEqual(response.status_code, 403)

    def test_quick_add_tag_empty_rejects(self):
        self.client.force_login(self.owner)
        url = reverse("recipes:add_tag", kwargs={"slug": self.recipe.slug})
        response = self.client.post(url, {"tag": ""}, follow=True)
        message_text = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("enter", message_text.lower())

    def test_recipe_list_filters_by_single_tag(self):
        self.client.force_login(self.other_user)
        t = Tag.objects.create(name="Snack", slug="snack")
        self.recipe.tags.add(t)
        Recipe.objects.create(owner=self.owner, title="Other Dish")
        response = self.client.get(reverse("recipes:list"), {"tag": "snack"})
        body = response.content.decode()
        self.assertIn("Sunday Pancakes", body)
        self.assertNotIn("Other Dish", body)

    def test_recipe_list_partial_returns_tag_filter_sync_fragment(self):
        self.client.force_login(self.other_user)
        t = Tag.objects.create(name="Snack", slug="snack")
        self.recipe.tags.add(t)
        response = self.client.get(reverse("recipes:list"), {"partial": "1"})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("data-recipe-list-sync-extras", body)
        self.assertIn('data-tag-slug="snack"', body)

    def test_recipe_list_tag_chips_show_active_recipe_counts(self):
        self.client.force_login(self.other_user)
        t = Tag.objects.create(name="Snack", slug="snack")
        self.recipe.tags.add(t)
        second = Recipe.objects.create(owner=self.owner, title="Granola Bowl", description="")
        second.tags.add(t)
        deleted = Recipe.objects.create(owner=self.owner, title="Old Snack", description="")
        deleted.tags.add(t)
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at", "updated_at"])
        response = self.client.get(reverse("recipes:list"))
        body = response.content.decode()
        self.assertRegex(
            body,
            (
                r'data-tag-slug="snack"[^>]*>[\s\n]*Snack\s*'
                r'<span class="search-tag-chip-count">\s*\(2\)\s*</span>'
            ),
        )
        self.assertNotRegex(body, r'class="search-tag-chip-count">\s*\(3\)\s*</span>')

    def test_recipe_list_tag_chips_ordered_by_recipe_count_desc(self):
        self.client.force_login(self.other_user)
        rare = Tag.objects.create(name="Rare Tag", slug="rare-tag")
        common = Tag.objects.create(name="Common Tag", slug="common-tag")
        self.recipe.tags.add(rare, common)
        second = Recipe.objects.create(owner=self.owner, title="Second Dish", description="")
        second.tags.add(common)
        third = Recipe.objects.create(owner=self.owner, title="Third Dish", description="")
        third.tags.add(common)
        response = self.client.get(reverse("recipes:list"))
        body = response.content.decode()
        self.assertLess(
            body.index('data-tag-slug="common-tag"'),
            body.index('data-tag-slug="rare-tag"'),
        )

    def test_recipe_list_search_tags_only_tags_on_matching_recipes(self):
        self.client.force_login(self.other_user)
        only_match = Tag.objects.create(name="On Match", slug="on-match")
        no_match = Tag.objects.create(name="Not On Match", slug="not-on-match")
        self.recipe.tags.add(only_match)
        other = Recipe.objects.create(owner=self.owner, title="Other Bowl", description="beta")
        other.tags.add(no_match)
        response = self.client.get(reverse("recipes:list"), {"q": "Sunday"})
        body = response.content.decode()
        self.assertIn('data-tag-slug="on-match"', body)
        self.assertNotIn('data-tag-slug="not-on-match"', body)

    def test_recipe_list_tag_chip_counts_reflect_full_filtered_set_not_page(self):
        self.client.force_login(self.other_user)
        bulk = Tag.objects.create(name="Bulk", slug="bulk-tag")
        with patch.object(RecipeListView, "paginate_by", 2):
            for i in range(3):
                r = Recipe.objects.create(
                    owner=self.owner,
                    title=f"Zzz Paged {i}",
                    description="",
                )
                r.tags.add(bulk)
            for page in ("1", "2"):
                response = self.client.get(reverse("recipes:list"), {"page": page})
                body = response.content.decode()
                idx = body.index('data-tag-slug="bulk-tag"')
                self.assertRegex(
                    body[idx : idx + 400],
                    r'<span class="search-tag-chip-count">\s*\(3\)\s*</span>',
                    msg=f"page {page} should show total filtered count, not page slice",
                )

    def test_recipe_list_filter_two_tags_requires_both(self):
        self.client.force_login(self.other_user)
        vegan = Tag.objects.create(name="Vegan", slug="vegan")
        fast = Tag.objects.create(name="Fast", slug="fast")
        self.recipe.tags.set([vegan, fast])
        only_vegan = Recipe.objects.create(owner=self.owner, title="Only Vegan Tag")
        only_vegan.tags.add(vegan)
        url = f"{reverse('recipes:list')}?tag=vegan&tag=fast"
        response = self.client.get(url)
        body = response.content.decode()
        self.assertIn("Sunday Pancakes", body)
        self.assertNotIn("Only Vegan Tag", body)

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
                **tag_formset_post_data(["soup", "weeknight"]),
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
        tag_slugs = set(recipe.tags.values_list("slug", flat=True))
        self.assertEqual(tag_slugs, {"soup", "weeknight"})

    def test_recipe_form_has_dynamic_formset_hooks_without_extra_delete_boxes(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:create"))
        content = response.content.decode()

        self.assertContains(response, "data-formset-template", count=4)
        self.assertContains(response, "data-unsaved-warning")
        self.assertContains(response, "data-discard-changes", count=1)
        self.assertContains(response, "Back to recipes")
        self.assertNotContains(response, 'type="checkbox" name="tags-0-DELETE"')
        self.assertNotContains(response, 'data-form-row draggable="true"')
        self.assertContains(response, 'data-drag-handle aria-label="Drag to reorder ingredient"')
        self.assertLess(content.index("<h2>Photos</h2>"), content.index("<h2>Ingredients</h2>"))

    def test_recipe_edit_tag_suggestions_exclude_orphan_tags(self):
        Tag.objects.create(name="OrphanOnly", slug="orphan-only")
        on_recipe = Tag.objects.create(name="OnActive", slug="on-active")
        self.recipe.tags.add(on_recipe)
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("recipes:update", kwargs={"slug": self.recipe.slug}),
        )
        slugs = {t.slug for t in response.context["tag_suggestions"]}
        self.assertIn("on-active", slugs)
        self.assertNotIn("orphan-only", slugs)

    def test_recipe_edit_tag_suggestions_exclude_tags_only_on_soft_deleted_recipes(self):
        only_deleted = Tag.objects.create(name="OnlyDeleted", slug="only-deleted")
        deleted_recipe = Recipe.objects.create(
            owner=self.owner,
            title="Trashed Soup",
            description="x",
            deleted_at=timezone.now(),
        )
        deleted_recipe.tags.add(only_deleted)
        active_tag = Tag.objects.create(name="StillHere", slug="still-here")
        self.recipe.tags.add(active_tag)
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("recipes:update", kwargs={"slug": self.recipe.slug}),
        )
        slugs = {t.slug for t in response.context["tag_suggestions"]}
        self.assertIn("still-here", slugs)
        self.assertNotIn("only-deleted", slugs)

    def test_non_owner_cannot_edit_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("recipes:update", kwargs={"slug": self.recipe.slug}),
        )

        self.assertEqual(response.status_code, 403)

    def test_legacy_recipe_photo_syncs_to_gallery_on_edit(self):
        self.recipe.photo.save(
            "legacy-hero.jpg",
            SimpleUploadedFile("legacy-hero.jpg", b"legacy-image", content_type="image/jpeg"),
            save=True,
        )
        self.assertFalse(self.recipe.photos.exists())
        self.client.force_login(self.owner)

        response = self.client.get(reverse("recipes:update", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, "photo-editor-image")
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.photos.count(), 1)
        self.assertEqual(self.recipe.photos.get().image.name, self.recipe.photo.name)

    def test_recipe_edit_shows_photo_preview(self):
        RecipePhoto.objects.create(
            recipe=self.recipe,
            image=SimpleUploadedFile("gallery.jpg", b"fake-image", content_type="image/jpeg"),
            order=1,
        )
        self.client.force_login(self.owner)
        response = self.client.get(reverse("recipes:update", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, "photo-editor-image")
        self.assertContains(response, "photo-editor-preview")
        self.assertNotContains(response, "Currently:")

    def test_edit_recipe_shows_single_extra_rows_for_existing_items(self):
        self.client.force_login(self.owner)
        brunch = Tag.objects.create(name="Brunch", slug="brunch")
        self.recipe.tags.add(brunch)

        response = self.client.get(reverse("recipes:update", kwargs={"slug": self.recipe.slug}))

        ingredient_formset = response.context["ingredient_formset"]
        step_formset = response.context["step_formset"]
        tag_formset = response.context["tag_formset"]
        self.assertContains(response, self.recipe.get_absolute_url())
        self.assertContains(response, "Back to recipe")
        self.assertEqual(ingredient_formset.initial_form_count(), 1)
        self.assertEqual(step_formset.initial_form_count(), 1)
        self.assertEqual(ingredient_formset.total_form_count(), 2)
        self.assertEqual(step_formset.total_form_count(), 2)
        self.assertEqual(tag_formset.initial_form_count(), 1)
        self.assertEqual(tag_formset.total_form_count(), 2)

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
                **tag_formset_post_data([""]),
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
                **tag_formset_post_data([""]),
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

    def test_recipe_detail_renders_markdown_in_description_and_steps(self):
        self.recipe.description = "**Bold** intro\n\nSecond paragraph"
        self.recipe.save(update_fields=["description"])
        step = self.recipe.steps.get()
        step.text = "Heat pan\n\n- flip once\n- serve hot"
        step.save(update_fields=["text"])
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, "recipe-markdown")
        self.assertContains(response, "<strong>Bold</strong>")
        self.assertContains(response, "Second paragraph")
        self.assertContains(response, "<li>flip once</li>")
        self.assertContains(response, "serve hot")

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

    def test_make_views_require_login(self):
        for url_name in ("recipes:make", "recipes:make_steps"):
            response = self.client.get(reverse(url_name, kwargs={"slug": self.recipe.slug}))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response["Location"])

    def test_recipe_detail_shows_make_this_for_active_recipes(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, "Make This")
        self.assertContains(
            response,
            reverse("recipes:make", kwargs={"slug": self.recipe.slug}),
        )

    def test_recipe_detail_hides_make_this_when_deleted(self):
        self.recipe.deleted_at = timezone.now()
        self.recipe.save(update_fields=["deleted_at"])
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))

        self.assertNotContains(response, "Make This")

    def test_make_ingredients_page_lists_ingredients(self):
        Ingredient.objects.create(recipe=self.recipe, quantity="1 cup", name="milk", order=2)
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:make", kwargs={"slug": self.recipe.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step 1 of 2")
        self.assertContains(response, "flour")
        self.assertContains(response, "milk")
        self.assertContains(response, "1 cup")
        self.assertContains(
            response,
            reverse("recipes:make_steps", kwargs={"slug": self.recipe.slug}),
        )

    def test_make_steps_page_lists_instructions(self):
        step = self.recipe.steps.get()
        step.text = "Whisk until smooth."
        step.save(update_fields=["text"])
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:make_steps", kwargs={"slug": self.recipe.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-make-mode")
        self.assertContains(response, 'data-make-initial-panel="steps"')
        self.assertContains(response, "Step 2 of 2")
        self.assertContains(response, "Whisk until smooth.")
        self.assertContains(response, reverse("recipes:make", kwargs={"slug": self.recipe.slug}))
        self.assertContains(response, reverse("recipes:detail", kwargs={"slug": self.recipe.slug}))

    def test_make_page_includes_swipe_panels_and_scroll_targets(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:make", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, "data-make-swipe-zone")
        self.assertContains(response, 'data-make-panel="ingredients"')
        self.assertContains(response, 'data-make-panel="steps"')
        self.assertContains(response, "data-make-scroll", count=2)
        self.assertContains(response, 'data-make-nav="prev"')
        self.assertContains(response, 'data-make-nav="next"')

    def test_make_page_includes_exit_dialog_and_record_form(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("recipes:make", kwargs={"slug": self.recipe.slug}))

        self.assertContains(response, "data-make-exit-overlay")
        self.assertContains(response, "Did you make this recipe?")
        self.assertContains(response, "data-make-exit-stay")
        self.assertContains(response, "data-make-exit-back")
        self.assertContains(response, "Close and keep making")
        self.assertNotContains(response, "data-make-exit-skip-close")
        self.assertNotContains(response, "Keep making")
        self.assertContains(response, "data-make-exit-rating")
        self.assertContains(response, "star-rating-field")
        self.assertNotContains(response, "data-make-exit-go-review")
        self.assertContains(response, "data-make-exit")
        self.assertContains(response, reverse("recipes:make_record", kwargs={"slug": self.recipe.slug}))

    def test_record_recipe_made_requires_login(self):
        response = self.client.post(
            reverse("recipes:make_record", kwargs={"slug": self.recipe.slug}),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_record_recipe_made_creates_entry(self):
        self.client.force_login(self.other_user)

        response = self.client.post(
            reverse("recipes:make_record", kwargs={"slug": self.recipe.slug}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["has_rating"])
        self.assertIsNone(payload["rating"])
        made = RecipeMade.objects.get(recipe=self.recipe, user=self.other_user)
        self.assertIsNotNone(made.made_at)

    def test_record_recipe_made_reports_existing_rating(self):
        Rating.objects.create(recipe=self.recipe, user=self.other_user, value=4)
        self.client.force_login(self.other_user)

        response = self.client.post(
            reverse("recipes:make_record", kwargs={"slug": self.recipe.slug}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        payload = response.json()
        self.assertTrue(payload["has_rating"])
        self.assertEqual(payload["rating"], 4)

    def test_detail_prompt_review_from_query(self):
        self.client.force_login(self.other_user)

        response = self.client.get(
            f"{self.recipe.get_absolute_url()}?review=1",
        )

        self.assertContains(response, "data-prompt-review")
        self.assertContains(response, "is-prompt-review-focus")


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
