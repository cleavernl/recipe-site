from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest

    from recipes.models import Ingredient, Recipe


def format_ingredient_export_line(ingredient: Ingredient) -> str:
    parts = [ingredient.quantity, ingredient.name, ingredient.notes]
    return " ".join(part for part in parts if part)


def ingredient_export_lines(recipe: Recipe) -> list[str]:
    return [format_ingredient_export_line(ingredient) for ingredient in recipe.ingredients.all()]


def _minutes_to_iso8601_duration(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    return f"PT{minutes}M"


def _recipe_image_url(recipe: Recipe, request: HttpRequest) -> str | None:
    hero_photo = recipe.photos.first()
    if hero_photo and hero_photo.image:
        return request.build_absolute_uri(hero_photo.image.url)
    if recipe.photo:
        return request.build_absolute_uri(recipe.photo.url)
    return None


def build_recipe_json_ld(recipe: Recipe, request: HttpRequest) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.title,
        "url": request.build_absolute_uri(),
    }
    if recipe.description:
        data["description"] = recipe.description
    if recipe.servings is not None:
        data["recipeYield"] = str(recipe.servings)
    prep_time = _minutes_to_iso8601_duration(recipe.prep_time_minutes)
    if prep_time:
        data["prepTime"] = prep_time
    cook_time = _minutes_to_iso8601_duration(recipe.cook_time_minutes)
    if cook_time:
        data["cookTime"] = cook_time
    total_time = _minutes_to_iso8601_duration(recipe.total_time_minutes)
    if total_time:
        data["totalTime"] = total_time
    image_url = _recipe_image_url(recipe, request)
    if image_url:
        data["image"] = image_url
    if recipe.source_url:
        data["mainEntityOfPage"] = recipe.source_url
    ingredient_lines = ingredient_export_lines(recipe)
    if ingredient_lines:
        data["recipeIngredient"] = ingredient_lines
    steps = [
        {"@type": "HowToStep", "text": step.text}
        for step in recipe.steps.all()
        if step.text.strip()
    ]
    if steps:
        data["recipeInstructions"] = steps
    return data
