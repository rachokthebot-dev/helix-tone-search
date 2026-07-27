import MiniSearch from 'https://cdn.jsdelivr.net/npm/minisearch@7/+esm';
import { pipeline, env } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/+esm';
env.allowLocalModels = false;

const $ = id => document.getElementById(id);
const statusEl = $('status'), resultsEl = $('results'), countEl = $('count'), view = $('searchview');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

const MAX_RESULTS = 60, RRF_K = 60, LS_KEY = 'helix.searchSettings', CTRLS = ['sort', 'device', 'genre', 'mindl', 'feature'];
let presets = [], vectors = null, meta = null, mini = null;
let extractor = null, extractorLoading = null, queryTerms = [], gearFilter = '';

// Color-code cards by genre/tone family (order matters: most specific first).
const FAMILIES = [
  { key: 'metal',    color: '#d64545', kw: ['metal', 'djent', 'core', 'grind', 'death', 'thrash', 'doom', 'black'] },
  { key: 'blues',    color: '#4a7fd6', kw: ['blues'] },
  { key: 'jazz',     color: '#9b6bd6', kw: ['jazz', 'fusion'] },
  { key: 'ambient',  color: '#3fb0a0', kw: ['ambient', 'atmospher', 'post-', 'shoegaze', 'clean'] },
  { key: 'acoustic', color: '#5aa564', kw: ['acoustic', 'folk', 'country', 'worship', 'praise', 'gospel'] },
  { key: 'pop/funk', color: '#d65a9e', kw: ['pop', 'funk', 'soul', 'r&b', 'disco', 'synth'] },
  { key: 'rock',     color: '#dd7a2e', kw: ['rock', 'punk', 'grunge', 'alternative', 'indie'] },
];
const OTHER = { key: 'other', color: '#8a8a8a' };
function family(r) {
  const tags = [...(r.genre_tags || []), ...(r.tone_tags || [])].join(' ').toLowerCase();
  return FAMILIES.find(f => f.kw.some(k => tags.includes(k))) || OTHER;
}

async function boot() {
  const [m, p, vb] = await Promise.all([
    fetch('data/meta.json').then(r => r.json()),
    fetch('data/presets.json').then(r => r.json()),
    fetch('data/vectors.bin').then(r => r.arrayBuffer()),
  ]);
  meta = m; presets = p; vectors = new Int8Array(vb);
  presets.forEach((r, i) => (r._i = i));

  mini = new MiniSearch({
    fields: ['name', 'band', 'song', 'artist', 'style', 'genreStr', 'toneStr', 'bandExtra', 'gearStr', 'featStr'],
    storeFields: ['_i'], idField: 'id',
    searchOptions: { prefix: true, fuzzy: 0.2, boost: { name: 3, band: 3, song: 3, artist: 2, bandExtra: 3 } },
  });
  mini.addAll(presets.map(r => ({ ...r,
    genreStr: (r.genre_tags || []).join(' '), toneStr: (r.tone_tags || []).join(' '),
    bandExtra: [r.band_norm, r.band_inferred, r.song_inferred, ...(r.bands || []), ...(r.aliases || []),
                ...(r.mentioned_bands || []), ...(r.mentioned_songs || [])].filter(Boolean).join(' '),
    gearStr: (r.gear || []).join(' '), featStr: (r.features || []).join(' '),
  })));

  buildFacets();
  buildLegend();
  loadSettings();
  statusEl.classList.remove('loading');
  statusEl.innerHTML = `<span class="dot"></span>${presets.length.toLocaleString()} tones indexed · search by band, song, or vibe`;
  wire();
  startTyping();
  render();
}

function buildLegend() {
  $('legend').innerHTML = [...FAMILIES, OTHER]
    .map(f => `<span><i style="background:${f.color}"></i>${f.key}</span>`).join('');
}

