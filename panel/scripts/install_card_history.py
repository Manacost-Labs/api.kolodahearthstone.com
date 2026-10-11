#!/usr/bin/env python3
"""Print a migration plan by default; --apply installs triggers and baselines.

Use an application connection with TRIGGER/CREATE privileges supplied through the
existing runtime config. Never runs premium requests or prints credentials.
"""
import argparse
import json

from card_history_schema import DDL, TABLES, identifier, statements
from sync_constructed_cards import connect_db, load_php_config


def install(connection, apply=False):
    plan = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK('catalog_card_history_install', 30) AS acquired")
        if not cursor.fetchone()["acquired"]:
            raise RuntimeError("Another history migration is running")
        try:
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
                            raise RuntimeError("History trigger needs a reviewed schema migration: " + name)
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
    parser.add_argument("--catalog-schema", action="store_true", help="Prepare bartender catalog before trigger installation")
    args = parser.parse_args()
    connection = connect_db(load_php_config())
    try:
        if args.apply and args.catalog_schema:
            from reconcile_card_catalog import BARTENDER_DDL
            with connection.cursor() as cursor:
                cursor.execute(BARTENDER_DDL)
        print(json.dumps({"applied": args.apply, "plan": install(connection, args.apply)}, ensure_ascii=False))
    finally:
        connection.close()
