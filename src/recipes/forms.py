from __future__ import annotations

import difflib

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, BaseInlineFormSet, formset_factory, inlineformset_factory

from recipes.models import (
    Comment,
    Ingredient,
    InstructionStep,
    Rating,
    Recipe,
    RecipePhoto,
    Tag,
    parse_recipe_tag_names,
)


def similar_tag_pairs_for_names(names: list[str]) -> list[tuple[str, str]]:
    """Pairs (as_typed, existing_canonical_name) for tags close to an existing tag (not exact)."""
    if not names:
        return []
    existing = list(Tag.objects.order_by("name").values_list("name", flat=True))
    if not existing:
        return []
    lowered_to_display = {e.lower(): e for e in existing}
    lowered_names = list(lowered_to_display.keys())
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name in names:
        if Tag.objects.filter(name__iexact=name).exists():
            continue
        matches = difflib.get_close_matches(name.lower(), lowered_names, n=1, cutoff=0.78)
        if not matches:
            continue
        canonical = lowered_to_display[matches[0]]
        if canonical.lower() == name.lower():
            continue
        key = (name, canonical)
        if key not in seen:
            seen.add(key)
            pairs.append(key)
    return pairs


def similar_notes_for_new_tag_names(names: list[str]) -> list[str]:
    """Human-readable notes (legacy / tests); prefer similar_tag_pairs_for_names for UI."""
    return [
        f"'{typed}' is similar to the existing tag '{suggested}'."
        for typed, suggested in similar_tag_pairs_for_names(names)
    ]


class RecipeTagLineForm(forms.Form):
    tag_name = forms.CharField(
        label="Tag",
        max_length=64,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. weeknight",
                "autocomplete": "off",
                "aria-autocomplete": "list",
                "data-tag-suggest": "true",
            },
        ),
    )

    def clean_tag_name(self) -> str:
        raw = self.cleaned_data.get("tag_name") or ""
        return " ".join(str(raw).split()).strip().lower()[:64]


