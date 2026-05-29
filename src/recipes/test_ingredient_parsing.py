"""Regression tests for URL-import ingredient quantity/name/notes parsing."""

from __future__ import annotations

import pytest

from recipes.url_import import (
    clean_orphan_parentheses,
    extract_parenthetical_notes,
    normalize_ingredient_parentheses,
    parse_ingredient_string,
    split_ingredient_name_notes,
)

# (raw ingredient line, expected quantity, expected name, expected note substrings)
INGREDIENT_CASES: list[tuple[str, str, str, list[str]]] = [
  # Quantity + unit splitting
    ("2 cups all-purpose flour", "2 cups", "all-purpose flour", []),
    ("4 x 6 oz salmon filets", "4 x 6 oz", "salmon filets", []),
    ("1/2 C plain Greek yogurt", "1/2 C", "plain Greek yogurt", []),
    ("4x6 oz salmon filets", "4 x 6 oz", "salmon filets", []),
    ("½ teaspoon pepper", "½ teaspoon", "pepper", []),
    ("2-3 teaspoons olive oil", "2-3 teaspoons", "olive oil", []),
    ("3-4 heads little gems lettuce", "3-4 heads", "little gems lettuce", []),
    ("14 oz / 400 g ground beef", "14 oz / 400 g", "ground beef", []),
    ("3 oz / 100g ground pork", "3 oz / 100g", "ground pork", []),
    # Pinch of Yum: numeric HTML entity en-dash between amounts
    (
        "1/4 &#8211; 1/2 cup olive oil or butter for frying",
        "1/4 – 1/2 cup",
        "olive oil or butter for frying",
        [],
    ),
    (
        "1/4 – 1/2 cup olive oil or butter for frying",
        "1/4 – 1/2 cup",
        "olive oil or butter for frying",
        [],
    ),
    ("1/4 to 1/2 cup broth", "1/4 to 1/2 cup", "broth", []),
    # Pinch of Yum: compound prep after comma must not split on trailing verb
    (
        "1 (14-ounce) can of black beans, rinsed and drained",
        "1 can",
        "black beans",
        ["14-ounce", "rinsed and drained"],
    ),
    # AllRecipes: parenthetical can size + cans prefix
    (
        "2 (6 ounce) cans tuna, drained and flaked",
        "2 cans",
        "tuna",
        ["6 ounce", "drained and flaked"],
    ),
    (
        "1 (10.75 ounce) can Campbell's® Condensed Cream of Celery Soup or Campbell's® Condensed 98% Fat Free Cream of Celery Soup",
        "1 can",
        "Campbell's® Condensed Cream of Celery Soup",
        ["10.75 ounce", "or Campbell's® Condensed 98% Fat Free"],
    ),
    # Parentheses and prep
    ("1 avocado (ripe, but slightly firm) diced", "1", "avocado", ["ripe", "diced"]),
    (
        "1/2 a jalapeno, sliced (more for more heat)",
        "1/2",
        "jalapeno",
        ["sliced", "more for more heat"],
    ),
    ("3 Turkish cucumbers, sliced, salted", "3", "Turkish cucumbers", ["sliced", "salted"]),
    ("¼ cup chopped cilantro", "¼ cup", "cilantro", ["chopped"]),
    ("Lime for garnish", "", "Lime", ["for garnish"]),
    (
        "4 x 4-6 ounce pieces of salmon (or sub steelhead or ocean trout)",
        "4 x 4-6 ounce pieces",
        "salmon",
        ["steelhead"],
    ),
    # More to taste (Feasting at Home)
    ("1/4 cup lime juice, more to taste", "1/4 cup", "lime juice", ["more to taste"]),
    ("1/2 teaspoon honey, more to taste", "1/2 teaspoon", "honey", ["more to taste"]),
    # RecipeTin Eats JSON-LD shapes (doubled parens, nested notes)
    (
        "1  lightly packed cup of diced white sandwich bread (, crusts removed (Note 1 for SUB))",
        "1",
        "cup of diced white sandwich bread",
        ["lightly packed", "crusts removed", "Note 1"],
    ),
    (
        "1  small onion ((brown, white or yellow))",
        "1 small",
        "onion",
        ["brown", "white or yellow"],
    ),
    (
        "14 oz / 400 g   ground beef ((mince))",
        "14 oz / 400 g",
        "ground beef",
        ["mince"],
    ),
    (
        "3 oz / 100g   ground pork ((mince), or sub with more beef (Note 2))",
        "3 oz / 100g",
        "ground pork",
        ["mince", "or sub with more beef", "Note 2"],
    ),
    (
        "1/4 cup fresh parsley (, finely chopped (Note 3))",
        "1/4 cup",
        "fresh parsley",
        ["finely chopped", "Note 3"],
    ),
    (
        "1/4 cup Parmigiano-Reggiano ((or parmesan), freshly grated)",
        "1/4 cup",
        "Parmigiano-Reggiano",
        ["parmesan", "freshly grated"],
    ),
    (
        "24 oz / 700 g   tomato passata ((Tomato Puree in US/CAN - Note 4))",
        "24 oz / 700 g",
        "tomato passata",
        ["Tomato Puree", "Note 4"],
    ),
    (
        "Parsley (, finely chopped (optional))",
        "",
        "Parsley",
        ["finely chopped", "optional"],
    ),
]


