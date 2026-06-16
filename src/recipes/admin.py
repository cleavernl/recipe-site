from __future__ import annotations

from django.contrib import admin

from recipes.models import (
    Comment,
    Ingredient,
    InstructionStep,
    Rating,
    Recipe,
    RecipeLineage,
    RecipeMade,
    RecipePhoto,
    Tag,
)


class IngredientInline(admin.TabularInline):
    model = Ingredient
    extra = 1


class InstructionStepInline(admin.TabularInline):
    model = InstructionStep
    extra = 1


class RecipePhotoInline(admin.TabularInline):
    model = RecipePhoto
    extra = 1


@admin.register(RecipeLineage)
class RecipeLineageAdmin(admin.ModelAdmin):
    list_display = ("slug", "created_at")
    search_fields = ("slug",)


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("title", "lineage", "version_number", "owner", "last_edited_by", "servings", "deleted_at", "created_at")
    list_filter = ("deleted_at", "created_at", "updated_at")
    search_fields = ("title", "description", "ingredients__name", "lineage__slug")
    filter_horizontal = ("tags",)
    inlines = [IngredientInline, InstructionStepInline, RecipePhotoInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("recipe", "author", "created_at")
    list_filter = ("created_at",)
    search_fields = ("body", "recipe__title", "author__username")


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ("recipe", "user", "value", "updated_at")
    list_filter = ("value", "updated_at")
    search_fields = ("recipe__title", "user__username")


@admin.register(RecipeMade)
class RecipeMadeAdmin(admin.ModelAdmin):
    list_display = ("recipe", "user", "made_at")
    list_filter = ("made_at",)
    search_fields = ("recipe__title", "user__username")
    readonly_fields = ("made_at",)