function buildFacets() {
  for (const d of [...new Set(presets.map(r => r.device).filter(Boolean))].sort()) $('device').add(new Option(d, d));
  // 200+ raw genres is unusable — show only common ones, most-frequent first
  const gcount = {};
  presets.forEach(r => (r.genre_tags || []).forEach(g => (gcount[g] = (gcount[g] || 0) + 1)));
  const genres = Object.entries(gcount).filter(([, c]) => c >= 10).sort((a, b) => b[1] - a[1]).map(([g]) => g);
  for (const g of genres) $('genre').add(new Option(g, g));
  // low-cardinality signal-chain features -> a clean facet
  const fcount = {};
  presets.forEach(r => (r.features || []).forEach(f => { const k = f.toLowerCase(); fcount[k] = (fcount[k] || 0) + 1; }));
  const feats = Object.entries(fcount).filter(([, c]) => c >= 5).sort((a, b) => b[1] - a[1]).map(([f]) => f);
  for (const f of feats) $('feature').add(new Option(f, f));
  if (feats.length === 0) $('feature').closest('.field').style.display = 'none';  // hide until data has features
}

// ---- typewriter placeholder (idle only) ----
const EXAMPLES = [
  'nirvana', 'metallica enter sandman', 'gilmour lead delay', 'deftones drop tuning',
  'marshall jcm800 crunch', 'dumble clean', 'ambient looper', 'djent high-gain rhythm',
  'tool pitch shifter', 'red hot chili peppers funk', 'snapshots gig rig', 'klon blues lead',
];
let tw = { i: 0, j: 0, del: false, timer: null };
function typeStep() {
  const word = EXAMPLES[tw.i % EXAMPLES.length];
  tw.j += tw.del ? -1 : 1;
  $('q').setAttribute('placeholder', 'Search  ' + word.slice(0, tw.j) + '▉');
  let d = tw.del ? 45 : 90;
  if (!tw.del && tw.j === word.length) { tw.del = true; d = 1300; }
  else if (tw.del && tw.j === 0) { tw.del = false; tw.i++; d = 350; }
  tw.timer = setTimeout(typeStep, d);
}
function stopTyping() { if (tw.timer) { clearTimeout(tw.timer); tw.timer = null; } $('q').setAttribute('placeholder', 'Search by band, song, or vibe'); }
function startTyping() { if (reduce || tw.timer || $('q').value) return; tw = { i: 0, j: 0, del: false, timer: null }; typeStep(); }

// ---- semantic ----
function loadModel() {
  if (extractor) return Promise.resolve(extractor);
  if (extractorLoading) return extractorLoading;
  statusEl.classList.add('loading');
  statusEl.innerHTML = `<span class="dot"></span>Warming up the semantic model (one-time download)…`;
  extractorLoading = pipeline('feature-extraction', meta.browser_model)
    .then(e => { extractor = e; statusEl.classList.remove('loading');
      statusEl.innerHTML = `<span class="dot"></span>${presets.length.toLocaleString()} tones · semantic search ready`; return e; })
    .catch(err => { statusEl.classList.remove('loading');
      statusEl.innerHTML = `<span class="dot"></span>Semantic model unavailable — using keyword search`; throw err; });
  return extractorLoading;
}
async function semanticRanks(query) {
  const ex = await loadModel();
  const out = await ex(query, { pooling: 'mean', normalize: true });
  const q = out.data, dims = meta.dims, n = presets.length, scale = meta.scale, scored = new Array(n);
  for (let i = 0; i < n; i++) { let dot = 0, off = i * dims; for (let j = 0; j < dims; j++) dot += q[j] * (vectors[off + j] / scale); scored[i] = [i, dot]; }
  return scored.sort((a, b) => b[1] - a[1]);
}
function rrf(lex, sem) {
  const s = new Map();
  lex.forEach((i, r) => s.set(i, (s.get(i) || 0) + 1 / (RRF_K + r)));
  sem.forEach(([i], r) => s.set(i, (s.get(i) || 0) + 1 / (RRF_K + r)));
  return [...s.entries()].sort((a, b) => b[1] - a[1]);
}

