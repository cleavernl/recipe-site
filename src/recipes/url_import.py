from __future__ import annotations

import html
import ipaddress
import json
import re
import secrets
import socket
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files import File
from PIL import Image, ImageOps

from recipes.models import RecipePhoto
from recipes.yaml_import import RecipeImportError, validate_recipe_document

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMPORT_PHOTOS = 2
FETCH_TIMEOUT_SECONDS = 15
USER_AGENT = "FamilyRecipes/1.0 (recipe URL import)"
STAGED_PHOTO_ROOT = "url_import/staging"
ALLOWED_IMAGE_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "image/jpg"},
)
IMAGE_DIMENSION_IN_URL_RE = re.compile(
    r"-(\d+)x(\d+)\.(?:jpe?g|png|webp|gif)(?:\?.*)?$",
    re.IGNORECASE,
)
ARTICLE_IMAGE_SRC_RE = re.compile(
    r"""(?:src|data-src)=["'](https?://[^"']+)["']""",
    re.IGNORECASE,
)
ARTICLE_IMAGE_SKIP_PATH_RE = re.compile(
    r"(?:gravatar|emoji|icon|logo|avatar|pixel|tracking|spinner|badge|button|widget)",
    re.IGNORECASE,
)

DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", re.IGNORECASE)
HUMAN_HOURS_RE = re.compile(r"(\d+)\s*hours?", re.IGNORECASE)
HUMAN_MINUTES_RE = re.compile(r"(\d+)\s*min(?:ute)?s?", re.IGNORECASE)
HUMAN_TIME_TOKEN_RE = re.compile(r"\b(?:hours?|min(?:ute)?s?)\b", re.IGNORECASE)
SERVINGS_META_RE = re.compile(
    r"\b(?:persons?|servings?|serves|cookies?|yield|makes)\b",
    re.IGNORECASE,
)
INGREDIENTS_HEADING_RE = re.compile(r"^ingredients:?$", re.IGNORECASE)
INSTRUCTIONS_HEADING_RE = re.compile(r"^instructions?:?$", re.IGNORECASE)
INSTRUCTION_STOP_HEADING_RE = re.compile(
    r"^(?:cook[’']?s?\s+note|nutritional|share with|recipes you may like|"
    r"this recipe uses|join the|follow us|quick links|newsletter)",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
FRACTION_CHARS = "¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
QUANTITY_TOKEN_RE = re.compile(
    rf"^[\d./{FRACTION_CHARS}]+(?:-\d+)?$",
    re.IGNORECASE,
)
UNIT_TOKEN_RE = re.compile(
    r"^(?:"
    r"cups?|c\.?|"
    r"tbsp|tbsps|tablespoons?|"
    r"tsp|tsps|teaspoons?|"
    r"oz|ounces?|fl\s*oz|fluid\s*ounces?|"
    r"g|grams?|kg|kilograms?|"
    r"ml|milliliters?|l|liters?|"
    r"lb|lbs|pounds?|"
    r"cloves?|sticks?|slices?|"
    r"can|cans|package|packages|bunch|"
    r"pinch|dash|"
    r"quarts?|pints?|gallons?|"
    r"heads?|sprigs?|stalks?|ears?|pieces?|"
    r"large|medium|small"
    r")$",
    re.IGNORECASE,
)
DIMENSION_X_RE = re.compile(r"^[xX×]$")
QUANTITY_RANGE_SEPARATORS = frozenset({"-", "–", "—"})
QUANTITY_RANGE_WORDS = frozenset({"to"})
SLASH_METRIC_TOKEN_RE = re.compile(
    r"^[\d.]+(?:g|kg|oz|lbs?|ml|l)$",
    re.IGNORECASE,
)

PREP_VERB = (
    r"chopped|diced|minced|sliced|grated|peeled|crushed|drained|rinsed|"
    r"softened|melted|packed|sifted|seeded|deveined|halved|quartered|"
    r"shredded|crumbled|beaten|whisked|mashed|salted"
)
PREP_ADV = r"finely|coarsely|roughly|thinly|lightly|freshly"
COMMA_MODIFIER_SUFFIX_RE = re.compile(
    r",\s*(more\s+to\s+taste|to\s+taste)$",
    re.IGNORECASE,
)
TRAILING_FOR_RE = re.compile(
    r"\s+(?:for\s+(?:garnish|serving|drizzling)|optional)\s*$",
    re.IGNORECASE,
)
LEADING_CONNECTOR_RE = re.compile(r"^(?:a|an|of)\s+", re.IGNORECASE)
OR_SUBSTITUTION_SUFFIX_RE = re.compile(r"(?:,\s*|\s+)(or\s+.+)$", re.IGNORECASE)
CONTAINER_PREFIX_RE = re.compile(
    r"^(?P<container>(?:cans?|jars?|packages?|pkgs?|boxes?|bags?))(?:\s+of)?\s+(?P<name>.+)$",
    re.IGNORECASE,
)
TRAILING_FROM_RE = re.compile(r"\s+from\s+.+$", re.IGNORECASE)
TRAILING_PREP_RE = re.compile(
    rf"\s+(?:(?:{PREP_ADV})\s+)?(?<!\band\s)(?:{PREP_VERB})\s*$",
    re.IGNORECASE,
)
LEADING_PREP_RE = re.compile(
    rf"^((?:(?:{PREP_ADV})\s+)?(?:{PREP_VERB}))\s+(.+)$",
    re.IGNORECASE,
)
FROM_PHRASE_RE = re.compile(r"^from\s+.+$", re.IGNORECASE)
JUICE_ZEST_RE = re.compile(r"^juice\s+and\s+zest$", re.IGNORECASE)

RECIPE_URL_IMPORT_SESSION_KEY = "recipe_url_import_draft"

# Broad labels (meal type, cuisine) — not schema.org keywords (often SEO dish phrases).
IMPORT_TAG_MAX_COUNT = 6
IMPORT_TAG_MAX_LENGTH = 64
IMPORT_TAG_MAX_WORDS = 4
_CATEGORY_SUFFIX_RE = re.compile(r"\s+recipes?$", re.IGNORECASE)


class JsonLdScriptExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._collecting = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attrs_dict = {key: value for key, value in attrs if key and value is not None}
        if attrs_dict.get("type", "").lower() == "application/ld+json":
            self._collecting = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._collecting:
            self.scripts.append("".join(self._buffer))
            self._collecting = False

    def handle_data(self, data: str) -> None:
        if self._collecting:
            self._buffer.append(data)


def strip_html(text: str) -> str:
    return " ".join(HTML_TAG_RE.sub(" ", html.unescape(text)).split())


def parse_iso8601_duration_minutes(value: Any) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip().upper()
    match = DURATION_RE.match(text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total = hours * 60 + minutes + (1 if seconds >= 30 else 0)
    return total if total > 0 else None


def parse_human_duration_minutes(value: Any) -> int | None:
    """Parse '35 Minutes' / '1 Hour 20 Minutes'; first comma segment only."""
    if value is None or value == "":
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    primary = text.split(",")[0].strip()
    if not HUMAN_TIME_TOKEN_RE.search(primary):
        if not HUMAN_TIME_TOKEN_RE.search(text):
            return None
        primary = text
    hours = 0
    minutes = 0
    hour_match = HUMAN_HOURS_RE.search(primary)
    if hour_match:
        hours = int(hour_match.group(1))
    minute_match = HUMAN_MINUTES_RE.search(primary)
    if minute_match:
        minutes = int(minute_match.group(1))
    total = hours * 60 + minutes
    return total if total > 0 else None


def minutes_to_iso8601_duration(minutes: int) -> str:
    return f"PT{minutes}M"


def parse_servings(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        parsed = int(value)
        return parsed if parsed > 0 else None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    parsed = int(match.group())
    return parsed if parsed > 0 else None


def decode_import_text(text: str) -> str:
    """Decode HTML entities from JSON-LD / markup (e.g. &#8211; en-dash)."""
    return html.unescape(text)


def parse_ingredient_entry(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        quantity = decode_import_text(str(value.get("amount") or value.get("quantity") or "")).strip()
        name = decode_import_text(str(value.get("name") or value.get("item") or "")).strip()
        notes = decode_import_text(
            str(value.get("notes") or value.get("description") or ""),
        ).strip()
        if not name and quantity:
            return parse_ingredient_string(quantity)
        return {
            "quantity": quantity[:80],
            "name": name[:180],
            "notes": notes[:180],
        }
    return parse_ingredient_string(str(value or ""))


def normalize_ingredient_text(text: str) -> str:
    cleaned = " ".join(decode_import_text(text).split()).strip()
    cleaned = re.sub(r"(\d)\s*[xX×]\s*(\d)", r"\1 x \2", cleaned)
    return normalize_ingredient_parentheses(cleaned)


def normalize_ingredient_parentheses(text: str) -> str:
    """Collapse RecipeTin Eats-style doubled parens and '(, note' typos from JSON-LD."""
    cleaned = text.strip()
    while "((" in cleaned:
        cleaned = cleaned.replace("((", "(")
    while "))" in cleaned:
        cleaned = cleaned.replace("))", ")")
    cleaned = re.sub(r"\(\s*,\s*", "(", cleaned)
    return cleaned


def _clean_note_fragment(note: str) -> str:
    return note.strip(" ,")


def clean_orphan_parentheses(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s+\)", "", cleaned)
    cleaned = re.sub(r"\(\s+", "", cleaned)
    cleaned = re.sub(r"^\(+", "", cleaned)
    cleaned = re.sub(r"\)+$", "", cleaned)
    return cleaned.strip(" ,")


def _pop_one_balanced_parenthetical(text: str) -> tuple[str, str | None]:
    start = text.find("(")
    if start == -1:
        return text, None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                inner = text[start + 1 : index].strip()
                remaining = f"{text[:start]} {text[index + 1 :]}".strip()
                remaining = re.sub(r"\s+", " ", remaining)
                return remaining, inner or None
    return text[:start].strip(), text[start + 1 :].strip() or None


def extract_parenthetical_notes(text: str) -> tuple[str, list[str]]:
    """Extract nested/balanced parenthetical notes; avoids leaving stray ')'."""
    notes: list[str] = []
    remaining = text.strip()
    while "(" in remaining:
        remaining, inner = _pop_one_balanced_parenthetical(remaining)
        if inner is None:
            break
        nested_remaining, nested_notes = extract_parenthetical_notes(inner)
        if nested_remaining:
            notes.append(_clean_note_fragment(nested_remaining))
        notes.extend(nested_notes)
    return clean_orphan_parentheses(remaining), notes


def is_quantity_token(token: str) -> bool:
    if token in {"/", "-"}:
        return False
    return bool(QUANTITY_TOKEN_RE.match(token))


def is_unit_token(token: str) -> bool:
    if UNIT_TOKEN_RE.match(token):
        return True
    return len(token) == 1 and token.upper() == "C"


def is_slash_metric_token(token: str) -> bool:
    return bool(SLASH_METRIC_TOKEN_RE.match(token))


def is_quantity_range_separator(token: str) -> bool:
    return token in QUANTITY_RANGE_SEPARATORS or token.casefold() in QUANTITY_RANGE_WORDS


def _join_ingredient_notes(parts: list[str]) -> str:
    cleaned = [_clean_note_fragment(part) for part in parts if part and part.strip()]
    return "; ".join(cleaned)


def _looks_like_ingredient_note(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if COMMA_MODIFIER_SUFFIX_RE.search(f",{candidate}"):
        return True
    if re.match(r"^(?:more\s+to\s+taste|to\s+taste)$", candidate, re.IGNORECASE):
        return True
    if FROM_PHRASE_RE.match(candidate) or JUICE_ZEST_RE.match(candidate):
        return True
    return bool(
        re.match(
            rf"^(?:(?:{PREP_ADV})\s+)?(?:{PREP_VERB})(?:\s+.+)?$",
            candidate,
            re.IGNORECASE,
        ),
    )


def peel_container_prefix(name: str) -> tuple[str, str]:
    """Move leading can/jar/etc. from name onto quantity (e.g. 'cans tuna' -> 'cans', 'tuna')."""
    match = CONTAINER_PREFIX_RE.match(name.strip())
    if not match:
        return "", name
    return match.group("container").strip(), match.group("name").strip()


def _find_or_product_substitution_match(remaining: str) -> re.Match[str] | None:
    """Split long/brand 'or alternate product' suffixes; keep short 'butter or margarine' in name."""
    match = OR_SUBSTITUTION_SUFFIX_RE.search(remaining)
    if not match:
        return None
    alternative = match.group(1)[2:].strip()  # drop leading "or"
    if re.match(r"sub(?:stitute)?\b", alternative, re.IGNORECASE):
        return match
    if (
        len(alternative) <= 24
        and "®" not in alternative
        and "™" not in alternative
        and alternative.count(" ") <= 3
    ):
        return None
    return match


def _attach_container_to_quantity(quantity: str, container: str) -> str:
    if not container:
        return quantity
    return f"{quantity} {container}".strip() if quantity else container


def _extract_comma_separated_notes(remaining: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    while True:
        match = re.match(r"^([^,]+),\s*(.+)$", remaining)
        if not match:
            break
        head = match.group(1).strip()
        tail = match.group(2).strip()
        tail_parts = [part.strip() for part in tail.split(",") if part.strip()]
        if tail_parts and all(_looks_like_ingredient_note(part) for part in tail_parts):
            notes.extend(tail_parts)
            remaining = head
            continue
        break
    return remaining, notes


def split_ingredient_name_notes(name: str) -> tuple[str, str]:
    """Pull preparation and qualifiers out of a bare ingredient name."""
    notes_parts: list[str] = []
    remaining = " ".join(name.split()).strip()
    if not remaining:
        return "", ""

    remaining, paren_notes = extract_parenthetical_notes(remaining)
    notes_parts.extend(paren_notes)

    modifier_match = COMMA_MODIFIER_SUFFIX_RE.search(remaining)
    if modifier_match:
        notes_parts.append(modifier_match.group(1).strip())
        remaining = remaining[: modifier_match.start()].strip()

    substitution_match = _find_or_product_substitution_match(remaining)
    if substitution_match:
        notes_parts.append(substitution_match.group(1).strip())
        remaining = remaining[: substitution_match.start()].strip()

    for pattern in (TRAILING_FOR_RE, TRAILING_FROM_RE, TRAILING_PREP_RE):
        match = pattern.search(remaining)
        if match:
            notes_parts.append(match.group().strip())
            remaining = remaining[: match.start()].strip()

    remaining, comma_notes = _extract_comma_separated_notes(remaining)
    notes_parts.extend(comma_notes)

    leading_match = LEADING_PREP_RE.match(remaining)
    if leading_match:
        notes_parts.append(leading_match.group(1).strip())
        remaining = leading_match.group(2).strip()

    # "lime- juice and zest" or "lime - juice and zest"
    hyphen_match = re.match(r"^(.+?)-\s*(.+)$", remaining)
    if hyphen_match and _looks_like_ingredient_note(hyphen_match.group(2)):
        notes_parts.append(hyphen_match.group(2).strip())
        remaining = hyphen_match.group(1).strip()

    remaining = LEADING_CONNECTOR_RE.sub("", remaining)
    remaining = clean_orphan_parentheses(remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip(" ,-")
    notes = clean_orphan_parentheses(_join_ingredient_notes(notes_parts))
    return remaining[:180], notes[:180]


def parse_ingredient_string(text: str) -> dict[str, str]:
    cleaned = normalize_ingredient_text(text)
    if not cleaned:
        return {"quantity": "", "name": "", "notes": ""}

    tokens = cleaned.split()
    quantity_parts: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if is_quantity_token(token):
            quantity_parts.append(token)
            index += 1
            if (
                index < len(tokens)
                and is_quantity_range_separator(tokens[index])
                and index + 1 < len(tokens)
                and is_quantity_token(tokens[index + 1])
            ):
                quantity_parts.append(tokens[index])
                index += 1
                quantity_parts.append(tokens[index])
                index += 1
            continue
        if DIMENSION_X_RE.match(token) and quantity_parts:
            quantity_parts.append("x")
            index += 1
            continue
        if token == "/" and quantity_parts:
            quantity_parts.append("/")
            index += 1
            if index < len(tokens) and is_quantity_token(tokens[index]):
                quantity_parts.append(tokens[index])
                index += 1
                if index < len(tokens) and is_unit_token(tokens[index]):
                    quantity_parts.append(tokens[index])
                    index += 1
            elif index < len(tokens) and is_slash_metric_token(tokens[index]):
                quantity_parts.append(tokens[index])
                index += 1
            continue
        if is_unit_token(token) and quantity_parts:
            quantity_parts.append(token)
            index += 1
            continue
        break

    if not quantity_parts:
        name, notes = split_ingredient_name_notes(cleaned)
        container, name = peel_container_prefix(name)
        return {
            "quantity": _attach_container_to_quantity("", container)[:80],
            "name": name,
            "notes": notes,
        }

    name = " ".join(tokens[index:]).strip()
    name, notes = split_ingredient_name_notes(name)
    container, name = peel_container_prefix(name)
    quantity = _attach_container_to_quantity(" ".join(quantity_parts), container)
    return {
        "quantity": quantity[:80],
        "name": name,
        "notes": notes,
    }


NUMBERED_STEP_MARKER_RE = re.compile(r"(?:^|\s)\d+\.\s+")


def normalize_instruction_text(text: str) -> str:
    return " ".join(strip_html(text).replace("\u00a0", " ").split())


def split_numbered_instruction_text(text: str) -> list[str]:
    """Split '1. Do X. 2. Do Y.' blobs (common in WP Recipe Maker JSON-LD)."""
    cleaned = normalize_instruction_text(text)
    if not cleaned:
        return []
    if len(NUMBERED_STEP_MARKER_RE.findall(cleaned)) < 2:
        return [cleaned]
    parts = re.split(r"\s+(?=\d+\.\s+)", cleaned)
    steps: list[str] = []
    for part in parts:
        step = re.sub(r"^\d+\.\s*", "", part.strip()).strip()
        if step:
            steps.append(step)
    return steps if len(steps) > 1 else [cleaned]


def parse_instruction_text(value: Any) -> str:
    if isinstance(value, str):
        return normalize_instruction_text(value)
    if isinstance(value, dict):
        for key in ("text", "name", "description", "item"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return normalize_instruction_text(candidate)
    return ""


def instruction_texts_from_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return split_numbered_instruction_text(value)
    if isinstance(value, dict):
        return split_numbered_instruction_text(parse_instruction_text(value))
    return []


def parse_instructions(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return split_numbered_instruction_text(value)
    if isinstance(value, list):
        steps: list[str] = []
        for item in value:
            steps.extend(parse_instructions(item))
        return steps
    if isinstance(value, dict):
        schema_type = value.get("@type")
        if isinstance(schema_type, list):
            types = schema_type
        elif schema_type:
            types = [schema_type]
        else:
            types = []
        normalized_types = {str(item).split("/")[-1] for item in types}
        if "HowToSection" in normalized_types:
            steps: list[str] = []
            for item in value.get("itemListElement") or []:
                steps.extend(parse_instructions(item))
            return steps
        return instruction_texts_from_value(value)
    return []


def parse_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;]", value)
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, list):
        tags: list[str] = []
        for item in value:
            tags.extend(parse_keywords(item))
        return tags
    return []


def normalize_import_tag_label(raw: str) -> str:
    cleaned = " ".join(strip_html(str(raw)).split()).strip()
    if not cleaned:
        return ""
    cleaned = _CATEGORY_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned[:IMPORT_TAG_MAX_LENGTH]


def filter_import_tags(raw_tags: list[str], *, title: str) -> list[str]:
    title_norm = " ".join(title.lower().split())
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_tags:
        tag = normalize_import_tag_label(raw)
        if not tag:
            continue
        if len(tag.split()) > IMPORT_TAG_MAX_WORDS:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        tag_norm = key
        word_count = len(tag.split())
        if title_norm and word_count >= 2 and tag_norm in title_norm:
            continue
        if title_norm and len(tag) > 15 and tag_norm in title_norm:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= IMPORT_TAG_MAX_COUNT:
            break
    return result


def parse_import_tags(recipe: dict[str, Any], *, title: str) -> list[str]:
    """Category and cuisine only; ignore schema.org keywords (usually too specific)."""
    raw: list[str] = []
    for field in ("recipeCategory", "recipeCuisine"):
        raw.extend(parse_keywords(recipe.get(field)))
    return filter_import_tags(raw, title=title)


def iter_json_ld_objects(data: Any):
    if isinstance(data, list):
        for item in data:
            yield from iter_json_ld_objects(item)
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if graph is not None:
            yield from iter_json_ld_objects(graph)


def is_recipe_object(data: dict[str, Any]) -> bool:
    schema_type = data.get("@type")
    if isinstance(schema_type, str):
        types = [schema_type]
    elif isinstance(schema_type, list):
        types = schema_type
    else:
        return False
    for item in types:
        normalized = str(item).split("/")[-1]
        if normalized == "Recipe":
            return True
    return False


def find_recipe_objects(data: Any) -> list[dict[str, Any]]:
    return [
        obj
        for obj in iter_json_ld_objects(data)
        if isinstance(obj, dict) and is_recipe_object(obj)
    ]


def extract_json_ld_recipes(html: str) -> list[dict[str, Any]]:
    parser = JsonLdScriptExtractor()
    parser.feed(html)
    recipes: list[dict[str, Any]] = []
    for script in parser.scripts:
        try:
            payload = json.loads(script)
        except json.JSONDecodeError:
            continue
        recipes.extend(find_recipe_objects(payload))
    return recipes


class HtmlRecipeExtractor(HTMLParser):
    """Extract Pioneer Sugar–style Elementor recipe markup (no JSON-LD)."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.og_image = ""
        self.document_title = ""
        self.meta_texts: list[str] = []
        self.ingredients: list[str] = []
        self.ingredient_lines = 0
        self.steps: list[str] = []
        self.description_candidates: list[str] = []
        self.saw_ingredients_heading = False
        self.saw_instructions_heading = False
        self._phase = "pre"
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self._capture_depth = 0
        self._buffer: list[str] = []
        self._heading_classes: set[str] = set()

    def _attrs_dict(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value for key, value in attrs if key and value is not None}

    def _class_set(self, attrs: list[tuple[str, str | None]]) -> set[str]:
        return {part for part in self._attrs_dict(attrs).get("class", "").split() if part}

    def _start_capture(self, kind: str, tag: str) -> None:
        self._capture = kind
        self._capture_tag = tag
        self._capture_depth = 1
        self._buffer = []

    def _emit_capture(self) -> None:
        kind = self._capture or ""
        text = " ".join("".join(self._buffer).split()).strip()
        self._capture = None
        self._capture_tag = None
        self._capture_depth = 0
        self._buffer = []
        if not text:
            return
        if kind.startswith("heading:"):
            self._handle_heading(kind.split(":", 1)[1], text, self._heading_classes)
        elif kind == "ingredient":
            self.ingredients.append(text)
            self.ingredient_lines += 1
        elif kind == "meta":
            self.meta_texts.append(text)
        elif kind == "step" and self._phase == "instructions":
            self.steps.append(text)
        elif kind == "description" and self._phase in {"pre", "ingredients"} and len(text) >= 40:
            self.description_candidates.append(text)
        elif kind == "document_title" and not self.document_title:
            self.document_title = text

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = self._attrs_dict(attrs)
        classes = self._class_set(attrs)

        if tag == "meta":
            name = (attrs_dict.get("name") or "").casefold()
            prop = (attrs_dict.get("property") or "").casefold()
            content = attrs_dict.get("content") or ""
            if name == "description" and content and not self.meta_description:
                self.meta_description = content.strip()
            if prop == "og:image" and content and not self.og_image:
                self.og_image = content.strip()
            return

        if self._capture:
            if tag == self._capture_tag:
                self._capture_depth += 1
            elif tag == "br":
                self._buffer.append(" ")
            return

        if tag == "title" and not self.document_title:
            self._start_capture("document_title", tag)
            return

        if tag in {"h1", "h2", "h3"}:
            self._heading_classes = classes
            self._start_capture(f"heading:{tag}", tag)
            return

        if tag == "span" and "ingredient-text" in classes and self._phase == "ingredients":
            self._start_capture("ingredient", tag)
            return

        if tag == "span" and "elementor-icon-list-text" in classes and self._phase == "pre":
            self._start_capture("meta", tag)
            return

        if tag in {"p", "li"} and self._phase == "instructions":
            self._start_capture("step", tag)
            return

        # Description often sits after ingredients (e.g. before Instructions).
        if tag == "p" and self._phase in {"pre", "ingredients"}:
            self._start_capture("description", tag)
            return

    def handle_endtag(self, tag: str) -> None:
        if not self._capture or tag != self._capture_tag:
            return
        self._capture_depth -= 1
        if self._capture_depth <= 0:
            self._emit_capture()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._capture and tag == "br":
            self._buffer.append(" ")
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def _handle_heading(self, tag: str, text: str, classes: set[str]) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        if tag == "h1" and not self.title:
            if "elementor-heading-title" in classes or not self.title:
                self.title = cleaned
            return
        if INGREDIENTS_HEADING_RE.match(cleaned):
            self.saw_ingredients_heading = True
            self._phase = "ingredients"
            return
        if INSTRUCTIONS_HEADING_RE.match(cleaned):
            self.saw_instructions_heading = True
            self._phase = "instructions"
            return
        if self._phase == "instructions" and INSTRUCTION_STOP_HEADING_RE.match(cleaned):
            self._phase = "done"
            return
        if (
            self._phase == "ingredients"
            and tag == "h3"
            and "ingredients-section-title" in classes
            and cleaned
        ):
            self.ingredients.append(cleaned)


def _classify_html_meta_texts(
    meta_texts: list[str],
) -> tuple[int | None, int | None, str | None, list[str]]:
    times: list[int] = []
    servings_text: str | None = None
    categories: list[str] = []
    for raw in meta_texts:
        text = " ".join(strip_html(raw).split()).strip()
        if not text:
            continue
        # Footer / address noise
        if any(
            marker in text.casefold()
            for marker in ("headquarters", "phone:", "uptown", "monday through")
        ):
            continue
        duration = parse_human_duration_minutes(text)
        if duration is not None and HUMAN_TIME_TOKEN_RE.search(text.split(",")[0]):
            times.append(duration)
            continue
        if servings_text is None and SERVINGS_META_RE.search(text) and re.search(r"\d", text):
            servings_text = text
            continue
        if len(text) <= IMPORT_TAG_MAX_LENGTH and len(text.split()) <= IMPORT_TAG_MAX_WORDS:
            categories.append(text)
    prep = times[0] if times else None
    cook = times[1] if len(times) > 1 else None
    return prep, cook, servings_text, categories


def extract_html_recipe(html: str) -> dict[str, Any] | None:
    """Build a schema.org-shaped Recipe dict from Elementor-style HTML markup."""
    if "ingredient-text" not in html:
        return None
    parser = HtmlRecipeExtractor()
    parser.feed(html)
    if not parser.saw_ingredients_heading or parser.ingredient_lines < 1:
        return None

    title = parser.title.strip()
    if not title and parser.document_title:
        title = re.sub(r"\s+[-|].*$", "", parser.document_title).strip()
    if not title:
        return None

    description = ""
    if parser.description_candidates:
        description = parser.description_candidates[0]
    elif parser.meta_description:
        description = parser.meta_description.strip()

    prep_minutes, cook_minutes, yield_text, categories = _classify_html_meta_texts(
        parser.meta_texts,
    )

    recipe: dict[str, Any] = {
        "@type": "Recipe",
        "name": title,
        "description": description,
        "recipeIngredient": list(parser.ingredients),
        "recipeInstructions": list(parser.steps),
    }
    if prep_minutes is not None:
        recipe["prepTime"] = minutes_to_iso8601_duration(prep_minutes)
    if cook_minutes is not None:
        recipe["cookTime"] = minutes_to_iso8601_duration(cook_minutes)
    if yield_text:
        recipe["recipeYield"] = yield_text
    if categories:
        recipe["recipeCategory"] = categories
    if parser.og_image:
        recipe["image"] = parser.og_image
    return recipe


def resolve_recipe_object(html: str) -> dict[str, Any]:
    """Prefer JSON-LD Recipe; fall back to HTML heuristics."""
    recipes = extract_json_ld_recipes(html)
    if recipes:
        return recipes[0]
    html_recipe = extract_html_recipe(html)
    if html_recipe:
        return html_recipe
    msg = "No recipe data was found on that page."
    raise RecipeImportError(msg)


def is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_fetch_url(url: str) -> str:
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        msg = "URL must start with http:// or https://."
        raise RecipeImportError(msg)
    if parsed.username or parsed.password:
        msg = "URLs with embedded credentials are not allowed."
        raise RecipeImportError(msg)
    hostname = parsed.hostname
    if not hostname:
        msg = "Enter a complete recipe URL."
        raise RecipeImportError(msg)
    if hostname.endswith("."):
        hostname = hostname[:-1]
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        msg = f"Could not resolve host: {hostname}"
        raise RecipeImportError(msg) from exc
    for info in addr_infos:
        ip_text = info[4][0]
        try:
            address = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if not is_public_ip(address):
            msg = "That URL points to a private or local address."
            raise RecipeImportError(msg)
    return cleaned


def _read_limited_response(response, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            msg = "That download is too large to import."
            raise RecipeImportError(msg)
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url_bytes(
    url: str,
    *,
    accept: str,
    max_bytes: int,
    referer: str | None = None,
    content_type_check: str | None = None,
) -> bytes:
    safe_url = validate_fetch_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }
    if referer:
        headers["Referer"] = referer
    request = Request(safe_url, headers=headers)
    try:
        with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            raw_type = response.headers.get("Content-Type") or ""
            content_type = raw_type.split(";")[0].strip().lower()
            if content_type_check and content_type and content_type_check not in content_type:
                if content_type_check == "image" and not content_type.startswith("image/"):
                    msg = "That URL did not return an image."
                    raise RecipeImportError(msg)
            return _read_limited_response(response, max_bytes=max_bytes)
    except HTTPError as exc:
        msg = f"Could not fetch URL (HTTP {exc.code})."
        raise RecipeImportError(msg) from exc
    except URLError as exc:
        msg = "Could not fetch that URL."
        raise RecipeImportError(msg) from exc


def fetch_url_text(url: str) -> str:
    raw = fetch_url_bytes(
        url,
        accept="text/html,application/xhtml+xml",
        max_bytes=MAX_RESPONSE_BYTES,
        content_type_check="text/html",
    )
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def iter_recipe_image_urls(value: Any):
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.startswith(("http://", "https://")):
            yield cleaned
        return
    if isinstance(value, dict):
        schema_type = value.get("@type")
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        if any(str(item).split("/")[-1] == "ImageObject" for item in types if item):
            for key in ("url", "contentUrl", "thumbnailUrl"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    yield from iter_recipe_image_urls(candidate)
            return
        for key in ("url", "contentUrl", "image", "thumbnailUrl"):
            candidate = value.get(key)
            if candidate is not None:
                yield from iter_recipe_image_urls(candidate)
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_recipe_image_urls(item)


def collect_recipe_image_urls(recipe: dict[str, Any]) -> list[str]:
    raw = recipe.get("image") or recipe.get("photo")
    urls: list[str] = []
    seen: set[str] = set()
    for url in iter_recipe_image_urls(raw):
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


def _image_url_rank(url: str, *, from_recipe_schema: bool = False) -> tuple[int, int, int]:
    match = IMAGE_DIMENSION_IN_URL_RE.search(url)
    area = int(match.group(1)) * int(match.group(2)) if match else 0
    schema_boost = 10_000_000 if from_recipe_schema else 0
    full_size_boost = 1_000_000 if not match else 0
    return (schema_boost + full_size_boost, area, len(url))


def _image_url_dedupe_key(url: str) -> str:
    """Group full-size and -WxH WordPress variants (e.g. dish-500-3.jpg vs dish-500-3-320x180.jpg)."""
    stem = Path(urlparse(url).path).stem
    stem = re.sub(r"-\d+x\d+$", "", stem, flags=re.IGNORECASE)
    return stem.casefold()


def _pick_best_import_image_urls(
    candidates: list[tuple[tuple[int, int, int], str]],
    *,
    max_photos: int,
) -> list[str]:
    best_per_key: dict[str, tuple[tuple[int, int, int], str]] = {}
    for score, url in candidates:
        key = _image_url_dedupe_key(url)
        current = best_per_key.get(key)
        if current is None or score > current[0]:
            best_per_key[key] = (score, url)
    ordered = sorted(best_per_key.values(), key=lambda item: item[0], reverse=True)
    return [url for _, url in ordered[:max_photos]]


def select_recipe_image_urls(urls: list[str], *, max_photos: int = MAX_IMPORT_PHOTOS) -> list[str]:
    candidates = [(_image_url_rank(url, from_recipe_schema=True), url) for url in urls]
    return _pick_best_import_image_urls(candidates, max_photos=max_photos)


def infer_image_stem_prefix_from_urls(urls: list[str]) -> str:
    stems = [_image_url_dedupe_key(url) for url in urls]
    if not stems:
        return ""
    reference = max(stems, key=len)
    parts = reference.split("-")
    while parts and parts[-1].isdigit():
        parts.pop()
    return "-".join(parts)


def collect_article_image_urls(html: str, *, page_url: str) -> list[str]:
    host = (urlparse(page_url).hostname or "").casefold()
    if not host:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for match in ARTICLE_IMAGE_SRC_RE.finditer(html):
        url = match.group(1).strip()
        parsed = urlparse(url)
        if (parsed.hostname or "").casefold() != host:
            continue
        path = (parsed.path or "").casefold()
        if "/wp-content/uploads/" not in path:
            continue
        if ARTICLE_IMAGE_SKIP_PATH_RE.search(path):
            continue
        if not re.search(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", path, re.IGNORECASE):
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


OG_IMAGE_RE = re.compile(
    r"""<meta[^>]+property=["']og:image["'][^>]+content=["'](https?://[^"']+)["']"""
    r"""|<meta[^>]+content=["'](https?://[^"']+)["'][^>]+property=["']og:image["']""",
    re.IGNORECASE,
)


def collect_og_image_urls(html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in OG_IMAGE_RE.finditer(html):
        url = (match.group(1) or match.group(2) or "").strip()
        if not url:
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


def collect_import_image_urls(
    recipe: dict[str, Any],
    html: str,
    *,
    page_url: str,
    max_photos: int = MAX_IMPORT_PHOTOS,
) -> list[str]:
    """JSON-LD / og:image heroes plus distinct in-article photos (not size variants)."""
    schema_urls = collect_recipe_image_urls(recipe)
    og_urls = collect_og_image_urls(html) if html.strip() else []
    candidates = [(_image_url_rank(url, from_recipe_schema=True), url) for url in schema_urls]
    schema_keys = {url.casefold() for url in schema_urls}
    for url in og_urls:
        if url.casefold() in schema_keys:
            continue
        candidates.append((_image_url_rank(url, from_recipe_schema=True), url))

    seed_urls = schema_urls or og_urls
    if html.strip():
        prefix = infer_image_stem_prefix_from_urls(seed_urls)
        for url in collect_article_image_urls(html, page_url=page_url):
            if prefix and prefix not in _image_url_dedupe_key(url):
                continue
            candidates.append((_image_url_rank(url, from_recipe_schema=False), url))

    return _pick_best_import_image_urls(candidates, max_photos=max_photos)


def normalize_import_image_bytes(data: bytes) -> bytes:
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except OSError as exc:
        msg = "That file is not a valid image."
        raise RecipeImportError(msg) from exc
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA", "P"}:
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "P":
            image = image.convert("RGBA")
        alpha = image.split()[-1] if image.mode in {"RGBA", "LA"} else None
        background.paste(image, mask=alpha)
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def media_root() -> Path:
    return Path(settings.MEDIA_ROOT)


def new_staging_token() -> str:
    return secrets.token_hex(16)


def validate_staged_photo_path(storage_path: str) -> Path:
    normalized = storage_path.strip().replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    if ".." in parts or len(parts) < 4:
        msg = "Invalid staged photo path."
        raise RecipeImportError(msg)
    if parts[0] != "url_import" or parts[1] != "staging":
        msg = "Invalid staged photo path."
        raise RecipeImportError(msg)
    root = media_root().resolve()
    full_path = (root / normalized).resolve()
    try:
        full_path.relative_to(root)
    except ValueError as exc:
        msg = "Invalid staged photo path."
        raise RecipeImportError(msg) from exc
    if not full_path.is_file():
        msg = "Staged photo is missing."
        raise RecipeImportError(msg)
    return full_path


def stage_recipe_photos(
    recipe: dict[str, Any],
    *,
    referer: str,
    token: str,
    html: str = "",
) -> list[dict[str, str]]:
    selected_urls = collect_import_image_urls(recipe, html, page_url=referer)
    if not selected_urls:
        return []

    staging_dir = media_root() / STAGED_PHOTO_ROOT / token
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, str]] = []
    for order, image_url in enumerate(selected_urls):
        try:
            raw = fetch_url_bytes(
                image_url,
                accept="image/*",
                max_bytes=MAX_IMAGE_BYTES,
                referer=referer,
                content_type_check="image",
            )
            normalized = normalize_import_image_bytes(raw)
        except RecipeImportError:
            continue
        filename = f"photo-{order}.jpg"
        storage_path = f"{STAGED_PHOTO_ROOT}/{token}/{filename}"
        dest = staging_dir / filename
        dest.write_bytes(normalized)
        staged.append(
            {
                "storage_path": storage_path,
                "preview_url": f"{settings.MEDIA_URL}{storage_path}",
                "caption": "",
                "order": str(order),
            },
        )
    return staged


def remove_staged_photo_tree(storage_path: str) -> None:
    try:
        full_path = validate_staged_photo_path(storage_path)
    except RecipeImportError:
        return
    token_dir = full_path.parent
    if token_dir.is_dir() and token_dir.parent.name == "staging":
        for child in token_dir.iterdir():
            child.unlink(missing_ok=True)
        token_dir.rmdir()


def cleanup_staged_photos(storage_paths: list[str]) -> None:
    tokens: set[Path] = set()
    for storage_path in storage_paths:
        try:
            full_path = validate_staged_photo_path(storage_path)
        except RecipeImportError:
            continue
        tokens.add(full_path.parent)
    for token_dir in tokens:
        if token_dir.is_dir():
            for child in token_dir.iterdir():
                child.unlink(missing_ok=True)
            token_dir.rmdir()


def attach_staged_photo_to_recipe(
    recipe,
    storage_path: str,
    *,
    caption: str = "",
    order: int = 0,
) -> RecipePhoto:
    full_path = validate_staged_photo_path(storage_path)
    with full_path.open("rb") as handle:
        return RecipePhoto.objects.create(
            recipe=recipe,
            image=File(handle, name=full_path.name),
            caption=caption[:180],
            order=order,
        )


def recipe_object_to_document(recipe: dict[str, Any], *, source_url: str) -> dict[str, Any]:
    title = strip_html(str(recipe.get("name") or recipe.get("headline") or "")).strip()
    description = strip_html(str(recipe.get("description") or "")).strip()
    prep_time = parse_iso8601_duration_minutes(recipe.get("prepTime"))
    cook_time = parse_iso8601_duration_minutes(recipe.get("cookTime"))
    total_time = parse_iso8601_duration_minutes(recipe.get("totalTime"))
    if prep_time is None and cook_time is None and total_time is not None:
        cook_time = total_time

    ingredients_raw = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
    if isinstance(ingredients_raw, dict):
        ingredients_raw = [ingredients_raw]
    ingredients = [parse_ingredient_entry(item) for item in ingredients_raw]

    steps = parse_instructions(recipe.get("recipeInstructions"))
    tags = parse_import_tags(recipe, title=title)

    return {
        "title": title,
        "description": description,
        "prep_time_minutes": prep_time,
        "cook_time_minutes": cook_time,
        "servings": parse_servings(recipe.get("recipeYield") or recipe.get("yield")),
        "source_url": source_url,
        "tags": tags,
        "ingredients": ingredients,
        "steps": steps,
        "photos": [],
    }


def parse_recipe_html(html: str, *, source_url: str) -> dict[str, Any]:
    recipe = resolve_recipe_object(html)
    document = recipe_object_to_document(recipe, source_url=source_url)
    return validate_recipe_document(document, path=Path("url-import"))


def fetch_and_parse_recipe_url(
    url: str,
    *,
    stage_photos_token: str | None = None,
) -> dict[str, Any]:
    safe_url = validate_fetch_url(url)
    html = fetch_url_text(safe_url)
    recipe = resolve_recipe_object(html)
    document = recipe_object_to_document(recipe, source_url=safe_url)
    document = validate_recipe_document(document, path=Path("url-import"))
    if stage_photos_token:
        document["staged_photos"] = stage_recipe_photos(
            recipe,
            referer=safe_url,
            token=stage_photos_token,
            html=html,
        )
    return document


def draft_form_initial_from_document(document: dict[str, Any]) -> dict[str, Any]:
    ingredient_initial = []
    for order, entry in enumerate(document["ingredients"]):
        if not isinstance(entry, dict):
            continue
        if not any(str(entry.get(key) or "").strip() for key in ("quantity", "name", "notes")):
            continue
        ingredient_initial.append(
            {
                "quantity": str(entry.get("quantity") or "").strip(),
                "name": str(entry.get("name") or "").strip(),
                "notes": str(entry.get("notes") or "").strip(),
                "order": order,
            },
        )

    step_initial = []
    for order, text in enumerate(document["steps"]):
        cleaned = str(text or "").strip()
        if cleaned:
            step_initial.append({"text": cleaned, "order": order})

    tag_initial = [{"tag_name": tag} for tag in document["tags"] if str(tag).strip()]

    return {
        "recipe": {
            "title": document["title"],
            "description": document["description"],
            "prep_time_minutes": document["prep_time_minutes"],
            "cook_time_minutes": document["cook_time_minutes"],
            "servings": document["servings"],
            "source_url": document["source_url"],
        },
        "ingredients": ingredient_initial,
        "steps": step_initial,
        "tags": tag_initial,
        "staged_photos": list(document.get("staged_photos") or []),
    }
