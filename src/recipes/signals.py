from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from recipes.models import Recipe, RecipePhoto
from recipes.thumbnails import warm_tile_thumbnails


@receiver(post_save, sender=RecipePhoto)
def warm_recipe_photo_thumbnails(sender, instance: RecipePhoto, **kwargs) -> None:
    if instance.image.name:
        warm_tile_thumbnails(instance.image.name)


@receiver(post_save, sender=Recipe)
def warm_legacy_recipe_photo_thumbnails(sender, instance: Recipe, **kwargs) -> None:
    if instance.photo.name:
        warm_tile_thumbnails(instance.photo.name)
