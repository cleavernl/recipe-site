from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
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
)
from recipes.models import Rating, Recipe


class PrivateRecipeMixin(LoginRequiredMixin):
    login_url = "login"


def purge_expired_deleted_recipes() -> int:
    cutoff = timezone.now() - timedelta(days=7)
    deleted_count, _ = Recipe.objects.filter(deleted_at__lte=cutoff).delete()
    return deleted_count


def active_recipes():
    return Recipe.objects.filter(deleted_at__isnull=True)


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


class RecipeListView(PrivateRecipeMixin, ListView):
    model = Recipe
    paginate_by = 24
    template_name = "recipes/list.html"
    context_object_name = "recipes"

    def get_queryset(self):
        purge_expired_deleted_recipes()
        queryset = (
            active_recipes()
            .select_related("owner")
            .prefetch_related("ingredients", "photos")
            .annotate(average_rating=Avg("ratings__value"))
            .annotate(rating_count=Count("ratings"))
            .order_by("title", "id")
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(ingredients__name__icontains=query)
            ).distinct()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        context["query"] = query
        deleted_recipes = (
            current_recipes()
            .filter(deleted_at__isnull=False)
            .select_related("owner")
            .prefetch_related("photos")
            .annotate(average_rating=Avg("ratings__value"))
            .annotate(rating_count=Count("ratings"))
        )
        if query:
            deleted_recipes = deleted_recipes.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(ingredients__name__icontains=query)
            ).distinct()
        context["deleted_recipes"] = deleted_recipes.order_by("-deleted_at", "title")
        return context


class RecipeDetailView(PrivateRecipeMixin, DetailView):
    model = Recipe
    template_name = "recipes/detail.html"
    context_object_name = "recipe"

    def get_queryset(self):
        return (
            current_recipes()
            .select_related("owner")
            .prefetch_related("ingredients", "steps", "photos", "comments__author", "ratings__user")
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
        else:
            recipe_form = RecipeForm(instance=recipe)
            ingredient_formset = IngredientFormSet(instance=recipe, prefix="ingredients")
            step_formset = InstructionStepFormSet(instance=recipe, prefix="steps")
            photo_formset = RecipePhotoFormSet(instance=recipe, prefix="photos")
        return recipe_form, ingredient_formset, step_formset, photo_formset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "recipe_form" not in context:
            recipe_form, ingredient_formset, step_formset, photo_formset = self.get_forms()
            context.update(
                {
                    "recipe_form": recipe_form,
                    "ingredient_formset": ingredient_formset,
                    "step_formset": step_formset,
                    "photo_formset": photo_formset,
                    "recipe": self.get_recipe(),
                }
            )
        return context

    def post(self, request, *args, **kwargs):
        recipe_form, ingredient_formset, step_formset, photo_formset = self.get_forms()
        if (
            recipe_form.is_valid()
            and ingredient_formset.is_valid()
            and step_formset.is_valid()
            and photo_formset.is_valid()
        ):
            return self.forms_valid(recipe_form, ingredient_formset, step_formset, photo_formset)
        return self.render_to_response(
            self.get_context_data(
                recipe_form=recipe_form,
                ingredient_formset=ingredient_formset,
                step_formset=step_formset,
                photo_formset=photo_formset,
            )
        )

    @transaction.atomic
    def forms_valid(self, recipe_form, ingredient_formset, step_formset, photo_formset):
        recipe = recipe_form.save(commit=False)
        recipe.owner = self.get_recipe().owner
        recipe.save()
        ingredient_formset.instance = recipe
        step_formset.instance = recipe
        photo_formset.instance = recipe
        ingredient_formset.save()
        step_formset.save()
        photo_formset.save()
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
