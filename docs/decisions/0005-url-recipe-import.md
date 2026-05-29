# URL recipe import

**Date:** 2026-05-28  
**Status:** Accepted

## Context

Users want to add recipes from external sites by pasting a URL during recipe creation, review the extracted content on the existing create/edit form, and save only after confirming the details.

The project already supports bulk YAML import (`yaml_import.py`) and stores `source_url` on recipes. A prior rule deferred external scraping libraries until import was intentionally designed.

## Decision

Implement server-side URL import on the create-recipe page using **stdlib only**:

1. **Fetch:** `urllib.request` with timeout, response size cap, and SSRF checks (public IPs only, `http`/`https` only).
2. **Parse:** Extract [schema.org](https://schema.org/Recipe) `Recipe` objects from `application/ld+json` script tags.
3. **Normalize:** Map parsed data into the same document shape used by YAML import (`title`, times, servings, ingredients, steps, tags, `source_url`). Tags come from `recipeCategory` and `recipeCuisine` only (not `keywords`, which are often long SEO dish phrases); labels are capped and de-duplicated.
4. **UX:** POST `/recipes/new/import/` stores a session draft and redirects to `/recipes/new/` with the create form pre-filled. Nothing is persisted until the user clicks **Save recipe**.

When JSON-LD includes `image` URLs, the importer downloads up to **two** photos: the schema.org hero image plus one other distinct in-article image when the page HTML includes related uploads (skipping size variants of the same file). Files are staged under `media/url_import/staging/` for preview on the create form and attached on save. Downloads reuse the same SSRF checks, size cap, and Pillow normalization as other media handling.

## Consequences

- Works well on sites that publish JSON-LD Recipe data (many WordPress recipe plugins, AllRecipes-style publishers, etc.).
- Sites without structured recipe markup will show a clear error; a future iteration could add optional HTML heuristics or an approved scraping dependency.
- Import logic lives in `src/recipes/url_import.py` with tests in `src/recipes/test_url_import.py`.
- The create form shows an “Import from URL” panel; edit pages are unchanged.

## Alternatives considered

- **Create draft recipe in DB immediately:** Rejected; user asked to review before creating.
- **`recipe-scrapers` dependency:** Deferred; stdlib JSON-LD covers many sites without expanding attack surface or maintenance burden yet.
- **Client-side fetch:** Rejected; CORS blocks most recipe sites and would expose users to untrusted page content in the browser.
