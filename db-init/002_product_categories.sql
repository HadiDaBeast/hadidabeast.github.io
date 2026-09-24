CREATE TABLE IF NOT EXISTS product_categories (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL UNIQUE,   --"kyckling"
    keywords TEXT[] NOT NULL,        
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pc_category ON product_categories(category);

ALTER TABLE price_history ADD COLUMN IF NOT EXISTS category TEXT;
CREATE INDEX IF NOT EXISTS idx_ph_category ON price_history(category);

INSERT INTO schema_version (version) VALUES (2) ON CONFLICT DO NOTHING;
