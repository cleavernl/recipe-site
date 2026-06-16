from __future__ import annotations

import os

from django.core.files.base import ContentFile
from django.db.models import Max, OuterRef, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from recipes.models import Ingredient, InstructionStep, Recipe, RecipeLineage, RecipePhoto


def parse_recipe_version_number(raw: str | None) -> int | None:
    """Return a positive version number from the query string, or None."""
    if raw is None:
        return None
    value = str(raw).strip()
    if not value.isdigit():
        return None
    number = int(value)
    return number if number > 0 else None


def recipe_versions_for_lineage(lineage: RecipeLineage, *, base_qs=None):
    queryset = base_qs if base_qs is not None else Recipe.objects.all()
    return queryset.filter(lineage=lineage).order_by("-version_number", "-id")


def restrict_to_latest_versions(queryset):
    """Keep only the newest version row per lineage within the given queryset."""
    latest = queryset.filter(lineage_id=OuterRef("lineage_id")).order_by(
        "-version_number",
        "-id",
    ).values("pk")[:1]
    return queryset.filter(pk=Subquery(latest))


def get_recipe_for_slug(
    slug: str,
    *,
    version_number: int | None = None,
    base_qs=None,
) -> Recipe:
    """Resolve a lineage slug to a recipe version (latest when version_number is omitted)."""
    lineage = get_object_or_404(RecipeLineage, slug=slug)
    versions = recipe_versions_for_lineage(lineage, base_qs=base_qs)
    if version_number is None:
        recipe = versions.first()
    else:
        recipe = versions.filter(version_number=version_number).first()
    if recipe is None:
        raise Http404("Recipe version not found.")
    return recipe


def next_version_number(lineage: RecipeLineage) -> int:
    current = lineage.versions.aggregate(max_version=Max("version_number"))["max_version"]
    return (current or 0) + 1


def lineage_version_choices(recipe: Recipe, *, base_qs=None) -> list[dict]:
    """Version switcher entries for the detail page."""
    queryset = recipe_versions_for_lineage(recipe.lineage, base_qs=base_qs)
    versions = list(queryset.order_by("version_number"))
    if not versions:
        return [
            {
                "number": recipe.version_number,
                "is_current": True,
                "is_latest": True,
                "updated_at": recipe.updated_at,
            },
        ]
    latest_number = versions[-1].version_number
    return [
        {
            "number": version.version_number,
            "is_current": version.pk == recipe.pk,
            "is_latest": version.version_number == latest_number,
            "updated_at": version.updated_at,
        }
        for version in versions
    ]


def version_navigation_context(recipe: Recipe, versions: list[dict]) -> dict:
    """Prev/next URLs and flags for the detail page version control."""
    if len(versions) <= 1:
        return {"show_version_nav": False}

    current_index = next(i for i, version in enumerate(versions) if version["is_current"])

    def version_url(version: dict) -> str:
        base = reverse("recipes:detail", kwargs={"slug": recipe.lineage.slug})
        if version["is_latest"]:
            return base
        return f"{base}?version={version['number']}"

    prev_version = versions[current_index - 1] if current_index > 0 else None
    next_version = versions[current_index + 1] if current_index < len(versions) - 1 else None
    return {
        "show_version_nav": True,
        "version_total": len(versions),
        "version_has_prev": prev_version is not None,
        "version_has_next": next_version is not None,
        "version_prev_url": version_url(prev_version) if prev_version else "",
        "version_next_url": version_url(next_version) if next_version else "",
    }


def copy_storage_file(source_field, dest_field) -> None:
    """Copy an uploaded file from one model FileField/ImageField to another."""
    if not source_field or not source_field.name:
        return
    with source_field.open("rb") as handle:
        content = ContentFile(handle.read(), name=os.path.basename(source_field.name))
    dest_field.save(content.name, content, save=False)


def create_recipe_version_from_form(
    existing: Recipe,
    recipe_form,
    version_number: int,
    *,
    editor,
) -> Recipe:
    """Create a new recipe version row from edit-form scalar fields."""
    recipe = Recipe(
        lineage=existing.lineage,
        version_number=version_number,
        owner=existing.owner,
        last_edited_by=editor,
    )
    for field in recipe_form.Meta.fields:
        setattr(recipe, field, recipe_form.cleaned_data[field])
    recipe.save()
    if existing.photo and existing.photo.name:
        copy_storage_file(existing.photo, recipe.photo)
        recipe.save(update_fields=["photo", "updated_at"])
    return recipe


def save_ingredient_formset_on_new_version(recipe: Recipe, formset) -> None:
    """Persist every non-deleted ingredient row onto a new recipe version."""
    for form in formset.forms:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        if form.cleaned_data.get("DELETE"):
            continue
        name = (form.cleaned_data.get("name") or "").strip()
        quantity = (form.cleaned_data.get("quantity") or "").strip()
        notes = (form.cleaned_data.get("notes") or "").strip()
        if not name and not quantity and not notes:
            continue
        Ingredient.objects.create(
            recipe=recipe,
            name=name,
            quantity=quantity,
            notes=notes,
            order=form.cleaned_data.get("order") or 0,
        )


def save_step_formset_on_new_version(recipe: Recipe, formset) -> None:
    """Persist every non-deleted instruction row onto a new recipe version."""
    for form in formset.forms:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        if form.cleaned_data.get("DELETE"):
            continue
        text = (form.cleaned_data.get("text") or "").strip()
        if not text:
            continue
        InstructionStep.objects.create(
            recipe=recipe,
            text=text,
            order=form.cleaned_data.get("order") or 0,
        )


def copy_gallery_photo_for_new_version(
    recipe: Recipe,
    *,
    source_photo: RecipePhoto,
    caption: str,
    order: int,
) -> None:
    """Copy an existing gallery photo onto a new recipe version."""
    if not source_photo.image or not source_photo.image.name:
        return
    new_photo = RecipePhoto(recipe=recipe, caption=caption, order=order)
    copy_storage_file(source_photo.image, new_photo.image)
    new_photo.save()
