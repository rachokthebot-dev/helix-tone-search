// The frame around the search: sidebar, drawer, theme toggle and — behind the
// music-apps proxy — the app menu.
//
// A plain-JS copy of music-apps' shared AppShell and AppSwitcher
// (packages/shared/src/app-shell.tsx, app-switcher.tsx), since this site has no
// build step to import them. Keep the two in step by hand.

(function () {
  const $ = id => document.getElementById(id);
  const root = document.documentElement;

  // ---- drawer (below iPad landscape) ----
  const sidebar = $('sidebar'), backdrop = $('backdrop');
  const setDrawer = open => { sidebar.classList.toggle('is-open', open); backdrop.hidden = !open; };
  $('menubtn').addEventListener('click', () => setDrawer(true));
  backdrop.addEventListener('click', () => setDrawer(false));
  // Picking a filter is the end of the visit, the same as picking a folder.
  sidebar.addEventListener('change', () => setDrawer(false));

  // ---- theme ----
  const toggle = $('themetoggle');
  const isDark = () => root.dataset.theme
    ? root.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  const SUN = '<svg width="20" height="20" viewBox="0 0 256 256" fill="currentColor"><path d="M120 40V16a8 8 0 0 1 16 0v24a8 8 0 0 1-16 0Zm72 88a64 64 0 1 1-64-64 64.07 64.07 0 0 1 64 64Zm-16 0a48 48 0 1 0-48 48 48.05 48.05 0 0 0 48-48ZM58.34 69.66a8 8 0 0 0 11.32-11.32l-16-16a8 8 0 0 0-11.32 11.32Zm0 116.68-16 16a8 8 0 0 0 11.32 11.32l16-16a8 8 0 0 0-11.32-11.32ZM192 72a8 8 0 0 0 5.66-2.34l16-16a8 8 0 0 0-11.32-11.32l-16 16A8 8 0 0 0 192 72Zm5.66 114.34a8 8 0 0 0-11.32 11.32l16 16a8 8 0 0 0 11.32-11.32ZM48 128a8 8 0 0 0-8-8H16a8 8 0 0 0 0 16h24a8 8 0 0 0 8-8Zm80 80a8 8 0 0 0-8 8v24a8 8 0 0 0 16 0v-24a8 8 0 0 0-8-8Zm112-88h-24a8 8 0 0 0 0 16h24a8 8 0 0 0 0-16Z"/></svg>';
  const MOON = '<svg width="20" height="20" viewBox="0 0 256 256" fill="currentColor"><path d="M233.54 142.23a8 8 0 0 0-8-2 88.08 88.08 0 0 1-109.8-109.8 8 8 0 0 0-10-10 104.84 104.84 0 0 0-52.91 37A104 104 0 0 0 136 224a103.09 103.09 0 0 0 62.52-20.88 104.84 104.84 0 0 0 37-52.91 8 8 0 0 0-1.98-7.98Zm-44.64 48.11A88 88 0 0 1 65.66 67.11a89 89 0 0 1 31.4-26A106 106 0 0 0 96 56a104.11 104.11 0 0 0 104 104 106 106 0 0 0 14.92-1.06 89 89 0 0 1-26.02 31.4Z"/></svg>';
  const paintToggle = () => { toggle.innerHTML = isDark() ? SUN + 'Light mode' : MOON + 'Dark mode'; };
  toggle.addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch { /* private mode: lasts until reload */ }
    paintToggle();
  });
  paintToggle();

  // ---- app menu, only behind the proxy ----
  // On GitHub Pages there are no sibling apps to switch to, so the corner keeps
  // the GitHub link instead.
  if (!location.pathname.startsWith('/tones')) return;

  // Mirrors APP_REGISTRY in music-apps/packages/shared/src/app-registry.ts.
  const APPS = [
    ['shreddy', 'Shreddy', 'Practice companion', 'S', '#fb923c'],
    ['lickbank', 'LickBank', 'Lick & solo library', 'L', '#60a5fa'],
    ['chordcraft', 'ChordCraft', 'Chord progressions', 'C', '#c084fc'],
    ['metronome', 'Metronome', 'Tempo & timer', 'M', '#fb7185'],
    ['soundpath', 'SoundPath', 'Tone & signal paths', 'P', '#a78bfa'],
    ['helaix', 'HelAIx', 'AI Helix presets', 'H', '#fbbf24'],
    ['setlists', 'Setlists', 'Gig prep & patches', 'G', '#10b981'],
    ['tones', 'Tone Search', 'Find a Helix preset', 'T', '#06b6d4'],
  ];

  const btn = document.createElement('button');
  btn.className = 'appmenu';
  btn.type = 'button';
  btn.title = 'Tone Search';
  btn.setAttribute('aria-haspopup', 'true');
  btn.setAttribute('aria-expanded', 'false');
  btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><rect x="1" y="1" width="5" height="5" rx="1" fill="currentColor" opacity=".8"/><rect x="10" y="1" width="5" height="5" rx="1" fill="currentColor" opacity=".5"/><rect x="1" y="10" width="5" height="5" rx="1" fill="currentColor" opacity=".5"/><rect x="10" y="10" width="5" height="5" rx="1" fill="currentColor" opacity=".3"/></svg>';
  $('repolink').replaceWith(btn);

  // On <body>, not in the sidebar: the drawer's transform would otherwise
  // carry the panel off screen with it.
  const panel = document.createElement('div');
  panel.className = 'apppanel';
  panel.setAttribute('role', 'menu');
  panel.hidden = true;
  panel.innerHTML = '<div class="apppanel-head">Music Apps</div><div class="apppanel-body">'
    + '<a class="appitem" href="/"><span class="appicon home">⌂</span><span><b>All apps</b><small>Back to the launcher</small></span></a><hr>'
    + APPS.map(([id, name, desc, letter, color]) => id === 'tones'
      ? `<span class="appitem current"><span class="appicon" style="--c:${color}">${letter}</span><span><b>${name} <em>current</em></b><small>${desc}</small></span></span>`
      : `<a class="appitem" href="/${id}"><span class="appicon" style="--c:${color}">${letter}</span><span><b>${name}</b><small>${desc}</small></span></a>`).join('')
    + '</div>';
  document.body.appendChild(panel);

  const MARGIN = 8;
  const place = () => {
    const a = btn.getBoundingClientRect();
    const maxRight = Math.max(MARGIN, innerWidth - panel.offsetWidth - MARGIN);
    panel.style.top = Math.min(Math.max(MARGIN, a.bottom + 4), Math.max(MARGIN, innerHeight - panel.offsetHeight - MARGIN)) + 'px';
    panel.style.right = Math.min(Math.max(MARGIN, innerWidth - a.right), maxRight) + 'px';
  };
  const setOpen = open => {
    panel.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (open) place();
  };
  btn.addEventListener('click', () => setOpen(panel.hidden));
  document.addEventListener('mousedown', e => {
    if (!panel.hidden && !panel.contains(e.target) && !btn.contains(e.target)) setOpen(false);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
  addEventListener('resize', () => { if (!panel.hidden) place(); });
  addEventListener('scroll', () => { if (!panel.hidden) place(); }, true);
})();
