"""Parametrized regression tests for URL-import parsing (not ingredient token regex).

Ingredient quantity/name/notes cases live in test_ingredient_parsing.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from recipes.url_import import (
    collect_article_image_urls,
    collect_import_image_urls,
    collect_recipe_image_urls,
    draft_form_initial_from_document,
    extract_json_ld_recipes,
    filter_import_tags,
    infer_image_stem_prefix_from_urls,
    instruction_texts_from_value,
    is_public_ip,
    normalize_import_tag_label,
    normalize_instruction_text,
    parse_import_tags,
    parse_ingredient_entry,
    parse_instructions,
    parse_iso8601_duration_minutes,
    parse_recipe_html,
    parse_servings,
    recipe_object_to_document,
    select_recipe_image_urls,
    split_numbered_instruction_text,
    strip_html,
    validate_fetch_url,
)
from recipes.yaml_import import RecipeImportError

SAMPLE_RECIPE_HTML = """
<!doctype html>
<html>
  <head>
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": "Weeknight Chili",
        "description": "<p>Hearty bean chili.</p>",
        "prepTime": "PT15M",
        "cookTime": "PT45M",
        "recipeYield": "6 servings",
        "keywords": "weeknight chili with beans, hearty bean chili",
        "recipeCategory": "dinner, soup",
        "recipeIngredient": [
          "1 tbsp olive oil",
          "2 cans kidney beans"
        ],
        "recipeInstructions": [
          {"@type": "HowToStep", "text": "Heat oil in a pot."},
          {
            "@type": "HowToSection",
            "name": "Simmer",
            "itemListElement": [
              {"@type": "HowToStep", "text": "Add beans and simmer 30 minutes."}
            ]
          }
        ]
      }
    </script>
  </head>
  <body>Recipe page</body>
</html>
"""

GRAPH_RECIPE_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {"@type": "WebPage", "name": "Wrapper"},
    {
      "@type": "Recipe",
      "name": "Graph Soup",
      "recipeYield": 4,
      "recipeIngredient": ["1 cup broth"],
      "recipeInstructions": "Simmer broth 20 minutes."
    }
  ]
}
</script>
</head><body></body></html>
"""

DURATION_CASES: list[tuple[str | None, int | None]] = [
    ("PT15M", 15),
    ("PT1H", 60),
    ("PT1H30M", 90),
    ("PT20M", 20),
    ("PT45S", 1),
    ("PT1H5M", 65),
    ("pt2h10m", 130),
    ("", None),
    (None, None),
    ("P1D", None),
    ("not-a-duration", None),
]

SERVINGS_CASES: list[tuple[object, int | None]] = [
    ("6 servings", 6),
    ("Serves 4-6 people", 4),
    ("Yield: 12", 12),
    (8, 8),
    (0, None),
    ("", None),
    (None, None),
    ("no digits here", None),
]

STRIP_HTML_CASES: list[tuple[str, str]] = [
    ("<p>Hearty bean chili.</p>", "Hearty bean chili."),
    ("Tomato &amp; basil", "Tomato & basil"),
    ("  extra   spaces  ", "extra spaces"),
    ("no tags", "no tags"),
]

# (input value for parse_instructions, expected step texts)
INSTRUCTION_PARSE_CASES: list[tuple[object, list[str]]] = [
    (
        [
            {"@type": "HowToStep", "text": "Heat oil in a pot."},
            {
                "@type": "HowToSection",
                "itemListElement": [
                    {"@type": "HowToStep", "text": "Add beans and simmer 30 minutes."},
                ],
            },
        ],
        ["Heat oil in a pot.", "Add beans and simmer 30 minutes."],
    ),
    ("Simmer broth 20 minutes.", ["Simmer broth 20 minutes."]),
    (
        {"@type": "HowToStep", "text": "<p>Pat chicken <strong>dry</strong>.</p>"},
        ["Pat chicken dry ."],
    ),
    (
        {
            "@type": "HowToStep",
            "name": "Bake",
            "text": "Bake at 400 F for 12 minutes.",
        },
        ["Bake at 400 F for 12 minutes."],
    ),
    ([], []),
    (None, []),
]

