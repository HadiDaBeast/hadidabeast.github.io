let allOffers = [];
let currentOffers = [];
let activeCategory = '';  // '' = all

// Swedish display names for categories
const CATEGORY_LABELS = {
    'kyckling':     'Kyckling',
    'ost':          'Ost',
    'tomater':      'Tomater',
    'bröd':         'Bröd',
    'kaffe':        'Kaffe',
    'smör':         'Smör',
    'pasta':        'Pasta',
    'ris':          'Ris',
    'ägg':          'Ägg',
    'korv':         'Korv',
    'chips_snacks': 'Chips & Snacks',
    'glass':        'Glass',
    'pizza':        'Pizza',
    'nötfärs':      'Nötfärs',
    'bananer':      'Bananer',
    'mjölk':        'Mjölk',
    'grädde':       'Grädde',
    'fläsk':        'Fläsk',
    'lax':          'Lax',
    'räkor':        'Räkor',
};

const STORE_COLORS = [
    'bg-violet-100 text-violet-700',
    'bg-blue-100 text-blue-700',
    'bg-amber-100 text-amber-700',
    'bg-rose-100 text-rose-700',
    'bg-cyan-100 text-cyan-700',
    'bg-orange-100 text-orange-700',
    'bg-teal-100 text-teal-700',
    'bg-pink-100 text-pink-700',
];
const storeColorMap = {};
let colorIndex = 0;

function getStoreColor(store) {
    if (!storeColorMap[store]) {
        storeColorMap[store] = STORE_COLORS[colorIndex % STORE_COLORS.length];
        colorIndex++;
    }
    return storeColorMap[store];
}

const els = {
    search: document.getElementById('search'),
    store: document.getElementById('store-filter'),
    sort: document.getElementById('sort'),
    offers: document.getElementById('offers'),
    stats: document.getElementById('stats'),
    count: document.getElementById('result-count'),
    title: document.getElementById('result-title'),
    updated: document.getElementById('last-updated'),
    pills: document.getElementById('category-pills'),
};

