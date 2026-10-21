# Horizontal art for deck tiles

`horizontal_image_url` (`images.horizontal`) points to a 320×64 WebP with alpha
transparency; `horizontal_image_2x_url` (`images.horizontal_2x`) is the same
frame at 640×128 for dense screens. Use both in `srcset`. Recipe
`8-hires-focus-2x` crops the subject from the full illustration without
distortion, places it on the right and fades the artwork into a transparent
text area on the left. Set the tile's background color to the card's rarity;
the image does not contain a fixed background color.

Every source is a clean illustration: no card frame, text, rarity gem or hero
portrait ring. Rendered cards and crops of them are never used; without clean
art the field is `null`.

| Entity | Sources, in priority order |
| --- | --- |
| Constructed | local wiki full art, HearthstoneJSON original texture (`orig`) of the card and of the original card of a Core/Classic/Legacy reprint, remote wiki full art, official reveal strip for temporary `blizzard:<dbf>` IDs, HearthstoneJSON game tile (low resolution, last resort) |
| Battlegrounds, timewarped | local art, HearthstoneJSON original texture |
| Battlegrounds library | local full art, its source URL, HearthstoneJSON original texture |
| Hero, hero skin, coin | HearthstoneJSON original texture, then wiki full art |
| Pet | end-of-game scene; pets without it have no art |

**Framing.** Uniform white, gray, black or transparent padding is trimmed on
all four edges first: Unity textures of new cards pad portrait art with white
columns, older ones carry gray packing bars. Constructed cards appear in decks,
so their in-game deck tile (HearthstoneJSON `tiles`) is located in the full
art by normalized cross-correlation; the designers' framing, usually the face,
sets the focus when the match is reliable (score ≥ 0.85). Other entity types
use a deterministic detail and skin-tone heuristic that keeps a portrait's
focus in the upper torso. Entries in `panel/data/horizontal_art_focus.json`
override the focus for individual cards. The crop has the art area's aspect
3.5 : 1, spans the full width of portrait art and keeps the subject out of the
text fade.

**Checks.** Each rendition is rejected, and the next source tried, when its
fully visible part is blank, has transparent holes, a white margin or a black
block. The job summary counts focus methods and lists flagged images.

**Rollout and rollback.** Recipe 8 writes to `/uploads/horizontal-art/r8/…`
next to, never over, older files. A row switches to its new URL only after both
renditions exist, so the API never points to a missing file. Rows that fail
keep their previous asset. To roll back, restore `local_image_url`,
`recipe_version`, `source_*`, `status` and `generated_at` from a backup of
`horizontal_art_assets`; the old files are untouched. A scheduled run renders
at most 2,500 assets with three workers (`--max-generate`, `--workers`); a
complete run, without `--entity`, `--id` or `--limit`, also hides remaining
legacy card or crop assets.

Use [horizontal-art-tiles.css](../examples/horizontal-art-tiles.css) for a grid
of three rows and five columns: supply exactly 15 cards. Each tile aligns cost,
name and quantity in fixed columns and uses bold Hearthstone typography.
The official [Hearthstone website](https://hearthstone.blizzard.com/ru-ru/)
uses Benguiat Bold for Russian and Belwe Bold for Latin text. The stylesheet
loads these fonts from its public CDN; local font files can replace the URLs.
On narrow screens the grid scrolls horizontally to retain five columns.

The Next panel offers a **Плитки** catalog mode (`?view=tiles`). It requests
15 records per page, keeping pagination aligned with the three-by-five grid.
Cost and name remain HTML text over `horizontal_image_url`; cost uses the
card's `mana_cost` or `cost`, including zero. Unknown cost displays a dash
without a mana crystal. Legendary cards show a star; the catalog does not
invent deck quantities. Clicking a tile opens the existing record details.
Battlegrounds tiles show tavern tier on the left and purchase gold on the right:
minions cost 3; spells use the latest HearthstoneJSON cost already retained in
`battlegrounds_card_changes`. Missing spell costs display a dash. Constructed
tiles retain mana cost on the left and a legendary star on the right.
The mana crystal PNG comes from [Sunwell](https://github.com/HearthSim/Sunwell/blob/master/assets/cost-mana.png),
and Next bundles it under `/_next/static/media/`.

```html
<div class="deck-grid-viewport">
  <div class="deck-grid" role="list">
    <div class="deck-tile" lang="en" role="listitem" style="--rarity: #503961">
      <img class="deck-tile__art" src="HORIZONTAL_IMAGE_URL"
        srcset="HORIZONTAL_IMAGE_URL 1x, HORIZONTAL_IMAGE_2X_URL 2x" alt="">
      <span class="deck-tile__cost">4</span>
      <span class="deck-tile__name">Ancient Augur</span>
      <span class="deck-tile__quantity">2</span>
    </div>
    <!-- Add the remaining 14 cards. Set lang="ru" for Russian names. -->
  </div>
</div>
```

```css
@import url("horizontal-art-tiles.css");
```

The empty `alt` avoids repeating the visible card name. Omit the image when the
API returns `null`; the tile's color and text still render. Set `--name-scale`
(0.72…1) to shrink long names; they truncate visually only below that size and
retain their full text in the DOM. The quantity sits on a short fade to the
rarity color; an empty quantity column is not reserved.

Use the full art synchronization jobs to populate missing source fields. The
renderer cannot reconstruct illustration details from a framed crop.