HBH_NUMBERED_BLOB = (
    "1. Preheat the oven to 425 degrees F. "
    "2. In a large skillet, heat the olive oil over high heat. "
    "3.\u00a0Line the taco shells up on a sheet pan. "
    "4. Meanwhile, make the ranch. "
    "5. Serve the tacos topped with ranch."
)

NUMBERED_INSTRUCTION_CASES: list[tuple[str, list[str]]] = [
    (
        HBH_NUMBERED_BLOB,
        [
            "Preheat the oven to 425 degrees F.",
            "In a large skillet, heat the olive oil over high heat.",
            "Line the taco shells up on a sheet pan.",
            "Meanwhile, make the ranch.",
            "Serve the tacos topped with ranch.",
        ],
    ),
    (
        "1. First step only.",
        ["1. First step only."],
    ),
    (
        "Mix flour. Add water. No numbers here.",
        ["Mix flour. Add water. No numbers here."],
    ),
]

TAG_NORMALIZE_CASES: list[tuple[str, str]] = [
    ("  Fish Recipes  ", "Fish"),
    ("<em>Dinner</em>", "Dinner"),
    ("", ""),
]

TAG_FILTER_CASES: list[tuple[list[str], str, list[str]]] = [
    (["Fish Recipes", "Summer Recipes"], "Grilled Trout", ["Fish", "Summer"]),
    (
        ["Salad", "Northwest", "Salad"],
        "Grilled Salmon Salad",
        ["Salad", "Northwest"],
    ),
    (
        ["This Is A Very Long Tag Label That Exceeds Word Limit"],
        "Soup",
        [],
    ),
    (
        ["Grilled Salmon Salad with Avocado"],
        "Grilled Salmon Salad with Avocado Cucumber Salsa",
        [],
    ),
]

PARSE_IMPORT_TAGS_CASES: list[tuple[dict[str, str], str, list[str]]] = [
    (
        {
            "name": "Grilled Salmon Salad with Avocado Cucumber Salsa",
            "keywords": "grilled salmon, avocado salsa, Mexican grilled salmon",
            "recipeCategory": "Salad",
            "recipeCuisine": "Northwest",
        },
        "Grilled Salmon Salad with Avocado Cucumber Salsa",
        ["Salad", "Northwest"],
    ),
    (
        {
            "name": "Chili",
            "keywords": "weeknight chili with beans",
            "recipeCategory": "dinner, soup",
        },
        "Chili",
        ["dinner", "soup"],
    ),
]

INGREDIENT_ENTRY_CASES: list[tuple[object, str, str, list[str]]] = [
    ("2 cups all-purpose flour", "2 cups", "all-purpose flour", []),
    (
        {"amount": "1 tbsp", "name": "olive oil"},
        "1 tbsp",
        "olive oil",
        [],
    ),
    (
        {"quantity": "1/4 cup", "name": "lime juice", "notes": "more to taste"},
        "1/4 cup",
        "lime juice",
        ["more to taste"],
    ),
    (
        {"amount": "3 cloves garlic, minced"},
        "3 cloves",
        "garlic",
        ["minced"],
    ),
]

IMAGE_SELECT_CASES: list[tuple[list[str], int, list[str]]] = [
    (
        [
            "https://example.com/salad-225x225.jpg",
            "https://example.com/salad-320x180.jpg",
            "https://example.com/salad.jpg",
        ],
        1,
        ["https://example.com/salad.jpg"],
    ),
    (
        [
            "https://example.com/salmon-avocado-salad-500-3-225x225.jpg",
            "https://example.com/salmon-avocado-salad-500-3-320x180.jpg",
            "https://example.com/salmon-avocado-salad-500-3.jpg",
        ],
        2,
        ["https://example.com/salmon-avocado-salad-500-3.jpg"],
    ),
    (
        [
            "https://example.com/hero.jpg",
            "https://example.com/hero-100x100.jpg",
            "https://example.com/other-dish.jpg",
        ],
        2,
        [
            "https://example.com/other-dish.jpg",
            "https://example.com/hero.jpg",
        ],
    ),
]

