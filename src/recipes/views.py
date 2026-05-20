from __future__ import annotations

import random
import re
from collections import Counter
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Exists, ExpressionWrapper, F, IntegerField, OuterRef, Q
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView, DetailView, ListView, TemplateView, View

from recipes.forms import (
    CommentForm,
    IngredientFormSet,
    InstructionStepFormSet,
    RatingForm,
    RecipeForm,
    RecipePhotoFormSet,
    RecipeQuickAddTagForm,
    RecipeTagLineFormSet,
    similar_tag_pairs_for_names,
)
from recipes.models import Rating, Recipe, RecipeMade, Tag, sync_recipe_tags
from recipes.photo_sync import sync_legacy_recipe_photo_to_gallery


class PrivateRecipeMixin(LoginRequiredMixin):
    login_url = "login"


def purge_expired_deleted_recipes() -> int:
    cutoff = timezone.now() - timedelta(days=7)
    deleted_count, _ = Recipe.objects.filter(deleted_at__lte=cutoff).delete()
    return deleted_count


def active_recipes():
    return Recipe.objects.filter(deleted_at__isnull=True)


def tag_suggestions_queryset():
    """Tags used on at least one active recipe (excludes orphans for autocomplete / datalist)."""
    tag_linked_to_active = Recipe.tags.through.objects.filter(
        tag_id=OuterRef("pk"),
        recipe__deleted_at__isnull=True,
    )
    return Tag.objects.filter(Exists(tag_linked_to_active)).order_by("name")[:400]


def current_recipes():
    cutoff = timezone.now() - timedelta(days=7)
    return Recipe.objects.filter(Q(deleted_at__isnull=True) | Q(deleted_at__gt=cutoff))


def display_user_name(user) -> str:
    if user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name[:1]}."
    if user.first_name:
        return user.first_name
    return user.username


def rating_payload(recipe: Recipe, user) -> dict:
    aggregate = recipe.ratings.aggregate(average=Avg("value"), count=Count("id"))
    average = aggregate["average"]
    count = aggregate["count"] or 0
    user_name = display_user_name(user)
    return {
        "average": round(average, 1) if average is not None else None,
        "average_percent": round((average or 0) / 5 * 100, 2),
        "count": count,
        "reviewer_label": f"{user_name} (you)",
        "user_id": user.id,
        "user_name": user_name,
    }


def user_can_edit_recipe(user, recipe: Recipe) -> bool:
    return user.is_staff or recipe.owner_id == user.id


def tag_line_formset_initial(recipe: Recipe) -> list[dict[str, str]]:
    if not recipe.pk:
        return []
    return [{"tag_name": n} for n in recipe.tags.order_by("name").values_list("name", flat=True)]


SORT_TITLE = "title"
SORT_RATING = "rating"
SORT_COOK_TIME = "cook_time"
SORT_PREP_TIME = "prep_time"
SORT_EASE = "ease"
SORT_UPDATED = "updated"

RECIPE_LIST_SORTS = frozenset(
    {
        SORT_TITLE,
        SORT_RATING,
        SORT_COOK_TIME,
        SORT_PREP_TIME,
        SORT_EASE,
        SORT_UPDATED,
    }
)

DEFAULT_RECIPE_LIST_SORT_DIR: dict[str, str] = {
    SORT_TITLE: "asc",
    SORT_RATING: "desc",
    SORT_COOK_TIME: "asc",
    SORT_PREP_TIME: "asc",
    SORT_EASE: "asc",
    SORT_UPDATED: "desc",
}

RECIPE_LIST_SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    (SORT_TITLE, "Title"),
    (SORT_RATING, "Rating"),
    (SORT_COOK_TIME, "Cook time"),
    (SORT_PREP_TIME, "Prep time"),
    (SORT_EASE, "Ease"),
    (SORT_UPDATED, "Updated"),
)

RECIPE_LIST_SORT_LABELS = dict(RECIPE_LIST_SORT_OPTIONS)

LIST_TAG_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def normalized_recipe_list_tag_slugs(request) -> list[str]:
    """Return validated tag slugs from the query string (order preserved, de-duplicated)."""
    ordered: list[str] = []
    seen: set[str] = set()
    for item in request.GET.getlist("tag"):
        slug = item.strip().lower()
        if not slug or slug in seen or not LIST_TAG_SLUG_RE.match(slug):
            continue
        seen.add(slug)
        ordered.append(slug)
    if not ordered:
        return []
    valid = set(Tag.objects.filter(slug__in=ordered).values_list("slug", flat=True))
    return [slug for slug in ordered if slug in valid]