// ---- filters / ranking ----
function passes(r) {
  const dev = $('device').value, gen = $('genre').value, min = +$('mindl').value, feat = $('feature').value;
  if (dev && r.device !== dev) return false;
  if (gen && !(r.genre_tags || []).includes(gen)) return false;
  if (feat && !(r.features || []).some(f => f.toLowerCase() === feat)) return false;
  if (gearFilter && !(r.gear || []).some(g => g.toLowerCase() === gearFilter.toLowerCase())) return false;
  if ((r.downloads || 0) < min) return false;
  return true;
}
const bandBlob = r => [r.band, r.song, r.artist, r.band_norm, r.band_inferred, r.song_inferred,
  ...(r.bands || []), ...(r.aliases || []), ...(r.mentioned_bands || []), ...(r.mentioned_songs || [])]
  .filter(Boolean).join(' ').toLowerCase();
const isBandMatch = r => queryTerms.length && queryTerms.every(w => bandBlob(r).includes(w));
// a "real" match = every query term appears literally somewhere (not fuzzy) — used to tell
// genuine hits apart from the semantic tail and to detect no-match queries.
const searchText = r => [r.name, r.band, r.song, r.artist, r.style, r.band_norm, r.band_inferred, r.song_inferred,
  ...(r.bands || []), ...(r.aliases || []), ...(r.mentioned_bands || []), ...(r.mentioned_songs || []),
  ...(r.genre_tags || []), ...(r.tone_tags || []), ...(r.gear || []), ...(r.features || [])]
  .filter(Boolean).join(' ').toLowerCase();
const isRealMatch = r => queryTerms.length && queryTerms.every(w => searchText(r).includes(w));

function rankItems(ranked, sort) {
  let items = ranked.map(([i, s]) => ({ row: presets[i], score: s, i })).filter(x => passes(x.row));
  if (sort === 'downloads') { items.sort((a, b) => b.row.downloads - a.row.downloads); return items.slice(0, MAX_RESULTS); }
  if (sort === 'newest') { items.sort((a, b) => (b.row.date || '').localeCompare(a.row.date || '')); return items.slice(0, MAX_RESULTS); }
  if (!queryTerms.length) return items.slice(0, MAX_RESULTS);
  // relevance: band/song matches (by downloads), then other literal matches, then a labelled
  // "related" tail of semantic neighbours that didn't literally match the query.
  const band = items.filter(x => isBandMatch(x.row)).sort((a, b) => b.row.downloads - a.row.downloads);
  const bandSet = new Set(band.map(x => x.i));
  const other = items.filter(x => !bandSet.has(x.i) && isRealMatch(x.row));
  const related = items.filter(x => !bandSet.has(x.i) && !isRealMatch(x.row));
  related.forEach(x => (x.related = true));
  return [...band, ...other, ...related].slice(0, MAX_RESULTS);
}

async function render() {
  const query = $('q').value.trim();
  const sort = $('sort').value;
  queryTerms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!query) { view.hidden = true; startTyping(); return; }
  stopTyping(); view.hidden = false;

  const lex = mini.search(query).map(h => h._i);
  paint(rankItems(lex.map(i => [i, 0]), sort));         // instant keyword pass
  try { const sem = await semanticRanks(query); paint(rankItems(rrf(lex, sem), sort)); }
  catch { /* keyword results already shown */ }
}

