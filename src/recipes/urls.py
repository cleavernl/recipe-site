from __future__ import annotations

from django.urls import path

from recipes import views

app_name = "recipes"

urlpatterns = [
    path("", views.RecipeListView.as_view(), name="list"),
    path("new/", views.RecipeCreateView.as_view(), name="create"),
    path(
        "recently-deleted/",
        views.RecentlyDeletedRecipeListView.as_view(),
        name="recently_deleted",
    ),
    path("<slug:slug>/", views.RecipeDetailView.as_view(), name="detail"),
    path("<slug:slug>/edit/", views.RecipeUpdateView.as_view(), name="update"),
    path("<slug:slug>/delete/", views.RecipeDeleteView.as_view(), name="delete"),
    path("<slug:slug>/restore/", views.RestoreRecipeView.as_view(), name="restore"),
    path("<slug:slug>/print/", views.RecipePrintView.as_view(), name="print"),
    path("<slug:slug>/comments/", views.AddCommentView.as_view(), name="comment"),
    path("<slug:slug>/rating/", views.rate_recipe, name="rate"),
]
