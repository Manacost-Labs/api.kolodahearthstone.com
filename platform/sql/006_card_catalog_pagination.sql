BEGIN;

CREATE INDEX IF NOT EXISTS battlegrounds_cards_catalog_cursor_idx
    ON catalog.battlegrounds_cards (
        (COALESCE(name, name_en, '')),
        card_id
    )
    WHERE variant_kind = 'base';

CREATE INDEX IF NOT EXISTS constructed_cards_catalog_cursor_idx
    ON catalog.constructed_cards (
        (COALESCE(name_ru, name_en, '')),
        card_id
    );

INSERT INTO platform.schema_migrations (version)
VALUES ('006_card_catalog_pagination')
ON CONFLICT (version) DO NOTHING;

COMMIT;
