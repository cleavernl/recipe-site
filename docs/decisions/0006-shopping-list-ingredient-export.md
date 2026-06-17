# Shopping list ingredient export

**Date:** 2026-06-16  
**Status:** Accepted

## Context

Users want to send recipe ingredients to shopping list apps (especially AnyList) from the recipe detail share menu. There is no universal cross-app API for pushing items to third-party shopping lists.

## Decision

1. **Universal export:** Copy or Web Share a plain-text ingredient list (one line per ingredient: quantity, name, notes). Works with paste into AnyList, OurGroceries, Reminders, Messages, etc.
2. **AnyList recipe import:** Emit schema.org `Recipe` JSON-LD on the authenticated recipe detail page so AnyList’s browser extension and mobile “Recipe Import” action can parse the **open page** while the user is logged in.
3. **No third-party credentials:** Do not store AnyList/B Bring logins or call unofficial shopping-list APIs from the server.

## Consequences

- Ingredient export logic lives in `src/recipes/recipe_export.py`; share menu actions are client-side in `site.js`.
- JSON-LD helps AnyList when the user imports from the page they are viewing; sharing only a link may fail because recipe pages require login and AnyList’s server-side fetch cannot use the member session.
- Direct “push to AnyList list” without user action in the AnyList app remains out of scope unless a future decision adds signed public recipe URLs or official AnyList integration.

## Alternatives considered

- **Unofficial AnyList API with per-user passwords:** Rejected (security, maintenance, ToS).
- **Signed public read-only recipe URLs:** Deferred; would enable URL-only AnyList import but changes privacy model.