def build_recipe_list_query_pairs(
    query: str,
    tag_slugs: list[str],
    sort: str,
    sort_dir: str,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    stripped = query.strip()
    if stripped:
        pairs.append(("q", stripped))
    for slug in tag_slugs:
        pairs.append(("tag", slug))
    default_dir = DEFAULT_RECIPE_LIST_SORT_DIR[sort]
    title_default_dir = DEFAULT_RECIPE_LIST_SORT_DIR[SORT_TITLE]
    omit_sort_params = sort == SORT_TITLE and sort_dir == title_default_dir
    if not omit_sort_params:
        pairs.append(("sort", sort))
    if not omit_sort_params and sort_dir != default_dir:
        pairs.append(("sort_dir", sort_dir))
    return pairs


def normalize_recipe_list_sort(request) -> str:
    candidate = request.GET.get("sort", "").strip()
    return candidate if candidate in RECIPE_LIST_SORTS else SORT_TITLE


def normalize_recipe_list_sort_dir(request, sort_key: str) -> str:
    candidate = request.GET.get("sort_dir", "").strip().lower()
    if candidate in {"asc", "desc"}:
        return candidate
    return DEFAULT_RECIPE_LIST_SORT_DIR.get(sort_key, "asc")


def filtered_active_recipe_queryset(search_text: str, tag_slugs: list[str] | None = None):
    """Active recipes optionally filtered by search text and/or tags (AND)."""
    queryset = (
        active_recipes()
        .select_related("owner")
        .prefetch_related("ingredients", "photos", "tags")
        .annotate(average_rating=Avg("ratings__value"))
        .annotate(rating_count=Count("ratings"))
    )
    query = search_text.strip()
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(ingredients__name__icontains=query)
        )
    for slug in tag_slugs or []:
        queryset = queryset.filter(tags__slug=slug)
    if query or tag_slugs:
        queryset = queryset.distinct()
    return queryset


def filter_tags_for_recipe_list(search_text: str, tag_slugs: list[str]) -> list[Tag]:
    """Tags on recipes matching list filters, with counts across the full result set."""
    filtered = filtered_active_recipe_queryset(search_text, tag_slugs)
    tag_counts: Counter[int] = Counter(
        Recipe.tags.through.objects.filter(
            recipe_id__in=filtered.values("pk"),
        ).values_list("tag_id", flat=True),
    )
    if not tag_counts:
        return []
    tags_by_id = {
        t.pk: t
        for t in Tag.objects.filter(pk__in=tag_counts.keys()).only("pk", "name", "slug")
    }
    filter_tags: list[Tag] = []
    for tag_id, recipe_count in tag_counts.items():
        tag_obj = tags_by_id.get(tag_id)
        if tag_obj is None:
            continue
        tag_obj.recipe_count = recipe_count
        filter_tags.append(tag_obj)
    filter_tags.sort(key=lambda t: (-t.recipe_count, t.name.lower()))
    return filter_tags


def _recipe_title_order(*, descending: bool = False):
    """Case-insensitive title ordering for stable, human-friendly lists."""
    title = Lower("title")
    if descending:
        return title.desc(), "id"
    return title.asc(), "id"


def ordered_recipe_list_queryset(queryset, sort_key: str, sort_dir: str):
    """Apply list ordering; sort_key must be a RECIPE_LIST_SORTS value."""
    if sort_key not in RECIPE_LIST_SORTS:
        sort_key = SORT_TITLE
    if sort_dir not in {"asc", "desc"}:
        sort_dir = DEFAULT_RECIPE_LIST_SORT_DIR.get(sort_key, "asc")
    qs = queryset
    title_tiebreak = _recipe_title_order()
    if sort_key == SORT_EASE:
        qs = qs.annotate(
            _ease_ing=Count("ingredients", distinct=True),
            _ease_steps=Count("steps", distinct=True),
        ).annotate(
            ease_work=ExpressionWrapper(
                F("_ease_ing") + F("_ease_steps"),
                output_field=IntegerField(),
            )
        )
    if sort_key == SORT_TITLE:
        return qs.order_by(*_recipe_title_order(descending=sort_dir == "desc"))
    if sort_key == SORT_RATING:
        if sort_dir == "asc":
            return qs.order_by(F("average_rating").asc(nulls_last=True), *title_tiebreak)
        return qs.order_by(F("average_rating").desc(nulls_last=True), *title_tiebreak)
    if sort_key == SORT_COOK_TIME:
        if sort_dir == "desc":
            return qs.order_by(F("cook_time_minutes").desc(nulls_last=True), *title_tiebreak)
        return qs.order_by(F("cook_time_minutes").asc(nulls_last=True), *title_tiebreak)
    if sort_key == SORT_PREP_TIME:
        if sort_dir == "desc":
            return qs.order_by(F("prep_time_minutes").desc(nulls_last=True), *title_tiebreak)
        return qs.order_by(F("prep_time_minutes").asc(nulls_last=True), *title_tiebreak)
    if sort_key == SORT_EASE:
        if sort_dir == "desc":
            return qs.order_by(F("ease_work").desc(nulls_last=True), *title_tiebreak)
        return qs.order_by("ease_work", *title_tiebreak)
    if sort_key == SORT_UPDATED:
        if sort_dir == "asc":
            return qs.order_by("updated_at", *title_tiebreak)
        return qs.order_by("-updated_at", *title_tiebreak)
    return qs.order_by(*title_tiebreak)


