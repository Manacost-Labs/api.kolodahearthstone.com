#!/usr/bin/env python3
"""Print a migration plan by default; --apply installs triggers and baselines.

Use an application connection with TRIGGER/CREATE privileges supplied through the
existing runtime config. Never runs premium requests or prints credentials.
"""
import argparse
import json
from pathlib import Path

from card_history_schema import DDL, TABLES, identifier, statements
from sync_constructed_cards import connect_db, load_php_config


def install(connection, apply=False, *, upgrade_triggers=False, backup_path=None):
    plan = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK('catalog_card_history_install', 30) AS acquired")
        if not cursor.fetchone()["acquired"]:
            raise RuntimeError("Another history migration is running")
        try:
            if apply and upgrade_triggers:
                if backup_path is None:
                    raise ValueError("Trigger upgrade requires an exclusive backup path")
                cursor.execute("SELECT VERSION() AS version")
                if "mariadb" not in cursor.fetchone()["version"].lower():
                    raise RuntimeError("Atomic trigger upgrades require MariaDB CREATE OR REPLACE TRIGGER")
                cursor.execute("SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
                               "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME LIKE 'cv1_%'")
                with Path(backup_path).open("x", encoding="utf-8") as backup:
                    json.dump(cursor.fetchall(), backup, ensure_ascii=False)
            if apply:
                cursor.execute(DDL)
            for table, entity in TABLES.items():
                cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                               "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,))
                columns = [row["COLUMN_NAME"] for row in cursor.fetchall()]
                if not columns:
                    plan.append({"entity": entity, "status": "table_absent"})
                    continue
                triggers, baseline = statements(table, columns)
                cursor.execute("SELECT COUNT(*) AS count FROM " + identifier(table))
                plan.append({"entity": entity, "records": cursor.fetchone()["count"],
                             "triggers": 3, "status": "install" if apply else "planned"})
                if not apply:
                    continue
                for sql in triggers:
                    name = sql.split("`", 2)[1]
                    cursor.execute("SELECT ACTION_STATEMENT FROM information_schema.TRIGGERS "
                                   "WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=%s", (name,))
                    installed = cursor.fetchone()
                    if installed:
                        # Do not silently keep triggers compiled against an old
                        # schema; changing them requires an explicit migration.
                        expected = sql[sql.index("BEGIN "):]
                        canonical = lambda body: " ".join(body.strip().split()).lower()
                        if canonical(installed["ACTION_STATEMENT"]) != canonical(expected):
                            if not upgrade_triggers:
                                raise RuntimeError("History trigger needs a reviewed schema migration: " + name)
                            cursor.execute(sql.replace("CREATE TRIGGER", "CREATE OR REPLACE TRIGGER", 1))
                    else:
                        cursor.execute(sql)
                # The read lock avoids racing a writer between the existence
                # check and insertion. Each table releases its lock immediately.
                cursor.execute("LOCK TABLES " + identifier(table) + " READ, catalog_card_versions WRITE, "
                               "catalog_card_versions AS v WRITE")
                try:
                    cursor.execute(baseline)
                    connection.commit()
                finally:
                    cursor.execute("UNLOCK TABLES")
        finally:
            cursor.execute("SELECT RELEASE_LOCK('catalog_card_history_install')")
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--upgrade-triggers", action="store_true", help="Atomically replace reviewed trigger definitions; preserves all stored versions")
    parser.add_argument("--trigger-backup", type=Path, help="New file for the previous trigger definitions; never overwritten")
    parser.add_argument("--catalog-schema", action="store_true", help="Prepare the separate bartender catalog before history installation")
    args = parser.parse_args()
    connection = connect_db(load_php_config())
    try:
        if args.catalog_schema and args.apply:
            from reconcile_card_catalog import BARTENDER_DDL
            with connection.cursor() as cursor:
                cursor.execute(BARTENDER_DDL)
        print(json.dumps({"applied": args.apply, "plan": install(
            connection, args.apply, upgrade_triggers=args.upgrade_triggers,
            backup_path=args.trigger_backup,
        )}, ensure_ascii=False))
    finally:
        connection.close()
