from __future__ import annotations

import random
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import NamedTuple
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import (
    Avg,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Subquery,
)
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
    RecipeImportUrlForm,
    RecipePhotoFormSet,
    RecipeQuickAddTagForm,
    RecipeTagLineFormSet,
    expand_inline_formset_for_import_initial,
    similar_tag_pairs_for_names,
)
from recipes.models import Comment, Rating, Recipe, RecipeLineage, RecipeMade, Tag, sync_recipe_tags
from recipes.photo_sync import sync_legacy_recipe_photo_to_gallery
from recipes.versioning import (
    copy_gallery_photo_for_new_version,
    create_recipe_version_from_form,
    get_recipe_for_slug,
    lineage_version_choices,
    next_version_number,
    parse_recipe_version_number,
    restrict_to_latest_versions,
    save_ingredient_formset_on_new_version,
    save_step_formset_on_new_version,
    version_navigation_context,
)
from recipes.url_import import (
    RECIPE_URL_IMPORT_SESSION_KEY,
    attach_staged_photo_to_recipe,
    cleanup_staged_photos,
    draft_form_initial_from_document,
    fetch_and_parse_recipe_url,
    new_staging_token,
)
from recipes.yaml_import import RecipeImportError


class PrivateRecipeMixin(LoginRequiredMixin):
    login_url = "login"


def purge_expired_deleted_recipes() -> int:
    cutoff = timezone.now() - timedelta(days=7)
    expired = Recipe.objects.filter(deleted_at__lte=cutoff)
    deleted_count, _ = expired.delete()
    RecipeLineage.objects.filter(versions__isnull=True).delete()
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


def rating_form_post_data(post) -> dict:
    """Normalize POST data so RatingForm always receives a ``value`` field."""
    data = post.copy()
    if "value" not in data:
        for key in post:
            if key.endswith("-value"):
                data["value"] = post.get(key)
                break
    return data


def rating_payload(recipe: Recipe, user, *, rating: Rating | None = None) -> dict:
    aggregate = recipe.ratings.aggregate(average=Avg("value"), count=Count("id"))
    average = aggregate["average"]
    count = aggregate["count"] or 0
    user_name = display_user_name(user)
    payload = {
        "average": round(average, 1) if average is not None else None,
        "average_percent": round((average or 0) / 5 * 100, 2),
        "count": count,
        "reviewer_label": f"{user_name} (you)",
        "user_id": user.id,
        "user_name": user_name,
        "version_number": recipe.version_number,
    }
    if rating is not None:
        payload["rating_id"] = rating.id
    return payload


def user_can_edit_recipe(user, recipe: Recipe) -> bool:
    """Any signed-in member may edit recipe content."""
    return user.is_authenticated


def user_can_update_recipe_version_in_place(user, recipe: Recipe) -> bool:
    """Only the member who last saved this version may overwrite it."""
    if not user.is_authenticated or not recipe.pk:
        return False
    last_editor_id = recipe.last_edited_by_id or recipe.owner_id
    return last_editor_id == user.id


def user_must_save_new_recipe_version(user, recipe: Recipe) -> bool:
    if not recipe.pk:
        return False
    return not user_can_update_recipe_version_in_place(user, recipe)


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
SORT_MADE = "made"

RECIPE_LIST_SORTS = frozenset(
    {
        SORT_TITLE,
        SORT_RATING,
        SORT_COOK_TIME,
        SORT_PREP_TIME,
        SORT_EASE,
        SORT_UPDATED,
        SORT_MADE,
    }
)

DEFAULT_RECIPE_LIST_SORT_DIR: dict[str, str] = {
    SORT_TITLE: "asc",
    SORT_RATING: "desc",
    SORT_COOK_TIME: "asc",
    SORT_PREP_TIME: "asc",
    SORT_EASE: "asc",
    SORT_UPDATED: "desc",
    SORT_MADE: "desc",
}