class RandomRecipeView(PrivateRecipeMixin, View):
    """Redirect to a random recipe among those matching the optional list search (q)."""

    def get(self, request, *args, **kwargs):
        purge_expired_deleted_recipes()
        q = request.GET.get("q", "").strip()
        tag_slugs = normalized_recipe_list_tag_slugs(request)
        recipe_qs = filtered_active_recipe_queryset(q, tag_slugs)
        pk_list = list(recipe_qs.values_list("pk", flat=True))
        if not pk_list:
            if q or tag_slugs:
                messages.info(request, "No recipes match your current filters.")
            else:
                messages.info(request, "There are no recipes to choose from yet.")
            list_url = reverse("recipes:list")
            sort = normalize_recipe_list_sort(request)
            sort_dir = normalize_recipe_list_sort_dir(request, sort)
            pairs = build_recipe_list_query_pairs(q, tag_slugs, sort, sort_dir)
            query_string = urlencode(pairs)
            return redirect(f"{list_url}?{query_string}" if query_string else list_url)
        chosen_pk = random.choice(pk_list)
        slug = recipe_qs.filter(pk=chosen_pk).values_list("slug", flat=True).first()
        if not slug:
            return redirect(reverse("recipes:list"))
        return redirect("recipes:detail", slug=slug)


class RecipeListView(PrivateRecipeMixin, ListView):
    model = Recipe
    paginate_by = 24
    template_name = "recipes/list.html"
    context_object_name = "recipes"

    def get_queryset(self):
        purge_expired_deleted_recipes()
        sort = normalize_recipe_list_sort(self.request)
        sort_dir = normalize_recipe_list_sort_dir(self.request, sort)
        tag_slugs = normalized_recipe_list_tag_slugs(self.request)
        return ordered_recipe_list_queryset(
            filtered_active_recipe_queryset(self.request.GET.get("q", ""), tag_slugs),
            sort,
            sort_dir,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        sort = normalize_recipe_list_sort(self.request)
        sort_dir = normalize_recipe_list_sort_dir(self.request, sort)
        tag_slugs = normalized_recipe_list_tag_slugs(self.request)
        context["query"] = query
        context["sort"] = sort
        context["sort_dir"] = sort_dir
        context["list_tag_slugs"] = tag_slugs
        context["list_query_no_page"] = urlencode(
            build_recipe_list_query_pairs(query, tag_slugs, sort, sort_dir),
        )
        context["recipe_list_sort_options"] = RECIPE_LIST_SORT_OPTIONS
        context["sort_display_label"] = RECIPE_LIST_SORT_LABELS.get(
            sort,
            RECIPE_LIST_SORT_LABELS[SORT_TITLE],
        )
        context["filter_tags"] = filter_tags_for_recipe_list(query, tag_slugs)
        deleted_recipes = (
            current_recipes()
            .filter(deleted_at__isnull=False)
            .select_related("owner")
            .prefetch_related("photos", "tags")
            .annotate(average_rating=Avg("ratings__value"))
            .annotate(rating_count=Count("ratings"))
        )
        if query:
            deleted_recipes = deleted_recipes.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(ingredients__name__icontains=query)
            )
        for slug in tag_slugs:
            deleted_recipes = deleted_recipes.filter(tags__slug=slug)
        if query or tag_slugs:
            deleted_recipes = deleted_recipes.distinct()
        context["deleted_recipes"] = deleted_recipes.order_by("-deleted_at", *_recipe_title_order())
        return context

    def render_to_response(self, context, **response_kwargs):
        partial = self.request.GET.get("partial", "").strip()
        if partial == "append":
            return TemplateResponse(
                self.request,
                "recipes/_list_cards.html",
                context,
                **response_kwargs,
            )
        if partial == "1":
            return TemplateResponse(
                self.request,
                "recipes/_list_results.html",
                context,
                **response_kwargs,
            )
        return super().render_to_response(context, **response_kwargs)


