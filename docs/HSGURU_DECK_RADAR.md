# HSGuru Deck Radar

## Objective

`Deck Radar` preserves observations from the existing rolling HSGuru streamer
deck dataset and exposes exact, newly observed deckstrings through the public
API. It is a discovery signal, not a claim that a deck is already competitive.

## Beta behaviour

The scheduled streamer-deck refresh reconciles its successfully published
snapshot into SQLite. The first usable snapshot is a baseline: it stores deck
observations but creates no events. A deckstring first seen in a later,
different snapshot becomes a `candidate`. It is promoted to `confirmed` only
after it appears in a second distinct snapshot.

Before an event may be created, beta also compares the row with current HSGuru
builds embedded in `hsguru_meta_matrix`. It suppresses exact matches, matching
archetype titles, and card-multiset variants with Jaccard similarity of at
least 0.75. If that catalog is unavailable, beta stores the observation but
does not publish a candidate. This deliberately favours false negatives over
calling an established deck “new”.

The detector key is a SHA-256 fingerprint of the trimmed deckstring. A
snapshot is unique by its published dataset timestamp, so retrying the same
published snapshot is idempotent.

The public contract is:

```text
GET /v1/constructed/deck-radar?status=candidate|confirmed&limit=50&offset=0
```

It returns the normal v1 envelope with `meta.beta=true`. Each event includes the opaque `fingerprint`,
deckstring, last observed deck/streamer metadata, first/last seen timestamps,
the number of distinct source snapshots, and its `candidate` or `confirmed`
status. An empty result is successful and means that no later snapshot has
produced a qualifying event yet.

## Boundaries

- The input is only the already validated and published
  `hsguru_streamer_decks_legend_1000` snapshot. Radar never makes an extra
  HSGuru request.
- The known-deck guard uses only the published `hsguru_meta_matrix`; it does
  not invoke the catalog parser or make an extra provider request.
- Invalid or missing deckstrings are ignored; they are already rejected by the
  source publication gate.
- The detector does not infer that an event contains a *newly released card*
  in this first delivery. That requires a versioned card-set catalogue and a
  separate rule for the current release. The stored observation history is the
  prerequisite for that next slice.
- Events are retained in SQLite. No notification, public UI, score, or auto
  promotion is part of this delivery.

## Verification

Tests prove initial baseline suppression, idempotent repeated reconciliation,
candidate creation, confirmation only on a second snapshot, and the read API
filter/pagination contract. No test makes a network request.
