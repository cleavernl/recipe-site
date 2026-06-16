from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Tag(models.Model):
    """Short labels shared across recipes (e.g. weeknight, vegetarian)."""

    name = models.CharField(max_length=64)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name

    @classmethod
    def get_or_create_for_name(cls, name: str) -> Tag:
        cleaned = " ".join(str(name).split()).strip().lower()
        if not cleaned:
            msg = "Tag name cannot be empty."
            raise ValueError(msg)
        cleaned = cleaned[:64]
        existing = cls.objects.filter(name__iexact=cleaned).first()
        if existing:
            if existing.name != cleaned:
                existing.name = cleaned
                existing.save(update_fields=["name"])
            return existing
        slug = (slugify(cleaned) or "tag")[:72]
        tag, _ = cls.objects.get_or_create(slug=slug, defaults={"name": cleaned})
        return tag

    def save(self, *args, **kwargs) -> None:
        self.name = " ".join(str(self.name).split()).lower()[:64]
        if not self.slug:
            self.slug = self._make_unique_slug()
        super().save(*args, **kwargs)

    def _make_unique_slug(self) -> str:
        base = (slugify(self.name) or "tag")[:72]
        slug = base
        counter = 2
        while Tag.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug


def parse_recipe_tag_names(raw: str, *, max_tags: int = 40) -> list[str]:
    """Split user tag input on commas and newlines; strip and cap count."""
    if not raw or not str(raw).strip():
        return []
    names: list[str] = []
    for chunk in str(raw).replace("\n", ",").split(","):
        name = " ".join(chunk.split()).strip().lower()
        if not name:
            continue
        if len(name) > 64:
            name = name[:64].rstrip()
        names.append(name)
        if len(names) >= max_tags:
            break
    return names


def tags_from_parsed_names(names: list[str]) -> list[Tag]:
    """Resolve display names to Tag rows (create when missing)."""
    return [Tag.get_or_create_for_name(name) for name in names]


def sync_recipe_tags(recipe: Recipe, raw: str) -> None:
    """Replace recipe tags from a comma/newline separated string."""
    names = parse_recipe_tag_names(raw)
    recipe.tags.set(tags_from_parsed_names(names))


class RecipeLineage(models.Model):
    """Shared identity for all versions of one recipe (canonical URL slug)."""

    slug = models.SlugField(max_length=220, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slug", "id"]

    def __str__(self) -> str:
        return self.slug or f"Lineage {self.pk}"

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = self._make_unique_slug("recipe")
        super().save(*args, **kwargs)

    @classmethod
    def create_for_title(cls, title: str) -> RecipeLineage:
        base_slug = slugify(title) or "recipe"
        slug = cls._unique_slug(base_slug)
        return cls.objects.create(slug=slug)

    @classmethod
    def _unique_slug(cls, base_slug: str) -> str:
        slug = base_slug
        counter = 2
        while cls.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug

    def _make_unique_slug(self, fallback: str) -> str:
        return self._unique_slug(slugify(fallback) or fallback)


class Recipe(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipes",
    )
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipes_last_edited",
    )
    lineage = models.ForeignKey(
        RecipeLineage,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    prep_time_minutes = models.PositiveIntegerField(null=True, blank=True)
    cook_time_minutes = models.PositiveIntegerField(null=True, blank=True)
    servings = models.PositiveIntegerField(null=True, blank=True)
    source_url = models.URLField(blank=True)
    photo = models.ImageField(upload_to="recipes/photos/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="recipes")

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["lineage", "version_number"],
                name="unique_recipe_version_per_lineage",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def slug(self) -> str:
        return self.lineage.slug

    def save(self, *args, **kwargs) -> None:
        if not self.lineage_id:
            self.lineage = RecipeLineage.create_for_title(self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        url = reverse("recipes:detail", kwargs={"slug": self.lineage.slug})
        latest_number = (
            self.lineage.versions.filter(deleted_at__isnull=True)
            .order_by("-version_number", "-id")
            .values_list("version_number", flat=True)
            .first()
        )
        if latest_number and self.version_number != latest_number:
            return f"{url}?version={self.version_number}"
        return url

    @property
    def total_time_minutes(self) -> int | None:
        values = [self.prep_time_minutes, self.cook_time_minutes]
        if all(value is None for value in values):
            return None
        return sum(value or 0 for value in values)


class Ingredient(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")
    name = models.CharField(max_length=180)
    quantity = models.CharField(max_length=80, blank=True)
    notes = models.CharField(max_length=180, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        parts = [self.quantity, self.name, self.notes]
        return " ".join(part for part in parts if part)


class RecipePhoto(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="recipes/photos/")
    caption = models.CharField(max_length=180, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.caption or f"Photo for {self.recipe}"


class InstructionStep(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="steps")
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.text[:80]


class Comment(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recipe_comments",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Comment by {self.author} on {self.recipe}"


class RecipeMade(models.Model):
    """A logged cooking session when a user finishes make-it mode."""

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="made_records")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recipe_made_records",
    )
    made_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-made_at", "-id"]

    def __str__(self) -> str:
        return f"{self.user} made {self.recipe} at {self.made_at:%Y-%m-%d %H:%M}"


class Rating(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ratings")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recipe_ratings",
    )
    value = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["recipe", "user"],
                name="unique_rating_per_recipe_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.value}/5 for {self.recipe} by {self.user}"