class RecipeTagLineFormSetClass(BaseFormSet):
    """One row per tag; always at least one blank row (extra=1)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.similar_tag_notes: list[str] = []
        self.similar_tag_pairs: list[tuple[str, str]] = []

    def clean(self):
        super().clean()
        names_in_order: list[str] = []
        seen_lower: set[str] = set()
        for form in self.forms:
            if self._should_delete_form(form):
                continue
            if not hasattr(form, "cleaned_data"):
                continue
            name = (form.cleaned_data.get("tag_name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen_lower:
                raise ValidationError(
                    f"Duplicate tag “{name}”. Each tag can only appear once.",
                )
            seen_lower.add(key)
            names_in_order.append(name)
        if len(names_in_order) > 40:
            raise ValidationError("You can add at most 40 tags to one recipe.")
        ack = (self.data.get("similar_tags_ack") or "").strip() if self.data is not None else ""
        if ack == "skipped":
            self.similar_tag_pairs = []
            self.similar_tag_notes = []
            return self.cleaned_data
        self.similar_tag_pairs = similar_tag_pairs_for_names(names_in_order)
        self.similar_tag_notes = [
            f"'{a}' is similar to the existing tag '{b}'." for a, b in self.similar_tag_pairs
        ]
        return self.cleaned_data

    def ordered_tag_names(self) -> list[str]:
        """Non-empty tag names in form order (after successful is_valid)."""
        names: list[str] = []
        for form in self.forms:
            if self._should_delete_form(form):
                continue
            if not form.cleaned_data:
                continue
            name = (form.cleaned_data.get("tag_name") or "").strip()
            if name:
                names.append(name)
        return names


RecipeTagLineFormSet = formset_factory(
    RecipeTagLineForm,
    formset=RecipeTagLineFormSetClass,
    extra=1,
    max_num=40,
    can_delete=True,
)


class OptionalOrderMixin:
    def has_changed(self) -> bool:
        if not super().has_changed():
            return False
        if not self.instance.pk and set(self.changed_data).issubset({"order", "DELETE"}):
            return False
        return True

    def clean_order(self) -> int:
        order = self.cleaned_data.get("order")
        if order is not None:
            return order
        if not self.instance.pk and self.has_changed():
            return 10000
        return 0


class ExistingRowsSingleExtraFormSet(BaseInlineFormSet):
    extra_for_new = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # On edit pages with existing rows, keep only one blank row.
        if self.instance.pk and self.initial_form_count() > 0:
            self.extra = self.extra_for_new


def expand_inline_formset_for_import_initial(
    formset: BaseInlineFormSet,
    initial_rows: list,
) -> None:
    """Unsaved inline formsets count forms from the queryset (empty), not len(initial)."""
    if initial_rows:
        formset.extra = len(initial_rows) + formset.extra_for_new


class IngredientFormSetClass(ExistingRowsSingleExtraFormSet):
    extra_for_new = 1


class InstructionStepFormSetClass(ExistingRowsSingleExtraFormSet):
    extra_for_new = 1


class RecipeImportUrlForm(forms.Form):
    url = forms.URLField(
        label="Recipe URL",
        assume_scheme="https",
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://example.com/your-recipe",
                "inputmode": "url",
            },
        ),
    )


class RecipeForm(forms.ModelForm):
    similar_tags_ack = forms.CharField(required=False, widget=forms.HiddenInput)
    version_save_mode = forms.CharField(required=False, widget=forms.HiddenInput)

    source_url = forms.URLField(required=False, assume_scheme="https")

    class Meta:
        model = Recipe
        fields = [
            "title",
            "description",
            "prep_time_minutes",
            "cook_time_minutes",
            "servings",
            "source_url",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "data-autogrow": "true"}),
        }


class RecipeQuickAddTagForm(forms.Form):
    """Single-tag POST from the recipe detail page (first token if comma-separated)."""

    similar_tag_ack = forms.CharField(required=False, widget=forms.HiddenInput)

    tag = forms.CharField(
        label="Tag",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "New tag",
                "autocomplete": "off",
                "aria-autocomplete": "list",
                "data-tag-suggest": "true",
            },
        ),
    )

    def clean_tag(self) -> str:
        raw = self.cleaned_data.get("tag") or ""
        names = parse_recipe_tag_names(raw, max_tags=1)
        if not names:
            msg = "Enter a tag name."
            raise ValidationError(msg)
        return names[0]


class IngredientForm(OptionalOrderMixin, forms.ModelForm):
    order = forms.IntegerField(min_value=0, required=False)

    class Meta:
        model = Ingredient
        fields = ("quantity", "name", "notes", "order")


IngredientFormSet = inlineformset_factory(
    Recipe,
    Ingredient,
    form=IngredientForm,
    formset=IngredientFormSetClass,
    extra=1,
    can_delete=True,
)


class InstructionStepForm(OptionalOrderMixin, forms.ModelForm):
    order = forms.IntegerField(min_value=0, required=False)

    class Meta:
        model = InstructionStep
        fields = ("text", "order")
        widgets = {"text": forms.Textarea(attrs={"rows": 3, "data-autogrow": "true"})}


InstructionStepFormSet = inlineformset_factory(
    Recipe,
    InstructionStep,
    form=InstructionStepForm,
    formset=InstructionStepFormSetClass,
    extra=1,
    can_delete=True,
)


class RecipePhotoForm(OptionalOrderMixin, forms.ModelForm):
    order = forms.IntegerField(min_value=0, required=False)

    class Meta:
        model = RecipePhoto
        fields = ("image", "caption", "order")
        widgets = {
            "image": forms.FileInput(
                attrs={
                    "accept": "image/*",
                    "class": "photo-editor-file",
                },
            ),
            "caption": forms.TextInput(
                attrs={"placeholder": "Caption (optional)", "maxlength": "180"},
            ),
        }

    def __init__(self, *args, staged_path: str = "", staged_preview_url: str = "", **kwargs):
        self.staged_path = staged_path.strip()
        self.staged_preview_url = staged_preview_url.strip()
        super().__init__(*args, **kwargs)
        self.fields["image"].required = False

    def _is_marked_delete(self) -> bool:
        if not self.data:
            return False
        raw = self.data.get(self.add_prefix("DELETE"), "")
        return str(raw).strip().lower() in {"on", "true", "1", "yes", "y"}

    def clean(self):
        if self._is_marked_delete():
            return {"DELETE": True}
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        staged = (self.data.get(self.add_prefix("staged_path")) or "").strip()
        has_upload = bool(cleaned.get("image"))
        has_caption = bool((cleaned.get("caption") or "").strip())
        if not has_upload and not staged and not has_caption and not self.instance.pk:
            return cleaned
        if not has_upload and not staged and not self.instance.image:
            raise ValidationError("Add a photo or remove this row.")
        return cleaned


class RecipePhotoFormSetClass(BaseInlineFormSet):
    def __init__(self, *args, staged_photos: list[dict[str, str]] | None = None, **kwargs):
        self.staged_photos = list(staged_photos or [])
        super().__init__(*args, **kwargs)
        if self.staged_photos and not self.is_bound:
            self.extra = len(self.staged_photos) + 1

    def _construct_form(self, i, **kwargs):
        if self.staged_photos and i < len(self.staged_photos) and not self.is_bound:
            photo = self.staged_photos[i]
            kwargs.setdefault("staged_path", photo.get("storage_path", ""))
            kwargs.setdefault("staged_preview_url", photo.get("preview_url", ""))
        return super()._construct_form(i, **kwargs)


RecipePhotoFormSet = inlineformset_factory(
    Recipe,
    RecipePhoto,
    form=RecipePhotoForm,
    formset=RecipePhotoFormSetClass,
    extra=1,
    can_delete=True,
)


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Share a note or tip...",
                    "data-autogrow": "true",
                },
            )
        }


class RatingForm(forms.ModelForm):
    value = forms.TypedChoiceField(
        choices=[(number, f"{number} star{'s' if number > 1 else ''}") for number in range(1, 6)],
        coerce=int,
        widget=forms.RadioSelect,
    )

    class Meta:
        model = Rating
        fields = ["value"]