INVALID_FETCH_URL_CASES: list[tuple[str, str]] = [
    ("file:///etc/passwd", "http:// or https://"),
    ("ftp://example.com/recipe", "http:// or https://"),
    ("https://user:pass@example.com/r", "credentials"),
    ("not-a-url", "http:// or https://"),
]


class TestDurationAndServings:
    @pytest.mark.parametrize(("raw", "expected"), DURATION_CASES, ids=[str(c[0]) for c in DURATION_CASES])
    def test_parse_iso8601_duration_minutes(self, raw: str | None, expected: int | None) -> None:
        assert parse_iso8601_duration_minutes(raw) == expected

    @pytest.mark.parametrize(("raw", "expected"), SERVINGS_CASES, ids=[repr(c[0]) for c in SERVINGS_CASES])
    def test_parse_servings(self, raw: object, expected: int | None) -> None:
        assert parse_servings(raw) == expected


class TestStripHtml:
    @pytest.mark.parametrize(("raw", "expected"), STRIP_HTML_CASES)
    def test_strip_html(self, raw: str, expected: str) -> None:
        assert strip_html(raw) == expected


class TestInstructionParsing:
    @pytest.mark.parametrize(
        ("value", "expected_steps"),
        INSTRUCTION_PARSE_CASES,
        ids=[f"case-{index}" for index in range(len(INSTRUCTION_PARSE_CASES))],
    )
    def test_parse_instructions(self, value: object, expected_steps: list[str]) -> None:
        assert parse_instructions(value) == expected_steps

    @pytest.mark.parametrize(
        ("blob", "expected_steps"),
        NUMBERED_INSTRUCTION_CASES,
        ids=[f"numbered-{index}" for index in range(len(NUMBERED_INSTRUCTION_CASES))],
    )
    def test_split_numbered_instruction_text(self, blob: str, expected_steps: list[str]) -> None:
        assert split_numbered_instruction_text(blob) == expected_steps

    def test_parse_instructions_splits_hbh_style_blob(self) -> None:
        steps = parse_instructions({"@type": "HowToStep", "text": HBH_NUMBERED_BLOB})
        assert len(steps) == 5
        assert steps[0].startswith("Preheat the oven")

    def test_normalize_instruction_text_collapses_nbsp(self) -> None:
        assert "\u00a0" not in normalize_instruction_text("a\u00a0b")

    def test_instruction_texts_from_value_empty_dict(self) -> None:
        assert instruction_texts_from_value({}) == []


class TestTagParsing:
    @pytest.mark.parametrize(("raw", "expected"), TAG_NORMALIZE_CASES)
    def test_normalize_import_tag_label(self, raw: str, expected: str) -> None:
        assert normalize_import_tag_label(raw) == expected

    @pytest.mark.parametrize(
        ("raw_tags", "title", "expected"),
        TAG_FILTER_CASES,
        ids=[f"filter-{index}" for index in range(len(TAG_FILTER_CASES))],
    )
    def test_filter_import_tags(self, raw_tags: list[str], title: str, expected: list[str]) -> None:
        assert filter_import_tags(raw_tags, title=title) == expected

    @pytest.mark.parametrize(
        ("recipe", "title", "expected"),
        PARSE_IMPORT_TAGS_CASES,
        ids=[case[0].get("name", "recipe") for case in PARSE_IMPORT_TAGS_CASES],
    )
    def test_parse_import_tags(self, recipe: dict[str, str], title: str, expected: list[str]) -> None:
        assert parse_import_tags(recipe, title=title) == expected


class TestIngredientEntryParsing:
    @pytest.mark.parametrize(
        ("value", "quantity", "name", "note_parts"),
        INGREDIENT_ENTRY_CASES,
        ids=[str(index) for index in range(len(INGREDIENT_ENTRY_CASES))],
    )
    def test_parse_ingredient_entry(
        self,
        value: object,
        quantity: str,
        name: str,
        note_parts: list[str],
    ) -> None:
        parsed = parse_ingredient_entry(value)
        assert parsed["quantity"] == quantity
        assert parsed["name"] == name
        notes = parsed["notes"].casefold()
        for fragment in note_parts:
            assert fragment.casefold() in notes


