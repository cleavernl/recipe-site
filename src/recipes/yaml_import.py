from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import transaction

from recipes.models import Ingredient, InstructionStep, Recipe, RecipePhoto, sync_recipe_tags

User = get_user_model()


class RecipeImportError(Exception):
    """Raised when a recipe YAML file cannot be imported."""


@dataclass(frozen=True)
class ImportRowResult:
    path: Path
    action: str
    title: str
    detail: str = ""


def optional_positive_int(value: Any, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        msg = f"{field} must be an integer or empty, got {value!r}"
        raise RecipeImportError(msg) from exc
    if parsed < 0:
        msg = f"{field} must be non-negative, got {parsed}"
        raise RecipeImportError(msg)
    return parsed


def optional_url(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_recipe_document(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"Invalid YAML: {exc}"
        raise RecipeImportError(msg) from exc
    if not isinstance(raw, dict):
        msg = "Recipe file must be a YAML mapping at the top level."
        raise RecipeImportError(msg)
    return raw


def validate_recipe_document(data: dict[str, Any], *, path: Path) -> dict[str, Any]:
    title = str(data.get("title") or "").strip()
    if not title:
        msg = f"{path}: missing title"
        raise RecipeImportError(msg)

    ingredients = data.get("ingredients")
    if ingredients is None:
        ingredients = []
    if not isinstance(ingredients, list):
        msg = f"{path}: ingredients must be a list"
        raise RecipeImportError(msg)

    steps = data.get("steps")
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        msg = f"{path}: steps must be a list"
        raise RecipeImportError(msg)

    tags = data.get("tags")
    if tags is None:
        tags = []
    if not isinstance(tags, list):
        msg = f"{path}: tags must be a list"
        raise RecipeImportError(msg)

    photos = data.get("photos")
    if photos is None:
        photos = []
    if not isinstance(photos, list):
        msg = f"{path}: photos must be a list"
        raise RecipeImportError(msg)

    return {
        "title": title,
        "description": str(data.get("description") or "").strip(),
        "prep_time_minutes": optional_positive_int(
            data.get("prep_time_minutes"),
            field="prep_time_minutes",
        ),
        "cook_time_minutes": optional_positive_int(
            data.get("cook_time_minutes"),
            field="cook_time_minutes",
        ),
        "servings": optional_positive_int(data.get("servings"), field="servings"),
        "source_url": optional_url(data.get("source_url")),
        "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
        "ingredients": ingredients,
        "steps": steps,
        "photos": photos,
    }


def recipe_yaml_paths(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.recipe.yaml")) + sorted(directory.glob("*.recipe.yml"))


def existing_recipe_for_title(title: str) -> Recipe | None:
    return Recipe.objects.filter(title__iexact=title, deleted_at__isnull=True).first()


def ingredient_is_empty(entry: dict[str, Any]) -> bool:
    for key in ("quantity", "name", "notes"):
        if str(entry.get(key) or "").strip():
            return False
    return True


def step_is_empty(text: Any) -> bool:
    return not str(text or "").strip()


@transaction.atomic
def import_recipe_from_document(
    *,
    owner: User,
    base_dir: Path,
    path: Path,
    document: dict[str, Any],
    dry_run: bool = False,
) -> ImportRowResult:
    title = document["title"]

    if dry_run:
        photo_count = len(document["photos"])
        return ImportRowResult(
            path=path,
            action="would_import",
            title=title,
            detail=f"{len(document['ingredients'])} ingredients, "
            f"{len(document['steps'])} steps, {photo_count} photos",
        )

    recipe = Recipe.objects.create(
        owner=owner,
        title=title,
        description=document["description"],
        prep_time_minutes=document["prep_time_minutes"],
        cook_time_minutes=document["cook_time_minutes"],
        servings=document["servings"],
        source_url=document["source_url"],
    )

    for order, entry in enumerate(document["ingredients"]):
        if not isinstance(entry, dict):
            msg = f"{path}: ingredient {order} must be a mapping"
            raise RecipeImportError(msg)
        if ingredient_is_empty(entry):
            continue
        Ingredient.objects.create(
            recipe=recipe,
            quantity=str(entry.get("quantity") or "").strip()[:80],
            name=str(entry.get("name") or "").strip()[:180],
            notes=str(entry.get("notes") or "").strip()[:180],
            order=order,
        )

    for order, text in enumerate(document["steps"]):
        if step_is_empty(text):
            continue
        InstructionStep.objects.create(
            recipe=recipe,
            text=str(text).strip(),
            order=order,
        )

    if document["tags"]:
        sync_recipe_tags(recipe, ", ".join(document["tags"]))

    for order, photo in enumerate(document["photos"]):
        if not isinstance(photo, dict):
            msg = f"{path}: photo {order} must be a mapping"
            raise RecipeImportError(msg)
        relative = str(photo.get("path") or "").strip()
        if not relative:
            continue
        source = (base_dir / relative).resolve()
        if not source.is_file():
            msg = f"{path}: photo not found: {relative}"
            raise RecipeImportError(msg)
        with source.open("rb") as handle:
            RecipePhoto.objects.create(
                recipe=recipe,
                image=File(handle, name=source.name),
                caption=str(photo.get("caption") or "").strip()[:180],
                order=order,
            )

    return ImportRowResult(
        path=path,
        action="imported",
        title=title,
        detail=recipe.slug,
    )


def import_recipes_from_directory(
    *,
    owner: User,
    directory: Path,
    dry_run: bool = False,
    skip_existing: bool = True,
) -> list[ImportRowResult]:
    if not directory.is_dir():
        msg = f"Directory not found: {directory}"
        raise RecipeImportError(msg)

    paths = recipe_yaml_paths(directory)
    if not paths:
        msg = f"No *.recipe.yaml files in {directory}"
        raise RecipeImportError(msg)

    results: list[ImportRowResult] = []
    for path in paths:
        data = validate_recipe_document(load_recipe_document(path), path=path)
        if skip_existing and existing_recipe_for_title(data["title"]) is not None:
            existing = existing_recipe_for_title(data["title"])
            assert existing is not None
            results.append(
                ImportRowResult(
                    path=path,
                    action="skipped",
                    title=data["title"],
                    detail=f"already exists as {existing.slug}",
                ),
            )
            continue
        results.append(
            import_recipe_from_document(
                owner=owner,
                base_dir=directory,
                path=path,
                document=data,
                dry_run=dry_run,
            ),
        )
    return results