RECIPE_LIST_SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    (SORT_TITLE, "Title"),
    (SORT_RATING, "Rating"),
    (SORT_COOK_TIME, "Cook time"),
    (SORT_PREP_TIME, "Prep time"),
    (SORT_EASE, "Ease"),
    (SORT_UPDATED, "Updated"),
    (SORT_MADE, "Last made"),
)

RECIPE_LIST_SORT_LABELS = dict(RECIPE_LIST_SORT_OPTIONS)

LIST_TAG_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")

RECENTLY_MADE_HOME_LIMIT = 3
RECENTLY_DELETED_HOME_LIMIT = 3


class RecentlyMadeItem(NamedTuple):
    recipe: Recipe
    made_at: datetime
    user_rating: int | None
    rating_form: RatingForm | None


class RecentlyDeletedItem(NamedTuple):
    recipe: Recipe
    deleted_at: datetime


class RecipeListMakerFilter(NamedTuple):
    id: int
    display_name: str
    recipe_count: int


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


def normalize_recipe_list_made_by(request) -> int | None:
    """Return a validated user id from ``made_by``, or None for any maker."""
    raw = request.GET.get("made_by", "").strip()
    if not raw.isdigit():
        return None
    user_id = int(raw)
    if user_id <= 0:
        return None
    return user_id


