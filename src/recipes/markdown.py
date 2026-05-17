from __future__ import annotations

import bleach
import markdown

ALLOWED_TAGS = frozenset(
    {
        "a",
        "blockquote",
        "br",
        "code",
        "em",
        "h3",
        "h4",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "strong",
        "ul",
    }
)
ALLOWED_ATTRIBUTES = {"a": ["href", "rel", "title"]}
ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto"})

_MARKDOWN = markdown.Markdown(
    extensions=["fenced_code", "nl2br", "sane_lists"],
    output_format="html",
)


def _external_link_rel(attrs: dict, _new: bool) -> dict:
    if (None, "href") in attrs:
        attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


def render_recipe_markdown(text: str) -> str:
    """Render a small, safe subset of Markdown for recipe text fields."""
    if not str(text).strip():
        return ""
    html = _MARKDOWN.convert(text)
    _MARKDOWN.reset()
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return bleach.linkify(
        cleaned,
        callbacks=[_external_link_rel],
        parse_email=False,
        skip_tags=["pre", "code"],
    )
