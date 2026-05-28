from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe

from recipes.markdown import render_recipe_markdown
from recipes.thumbnails import TILE_IMAGE_MAX_WIDTH, thumbnail_url

register = template.Library()


@register.filter(name="recipe_markdown")
def recipe_markdown(value: str) -> str:
    return mark_safe(render_recipe_markdown(value or ""))


@register.filter(name="tile_image_url")
def tile_image_url(image_field, max_width: int = TILE_IMAGE_MAX_WIDTH) -> str:
    name = getattr(image_field, "name", "") or ""
    return thumbnail_url(name, max_width=int(max_width))
