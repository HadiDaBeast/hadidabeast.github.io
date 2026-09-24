-- Seed: initial product categories with keyword lists
-- Keywords are lowercase substrings matched against lowercased product_name.
-- To add more categories or keywords, INSERT or UPDATE rows in this table
-- directly (e.g. via pgAdmin) — no code changes needed.

INSERT INTO product_categories (category, keywords) VALUES
    ('kyckling',     ARRAY['kyckling', 'kycklingbröst', 'kycklinglår', 'kycklingfilé', 'kycklingdelar', 'kycklingfärs', 'kycklingspett', 'kycklinggrillkorv', 'majskyckling']),
    ('nötfärs',      ARRAY['nötfärs', 'högrevsfärs', 'högrevsburgare', 'beef burger', 'hamburgare']),
    ('ost',          ARRAY['ost', 'mozzarella', 'brie', 'burrata', 'fetaost', 'feta', 'halloumi', 'grilloumi', 'camembert', 'cheddar', 'gouda', 'grana padano', 'grevé', 'herrgård', 'präst', 'västerbotten', 'hushållsost', 'färskost', 'mjukost', 'salladsost', 'riven ost', 'stracciatella', 'brännvinsost', 'ädelost', 'blåmögelost', 'kvibille', 'quattrocento', 'appenzeller', 'comté', 'danbo']),
    ('tomater',      ARRAY['tomat', 'tomater', 'tomatpuré', 'passerade tomater', 'krossade tomater', 'plommontomater', 'cocktailtomater', 'småtomater', 'soltorkade tomater', 'snacktomater']),
    ('bröd',         ARRAY['bröd', 'limpa', 'fralla', 'baguette', 'knäckebröd', 'tortilla', 'pitabröd', 'hamburgerbröd', 'korvbröd', 'levain', 'surdeg', 'majskaka', 'vetekaka', 'rågkaka', 'skogaholm', 'pågenlimpan', 'kavring', 'rödbetsbröd']),
    ('kaffe',        ARRAY['kaffe', 'bryggkaffe', 'snabbkaffe', 'kaffekapslar', 'kaffebönor', 'espresso', 'gevalia', 'nescafe', 'gimoka']),
    ('smör',         ARRAY['smör', 'bregott', 'margarin', 'smör & raps', 'smör- & raps', 'rapsolja', 'gårdsgoda']),
    ('pasta',        ARRAY['pasta', 'fusilli', 'penne', 'tagliatelle', 'makaroner', 'spaghetti', 'lasagne', 'nudlar', 'färsk fylld pasta']),
    ('ris',          ARRAY['ris', 'basmati', 'jasminris']),
    ('ägg',          ARRAY['ägg']),
    ('korv',         ARRAY['korv', 'falukorv', 'grillkorv', 'varmkorv', 'kryddkorv', 'kycklinggrillkorv', 'kebab', 'gyros', 'fuet', 'salami']),
    ('chips_snacks', ARRAY['chips', 'ostbågar', 'cheez doodles', 'popcorn', 'salta kex', 'ostsnacks', 'cheez dippers', 'snacks', 'kex']),
    ('glass',        ARRAY['glass']),
    ('pizza',        ARRAY['pizza', 'pizzabotten', 'pizzadeg', 'pinsa']),
    ('bananer',      ARRAY['banan', 'bananer']),
    ('mjölk',        ARRAY['mjölk', 'laktosfri mjölk', 'milbona mjölk']),
    ('grädde',       ARRAY['grädde', 'vispgrädde', 'matgrädde', 'crème fraiche', 'gräddfil']),
    ('fläsk',        ARRAY['fläsk', 'fläskfärs', 'fläskytterfilé', 'bacon', 'ribs']),
    ('lax',          ARRAY['lax', 'pinklax', 'laxfilé']),
    ('räkor',        ARRAY['räkor'])
ON CONFLICT (category) DO UPDATE
    SET keywords = EXCLUDED.keywords,
        updated_at = now();
