from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe

from recipes.markdown import render_recipe_markdown

register = template.Library()


@register.filter(name="recipe_markdown")
def recipe_markdown(value: str) -> str:
    return mark_safe(render_recipe_markdown(value or ""))