class RecipeDetailView(PrivateRecipeMixin, DetailView):
    model = Recipe
    template_name = "recipes/detail.html"
    context_object_name = "recipe"

    def get_queryset(self):
        return (
            current_recipes()
            .select_related("owner")
            .prefetch_related(
                "ingredients",
                "steps",
                "photos",
                "comments__author",
                "ratings__user",
                "tags",
            )
            .annotate(average_rating=Avg("ratings__value"))
            .annotate(rating_count=Count("ratings"))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        recipe = self.object
        comment_sort = self.request.GET.get("comments", "oldest")
        if comment_sort not in {"oldest", "newest"}:
            comment_sort = "oldest"
        comment_ordering = (
            ("-created_at", "-id") if comment_sort == "newest" else ("created_at", "id")
        )
        context["can_edit"] = user_can_edit_recipe(self.request.user, recipe)
        context["comment_form"] = CommentForm()
        context["rating_form"] = RatingForm(
            instance=Rating.objects.filter(recipe=recipe, user=self.request.user).first()
        )
        average_rating = getattr(recipe, "average_rating", None)
        context["average_rating_percent"] = (
            round(average_rating / 5 * 100, 2) if average_rating is not None else 0
        )
        context["ratings"] = [
            {
                "is_current_user": rating.user_id == self.request.user.id,
                "user_id": rating.user_id,
                "user_name": display_user_name(rating.user),
                "value": rating.value,
                "value_percent": rating.value * 20,
            }
            for rating in recipe.ratings.all().order_by("-updated_at", "-id")
        ]
        context["comment_sort"] = comment_sort
        context["prompt_review"] = self.request.GET.get("review") == "1"
        context["comments"] = [
            {
                "author_name": display_user_name(comment.author),
                "body": comment.body,
                "created_at": comment.created_at,
                "id": comment.id,
            }
            for comment in recipe.comments.all().order_by(*comment_ordering)
        ]
        context["photo_count"] = (
            recipe.photos.count() if recipe.photos.exists() else int(bool(recipe.photo))
        )
        if context["can_edit"] and recipe.deleted_at is None:
            context["quick_tag_form"] = RecipeQuickAddTagForm()
            context["tag_suggestions"] = tag_suggestions_queryset()
        return context


class RecipeFormMixin(PrivateRecipeMixin, TemplateView):
    template_name = "recipes/form.html"
    recipe: Recipe | None = None

    def get_recipe(self) -> Recipe:
        if self.recipe is None:
            self.recipe = Recipe(owner=self.request.user)
        return self.recipe

    def get_forms(self):
        recipe = self.get_recipe()
        if recipe.pk:
            sync_legacy_recipe_photo_to_gallery(recipe)
        if self.request.method == "POST":
            recipe_form = RecipeForm(self.request.POST, self.request.FILES, instance=recipe)
            ingredient_formset = IngredientFormSet(
                self.request.POST,
                instance=recipe,
                prefix="ingredients",
            )
            step_formset = InstructionStepFormSet(
                self.request.POST,
                instance=recipe,
                prefix="steps",
            )
            photo_formset = RecipePhotoFormSet(
                self.request.POST,
                self.request.FILES,
                instance=recipe,
                prefix="photos",
            )
            tag_formset = RecipeTagLineFormSet(self.request.POST, prefix="tags")
        else:
            recipe_form = RecipeForm(instance=recipe)
            ingredient_formset = IngredientFormSet(instance=recipe, prefix="ingredients")
            step_formset = InstructionStepFormSet(instance=recipe, prefix="steps")
            photo_formset = RecipePhotoFormSet(instance=recipe, prefix="photos")
            tag_formset = RecipeTagLineFormSet(
                initial=tag_line_formset_initial(recipe),
                prefix="tags",
            )
        return recipe_form, ingredient_formset, step_formset, photo_formset, tag_formset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "recipe_form" not in context:
            recipe_form, ingredient_formset, step_formset, photo_formset, tag_formset = (
                self.get_forms()
            )
            context.update(
                {
                    "recipe_form": recipe_form,
                    "ingredient_formset": ingredient_formset,
                    "step_formset": step_formset,
                    "photo_formset": photo_formset,
                    "tag_formset": tag_formset,
                    "recipe": self.get_recipe(),
                }
            )
        context["tag_suggestions"] = tag_suggestions_queryset()
        return context

    def post(self, request, *args, **kwargs):
        recipe_form, ingredient_formset, step_formset, photo_formset, tag_formset = self.get_forms()
        if (
            recipe_form.is_valid()
            and ingredient_formset.is_valid()
            and step_formset.is_valid()
            and photo_formset.is_valid()
            and tag_formset.is_valid()
        ):
            pairs = getattr(tag_formset, "similar_tag_pairs", ()) or ()
            ack = (request.POST.get("similar_tags_ack") or "").strip()
            if pairs and ack not in ("skipped", "accepted"):
                similar_tag_modal_pairs = [{"typed": a, "suggested": b} for a, b in pairs]
                return self.render_to_response(
                    self.get_context_data(
                        recipe_form=recipe_form,
                        ingredient_formset=ingredient_formset,
                        step_formset=step_formset,
                        photo_formset=photo_formset,
                        tag_formset=tag_formset,
                        show_similar_tag_modal=True,
                        similar_tag_modal_pairs=similar_tag_modal_pairs,
                    )
                )
            return self.forms_valid(
                recipe_form,
                ingredient_formset,
                step_formset,
                photo_formset,
                tag_formset,
            )
        return self.render_to_response(
            self.get_context_data(
                recipe_form=recipe_form,
                ingredient_formset=ingredient_formset,
                step_formset=step_formset,
                photo_formset=photo_formset,
                tag_formset=tag_formset,
            )
        )

    @transaction.atomic
    def forms_valid(
        self,
        recipe_form,
        ingredient_formset,
        step_formset,
        photo_formset,
        tag_formset,
    ):
        recipe = recipe_form.save(commit=False)
        recipe.owner = self.get_recipe().owner
        recipe.save()
        ingredient_formset.instance = recipe
        step_formset.instance = recipe
        photo_formset.instance = recipe
        ingredient_formset.save()
        step_formset.save()
        photo_formset.save()
        sync_recipe_tags(recipe, ", ".join(tag_formset.ordered_tag_names()))
        messages.success(self.request, "Recipe saved.")
        return redirect(recipe)


class RecipeCreateView(RecipeFormMixin):
    pass


class RecipeUpdateView(RecipeFormMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.recipe = get_object_or_404(current_recipes(), slug=kwargs["slug"])
        if not user_can_edit_recipe(request.user, self.recipe):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class RecipeDeleteView(PrivateRecipeMixin, DeleteView):
    model = Recipe
    template_name = "recipes/confirm_delete.html"
    success_url = reverse_lazy("recipes:list")

    def get_queryset(self):
        return active_recipes()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        recipe = self.object
        recipe.deleted_at = timezone.now()
        recipe.save(update_fields=["deleted_at", "updated_at"])
        messages.success(self.request, "Recipe moved to recently deleted.")
        return redirect(self.success_url)


class RecentlyDeletedRecipeListView(PrivateRecipeMixin, ListView):
    model = Recipe
    template_name = "recipes/recently_deleted.html"
    context_object_name = "recipes"

    def get_queryset(self):
        purge_expired_deleted_recipes()
        return (
            current_recipes()
            .filter(deleted_at__isnull=False)
            .select_related("owner")
            .prefetch_related("photos")
            .annotate(average_rating=Avg("ratings__value"))
            .annotate(rating_count=Count("ratings"))
            .order_by(
                "-deleted_at",
                "title",
            )
        )


@method_decorator(login_required, name="dispatch")
class RestoreRecipeView(View):
    def post(self, request, slug):
        recipe = get_object_or_404(current_recipes(), slug=slug, deleted_at__isnull=False)
        recipe.deleted_at = None
        recipe.save(update_fields=["deleted_at", "updated_at"])
        messages.success(request, "Recipe restored.")
        return redirect(recipe)


class RecipePrintView(PrivateRecipeMixin, DetailView):
    model = Recipe
    template_name = "recipes/print.html"
    context_object_name = "recipe"

    def get_queryset(self):
        return current_recipes().select_related("owner").prefetch_related("ingredients", "steps")


class RecipeMakeMixin(PrivateRecipeMixin, DetailView):
    model = Recipe
    template_name = "recipes/make.html"
    context_object_name = "recipe"
    make_active_panel = "ingredients"

    def get_queryset(self):
        return current_recipes().prefetch_related("ingredients", "steps")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        recipe = self.object
        panel = self.make_active_panel
        context["make_active_panel"] = panel
        context["make_step"] = 1 if panel == "ingredients" else 2
        context["make_step_label"] = "Ingredients" if panel == "ingredients" else "Instructions"
        context["make_detail_url"] = recipe.get_absolute_url()
        context["make_record_url"] = reverse("recipes:make_record", kwargs={"slug": recipe.slug})
        context["rating_form"] = RatingForm(
            instance=Rating.objects.filter(recipe=recipe, user=self.request.user).first(),
        )
        return context


class RecipeMakeIngredientsView(RecipeMakeMixin):
    make_active_panel = "ingredients"


class RecipeMakeStepsView(RecipeMakeMixin):
    make_active_panel = "steps"


@login_required
@require_POST
def record_recipe_made(request, slug):
    recipe = get_object_or_404(current_recipes(), slug=slug)
    RecipeMade.objects.create(recipe=recipe, user=request.user)
    rating = Rating.objects.filter(recipe=recipe, user=request.user).first()
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "has_rating": rating is not None,
                "rating": rating.value if rating else None,
            },
        )
    messages.success(request, "Nice work — we saved that you made this recipe.")
    return redirect(recipe.get_absolute_url())


