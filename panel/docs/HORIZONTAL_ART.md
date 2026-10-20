# Horizontal art for deck tiles

`horizontal_image_url` points to a 320×64 WebP with alpha transparency.
Recipe `7-game-cuts-trimmed-art` preserves the subject on the right and fades
the artwork into a transparent text area on the left. Set the tile's background
color to the card's rarity; the image does not contain a fixed background color.

Card sources are pure artwork only:

| Card collection | Source fields, in priority order |
| --- | --- |
| Battlegrounds | `art_image` |
| Constructed | HearthstoneJSON game tile, `local_wiki_full_art_url`, `wiki_full_art_url`, HearthstoneJSON original art by card ID |
| Battlegrounds library | `local_full_art_url`, `full_art_source_url` |
| Timewarped | `art_image_url` |

New revealed cards can have temporary IDs such as `blizzard:128090` before
HearthstoneJSON publishes their game CardID. For these records, the generator
uses the official illustration strip (`cropImage`) retained in the Blizzard
or reveal payload, restricted to the publisher's Hearthstone asset CDN.
This strip contains artwork, not the rendered card. Generic `crop_image_url`
and local framed crops remain excluded. Core, Classic and Legacy reprints try
their own game tile, then the corresponding tile without the prefix, before
falling back to full artwork. Original-art fallbacks also try both IDs.

The [game tiles](https://hearthstonejson.com/docs/images.html#card-tiles) use
the crop coordinates embedded in Hearthstone's Unity assets. The generator
preserves the entire illustration strip instead of cropping it a second time.
Continuous near-white padding on all four edges is removed before positioning
or selecting a crop; bright objects that occupy only part of an edge remain.

The generator excludes rendered card images and crops derived from them for
these collections. When no full art exists, it leaves the tile without an image
instead of using a framed card. Other entity types retain their existing source
selection.

Keep the image's 5:1 aspect ratio. For example, a 64px row displays the art at
320×64px, anchored to the right beside the quantity. Stretching it across a wide
row distorts characters; `cover` can crop their faces vertically.

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
      <img class="deck-tile__art" src="HORIZONTAL_IMAGE_URL" alt="">
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
API returns `null`; the tile's color and text still render. Long names truncate
visually while retaining their full text in the DOM.

After deploying the code, the existing horizontal-art synchronization job
regenerates assets with an older recipe. Its version check skips current assets.
The public field, file paths and dimensions remain the same; `generated_at`
updates the existing URL cache version. This source change alone does not modify
production files or database rows.

After generating replacements, a real synchronization marks remaining `ready`
card assets with `source_kind` other than `art` as `unavailable`. This covers
missing full art and failed replacements; API readers already exclude these
statuses, so `horizontal_image_url` becomes `null`. Pure art assets and other
entity types keep their existing status. The original media files are preserved.
The summary reports `framed_assets_disabled`; a dry run does not disable assets.

Use the full art synchronization jobs to populate missing source fields. The
renderer cannot reconstruct illustration details from a framed crop.
