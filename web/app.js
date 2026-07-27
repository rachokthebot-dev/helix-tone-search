import MiniSearch from 'https://cdn.jsdelivr.net/npm/minisearch@7/+esm';
import { pipeline, env } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/+esm';
env.allowLocalModels = false;

const $ = id => document.getElementById(id);
const statusEl = $('status'), resultsEl = $('results'), countEl = $('count'), view = $('searchview');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

const MAX_RESULTS = 60, RRF_K = 60, LS_KEY = 'helix.searchSettings', CTRLS = ['sort', 'device', 'genre', 'mindl'];
let presets = [], vectors = null, meta = null, mini = null;
let extractor = null, extractorLoading = null, queryTerms = [];

async function boot() {
  const [m, p, vb] = await Promise.all([
    fetch('data/meta.json').then(r => r.json()),
    fetch('data/presets.json').then(r => r.json()),
    fetch('data/vectors.bin').then(r => r.arrayBuffer()),
  ]);
  meta = m; presets = p; vectors = new Int8Array(vb);
  presets.forEach((r, i) => (r._i = i));

  mini = new MiniSearch({
    fields: ['name', 'band', 'song', 'artist', 'style', 'description', 'genreStr', 'toneStr'],
    storeFields: ['_i'], idField: 'id',
    searchOptions: { prefix: true, fuzzy: 0.2, boost: { name: 3, band: 3, song: 3, artist: 2 } },
  });
  mini.addAll(presets.map(r => ({ ...r, genreStr: (r.genre_tags || []).join(' '), toneStr: (r.tone_tags || []).join(' ') })));

  buildFacets();
  loadSettings();
  statusEl.classList.remove('loading');
  statusEl.innerHTML = `<span class="dot"></span>${presets.length.toLocaleString()} tones indexed · search by band, song, or vibe`;
  wire();
  startTyping();
  render();
}

function buildFacets() {
  for (const d of [...new Set(presets.map(r => r.device).filter(Boolean))].sort()) $('device').add(new Option(d, d));
  for (const g of [...new Set(presets.flatMap(r => r.genre_tags || []))].sort()) $('genre').add(new Option(g, g));
}

// ---- typewriter placeholder (idle only) ----
const EXAMPLES = ['nirvana', 'green day', 'david gilmour', 'metallica enter sandman', 'ambient clean delay', 'pink floyd', 'djent high-gain'];
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
  const dev = $('device').value, gen = $('genre').value, min = +$('mindl').value;
  return (!dev || r.device === dev) && (!gen || (r.genre_tags || []).includes(gen)) && (r.downloads || 0) >= min;
}
const isBandMatch = r => { const bs = ((r.band || '') + ' ' + (r.song || '')).toLowerCase(); return queryTerms.length && queryTerms.every(w => bs.includes(w)); };

function rankItems(ranked, sort) {
  let items = ranked.map(([i, s]) => ({ row: presets[i], score: s })).filter(x => passes(x.row));
  if (sort === 'downloads') items.sort((a, b) => b.row.downloads - a.row.downloads);
  else if (sort === 'newest') items.sort((a, b) => (b.row.date || '').localeCompare(a.row.date || ''));
  else if (queryTerms.length) {
    const hits = items.filter(x => isBandMatch(x.row)).sort((a, b) => b.row.downloads - a.row.downloads);
    items = [...hits, ...items.filter(x => !isBandMatch(x.row))];
  }
  return items.slice(0, MAX_RESULTS);
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
  const bandHits = items.filter(x => isBandMatch(x.row)).length;
  countEl.innerHTML = `<b>${items.length}</b> result${items.length === 1 ? '' : 's'}` +
    (bandHits ? ` <span class="cband">· ${bandHits} by band/song</span>` : '');
  resultsEl.innerHTML = items.length
    ? items.map((x, i) => card(x.row, x.score, i)).join('')
    : `<div class="empty"><b>No matches.</b> Try a broader query or clear the filters.</div>`;
}
function card(r, score, idx) {
  const bl = r.band ? `<div class="band">${esc(r.band)}${r.song ? ` <span class="song">— ${esc(r.song)}</span>` : ''}</div>` : '';
  const chips = [...(r.genre_tags || []).slice(0, 3).map(g => `<span class="chip g">${esc(g)}</span>`),
    ...(r.tone_tags || []).slice(0, 4).map(t => `<span class="chip t">${esc(t)}</span>`)].join('');
  const bm = isBandMatch(r), delay = Math.min(idx, 12) * 28;
  return `<article class="card" style="animation-delay:${delay}ms">
    <div class="cardtop"><span class="device">${esc(r.device || 'Helix')}</span>
      <span class="dls"><b>${(r.downloads || 0).toLocaleString()}</b> dl</span></div>
    <h3 class="name"><a href="${r.url}" target="_blank" rel="noopener">${esc(r.name || 'Untitled')}</a></h3>${bl}
    <div class="sub">${[r.author ? 'by ' + esc(r.author) : '', r.date ? esc(r.date) : ''].filter(Boolean).join('  ·  ')}</div>
    ${r.description ? `<p class="desc">${esc(r.description)}</p>` : ''}
    ${chips ? `<div class="chips">${chips}</div>` : ''}
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

function wire() {
  let t;
  $('q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(render, 130); });
  $('q').addEventListener('focus', stopTyping);
  $('q').addEventListener('blur', () => { if (!$('q').value) startTyping(); });
  for (const id of CTRLS) $(id).addEventListener('change', () => { saveSettings(); render(); });
}

boot().catch(e => { statusEl.classList.remove('loading'); statusEl.textContent = 'Failed to load the index. Refresh to retry.'; console.error(e); });