// ---- paint ----
function esc(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function paint(items) {
  const q = $('q').value.trim();
  const hasMatches = items.some(x => !x.related);
  const bandHits = items.filter(x => isBandMatch(x.row)).length;
  countEl.innerHTML = (q && items.length && !hasMatches)
    ? `<b>0</b> matches <span class="cband">· ${items.length} similar</span>`
    : `<b>${items.length}</b> result${items.length === 1 ? '' : 's'}` +
      (bandHits ? ` <span class="cband">· ${bandHits} by band/song</span>` : '');
  if (!items.length) {
    resultsEl.innerHTML = `<div class="empty"><b>No matches.</b> Try a broader query or clear the filters.</div>`;
    return;
  }
  let html = '', dividerDone = false;
  if (q && !hasMatches) {   // nothing literally matched — say so, then show closest-sounding tones
    html += `<div class="notice">No preset matches “${esc(q)}” — showing the closest-sounding tones instead.</div>`;
    dividerDone = true;
  }
  items.forEach((x, i) => {
    if (x.related && hasMatches && !dividerDone) { html += `<div class="divider">Related tones</div>`; dividerDone = true; }
    html += card(x.row, x.score, i);
  });
  resultsEl.innerHTML = html;
}
function card(r, score, idx) {
  const bandName = r.band_norm || r.band || r.band_inferred;
  const songName = r.song || r.song_inferred;
  const guessed = !r.band && r.band_inferred;
  const isGeneric = !bandName && !songName && !(r.mentioned_bands || []).length;
  const bl = bandName || songName
    ? `<div class="band">${esc(bandName || '')}${guessed ? ` <span class="song" title="inferred from the preset name">· guessed</span>` : ''}${songName ? ` <span class="song">${bandName ? '— ' : ''}${esc(songName)}</span>` : ''}</div>`
    : (isGeneric ? `<div class="gp">General-purpose preset</div>` : '');
  const also = (r.mentioned_bands || []).filter(b => b.toLowerCase() !== (bandName || '').toLowerCase()).slice(0, 3);
  const chips = [...(r.genre_tags || []).slice(0, 3).map(g => `<span class="chip g">${esc(g)}</span>`),
    ...(r.tone_tags || []).slice(0, 3).map(t => `<span class="chip t">${esc(t)}</span>`)].join('');
  const gearChips = (r.gear || []).slice(0, 4)
    .map(g => `<span class="chip gear" data-gear="${esc(g)}" title="Filter by ${esc(g)}">${esc(g)}</span>`).join('');
  const bm = isBandMatch(r), delay = Math.min(idx, 12) * 28;
  return `<article class="card" style="--stripe:${family(r).color};animation-delay:${delay}ms">
    <div class="cardtop"><span class="device">${esc(r.device || 'Helix')}</span>
      <span class="dls"><b>${(r.downloads || 0).toLocaleString()}</b> dl</span></div>
    <h3 class="name"><a href="${r.url}" target="_blank" rel="noopener">${esc(r.name || 'Untitled')}</a></h3>${bl}
    ${also.length ? `<div class="sub">also: ${also.map(esc).join(', ')}</div>` : ''}
    <div class="sub">${[r.author ? 'by ' + esc(r.author) : '', r.date ? esc(r.date) : ''].filter(Boolean).join('  ·  ')}</div>
    ${r.description ? `<p class="desc">${esc(r.description)}</p>` : ''}
    ${chips ? `<div class="chips">${chips}</div>` : ''}
    ${gearChips ? `<div class="chips gearrow">${gearChips}</div>` : ''}
    <div class="foot"><a class="open" href="${r.url}" target="_blank" rel="noopener">Open on CustomTone →</a>
      ${bm ? `<span class="match band">band/song match</span>` : (score > 0 ? `<span class="match">match</span>` : '')}</div>
  </article>`;
}

// ---- persist settings ----
function saveSettings() { try { localStorage.setItem(LS_KEY, JSON.stringify(Object.fromEntries(CTRLS.map(id => [id, $(id).value])))); } catch (e) {} }
function loadSettings() {
  try { const s = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
    for (const id of CTRLS) { const el = $(id); if (s[id] != null && [...el.options].some(o => o.value === s[id])) el.value = s[id]; }
  } catch (e) {}
}

function syncGearFilter() {
  const el = $('gearfilter');
  if (gearFilter) { el.hidden = false; el.innerHTML = `gear: <b>${esc(gearFilter)}</b> <span class="x">✕</span>`; }
  else el.hidden = true;
}

function wire() {
  let t;
  $('q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(render, 130); });
  $('q').addEventListener('focus', stopTyping);
  $('q').addEventListener('blur', () => { if (!$('q').value) startTyping(); });
  for (const id of CTRLS) $(id).addEventListener('change', () => { saveSettings(); render(); });
  // click a gear chip on any card to filter by that gear; click the pill to clear
  resultsEl.addEventListener('click', e => {
    const g = e.target.closest('.chip.gear');
    if (!g) return;
    gearFilter = g.dataset.gear; syncGearFilter(); render();
  });
  $('gearfilter').addEventListener('click', () => { gearFilter = ''; syncGearFilter(); render(); });
}

boot().catch(e => { statusEl.classList.remove('loading'); statusEl.textContent = 'Failed to load the index. Refresh to retry.'; console.error(e); });
