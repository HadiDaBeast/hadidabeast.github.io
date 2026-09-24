-- Version 1: initial schema
-- Ports from SQLite price_history table, adds stores/ingestion_runs tables

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS price_history (
    id BIGSERIAL PRIMARY KEY,
    store_name TEXT NOT NULL,
    product_name TEXT,
    description TEXT,
    price DOUBLE PRECISION,
    unit_price DOUBLE PRECISION,
    base_unit TEXT,
    business TEXT,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    raw_json JSONB,
    UNIQUE (store_name, product_name, price, valid_from, valid_until)
);

CREATE INDEX IF NOT EXISTS idx_ph_store ON price_history(store_name);
CREATE INDEX IF NOT EXISTS idx_ph_product ON price_history(product_name);
CREATE INDEX IF NOT EXISTS idx_ph_valid_until ON price_history(valid_until);
CREATE INDEX IF NOT EXISTS idx_ph_valid_from ON price_history(valid_from);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued', -- queued, running, succeeded, failed
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    offers_stored INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
