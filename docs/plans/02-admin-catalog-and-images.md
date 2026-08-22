# Slice 2 — Administrator catalog and optional images

**Status:** Ready for implementation  
**Blocked by:** Slice 1

## Outcome

Give the authenticated administrator a complete, searchable catalog-management
workflow with optional, safely normalized images. The slice is demoable by
creating an item without an image, adding one later, replacing/removing it,
editing quantity and visibility, and deleting an unreferenced item.

## Scope

- Add the inventory-item schema: required name, optional plain-text
  description, nonnegative stock quantity, optional generated image filename,
  visibility, and UTC timestamps.
- Add admin inventory list/search and visible/hidden filters.
- Add create, view, edit, visibility toggle, and confirmed delete workflows.
- Show a neutral placeholder when no image exists.
- Accept optional JPEG, PNG, or WebP files up to 10 MB.
- Decode and verify actual content, reject decompression bombs, correct
  orientation, fit within 1600 by 1600 without enlargement, convert to WebP,
  strip metadata, and assign a random filename.
- Make add, replacement, removal, and item deletion atomic with cleanup after
  successful database commits.
- Store normalized images below the persistent data directory and serve them
  through an authorization-neutral, traversal-safe application route.

## Acceptance criteria

- [ ] Admin can create and edit items with required name/quantity and optional
      description/image.
- [ ] Negative, fractional, missing, or malformed quantities are rejected with
      accessible validation messages.
- [ ] Search matches item name and description; visibility filtering is clear.
- [ ] Items without images show the placeholder and can receive an image later.
- [ ] Valid uploads become metadata-free WebP files within the dimension bound.
- [ ] Invalid, oversized, truncated, or dangerous images are rejected without
      changing the prior record or file.
- [ ] Replacement/removal never leaves the database pointing at a missing file
      and cleans obsolete files only after commit.
- [ ] Deleting an unreferenced item removes its normalized image after explicit
      confirmation.
- [ ] Catalog and image actions remain admin-only and CSRF protected.

## Testing approach

- Drive CRUD and search through the admin HTTP interface.
- Generate small real images with orientation and metadata fixtures and inspect
  decoded output rather than internal Pillow calls.
- Inject filesystem and database failures at the atomic boundary and assert the
  prior item/image remains usable.
- Test traversal attempts and unsupported upload content through HTTP.

## Out of scope

Customer browsing, booking-linked deletion rules, categories, multiple images,
and external object storage are not part of this slice.

## Suggested implementation models

- **Anthropic:** Claude Sonnet 5 at medium effort
- **OpenAI:** GPT-5.6 Terra at medium reasoning effort

## Top runtime failure points

1. Pillow cannot decode a phone image or encounters excessive dimensions. The
   form shows a safe validation error and the existing image remains unchanged.
2. The image directory becomes read-only or full. Uploads fail visibly without
   creating a database filename or deleting the prior file.
3. A database commit succeeds but old-file cleanup fails. The new image remains
   visible, the orphan is logged for later cleanup, and no customer-facing 404
   is introduced.