class TestJsonLdExtraction:
    def test_extract_json_ld_recipes_from_sample_html(self) -> None:
        recipes = extract_json_ld_recipes(SAMPLE_RECIPE_HTML)
        assert len(recipes) == 1
        assert recipes[0]["name"] == "Weeknight Chili"

    def test_extract_json_ld_recipes_from_graph(self) -> None:
        recipes = extract_json_ld_recipes(GRAPH_RECIPE_HTML)
        assert len(recipes) == 1
        assert recipes[0]["name"] == "Graph Soup"

    def test_extract_json_ld_skips_invalid_json(self) -> None:
        html = """
        <script type="application/ld+json">{not valid}</script>
        <script type="application/ld+json">{"@type":"Recipe","name":"Valid"}</script>
        """
        recipes = extract_json_ld_recipes(html)
        assert len(recipes) == 1
        assert recipes[0]["name"] == "Valid"

    def test_parse_recipe_html_requires_recipe_data(self) -> None:
        with pytest.raises(RecipeImportError, match="No recipe data"):
            parse_recipe_html("<html><body>empty</body></html>", source_url="https://example.com/x")

    def test_parse_recipe_html_full_document(self) -> None:
        document = parse_recipe_html(SAMPLE_RECIPE_HTML, source_url="https://example.com/chili")
        assert document["title"] == "Weeknight Chili"
        assert document["description"] == "Hearty bean chili."
        assert document["prep_time_minutes"] == 15
        assert document["cook_time_minutes"] == 45
        assert document["servings"] == 6
        assert document["source_url"] == "https://example.com/chili"
        assert document["tags"] == ["dinner", "soup"]
        assert len(document["ingredients"]) == 2
        assert document["ingredients"][0] == {
            "quantity": "1 tbsp",
            "name": "olive oil",
            "notes": "",
        }
        assert document["steps"] == [
            "Heat oil in a pot.",
            "Add beans and simmer 30 minutes.",
        ]

    def test_recipe_object_to_document_uses_total_time_when_no_prep_cook(self) -> None:
        recipe = {
            "name": "Quick Braise",
            "totalTime": "PT40M",
            "recipeIngredient": [],
            "recipeInstructions": [],
        }
        document = recipe_object_to_document(recipe, source_url="https://example.com/b")
        assert document["prep_time_minutes"] is None
        assert document["cook_time_minutes"] == 40


class TestDraftFormInitial:
    def test_draft_form_initial_from_document_shapes_formsets(self) -> None:
        document = parse_recipe_html(SAMPLE_RECIPE_HTML, source_url="https://example.com/chili")
        draft = draft_form_initial_from_document(document)
        assert draft["recipe"]["title"] == "Weeknight Chili"
        assert len(draft["ingredients"]) == 2
        assert len(draft["steps"]) == 2
        assert draft["tags"] == [{"tag_name": "dinner"}, {"tag_name": "soup"}]


