from __future__ import annotations

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from recipes.models import Comment, Ingredient, InstructionStep, Rating, Recipe, RecipePhoto


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


class IngredientFormSetClass(ExistingRowsSingleExtraFormSet):
    extra_for_new = 1


class InstructionStepFormSetClass(ExistingRowsSingleExtraFormSet):
    extra_for_new = 1


class RecipeForm(forms.ModelForm):
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
            "description": forms.Textarea(attrs={"rows": 4}),
        }


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
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}


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


RecipePhotoFormSet = inlineformset_factory(
    Recipe,
    RecipePhoto,
    form=RecipePhotoForm,
    extra=1,
    can_delete=True,
)


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Share a note or tip..."},
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
