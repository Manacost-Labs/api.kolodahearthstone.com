"""Append-only observed catalog versions, shared by every database writer.

Times are observation times, never guessed patch release dates. An asset URL in a
snapshot does not archive the image bytes. This migration does not reconstruct
changes that happened before it was installed.
"""
from __future__ import annotations

import re

TABLES = {
    "constructed_cards": "constructed_card",
    "battlegrounds_cards": "card",
    "battlegrounds_heroes": "hero",
    "hero_skins": "hero_skin",
    "battlegrounds_library_cards": "library_card",
    "battlegrounds_timewarped_cards": "timewarped_card",
    "hearthstone_pets": "pet",
    "hearthstone_coins": "coin",
    "battlegrounds_bartender_skins": "bartender_skin",
}
# Changes in fetch bookkeeping are not new game versions. Keep raw provenance in
# the snapshot, but compare the normalized record rather than provider counters.
BOOKKEEPING = {
    "id", "created_at", "updated_at", "first_seen_at", "last_seen_at",
    "changed_at", "fetched_at", "wiki_fetched_at",
    "source_payload_json", "last_checked_at", "last_error", "error", "import_run_id",
}

DDL = """CREATE TABLE IF NOT EXISTS catalog_card_versions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    entity VARCHAR(40) NOT NULL,
    card_id VARCHAR(191) NOT NULL,
    observed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    event_kind VARCHAR(24) NOT NULL,
    previous_hash CHAR(64) NULL,
    snapshot_hash CHAR(64) NOT NULL,
    snapshot_json LONGTEXT NOT NULL CHECK (JSON_VALID(snapshot_json)),
    INDEX history_lookup (entity, card_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"""


def identifier(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ValueError("Invalid schema identifier")
    return "`" + value + "`"


def json_object(columns, prefix=""):
    return "JSON_OBJECT(" + ", ".join(
        "'%s', %s%s" % (key, prefix, identifier(key)) for key in columns
    ) + ")"


def statements(table, columns):
    entity = TABLES[table]
    for key in columns:
        identifier(key)
    content = [key for key in columns if key not in BOOKKEEPING]
    if "card_id" not in content:
        raise ValueError("Catalog must have a card_id")
    snapshots = [key for key in columns if key not in {"id", "error", "last_error"}]
    library = table == "battlegrounds_library_cards"

    def key(prefix):
        return ("CONCAT(%s`library`, ':', %s`card_id`)" % (prefix, prefix)
                if library else prefix + "`card_id`")

    def digest(prefix):
        return "SHA2(CAST(%s AS CHAR), 256)" % json_object(content, prefix)

    def insert(prefix, kind, previous="NULL", guard="", predecessor=None):
        snapshot = json_object(snapshots, prefix)
        if predecessor:
            snapshot = "JSON_MERGE_PATCH(%s, JSON_OBJECT('_previous_card_id', %s))" % (snapshot, predecessor)
        return ("INSERT INTO catalog_card_versions "
                "(entity, card_id, observed_at, event_kind, previous_hash, snapshot_hash, snapshot_json) "
                "SELECT '%s', %s, UTC_TIMESTAMP(6), '%s', %s, %s, %s%s;" %
                (entity, key(prefix), kind, previous, digest(prefix),
                 snapshot, guard))

    after_insert = ("CREATE TRIGGER %s AFTER INSERT ON %s FOR EACH ROW BEGIN %s END" %
                    (identifier("cv1_" + table + "_insert"), identifier(table), insert("NEW.", "new")))
    # Capture the old record before its first change even when a bootstrap hasn't
    # finished. A gap remains explicitly labelled if history wasn't continuous.
    old_guard = (" WHERE NOT EXISTS (SELECT 1 FROM catalog_card_versions "
                 "WHERE entity = '%s' AND card_id = %s COLLATE utf8mb4_bin AND id = "
                 "(SELECT MAX(v.id) FROM catalog_card_versions v WHERE v.entity = '%s' "
                 "AND v.card_id = %s COLLATE utf8mb4_bin) AND snapshot_hash = %s)" %
                 (entity, key("OLD."), entity, key("OLD."), digest("OLD.")))
    before_update = ("CREATE TRIGGER %s BEFORE UPDATE ON %s FOR EACH ROW BEGIN "
                     "IF NOT (%s <=> %s) THEN %s IF NOT (%s <=> %s) THEN %s %s ELSE %s END IF; END IF; END" %
                     (identifier("cv1_" + table + "_update"), identifier(table),
                      digest("OLD."), digest("NEW."), insert("OLD.", "baseline", guard=old_guard),
                      key("OLD."), key("NEW."), insert("OLD.", "removed", previous=digest("OLD.")),
                      insert("NEW.", "renamed", previous=digest("OLD."), predecessor=key("OLD.")),
                      insert("NEW.", "changed", previous=digest("OLD."))))
    before_delete = ("CREATE TRIGGER %s BEFORE DELETE ON %s FOR EACH ROW BEGIN %s %s END" %
                     (identifier("cv1_" + table + "_delete"), identifier(table),
                      insert("OLD.", "baseline", guard=old_guard),
                      insert("OLD.", "removed", previous=digest("OLD."))))
    baseline = (insert("", "baseline")[:-1] + " FROM " + identifier(table)
                + " WHERE NOT EXISTS (SELECT 1 FROM catalog_card_versions v "
                "WHERE v.entity = '%s' AND v.card_id = %s COLLATE utf8mb4_bin);" % (entity, key(identifier(table) + ".")))
    return [after_insert, before_update, before_delete], baseline