class TestImageImportParsing:
    @pytest.mark.parametrize(
        ("urls", "max_photos", "expected"),
        IMAGE_SELECT_CASES,
        ids=[f"select-{index}" for index in range(len(IMAGE_SELECT_CASES))],
    )
    def test_select_recipe_image_urls(self, urls: list[str], max_photos: int, expected: list[str]) -> None:
        assert select_recipe_image_urls(urls, max_photos=max_photos) == expected

    def test_collect_recipe_image_urls_from_schema_image_list(self) -> None:
        recipe = {
            "image": [
                "https://example.com/thumb-100x100.jpg",
                "https://example.com/hero.jpg",
            ],
        }
        assert collect_recipe_image_urls(recipe) == [
            "https://example.com/thumb-100x100.jpg",
            "https://example.com/hero.jpg",
        ]

    def test_collect_import_image_urls_includes_distinct_article_photos(self) -> None:
        recipe = {
            "image": [
                "https://example.com/salmon-salad-500-3-320x180.jpg",
                "https://example.com/salmon-salad-500-3.jpg",
            ],
        }
        html = """
        <img src="https://example.com/wp-content/uploads/salmon-salad-503-683x1024.jpg">
        <img src="https://example.com/wp-content/uploads/salmon-salad-500-3-225x225.jpg">
        """
        selected = collect_import_image_urls(
            recipe,
            html,
            page_url="https://example.com/recipe",
            max_photos=2,
        )
        assert len(selected) == 2
        assert "https://example.com/salmon-salad-500-3.jpg" in selected
        assert "https://example.com/wp-content/uploads/salmon-salad-503-683x1024.jpg" in selected

    def test_collect_article_image_urls_filters_external_and_non_uploads(self) -> None:
        html = """
        <img src="https://other.example.com/wp-content/uploads/x.jpg">
        <img src="https://example.com/wp-content/uploads/hero.jpg">
        <img src="https://example.com/wp-content/uploads/logo-widget.png">
        """
        urls = collect_article_image_urls(html, page_url="https://example.com/post")
        assert urls == ["https://example.com/wp-content/uploads/hero.jpg"]

    def test_infer_image_stem_prefix_from_urls(self) -> None:
        urls = [
            "https://example.com/salmon-salad-500-3.jpg",
            "https://example.com/salmon-salad-503-683x1024.jpg",
        ]
        prefix = infer_image_stem_prefix_from_urls(urls)
        assert prefix.startswith("salmon-salad")


class TestFetchUrlValidation:
    @pytest.mark.parametrize(("url", "message_fragment"), INVALID_FETCH_URL_CASES)
    def test_validate_fetch_url_rejects_bad_urls(self, url: str, message_fragment: str) -> None:
        with pytest.raises(RecipeImportError) as exc_info:
            validate_fetch_url(url)
        assert message_fragment.casefold() in str(exc_info.value).casefold()

    @patch("recipes.url_import.socket.getaddrinfo")
    def test_validate_fetch_url_rejects_private_ip(self, getaddrinfo_mock) -> None:
        getaddrinfo_mock.return_value = [(None, None, None, None, ("127.0.0.1", 443))]
        with pytest.raises(RecipeImportError, match="private or local"):
            validate_fetch_url("https://example.com/recipe")

    @patch("recipes.url_import.socket.getaddrinfo")
    def test_validate_fetch_url_accepts_public_ip(self, getaddrinfo_mock) -> None:
        getaddrinfo_mock.return_value = [(None, None, None, None, ("93.184.216.34", 443))]
        assert validate_fetch_url("https://example.com/recipe") == "https://example.com/recipe"


class TestIsPublicIp:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("8.8.8.8", True),
            ("127.0.0.1", False),
            ("10.0.0.1", False),
            ("::1", False),
        ],
    )
    def test_is_public_ip(self, address: str, expected: bool) -> None:
        import ipaddress

        assert is_public_ip(ipaddress.ip_address(address)) is expected


class TestLiveSiteImports:
    """Optional live fetches; skip when offline or blocked."""

    def test_halfbakedharvest_chipotle_tacos_five_steps(self) -> None:
        from recipes.url_import import fetch_and_parse_recipe_url

        document = fetch_and_parse_recipe_url(
            "https://www.halfbakedharvest.com/chipotle-chicken-tacos/",
        )
        assert len(document["steps"]) == 5
        assert document["steps"][0].startswith("Preheat the oven")

    def test_feastingathome_salmon_salad_import_shape(self) -> None:
        from recipes.url_import import fetch_and_parse_recipe_url

        document = fetch_and_parse_recipe_url(
            "https://www.feastingathome.com/grilled-salmon-salad-with-avocado-salsa/",
        )
        assert document["title"]
        assert len(document["ingredients"]) >= 5
        assert len(document["steps"]) >= 1
        for ingredient in document["ingredients"]:
            assert ")" not in ingredient["name"]
            assert "(" not in ingredient["name"]
