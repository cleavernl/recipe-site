from __future__ import annotations

from django.db.models import F

from recipes.models import Recipe, RecipePhoto


def sync_legacy_recipe_photo_to_gallery(recipe: Recipe) -> bool:
    """Ensure Recipe.photo appears in the gallery used by the edit form."""
    if not recipe.pk or not recipe.photo or not recipe.photo.name:
        return False

    hero_name = recipe.photo.name
    if recipe.photos.filter(image=hero_name).exists():
        return False

    if recipe.photos.exists():
        recipe.photos.update(order=F("order") + 1)
        order = 1
    else:
        order = 1

    gallery_photo = RecipePhoto(recipe=recipe, order=order)
    gallery_photo.image.name = hero_name
    gallery_photo.save()
    return True