@method_decorator(login_required, name="dispatch")
class AddRecipeTagView(View):
    """Append one tag to a recipe from the detail page (owner or staff)."""

    def post(self, request, slug):
        recipe = get_object_or_404(active_recipes(), slug=slug)
        if not user_can_edit_recipe(request.user, recipe):
            raise PermissionDenied
        if request.headers.get("X-Recipe-Similar-Tag-Check") == "1":
            form = RecipeQuickAddTagForm(request.POST)
            if not form.is_valid():
                return JsonResponse({"ok": False, "errors": form.errors}, status=400)
            name = form.cleaned_data["tag"]
            pairs = similar_tag_pairs_for_names([name])
            return JsonResponse(
                {
                    "ok": True,
                    "need_confirm": len(pairs) > 0,
                    "pairs": [{"typed": a, "suggested": b} for a, b in pairs],
                },
            )
        form = RecipeQuickAddTagForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["tag"]
            ack = (request.POST.get("similar_tag_ack") or "").strip()
            pairs = similar_tag_pairs_for_names([name])
            if pairs and ack not in ("skipped", "accepted"):
                messages.warning(
                    request,
                    f"“{pairs[0][0]}” looks like the existing tag “{pairs[0][1]}”. "
                    "Use the confirmation dialog if it appears, "
                    "or adjust the spelling and try again.",
                )
                return redirect(recipe.get_absolute_url())
            use_name = pairs[0][1] if (ack == "accepted" and pairs) else name
            tag = Tag.get_or_create_for_name(use_name)
            if recipe.tags.filter(pk=tag.pk).exists():
                messages.info(request, f'This recipe already has the tag "{tag.name}".')
            else:
                recipe.tags.add(tag)
                messages.success(request, f'Added tag "{tag.name}".')
        else:
            err = next(
                (e for errs in form.errors.values() for e in errs),
                "Enter a tag name.",
            )
            messages.error(request, err)
        return redirect(recipe.get_absolute_url())


@method_decorator(login_required, name="dispatch")
class AddCommentView(View):
    def post(self, request, slug):
        recipe = get_object_or_404(current_recipes(), slug=slug)
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.recipe = recipe
            comment.author = request.user
            comment.save()
            messages.success(request, "Comment added.")
        else:
            messages.error(request, "Please enter a comment before posting.")
        return redirect(f"{recipe.get_absolute_url()}#comment-form")


@login_required
@require_POST
def rate_recipe(request, slug):
    recipe = get_object_or_404(current_recipes(), slug=slug)
    form = RatingForm(request.POST)
    if form.is_valid():
        rating, _ = Rating.objects.update_or_create(
            recipe=recipe,
            user=request.user,
            defaults={"value": form.cleaned_data["value"]},
        )
        message = "Rating saved."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "ok": True,
                    "message": message,
                    "rating": rating.value,
                    **rating_payload(recipe, request.user),
                }
            )
        messages.success(request, message)
    else:
        message = "Choose a rating from 1 to 5."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "message": message}, status=400)
        messages.error(request, message)
    return redirect(f"{recipe.get_absolute_url()}#discussion")