def build_recipe_list_query_pairs(
    query: str,
    tag_slugs: list[str],
    sort: str,
    sort_dir: str,
    made_by_user_id: int | None = None,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    stripped = query.strip()
    if stripped:
        pairs.append(("q", stripped))
    for slug in tag_slugs:
        pairs.append(("tag", slug))
    if made_by_user_id:
        pairs.append(("made_by", str(made_by_user_id)))
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


def annotate_recipe_last_made(queryset):
    """Latest make-it session per recipe (any user), for list tiles and sorting."""
    latest_made = RecipeMade.objects.filter(recipe_id=OuterRef("pk")).order_by("-made_at", "-id")
    return queryset.annotate(
        last_made_at=Subquery(latest_made.values("made_at")[:1]),
        last_made_by_id=Subquery(latest_made.values("user_id")[:1]),
    )


def attach_last_made_display(recipes) -> None:
    """Set ``last_made_by_display`` on recipe instances that have ``last_made_at``."""
    if hasattr(recipes, "object_list"):
        items = recipes.object_list
    else:
        items = list(recipes)
    if not items:
        return
    user_ids = {
        recipe.last_made_by_id
        for recipe in items
        if getattr(recipe, "last_made_at", None) and getattr(recipe, "last_made_by_id", None)
    }
    users_by_id = get_user_model().objects.in_bulk(user_ids) if user_ids else {}
    for recipe in items:
        if not getattr(recipe, "last_made_at", None):
            recipe.last_made_by_display = None
            continue
        maker = users_by_id.get(recipe.last_made_by_id)
        recipe.last_made_by_display = display_user_name(maker) if maker else None


def filtered_active_recipe_queryset(
    search_text: str,
    tag_slugs: list[str] | None = None,
    *,
    made_by_user_id: int | None = None,
):
    """Active recipes optionally filtered by search text, tags (AND), and/or maker."""
    queryset = annotate_recipe_last_made(
        restrict_to_latest_versions(
            active_recipes()
            .select_related("owner", "lineage")
            .prefetch_related("ingredients", "photos", "tags")
            .annotate(average_rating=Avg("ratings__value"))
            .annotate(rating_count=Count("ratings")),
        ),
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
    if made_by_user_id:
        queryset = queryset.filter(made_records__user_id=made_by_user_id)
    if query or tag_slugs or made_by_user_id:
        queryset = queryset.distinct()
    return queryset


def makers_for_recipe_list_filter(
    search_text: str,
    tag_slugs: list[str],
) -> list[RecipeListMakerFilter]:
    """Family members who made at least one recipe matching the current text/tag filters."""
    recipe_ids = filtered_active_recipe_queryset(search_text, tag_slugs).values("pk")
    rows = list(
        RecipeMade.objects.filter(recipe_id__in=recipe_ids)
        .values("user_id")
        .annotate(recipe_count=Count("recipe_id", distinct=True))
        .order_by("-recipe_count", "user_id"),
    )
    if not rows:
        return []
    users_by_id = get_user_model().objects.in_bulk(row["user_id"] for row in rows)
    makers: list[RecipeListMakerFilter] = []
    for row in rows:
        user = users_by_id.get(row["user_id"])
        if user is None:
            continue
        makers.append(
            RecipeListMakerFilter(
                id=row["user_id"],
                display_name=display_user_name(user),
                recipe_count=row["recipe_count"],
            ),
        )
    return makers


def recently_made_recipes_for_user(
    user,
    *,
    limit: int = RECENTLY_MADE_HOME_LIMIT,
) -> list[RecentlyMadeItem]:
    """Active recipes the user cooked recently, newest session first (one tile per recipe)."""
    rows = list(
        RecipeMade.objects.filter(user=user, recipe__deleted_at__isnull=True)
        .values("recipe_id")
        .annotate(last_made_at=Max("made_at"))
        .order_by("-last_made_at", "-recipe_id")[:limit]
    )
    if not rows:
        return []
    made_at_by_id = {row["recipe_id"]: row["last_made_at"] for row in rows}
    recipe_ids = [row["recipe_id"] for row in rows]
    recipes_by_id = {
        recipe.pk: recipe
        for recipe in filtered_active_recipe_queryset("", []).filter(pk__in=recipe_ids)
    }
    ratings_by_id = dict(
        Rating.objects.filter(user=user, recipe_id__in=recipe_ids).values_list("recipe_id", "value"),
    )
    items: list[RecentlyMadeItem] = []
    for pk in recipe_ids:
        recipe = recipes_by_id.get(pk)
        if recipe is None:
            continue
        user_rating = ratings_by_id.get(pk)
        rating_form = (
            None
            if user_rating is not None
            else RatingForm(auto_id=f"recent-{pk}-%s")
        )
        items.append(
            RecentlyMadeItem(
                recipe=recipe,
                made_at=made_at_by_id[pk],
                user_rating=user_rating,
                rating_form=rating_form,
            ),
        )
    return items


def recently_deleted_recipes_for_home(
    *,
    limit: int = RECENTLY_DELETED_HOME_LIMIT,
) -> tuple[list[RecentlyDeletedItem], bool]:
    """Soft-deleted recipes for the home preview, newest first."""
    purge_expired_deleted_recipes()
    recipes = list(
        restrict_to_latest_versions(
            current_recipes()
            .filter(deleted_at__isnull=False)
            .select_related("owner", "lineage")
            .prefetch_related("photos"),
        )
        .order_by("-deleted_at", *_recipe_title_order())[: limit + 1],
    )
    has_more = len(recipes) > limit
    items = [
        RecentlyDeletedItem(recipe=recipe, deleted_at=recipe.deleted_at)
        for recipe in recipes[:limit]
        if recipe.deleted_at is not None
    ]
    return items, has_more


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
    if sort_key == SORT_MADE:
        if sort_dir == "asc":
            return qs.order_by(F("last_made_at").asc(nulls_last=True), *title_tiebreak)
        return qs.order_by(F("last_made_at").desc(nulls_last=True), *title_tiebreak)
    return qs.order_by(*title_tiebreak)


class RandomRecipeView(PrivateRecipeMixin, View):
    """Redirect to a random recipe among those matching the optional list search (q)."""

    def get(self, request, *args, **kwargs):
        purge_expired_deleted_recipes()
        q = request.GET.get("q", "").strip()
        tag_slugs = normalized_recipe_list_tag_slugs(request)
        made_by_user_id = normalize_recipe_list_made_by(request)
        recipe_qs = filtered_active_recipe_queryset(q, tag_slugs, made_by_user_id=made_by_user_id)
        pk_list = list(recipe_qs.values_list("pk", flat=True))
        if not pk_list:
            if q or tag_slugs or made_by_user_id:
                messages.info(request, "No recipes match your current filters.")
            else:
                messages.info(request, "There are no recipes to choose from yet.")
            list_url = reverse("recipes:list")
            sort = normalize_recipe_list_sort(request)
            sort_dir = normalize_recipe_list_sort_dir(request, sort)
            pairs = build_recipe_list_query_pairs(
                q,
                tag_slugs,
                sort,
                sort_dir,
                made_by_user_id,
            )
            query_string = urlencode(pairs)
            return redirect(f"{list_url}?{query_string}" if query_string else list_url)
        chosen_pk = random.choice(pk_list)
        slug = recipe_qs.filter(pk=chosen_pk).values_list("lineage__slug", flat=True).first()
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
        made_by_user_id = normalize_recipe_list_made_by(self.request)
        return ordered_recipe_list_queryset(
            filtered_active_recipe_queryset(
                self.request.GET.get("q", ""),
                tag_slugs,
                made_by_user_id=made_by_user_id,
            ),
            sort,
            sort_dir,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        sort = normalize_recipe_list_sort(self.request)
        sort_dir = normalize_recipe_list_sort_dir(self.request, sort)
        tag_slugs = normalized_recipe_list_tag_slugs(self.request)
        made_by_user_id = normalize_recipe_list_made_by(self.request)
        context["query"] = query
        context["sort"] = sort
        context["sort_dir"] = sort_dir
        context["list_tag_slugs"] = tag_slugs
        context["list_made_by_user_id"] = made_by_user_id
        context["list_query_no_page"] = urlencode(
            build_recipe_list_query_pairs(query, tag_slugs, sort, sort_dir, made_by_user_id),
        )
        context["recipe_list_sort_options"] = RECIPE_LIST_SORT_OPTIONS
        context["sort_display_label"] = RECIPE_LIST_SORT_LABELS.get(
            sort,
            RECIPE_LIST_SORT_LABELS[SORT_TITLE],
        )
        context["filter_tags"] = filter_tags_for_recipe_list(query, tag_slugs)
        filter_makers = makers_for_recipe_list_filter(query, tag_slugs)
        context["filter_makers"] = filter_makers
        list_made_by_display = None
        if made_by_user_id:
            for maker in filter_makers:
                if maker.id == made_by_user_id:
                    list_made_by_display = maker.display_name
                    break
        context["list_made_by_display"] = list_made_by_display
        context["recently_made_items"] = recently_made_recipes_for_user(self.request.user)
        recently_deleted_items, has_more_recently_deleted = recently_deleted_recipes_for_home()
        context["recently_deleted_items"] = recently_deleted_items
        context["has_more_recently_deleted"] = has_more_recently_deleted
        attach_last_made_display(context["recipes"])
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
            .select_related("owner", "lineage")
            .prefetch_related(
                "ingredients",
                "steps",
                "photos",
                "ratings__user",
                "tags",
            )
            .annotate(average_rating=Avg("ratings__value"))
            .annotate(rating_count=Count("ratings"))
        )

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        version_number = parse_recipe_version_number(self.request.GET.get("version"))
        return get_recipe_for_slug(
            self.kwargs["slug"],
            version_number=version_number,
            base_qs=queryset,
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
        lineage_recipe_ids = list(
            current_recipes()
            .filter(lineage=recipe.lineage)
            .values_list("pk", flat=True),
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
        context["lineage_ratings"] = [
            {
                "id": rating.id,
                "is_current_user": rating.user_id == self.request.user.id,
                "user_id": rating.user_id,
                "user_name": display_user_name(rating.user),
                "value": rating.value,
                "value_percent": rating.value * 20,
                "version_number": rating.recipe.version_number,
            }
            for rating in Rating.objects.filter(recipe_id__in=lineage_recipe_ids)
            .select_related("user", "recipe")
            .order_by("-updated_at", "-id")
        ]
        context["comment_sort"] = comment_sort
        context["prompt_review"] = self.request.GET.get("review") == "1"
        context["comments"] = [
            {
                "author_name": display_user_name(comment.author),
                "body": comment.body,
                "created_at": comment.created_at,
                "id": comment.id,
                "version_number": comment.recipe.version_number,
            }
            for comment in Comment.objects.filter(recipe_id__in=lineage_recipe_ids)
            .select_related("author", "recipe")
            .order_by(*comment_ordering)
        ]
        context["recipe_versions"] = lineage_version_choices(
            recipe,
            base_qs=current_recipes().filter(lineage=recipe.lineage),
        )
        context.update(version_navigation_context(recipe, context["recipe_versions"]))
        context["is_latest_version"] = any(
            version["is_current"] and version["is_latest"] for version in context["recipe_versions"]
        )
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
                }
            )
        if "recipe" not in context:
            context["recipe"] = self.get_recipe()
        context["tag_suggestions"] = tag_suggestions_queryset()
        recipe = context["recipe"]
        if recipe.pk:
            context["require_new_version_on_save"] = user_must_save_new_recipe_version(
                self.request.user,
                recipe,
            )
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
            recipe = self.get_recipe()
            version_save_mode = (request.POST.get("version_save_mode") or "").strip()
            if recipe.pk and version_save_mode not in {"update", "new_version"}:
                if user_must_save_new_recipe_version(request.user, recipe):
                    return self.render_to_response(
                        self.get_context_data(
                            recipe_form=recipe_form,
                            ingredient_formset=ingredient_formset,
                            step_formset=step_formset,
                            photo_formset=photo_formset,
                            tag_formset=tag_formset,
                            show_version_save_required_modal=True,
                        )
                    )
                return self.render_to_response(
                    self.get_context_data(
                        recipe_form=recipe_form,
                        ingredient_formset=ingredient_formset,
                        step_formset=step_formset,
                        photo_formset=photo_formset,
                        tag_formset=tag_formset,
                        show_version_save_modal=True,
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
        existing = self.get_recipe()
        version_save_mode = (self.request.POST.get("version_save_mode") or "update").strip()
        must_new_version = user_must_save_new_recipe_version(self.request.user, existing)
        create_new_version = bool(
            existing.pk and (must_new_version or version_save_mode == "new_version")
        )

        if create_new_version:
            recipe = create_recipe_version_from_form(
                existing,
                recipe_form,
                next_version_number(existing.lineage),
                editor=self.request.user,
            )
            save_ingredient_formset_on_new_version(recipe, ingredient_formset)
            save_step_formset_on_new_version(recipe, step_formset)
            self.save_photo_formset(recipe, photo_formset, for_new_version=True)
            sync_recipe_tags(recipe, ", ".join(tag_formset.ordered_tag_names()))
            messages.success(self.request, f"Saved as version {recipe.version_number}.")
            return redirect(recipe)

        recipe = recipe_form.save(commit=False)
        if existing.pk:
            recipe.pk = existing.pk
            recipe.lineage = existing.lineage
            recipe.version_number = existing.version_number
            recipe.owner = existing.owner
        else:
            recipe.owner = self.request.user

        recipe.last_edited_by = self.request.user
        recipe.save()
        ingredient_formset.instance = recipe
        step_formset.instance = recipe
        photo_formset.instance = recipe
        ingredient_formset.save()
        step_formset.save()
        self.save_photo_formset(recipe, photo_formset)
        sync_recipe_tags(recipe, ", ".join(tag_formset.ordered_tag_names()))
        messages.success(self.request, "Recipe saved.")
        return redirect(recipe)

    def _photo_form_marked_delete(self, form) -> bool:
        if hasattr(form, "cleaned_data") and form.cleaned_data.get("DELETE"):
            return True
        raw = (self.request.POST.get(form.add_prefix("DELETE")) or "").strip().lower()
        return raw in {"on", "true", "1", "yes", "y"}

    def save_photo_formset(self, recipe, photo_formset, *, for_new_version: bool = False) -> None:
        staged_paths: list[str] = []
        for form in photo_formset.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            staged = (self.request.POST.get(form.add_prefix("staged_path")) or "").strip()
            if self._photo_form_marked_delete(form):
                if not for_new_version and form.instance.pk:
                    form.instance.delete()
                if staged:
                    staged_paths.append(staged)
                continue

            uploaded = form.cleaned_data.get("image")
            caption = str(form.cleaned_data.get("caption") or "").strip()[:180]
            order = form.cleaned_data.get("order")
            order_value = order if order is not None else 0

            if uploaded:
                form.instance.pk = None
                form.instance.recipe = recipe
                form.save()
                if staged:
                    staged_paths.append(staged)
                continue

            if staged:
                attach_staged_photo_to_recipe(
                    recipe,
                    staged,
                    caption=caption,
                    order=order_value,
                )
                staged_paths.append(staged)
                continue

            if for_new_version and form.instance.pk and form.instance.image:
                copy_gallery_photo_for_new_version(
                    recipe,
                    source_photo=form.instance,
                    caption=caption or form.instance.caption,
                    order=order_value,
                )

        cleanup_staged_photos(staged_paths)


class RecipeCreateView(RecipeFormMixin):
    def pop_import_draft(self) -> dict | None:
        draft = self.request.session.pop(RECIPE_URL_IMPORT_SESSION_KEY, None)
        if isinstance(draft, dict):
            return draft
        return None

    def get_forms(self):
        recipe = self.get_recipe()
        if recipe.pk:
            sync_legacy_recipe_photo_to_gallery(recipe)
        if self.request.method == "POST":
            return super().get_forms()

        import_draft = self.pop_import_draft()
        if not import_draft:
            return super().get_forms()

        recipe_form = RecipeForm(instance=recipe, initial=import_draft.get("recipe", {}))
        ingredient_initial = import_draft.get("ingredients", [])
        ingredient_formset = IngredientFormSet(
            instance=recipe,
            initial=ingredient_initial,
            prefix="ingredients",
        )
        expand_inline_formset_for_import_initial(ingredient_formset, ingredient_initial)

        step_initial = import_draft.get("steps", [])
        step_formset = InstructionStepFormSet(
            instance=recipe,
            initial=step_initial,
            prefix="steps",
        )
        expand_inline_formset_for_import_initial(step_formset, step_initial)
        staged_photos = import_draft.get("staged_photos", [])
        photo_formset = RecipePhotoFormSet(
            instance=recipe,
            prefix="photos",
            staged_photos=staged_photos,
        )
        tag_formset = RecipeTagLineFormSet(
            initial=import_draft.get("tags", []),
            prefix="tags",
        )
        self._import_staged_photos = staged_photos
        return recipe_form, ingredient_formset, step_formset, photo_formset, tag_formset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["import_url_form"] = RecipeImportUrlForm()
        return context


class RecipeImportFromUrlView(PrivateRecipeMixin, View):
    def post(self, request, *args, **kwargs):
        form = RecipeImportUrlForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter a valid recipe URL.")
            return redirect("recipes:create")

        url = form.cleaned_data["url"]
        try:
            document = fetch_and_parse_recipe_url(url, stage_photos_token=new_staging_token())
        except RecipeImportError as exc:
            messages.error(request, str(exc))
            return redirect("recipes:create")

        request.session[RECIPE_URL_IMPORT_SESSION_KEY] = draft_form_initial_from_document(document)
        photo_count = len(document.get("staged_photos") or [])
        if photo_count:
            detail = (
                f"Imported “{document['title']}” with {photo_count} photo(s). "
                "Review the details below, then save the recipe."
            )
        else:
            detail = (
                f"Imported “{document['title']}”. "
                "Review the details below, then save the recipe."
            )
        messages.success(request, detail)
        return redirect("recipes:create")


class RecipeUpdateView(RecipeFormMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        version_number = parse_recipe_version_number(request.GET.get("version"))
        self.recipe = get_recipe_for_slug(
            kwargs["slug"],
            version_number=version_number,
            base_qs=current_recipes(),
        )
        return super().dispatch(request, *args, **kwargs)


class RecipeDeleteView(PrivateRecipeMixin, DeleteView):
    model = Recipe
    template_name = "recipes/confirm_delete.html"
    success_url = reverse_lazy("recipes:list")

    def get_queryset(self):
        return active_recipes().select_related("lineage")

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        return get_recipe_for_slug(self.kwargs["slug"], base_qs=queryset)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        recipe = self.object
        now = timezone.now()
        recipe.lineage.versions.filter(deleted_at__isnull=True).update(
            deleted_at=now,
            updated_at=now,
        )
        messages.success(self.request, "Recipe moved to recently deleted.")
        return redirect(self.success_url)


class RecentlyDeletedRecipeListView(PrivateRecipeMixin, ListView):
    model = Recipe
    template_name = "recipes/recently_deleted.html"
    context_object_name = "recipes"

    def get_queryset(self):
        purge_expired_deleted_recipes()
        return (
            restrict_to_latest_versions(
                current_recipes()
                .filter(deleted_at__isnull=False)
                .select_related("owner", "lineage")
                .prefetch_related("photos")
                .annotate(average_rating=Avg("ratings__value"))
                .annotate(rating_count=Count("ratings")),
            )
            .order_by(
                "-deleted_at",
                "title",
            )
        )


@method_decorator(login_required, name="dispatch")
class RestoreRecipeView(View):
    def post(self, request, slug):
        recipe = get_recipe_for_slug(
            slug,
            base_qs=current_recipes().filter(deleted_at__isnull=False),
        )
        recipe.lineage.versions.exclude(deleted_at__isnull=True).update(
            deleted_at=None,
            updated_at=timezone.now(),
        )
        messages.success(request, "Recipe restored.")
        return redirect(recipe)


class RecipePrintView(PrivateRecipeMixin, DetailView):
    model = Recipe
    template_name = "recipes/print.html"
    context_object_name = "recipe"

    def get_queryset(self):
        return current_recipes().select_related("owner", "lineage").prefetch_related("ingredients", "steps")

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        version_number = parse_recipe_version_number(self.request.GET.get("version"))
        return get_recipe_for_slug(
            self.kwargs["slug"],
            version_number=version_number,
            base_qs=queryset,
        )


class RecipeMakeMixin(PrivateRecipeMixin, DetailView):
    model = Recipe
    template_name = "recipes/make.html"
    context_object_name = "recipe"
    make_active_panel = "ingredients"

    def get_queryset(self):
        return current_recipes().select_related("lineage").prefetch_related("ingredients", "steps")

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        version_number = parse_recipe_version_number(self.request.GET.get("version"))
        return get_recipe_for_slug(
            self.kwargs["slug"],
            version_number=version_number,
            base_qs=queryset,
        )

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
    version_number = parse_recipe_version_number(request.GET.get("version"))
    recipe = get_recipe_for_slug(slug, version_number=version_number, base_qs=current_recipes())
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
        recipe = get_recipe_for_slug(slug, base_qs=active_recipes())
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
        version_number = parse_recipe_version_number(request.GET.get("version"))
        recipe = get_recipe_for_slug(
            slug,
            version_number=version_number,
            base_qs=current_recipes(),
        )
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
    version_number = parse_recipe_version_number(request.GET.get("version"))
    recipe = get_recipe_for_slug(
        slug,
        version_number=version_number,
        base_qs=current_recipes(),
    )
    if request.POST.get("clear") == "1":
        deleted, _ = Rating.objects.filter(recipe=recipe, user=request.user).delete()
        message = "Rating removed." if deleted else "No rating to remove."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "ok": True,
                    "message": message,
                    "rating": None,
                    "cleared": True,
                    **rating_payload(recipe, request.user),
                }
            )
        messages.success(request, message)
        return redirect(f"{recipe.get_absolute_url()}#discussion")
    form = RatingForm(rating_form_post_data(request.POST))
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
                    **rating_payload(recipe, request.user, rating=rating),
                }
            )
        messages.success(request, message)
    else:
        message = "Choose a rating from 1 to 5."
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "message": message}, status=400)
        messages.error(request, message)
    return redirect(f"{recipe.get_absolute_url()}#discussion")
