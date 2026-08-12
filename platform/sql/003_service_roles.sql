BEGIN;

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hs_data_reader') THEN
        CREATE ROLE hs_data_reader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hs_data_writer') THEN
        CREATE ROLE hs_data_writer NOLOGIN;
    END IF;
END
$roles$;

GRANT CONNECT ON DATABASE hs_data TO hs_data_reader, hs_data_writer;
GRANT USAGE ON SCHEMA catalog, analytics, raw, hub, platform
    TO hs_data_reader, hs_data_writer;

GRANT SELECT ON ALL TABLES IN SCHEMA catalog, analytics, raw, hub, platform
    TO hs_data_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA catalog, analytics, raw, platform
    TO hs_data_writer;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA catalog, analytics, raw, platform
    TO hs_data_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA catalog, analytics, raw, hub, platform
    GRANT SELECT ON TABLES TO hs_data_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog, analytics, raw, hub, platform
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO hs_data_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog, analytics, raw, platform
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO hs_data_writer;

INSERT INTO platform.schema_migrations (version)
VALUES ('003_service_roles')
ON CONFLICT (version) DO NOTHING;

COMMIT;