async function loadCategories() {
    try {
        const res = await fetch('/api/categories', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        const cats = Array.isArray(data) ? data.map(c => c.category) : [];

        cats.forEach(cat => {
            const btn = document.createElement('button');
            btn.dataset.category = cat;
            btn.className = 'category-pill flex-shrink-0 px-4 py-1.5 rounded-full text-sm font-medium transition-colors duration-150 bg-white border border-gray-200 text-gray-600 hover:border-brand-500 hover:text-brand-600';
            btn.textContent = CATEGORY_LABELS[cat] || cat;
            els.pills.appendChild(btn);
        });

        els.pills.addEventListener('click', e => {
            const btn = e.target.closest('.category-pill');
            if (!btn) return;
            selectCategory(btn.dataset.category);
        });
    } catch (_) { /* categories are optional — fail silently */ }
}

async function selectCategory(category) {
    activeCategory = category;

    // Update pill styles
    els.pills.querySelectorAll('.category-pill').forEach(btn => {
        const active = btn.dataset.category === category;
        btn.className = active
            ? 'category-pill flex-shrink-0 px-4 py-1.5 rounded-full text-sm font-medium transition-colors duration-150 bg-brand-600 text-white'
            : 'category-pill flex-shrink-0 px-4 py-1.5 rounded-full text-sm font-medium transition-colors duration-150 bg-white border border-gray-200 text-gray-600 hover:border-brand-500 hover:text-brand-600';
    });

    // Reset store filter and search when switching categories
    els.search.value = '';
    els.store.value = '';

    if (!category) {
        // "Alla" — use already-loaded allOffers
        currentOffers = getCurrentOffers(allOffers);
        populateStores(currentOffers);
        renderStats(currentOffers);
        renderOffers();
        return;
    }

    // Fetch category-specific offers
    els.offers.innerHTML = `
        <div class="col-span-full flex flex-col items-center justify-center py-24 gap-4 text-gray-400">
            <div class="spinner"></div>
            <p class="text-sm">Laddar ${escapeHtml(CATEGORY_LABELS[category] || category)}...</p>
        </div>`;

    try {
        const res = await fetch(`/api/categories/${encodeURIComponent(category)}`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Remap to the same shape as /products items
        currentOffers = (data.items || []).map(item => ({
            store: item.store,
            product: item.product,
            price: item.price,
            unit_price: item.unit_price,
            base_unit: item.base_unit,
            valid_from: item.valid_from,
            valid_until: item.valid_until,
            category: category,
        }));

        populateStores(currentOffers);
        renderStats(currentOffers);
        els.title.textContent = CATEGORY_LABELS[category] || category;
        renderOffers();
    } catch (err) {
        els.offers.innerHTML = `
            <div class="col-span-full flex flex-col items-center justify-center py-24 text-gray-400">
                <h3 class="text-base font-semibold text-gray-500 mb-1">Kunde inte ladda kategorin</h3>
                <p class="text-sm">${escapeHtml(err.message)}</p>
            </div>`;
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function money(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    return Number(value).toLocaleString('sv-SE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' kr';
}

function dateOnly(value) {
    if (!value) return null;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value.slice(0, 10);
    return d.toLocaleDateString('sv-SE', { day: 'numeric', month: 'short' });
}

function populateStores(offers) {
    const stores = [...new Set(offers.map(o => o.store).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'sv'));
    // Pre-assign colors
    stores.forEach(s => getStoreColor(s));
    els.store.innerHTML =
        '<option value="">Alla butiker</option>' +
        stores.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
}

function getCurrentOffers(offers) {
    const now = new Date();
    return offers.filter(offer => {
        if (!offer.valid_from && !offer.valid_until) return true;
        const from = offer.valid_from ? new Date(offer.valid_from) : null;
        const until = offer.valid_until ? new Date(offer.valid_until) : null;
        if (from && !Number.isNaN(from.getTime()) && from > now) return false;
        if (until && !Number.isNaN(until.getTime()) && until < now) return false;
        return true;
    });
}

function renderStats(offers) {
    const stores = new Set(offers.map(o => o.store)).size;
    const products = new Set(offers.map(o => o.product)).size;
    const prices = offers.map(o => Number(o.price)).filter(Number.isFinite);
    const lowest = prices.length ? Math.min(...prices) : null;

    const stat = (label, value, sub) => `
        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
            <p class="text-sm text-gray-500 mb-1">${label}</p>
            <p class="text-2xl font-bold text-gray-900">${value}</p>
            ${sub ? `<p class="text-xs text-gray-400 mt-1">${sub}</p>` : ''}
        </div>`;

    els.stats.innerHTML =
        stat('Produkter', products, 'unika varor') +
        stat('Butiker', stores, 'i Karlskrona') +
        stat('Lägsta pris', lowest !== null ? money(lowest) : '—', 'denna vecka');
}

function renderOffers() {
    const query = els.search.value.trim().toLocaleLowerCase('sv');
    const store = els.store.value;
    const sort = els.sort.value;

    let offers = currentOffers.filter(offer => {
        const matchSearch = !query ||
            String(offer.product || '').toLocaleLowerCase('sv').includes(query) ||
            String(offer.store || '').toLocaleLowerCase('sv').includes(query);
        const matchStore = !store || offer.store === store;
        return matchSearch && matchStore;
    });

    offers.sort((a, b) => {
        if (sort === 'price') return (Number(a.price) || Infinity) - (Number(b.price) || Infinity);
        if (sort === 'unit') return (Number(a.unit_price) || Infinity) - (Number(b.unit_price) || Infinity);
        if (sort === 'store') return String(a.store).localeCompare(String(b.store), 'sv');
        return String(a.product).localeCompare(String(b.product), 'sv');
    });

    els.count.textContent = `${offers.length} erbjudanden`;
    els.title.textContent = (query || store) ? 'Filtrerade erbjudanden' : 'Alla aktuella erbjudanden';

    if (!offers.length) {
        els.offers.innerHTML = `
            <div class="col-span-full flex flex-col items-center justify-center py-24 text-gray-400">
                <svg class="w-12 h-12 mb-4 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                </svg>
                <h3 class="text-base font-semibold text-gray-500 mb-1">Inga erbjudanden hittades</h3>
                <p class="text-sm">Prova ett annat sökord eller välj en annan butik.</p>
            </div>`;
        return;
    }

    els.offers.innerHTML = offers.map((offer, i) => {
        const colorClass = getStoreColor(offer.store);
        const validUntil = dateOnly(offer.valid_until);
        const hasUnitPrice = offer.unit_price !== null && offer.unit_price !== undefined;
        const catLabel = offer.category ? (CATEGORY_LABELS[offer.category] || offer.category) : null;

        return `
        <article class="card-enter bg-white rounded-2xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow duration-200 overflow-hidden flex flex-col"
                 style="animation-delay: ${Math.min(i * 20, 300)}ms">

            <!-- Card top: store + date -->
            <div class="flex items-center justify-between px-4 pt-4 pb-3">
                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${colorClass}">
                    ${escapeHtml(offer.store)}
                </span>
                ${validUntil
                    ? `<span class="text-xs text-gray-400">t.o.m. ${escapeHtml(validUntil)}</span>`
                    : ''}
            </div>

            <!-- Product name -->
            <div class="px-4 pb-3 flex-1">
                <h3 class="text-sm font-semibold text-gray-900 leading-snug line-clamp-2">
                    ${escapeHtml(offer.product)}
                </h3>
                ${catLabel && activeCategory === ''
                    ? `<span class="inline-block mt-1.5 px-2 py-0.5 rounded-full text-xs bg-brand-50 text-brand-700 font-medium">${escapeHtml(catLabel)}</span>`
                    : ''}
            </div>

            <!-- Price area -->
            <div class="bg-gray-50 border-t border-gray-100 px-4 py-3 mt-auto">
                <div class="flex items-end justify-between">
                    <div>
                        <span class="text-2xl font-bold text-gray-900">${money(offer.price)}</span>
                    </div>
                    ${hasUnitPrice
                        ? `<span class="text-xs text-gray-400 mb-1">${money(offer.unit_price)} / ${escapeHtml(offer.base_unit || '')}</span>`
                        : ''}
                </div>
            </div>
        </article>`;
    }).join('');
}

async function loadData() {
    try {
        const res = await fetch('/api/products?page_size=200', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const data = await res.json();
        allOffers = Array.isArray(data.items) ? data.items : [];
        currentOffers = getCurrentOffers(allOffers);

        populateStores(currentOffers);
        renderStats(currentOffers);
        renderOffers();

        els.updated.innerHTML = `
            <span class="inline-block w-2 h-2 rounded-full bg-brand-500"></span>
            <span>Uppdaterad just nu</span>`;
    } catch (err) {
        console.error(err);
        els.updated.innerHTML = `
            <span class="inline-block w-2 h-2 rounded-full bg-red-400"></span>
            <span class="text-red-500">Kunde inte ladda priser</span>`;
        els.offers.innerHTML = `
            <div class="col-span-full flex flex-col items-center justify-center py-24 text-gray-400">
                <svg class="w-12 h-12 mb-4 text-red-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                </svg>
                <h3 class="text-base font-semibold text-gray-500 mb-1">Kunde inte hämta prisdata</h3>
                <p class="text-sm">${escapeHtml(err.message)}</p>
            </div>`;
    }
}

els.search.addEventListener('input', renderOffers);
els.store.addEventListener('change', renderOffers);
els.sort.addEventListener('change', renderOffers);

loadCategories();
loadData();