PARENTHESIS_NORMALIZE_CASES = [
    ("((brown, white or yellow))", "(brown, white or yellow)"),
    ("bread (, crusts removed (Note 1))", "bread (crusts removed (Note 1)"),
    ("no change", "no change"),
]


PARENTHETICAL_EXTRACT_CASES = [
    ("onion ((brown, white or yellow))", "onion", ["brown, white or yellow"]),
    ("beef ((mince))", "beef", ["mince"]),
    (
        "pork ((mince), or sub with more beef (Note 2))",
        "pork",
        ["mince", "or sub with more beef", "Note 2"],
    ),
]


class TestIngredientParsing:
    def test_slash_is_not_parsed_as_quantity_token(self) -> None:
        from recipes.url_import import is_quantity_token

        assert not is_quantity_token("/")
        assert not is_quantity_token("-")

    @pytest.mark.parametrize(
        ("raw", "quantity", "name", "note_parts"),
        INGREDIENT_CASES,
        ids=[case[0][:48] for case in INGREDIENT_CASES],
    )
    def test_parse_ingredient_string_cases(
        self,
        raw: str,
        quantity: str,
        name: str,
        note_parts: list[str],
    ) -> None:
        parsed = parse_ingredient_string(raw)
        assert parsed["quantity"] == quantity, parsed
        assert parsed["name"] == name, parsed
        notes = parsed["notes"].casefold()
        for fragment in note_parts:
            assert fragment.casefold() in notes, parsed
        assert ")" not in parsed["name"], parsed
        assert "(" not in parsed["name"], parsed

    @pytest.mark.parametrize(("raw", "expected"), PARENTHESIS_NORMALIZE_CASES)
    def test_normalize_ingredient_parentheses(self, raw: str, expected: str) -> None:
        assert normalize_ingredient_parentheses(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "remaining", "notes"),
        PARENTHETICAL_EXTRACT_CASES,
        ids=[case[0][:40] for case in PARENTHETICAL_EXTRACT_CASES],
    )
    def test_extract_parenthetical_notes(self, raw: str, remaining: str, notes: list[str]) -> None:
        got_remaining, got_notes = extract_parenthetical_notes(raw)
        assert got_remaining == remaining
        for expected_note in notes:
            assert any(expected_note in note for note in got_notes), got_notes

    def test_clean_orphan_parentheses(self) -> None:
        assert clean_orphan_parentheses("bread )") == "bread"
        assert clean_orphan_parentheses("( onion") == "onion"

    def test_split_ingredient_name_notes_lime_juice_phrase(self) -> None:
        name, notes = split_ingredient_name_notes("lime juice from one small lime")
        assert name == "lime juice"
        assert notes == "from one small lime"


class TestRecipeTinEatsImportIntegration:
    """Live fetch; skipped when offline."""

    def test_recipetineats_meatballs_have_no_stray_parens(self) -> None:
        from recipes.url_import import fetch_and_parse_recipe_url

        document = fetch_and_parse_recipe_url(
            "https://www.recipetineats.com/classic-italian-meatballs-extra-soft-and-juicy/",
        )
        assert document["title"]
        for ingredient in document["ingredients"]:
            assert ")" not in ingredient["name"], ingredient
            assert "(" not in ingredient["name"], ingredient
            assert ")" not in ingredient["quantity"], ingredient
