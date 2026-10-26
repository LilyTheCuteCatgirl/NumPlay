/* NumPlay Studio - minimal UI, original kit format and in-browser .nwa linker.
 * Requires customize/kit/ and customize/ld/ produced by NumPlay's existing workflows.
 * No game data, firmware addresses, or sizes are hard-coded into the UI.
 */
'use strict';
(() => {
  const KIT = 'kit/';
  const MiB = 1048576;
  const ROM_HEAD = 12, ROM_ENTRY = 52;
  const PRESETS = {
    NumPlay: ['NumPlay', 'NumPlay'],
    'NumPlay-Invisible': ['Invisible', ' '],
    'NumPlay-Matrices': ['Matrices', 'Matrices'],
  };
  const LANGS = { en: 'English', fr: 'Français', zh: '中文' };
  const SPACES = { std: 'Standard NumWorks', e26: 'Epsilon 26', xl: 'Nwagra', xl26: 'Nwagra + Epsilon 26' };
  const SECRETS = ['x,n,t key', 'var key', 'Toolbox key', 'π key', '√ key', 'Menu: Examples', 'Konami code'];
  const EXT = { gb: 1, gbc: 2, nes: 3, sms: 4, gg: 5 };
  const SYS = { 1: 'Game Boy', 2: 'Game Boy Color', 3: 'NES', 4: 'Master System', 5: 'Game Gear' };
  const $ = (id) => document.getElementById(id);
  const node = (tag, className, text) => {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const fmt = (b) => b >= MiB ? (b / MiB).toFixed(2) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
  const align4 = (n) => (n + 3) & ~3;
  const store = (key, value) => {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); }
    catch (_) { return null; }
    return null;
  };
  let kit = null, retro = null, custom = null, romIndex = 0;
  const kits = {}, byId = {};
  const state = {
    picked: new Set(), order: [], roms: [], capacity: 'std', lang: 'en',
    search: '', filter: 'all', sort: 'recommended',
    app: { icon: 'NumPlay', name: 'NumPlay', open: 'numplay', secret: 0, hint: true },
  };
  let busy = false, toastTimeout = 0;

  // ---------- storage and selection ----------
  const capacities = () => kit.spaces?.length ? kit.spaces : [
    { id: 'std', size: kit.appSpace }, { id: 'xl', size: kit.xlAppSpace || 6 * MiB },
  ];
  const space = () => capacities().find((v) => v.id === state.capacity) || capacities()[0];
  const limit = () => space().size;
  const languageKit = () => kits[state.lang] || kit;
  const game = (id) => languageKit().byId[id] || byId[id];
  const pickedInOrder = () => state.order.filter((id) => state.picked.has(id));
  const extraRomBytes = (roms) => ROM_HEAD + roms.reduce((sum, r) => sum + ROM_ENTRY + align4(r.bytes.length), 0);
  const activeIds = (ids) => ids.filter((id) => !retro || id !== retro.id);
  const hasRetro = (ids, roms) => !!retro && ids.includes(retro.id) && roms.length > 0;
  function used(ids = pickedInOrder(), roms = state.roms) {
    const normal = activeIds(ids);
    const useRoms = hasRetro(ids, roms);
    const k = languageKit();
    const needsBase = normal.some((id) => !byId[id].xl) || useRoms;
    let bytes = needsBase ? k.base : (k.baseAlone || k.base);
    for (const id of normal) bytes += game(id).flash;
    if (useRoms) bytes += game(retro.id).flash + extraRomBytes(roms);
    return bytes;
  }
  // Some games physically cannot fit alongside the NumPlay launcher at a
  // given flash capacity. Studio no longer offers standalone downloads:
  // those games remain visible but unavailable until a larger supported
  // calculator capacity is chosen.
  function standaloneOnly(id) {
    const g = game(id);
    return !!g.alone && used([id], []) > limit();
  }
  function extraSize(id) {
    if (state.picked.has(id)) return 0;
    return used([...pickedInOrder(), id]) - used();
  }
  function canAdd(id) {
    if (state.picked.has(id)) return true;
    if (standaloneOnly(id)) return false;
    return used([...pickedInOrder(), id]) <= limit();
  }
  function popup(text) {
    const t = $('toast'); t.textContent = text; t.classList.add('show');
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => t.classList.remove('show'), 3100);
  }
  function updateSaved() {
    if (!kit) return;
    const a = state.app, q = new URLSearchParams();
    q.set('games', pickedInOrder().join(','));
    if (state.capacity !== 'std') q.set('space', state.capacity);
    if (state.lang !== 'en') q.set('lang', state.lang);
    if (a.icon !== 'NumPlay') q.set('icon', a.icon);
    if (a.name !== (PRESETS[a.icon]?.[1] ?? 'NumPlay')) q.set('name', a.name);
    if (a.open === 'matrices') q.set('open', 'matrices' + (a.secret ? '-' + a.secret : '') + (a.hint ? '' : '-nohint'));
    const hash = q.toString();
    try { history.replaceState(null, '', '#' + hash); } catch (_) { /* file:// preview */ }
    store('numplay-studio2', hash);  // compatible with the old Studio
    store('numplay-order2', state.order.join(','));
  }
  function restore() {
    const raw = location.hash.slice(1) || store('numplay-studio2') || '';
    const q = new URLSearchParams(raw);
    const known = kit.games.map((g) => g.id);
    const defaultOrder = kit.games.slice().sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || a.title.localeCompare(b.title)).map((g) => g.id);
    const savedOrder = (store('numplay-order2') || '').split(',').filter((id) => known.includes(id));
    const linked = q.has('games') ? q.get('games').split(',').filter((id) => known.includes(id)) : null;
    state.order = [...new Set([...(linked || []), ...savedOrder, ...defaultOrder])];
    state.picked = new Set(linked !== null ? linked : kit.games.filter((g) => g.default).map((g) => g.id));
    state.capacity = capacities().some((s) => s.id === q.get('space')) ? q.get('space') : 'std';
    state.lang = kits[q.get('lang')] && kit.langs.includes(q.get('lang')) ? q.get('lang') : 'en';
    const icon = q.get('icon');
    state.app.icon = PRESETS[icon] || (icon === 'custom' && custom) ? icon : 'NumPlay';
    state.app.name = q.has('name') ? q.get('name').slice(0, 20) : (PRESETS[state.app.icon]?.[1] ?? 'NumPlay');
    const opening = (q.get('open') || '').split('-');
    state.app.open = opening[0] === 'matrices' ? 'matrices' : 'numplay';
    state.app.secret = Math.max(0, Math.min(SECRETS.length - 1, Number(opening[1]) || 0));
    state.app.hint = !opening.includes('nohint');
    // Earlier kits can change: keep only a selection that still fits.
    const largest = pickedInOrder().slice().sort((a, b) => game(b).flash - game(a).flash);
    let changed = false;
    while ((used() > limit() || pickedInOrder().some(standaloneOnly)) && largest.length) {
      state.picked.delete(largest.shift()); changed = true;
    }
    if (changed) popup('Some saved games no longer fit and were unselected.');
  }
  async function loadLanguage(code) {
    if (code === 'en' || kits[code]) return;
    const response = await fetch(KIT + code + '/kit.json');
    if (!response.ok) throw new Error('Language kit unavailable');
    const k = await response.json();
    k.byId = Object.fromEntries(k.games.map((g) => [g.id, g]));
    kits[code] = k;
  }

  // ---------- game library ----------
  function libraryGames() {
    let games = kit.games.filter((g) =>
      (!state.search || (g.title + ' ' + g.tagline).toLowerCase().includes(state.search.toLowerCase())) &&
      (state.filter !== 'selected' || state.picked.has(g.id)));
    games.sort((a, b) => {
      // "Selected" is the actual NumPlay launcher order, not an A–Z view.
      if (state.filter === 'selected') return state.order.indexOf(a.id) - state.order.indexOf(b.id);
      if (state.sort === 'name') return a.title.localeCompare(b.title);
      if (state.sort === 'size') return b.flash - a.flash || a.title.localeCompare(b.title);
      return (a.rank ?? 99) - (b.rank ?? 99) || a.title.localeCompare(b.title);
    });
    return games;
  }
  function iconPath(g) { return g.icon ? KIT + 'icons/' + g.id + '.png' : KIT + 'variants/NumPlay.png'; }
  function toggleGame(id) {
    if (busy) return;
    if (state.picked.has(id)) {
      state.picked.delete(id);
    } else if (!canAdd(id)) {
      popup('Not enough space. Remove another game or change your calculator storage settings.');
      return;
    } else {
      state.picked.add(id);
      if (retro && id === retro.id) $('rom-panel').hidden = false;
    }
    render();
  }
  // Selected games are reordered in their own list using a six-dot handle.
  // Works with mouse, touch, pen, and keyboard arrow keys; no arrow buttons.
  function dragHandle(id) {
    const handle = node('button', 'drag-handle');
    handle.type = 'button';
    handle.dataset.drag = id;
    handle.title = 'Drag to reorder. Keyboard: use Up or Down arrow.';
    handle.setAttribute('aria-label', 'Reorder ' + byId[id].title + '. Drag, or use the arrow keys.');
    handle.innerHTML = '<svg viewBox="0 0 18 24" width="17" height="22" aria-hidden="true">' +
      '<circle cx="6" cy="5" r="1.65"/><circle cx="12" cy="5" r="1.65"/>' +
      '<circle cx="6" cy="12" r="1.65"/><circle cx="12" cy="12" r="1.65"/>' +
      '<circle cx="6" cy="19" r="1.65"/><circle cx="12" cy="19" r="1.65"/></svg>';
    handle.addEventListener('pointerdown', (e) => startReordering(e, id, handle));
    handle.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault();
      moveSelected(id, e.key === 'ArrowUp' ? -1 : 1);
      const current = [...document.querySelectorAll('.drag-handle')].find((b) => b.dataset.drag === id);
      if (current) current.focus({ preventScroll: true });
    });
    return handle;
  }
  let dragging = false;
  function startReordering(e, id, handle) {
    if (dragging || state.filter !== 'selected' || busy || e.button !== 0) return;
    e.preventDefault();
    const list = $('game-list'), row = handle.closest('.game-row');
    const start = row.getBoundingClientRect();
    dragging = true;
    try { handle.setPointerCapture(e.pointerId); } catch (_) { /* window listeners still receive movement */ }
    row.classList.add('is-dragging');
    const ghost = row.cloneNode(true);
    ghost.classList.remove('is-dragging');
    ghost.classList.add('drag-ghost');
    ghost.style.width = start.width + 'px';
    ghost.style.left = start.left + 'px';
    ghost.style.top = start.top + 'px';
    ghost.setAttribute('aria-hidden', 'true');
    document.body.append(ghost);
    const offset = e.clientY - start.top;
    let pointerY = e.clientY, scrollFrame = 0;
    const position = () => { ghost.style.top = (pointerY - offset) + 'px'; };
    function updateOrder() {
      const siblings = [...list.querySelectorAll('.game-row:not(.is-dragging)')];
      const before = siblings.find((candidate) => pointerY < candidate.getBoundingClientRect().top + candidate.offsetHeight / 2);
      if (before) list.insertBefore(row, before);
      else list.append(row);
    }
    function autoscroll() {
      if (!dragging) return;
      const edge = 75;
      const speed = pointerY < edge ? -Math.min(14, (edge - pointerY) / 4) :
        pointerY > innerHeight - edge ? Math.min(14, (pointerY - (innerHeight - edge)) / 4) : 0;
      if (speed) { window.scrollBy(0, speed); updateOrder(); }
      scrollFrame = requestAnimationFrame(autoscroll);
    }
    const move = (event) => {
      if (event.pointerId !== e.pointerId) return;
      pointerY = event.clientY;
      position();
      updateOrder();
    };
    const done = (event) => {
      if (event.pointerId !== e.pointerId) return;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', done);
      window.removeEventListener('pointercancel', done);
      cancelAnimationFrame(scrollFrame);
      ghost.remove();
      row.classList.remove('is-dragging');
      dragging = false;
      const chosen = [...list.querySelectorAll('.game-row')].map((item) => item.dataset.gameId);
      // Keep all unselected games in state.order so previously shared links
      // and later selections continue working normally.
      state.order = [...chosen, ...state.order.filter((entry) => !chosen.includes(entry))];
      render();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', done);
    window.addEventListener('pointercancel', done);
    scrollFrame = requestAnimationFrame(autoscroll);
  }
  function moveSelected(id, direction) {
    const selected = pickedInOrder(), i = selected.indexOf(id), target = selected[i + direction];
    if (!target) return;
    const a = state.order.indexOf(id), b = state.order.indexOf(target);
    [state.order[a], state.order[b]] = [state.order[b], state.order[a]];
    render();
  }
  function gameRow(g) {
    const selected = state.picked.has(g.id), available = canAdd(g.id);
    const neverFits = standaloneOnly(g.id);
    const need = Math.max(0, used([...pickedInOrder(), g.id]) - limit());
    const row = node('div', 'game-row' + (selected ? ' selected' : available ? '' : ' unavailable'));
    row.dataset.gameId = g.id;
    // The game selection and its README are two independent controls.
    // Do not nest the info button inside the selection button: that would
    // be invalid HTML and would accidentally toggle games when reading.
    const main = node('div', 'game-main');
    const b = node('button', 'game-pick'); b.type = 'button';
    b.setAttribute('aria-pressed', String(selected));
    b.disabled = !selected && !available;
    const why = !available ? (neverFits ? 'Too large to run inside NumPlay at this capacity' :
      'Needs ' + fmt(need) + ' more storage') : selected ? 'Remove from NumPlay' : 'Add to NumPlay';
    b.setAttribute('aria-label', g.title + ': ' + why);
    b.title = why;
    const img = node('img', 'game-icon'); img.alt = ''; img.loading = 'lazy'; img.src = iconPath(g);
    img.onerror = () => { if (!img.dataset.fallback) { img.dataset.fallback = '1'; img.src = KIT + 'variants/NumPlay.png'; } };
    b.append(img);

    const details = node('div', 'game-details');
    const titleLine = node('div', 'game-title-line');
    titleLine.append(node('span', 'game-title', g.title));
    const info = node('button', 'game-info');
    info.type = 'button';
    info.textContent = 'info';
    info.title = 'Read ' + g.title + ' README';
    info.setAttribute('aria-label', 'Information about ' + g.title);
    info.setAttribute('aria-haspopup', 'dialog');
    info.addEventListener('click', (event) => { event.stopPropagation(); openGameReadme(g); });
    titleLine.append(info);
    details.append(titleLine);
    const sizeInfo = retro && g.id === retro.id ? fmt(g.flash) + ' + ROM files' : fmt(g.flash);
    details.append(node('span', 'game-size', sizeInfo + (!selected && !available ?
      ' · ' + (neverFits ? 'won\'t fit in NumPlay' : 'needs ' + fmt(need) + ' more') : '')));

    const stateBox = node('span', 'game-state');
    stateBox.append(node('span', 'game-state-label', selected ? 'Added' : available ? 'Add' : 'Too large'));
    stateBox.append(node('span', 'state-icon', selected ? '✓' : available ? '+' : '×'));
    main.append(b, details, stateBox);
    // Clicking the icon, title, size, or status still selects/deselects.
    main.addEventListener('click', () => { if (selected || available) toggleGame(g.id); });
    row.append(main);
    if (state.filter === 'selected') row.append(dragHandle(g.id));
    return row;
  }
  function renderGames() {
    const list = $('game-list'); list.replaceChildren();
    const games = libraryGames();
    const fragment = document.createDocumentFragment();
    for (const g of games) fragment.append(gameRow(g));
    list.append(fragment);
    $('empty-state').hidden = games.length > 0;
    $('empty-state').textContent = state.filter === 'selected' && !state.picked.size ?
      'No selected games yet. Choose some in All games.' : 'No games match your search.';
    $('game-list').hidden = games.length === 0;
    $('order-hint').hidden = state.filter !== 'selected' || games.length < 2;
    $('game-count').textContent = kit.games.length + ' games available';
    $('filter-count').textContent = state.picked.size;
    $('clear-games').disabled = !state.picked.size || busy;
    $('sort').disabled = state.filter === 'selected';
    $('sort').closest('.sort-label').classList.toggle('inactive', state.filter === 'selected');
    for (const key of ['all', 'selected']) {
      const b = $('filter-' + key); b.classList.toggle('active', key === state.filter);
      b.setAttribute('aria-pressed', String(key === state.filter));
    }
  }
  function renderRoms() {
    $('rom-panel').hidden = !retro || !state.picked.has(retro.id);
    if (!retro || !state.picked.has(retro.id)) return;
    const list = $('rom-list'); list.replaceChildren();
    for (const rom of state.roms) {
      const li = node('li');
      li.append(node('span', '', rom.name), node('span', 'muted', fmt(rom.bytes.length)));
      const remove = node('button', '', 'Remove'); remove.type = 'button';
      remove.addEventListener('click', () => { state.roms = state.roms.filter((x) => x !== rom); render(); });
      li.append(remove); list.append(li);
    }
  }
  function render() {
    if (!kit) return;
    const bytes = used(), max = limit(), selected = pickedInOrder();
    const missingRoms = retro && state.picked.has(retro.id) && !state.roms.length;
    const canBuild = selected.length > 0 && !missingRoms && bytes <= max && !selected.some(standaloneOnly);
    $('selected-count').textContent = selected.length;
    $('storage-text').textContent = fmt(bytes) + ' / ' + fmt(max);
    $('storage-left').textContent = fmt(Math.max(0, max - bytes)) + ' free';
    $('space-name').textContent = SPACES[state.capacity] || state.capacity;
    const percent = Math.max(0, Math.min(100, bytes / max * 100));
    $('storage-fill').style.width = percent.toFixed(2) + '%';
    $('storage-meter').setAttribute('aria-valuenow', String(Math.round(percent)));
    $('mobile-storage').textContent = fmt(bytes) + ' / ' + fmt(max);
    $('mobile-count').textContent = selected.length + ' selected';
    $('build-note').textContent = missingRoms ? 'Add at least one ROM to use Retro-Go.' :
      !selected.length ? 'Select a game to get started.' : bytes > max ? 'Your selection is too large.' :
      'Ready to build one NumPlay app.';
    for (const b of [$('build-button'), $('mobile-download')]) {
      b.disabled = busy || !canBuild;
      b.textContent = busy ? 'Building…' : b.id === 'build-button' ? 'Download NumPlay.nwa' : 'Download';
    }
    if (window.NumPlaySaveVault) window.NumPlaySaveVault.updateInstallAvailability(canBuild && !busy, selected);
    $('build-version').textContent = kit.version ? 'v' + String(kit.version).replace(/^v/, '') : '';
    renderGames(); renderRoms(); updateSaved();
  }
  async function addRoms(files) {
    if (!retro || !state.picked.has(retro.id)) { popup('Select Retro-Go first.'); return; }
    const supported = new Set(retro.systems?.length ? retro.systems : [1, 2, 3]);
    let added = 0;
    for (const file of files) {
      const ext = file.name.split('.').pop().toLowerCase(), sys = EXT[ext];
      if (!sys || !supported.has(sys)) { popup('Unsupported ROM type: ' + file.name); continue; }
      const bytes = new Uint8Array(await file.arrayBuffer());
      const r = { key: ++romIndex, name: romName(file.name), sys, bytes };
      if (used(pickedInOrder(), [...state.roms, r]) > limit()) { popup(file.name + ' will not fit.'); continue; }
      state.roms.push(r); added++;
    }
    if (added) popup(added + ' ROM' + (added === 1 ? '' : 's') + ' added.');
    render();
  }
  function romName(file) {
    const enc = new TextEncoder(); let name = '';
    for (const c of file.replace(/\.[^.]*$/, '')) {
      if (enc.encode(name + c).length > 40) break;
      name += c;
    }
    return name;
  }

  // ---------- app settings ----------
  function settingsUi() {
    const places = $('space-setting'); places.replaceChildren();
    for (const sp of capacities()) {
      const opt = node('option', '', (SPACES[sp.id] || sp.id) + ' · ' + fmt(sp.size));
      opt.value = sp.id; places.append(opt);
    }
    const languages = $('language-setting'); languages.replaceChildren();
    for (const lang of kit.langs) { const opt = node('option', '', LANGS[lang] || lang); opt.value = lang; languages.append(opt); }
    const icons = $('icon-setting'); icons.replaceChildren();
    for (const v of kit.variants) { const opt = node('option', '', PRESETS[v]?.[0] || v); opt.value = v; icons.append(opt); }
    const opt = node('option', '', 'Custom icon'); opt.value = 'custom'; opt.disabled = !custom; icons.append(opt);
    const secret = $('secret-setting'); secret.replaceChildren();
    for (const [i, name] of SECRETS.entries()) { const o = node('option', '', name); o.value = String(i); secret.append(o); }
    syncSettings();
  }
  function syncSettings() {
    $('space-setting').value = state.capacity;
    $('language-setting').value = state.lang;
    $('app-name-setting').value = state.app.name;
    $('icon-setting').value = state.app.icon;
    $('open-setting').value = state.app.open;
    $('secret-setting').value = String(state.app.secret);
    $('hint-setting').checked = state.app.hint;
    $('secret-options').hidden = state.app.open !== 'matrices';
    $('space-warning').hidden = !state.capacity.startsWith('xl');
    $('dark-mode-setting').checked = document.documentElement.dataset.theme === 'dark';
  }
  async function setLanguage(lang) {
    const previous = state.lang;
    try {
      await loadLanguage(lang);
      state.lang = lang;
      if (used() > limit() || pickedInOrder().some(standaloneOnly))
        throw new Error('Your selected games will not fit inside NumPlay in that language.');
      render();
    } catch (e) { state.lang = previous; popup(e.message || 'Could not load language.'); }
    syncSettings();
  }
  function setCapacity(id) {
    const previous = state.capacity;
    state.capacity = id;
    if (used() > limit() || pickedInOrder().some(standaloneOnly)) {
      state.capacity = previous;
      popup('Remove some games before choosing a smaller capacity.');
    }
    syncSettings(); render();
  }

  // ---------- data retrieval / in-browser compiler (kept compatible with tools/kit.py) ----------
  const downloads = new Map();
  function fetchBytes(url) {
    if (!downloads.has(url)) downloads.set(url, fetch(url).then(async (r) => {
      if (!r.ok) throw new Error(url + ': HTTP ' + r.status);
      if (!url.endsWith('.gz')) return new Uint8Array(await r.arrayBuffer());
      if (!('DecompressionStream' in window)) throw new Error('Your browser cannot decompress the kit; use recent Chrome, Edge or Firefox.');
      const raw = r.body ? r.body : new Blob([await r.arrayBuffer()]).stream();
      return new Uint8Array(await new Response(raw.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());
    }).catch((e) => { downloads.delete(url); throw e; }));
    return downloads.get(url);
  }
  const fetchObj = (path) => fetchBytes(KIT + path + '.gz');
  function objectPath(id) {
    const lang = state.lang !== 'en' && kits[state.lang]?.byId[id];
    return (lang && !lang.en ? state.lang + '/' : '') + 'games/' + id + '.o';
  }
  let linkerTools = null;
  function addScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script'); s.src = src;
      s.onload = resolve; s.onerror = () => reject(new Error('Cannot load ' + src));
      document.head.append(s);
    });
  }
  function tools() {
    if (!linkerTools) linkerTools = (async () => {
      const binaries = Promise.all([fetchBytes('ld/ld.wasm'), fetchBytes('ld/objcopy.wasm')]);
      await addScript('ld/ld.js'); const ld = window.Module;
      await addScript('ld/objcopy.js'); const objcopy = window.Module;
      const [a, b] = await binaries;
      return { ld: [ld, a], objcopy: [objcopy, b] };
    })().catch((err) => { linkerTools = null; throw err; });
    return linkerTools;
  }
  async function run([factory, wasm], args, files) {
    let log = '';
    const m = await factory({ wasmBinary: wasm, print: (s) => { log += s + '\n'; }, printErr: (s) => { log += s + '\n'; } });
    for (const [path, data] of Object.entries(files)) m.FS.writeFile(path, data);
    let code = 0;
    try { code = m.callMain(args); }
    catch (e) { if (typeof e?.status === 'number') code = e.status; else throw e; }
    if (code) throw new Error(log || 'Linker error ' + code);
    return m;
  }
  // ARM ELF relocatable object, same layout as the previous Studio.
  function elfObject(sections) {
    const encoder = new TextEncoder();
    const names = '\0' + sections.map((s) => s.name).join('\0') + '\0.symtab\0.strtab\0.shstrtab\0';
    const symbols = '\0' + sections.map((s) => s.sym).join('\0') + '\0';
    const shs = encoder.encode(names), sts = encoder.encode(symbols);
    let at = 52;
    const offsets = sections.map((s) => { const o = at; at = align4(at + s.data.length); return o; });
    const symOffset = at, count = sections.length + 1, strOffset = symOffset + 16 * count;
    const nameOffset = strOffset + sts.length, headerOffset = align4(nameOffset + shs.length), headerCount = sections.length + 4;
    const bytes = new Uint8Array(headerOffset + 40 * headerCount), v = new DataView(bytes.buffer);
    bytes.set([0x7f, 0x45, 0x4c, 0x46, 1, 1, 1]);
    v.setUint16(16, 1, true); v.setUint16(18, 40, true); v.setUint32(20, 1, true);
    v.setUint32(32, headerOffset, true); v.setUint16(40, 52, true);
    v.setUint16(46, 40, true); v.setUint16(48, headerCount, true); v.setUint16(50, headerCount - 1, true);
    sections.forEach((s, i) => bytes.set(s.data, offsets[i]));
    let symName = 1;
    sections.forEach((s, i) => {
      const o = symOffset + 16 * (i + 1);
      v.setUint32(o, symName, true); symName += encoder.encode(s.sym).length + 1;
      v.setUint32(o + 8, s.data.length, true);
      v.setUint8(o + 12, 0x10 | (s.type === undefined ? 1 : s.type));
      v.setUint16(o + 14, i + 1, true);
    });
    bytes.set(sts, strOffset); bytes.set(shs, nameOffset);
    const writeHeader = (i, name, type, flags, off, sz, link, info, al, entry) => {
      const o = headerOffset + 40 * i;
      [name, type, flags, 0, off, sz, link, info, al, entry].forEach((x, k) => v.setUint32(o + k * 4, x, true));
    };
    let nameAt = 1;
    sections.forEach((s, i) => {
      writeHeader(i + 1, nameAt, 1, s.flags || 2, offsets[i], s.data.length, 0, 0, s.align || 1, 0);
      nameAt += encoder.encode(s.name).length + 1;
    });
    const n = sections.length;
    writeHeader(n + 1, names.indexOf('.symtab'), 2, 0, symOffset, 16 * count, n + 2, 1, 4, 16);
    writeHeader(n + 2, names.indexOf('.strtab'), 3, 0, strOffset, sts.length, 0, 0, 1, 0);
    writeHeader(n + 3, names.indexOf('.shstrtab'), 3, 0, nameOffset, shs.length, 0, 0, 1, 0);
    return bytes;
  }
  function romTable(roms) {
    let at = align4(ROM_HEAD + ROM_ENTRY * roms.length);
    const offsets = roms.map((r) => { const o = at; at = align4(at + r.bytes.length); return o; });
    const bytes = new Uint8Array(at), v = new DataView(bytes.buffer), encoder = new TextEncoder();
    v.setUint32(0, 0x4D52504E, true); v.setUint32(4, 1, true); v.setUint32(8, roms.length, true);
    roms.forEach((r, i) => {
      const at = ROM_HEAD + ROM_ENTRY * i;
      v.setUint32(at, offsets[i], true); v.setUint32(at + 4, r.bytes.length, true); bytes[at + 8] = r.sys;
      bytes.set(encoder.encode(r.name).subarray(0, 40), at + 12);
      bytes.set(r.bytes, offsets[i]);
    });
    return bytes;
  }
  const romObject = (table) => elfObject([{ name: '.rodata.np_roms', sym: 'np_roms', data: table, align: 4 }]);
  function appObject() {
    const a = state.app, raw = new TextEncoder().encode((a.name || ' ').slice(0, 20));
    const name = new Uint8Array(raw.length + 1); name.set(raw);
    const flags = (a.open === 'matrices' ? 1 : 0) | (a.hint ? 0 : 2);
    return elfObject([
      { name: '.rodata.eadk_app_name', sym: 'eadk_app_name', data: name },
      { name: '.rodata.np_defaults', sym: 'np_defaults', data: new Uint8Array([0x44, 1, flags, a.secret]) },
    ]);
  }
  function lz4(src) {
    const out = [], hash = new Int32Array(4096).fill(-1);
    const len = (n) => { while (n >= 255) { out.push(255); n -= 255; } out.push(n); };
    let anchor = 0, i = 0;
    while (i + 12 <= src.length) {
      const k = Math.imul(src[i] | src[i + 1] << 8 | src[i + 2] << 16 | src[i + 3] << 24, 2654435761) >>> 20;
      const ref = hash[k]; hash[k] = i;
      if (ref >= 0 && i - ref < 65536 && src[ref] === src[i] && src[ref + 1] === src[i + 1] &&
          src[ref + 2] === src[i + 2] && src[ref + 3] === src[i + 3]) {
        let match = 4;
        while (i + match < src.length - 5 && src[ref + match] === src[i + match]) match++;
        const literal = i - anchor;
        out.push((Math.min(literal, 15) << 4) | Math.min(match - 4, 15));
        if (literal >= 15) len(literal - 15);
        for (let j = anchor; j < i; j++) out.push(src[j]);
        out.push((i - ref) & 255, (i - ref) >> 8);
        if (match - 4 >= 15) len(match - 4 - 15);
        i += match; anchor = i;
      } else i++;
    }
    const literal = src.length - anchor;
    out.push(Math.min(literal, 15) << 4);
    if (literal >= 15) len(literal - 15);
    for (let j = anchor; j < src.length; j++) out.push(src[j]);
    return new Uint8Array(out);
  }
  async function iconFrom(file) {
    const bitmap = await createImageBitmap(file);
    const canvas = document.createElement('canvas'); canvas.width = 55; canvas.height = 56;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 55, 56);
    const scale = Math.max(55 / bitmap.width, 56 / bitmap.height);
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bitmap, (55 - bitmap.width * scale) / 2, (56 - bitmap.height * scale) / 2, bitmap.width * scale, bitmap.height * scale);
    bitmap.close?.();
    const pixels = ctx.getImageData(0, 0, 55, 56).data, rgb = new Uint8Array(55 * 56 * 2);
    for (let p = 0, q = 0; p < pixels.length; p += 4, q += 2) {
      const val = ((pixels[p] >> 3) << 11) | ((pixels[p + 1] >> 2) << 5) | (pixels[p + 2] >> 3);
      rgb[q] = val & 255; rgb[q + 1] = val >> 8;
    }
    const obj = elfObject([{ name: '.rodata.eadk_app_icon', sym: 'eadk_app_icon', data: lz4(rgb), flags: 3, type: 0 }]);
    return { url: canvas.toDataURL('image/png'), obj };
  }
  // Installer's actual flash footprint, not the .nwa file's compressed size.
  function installedSize(nwa) {
    const v = new DataView(nwa.buffer, nwa.byteOffset, nwa.byteLength);
    const shoff = v.getUint32(32, true), count = v.getUint16(48, true), namesIndex = v.getUint16(50, true);
    const header = (i) => { const off = shoff + 40 * i; return Array.from({ length: 10 }, (_, k) => v.getUint32(off + 4 * k, true)); };
    const names = header(namesIndex), decoder = new TextDecoder();
    const sectionName = (start) => { let end = names[4] + start; while (nwa[end]) end++; return decoder.decode(nwa.subarray(names[4] + start, end)); };
    const sections = [];
    for (let i = 0; i < count; i++) {
      const h = header(i);
      if ((h[2] & 2) && h[1] !== 8) sections.push([sectionName(h[0]), h[5], Math.max(1, h[8])]);
    }
    const group = (name) => name === '.rodata.eadk_app_name' ? 0 : name === '.rodata.eadk_app_icon' ? 1 :
      name === '.text' || name.startsWith('.text.') ? 2 : name === '.rodata.eadk_api_level' ? -1 :
      name === '.data' || name.startsWith('.data.') ? 4 : 3;
    let at = 32;
    for (let groupNum = 0; groupNum < 5; groupNum++) {
      if (groupNum === 2 || groupNum === 4) at = align4(at);
      for (const [name, bytes, alignment] of sections) {
        if (group(name) === groupNum) at = ((at + alignment - 1) & ~(alignment - 1)) + bytes;
      }
    }
    return at;
  }
  async function build() {
    await loadLanguage(state.lang);
    const k = languageKit();
    const selected = pickedInOrder().filter((id) => !retro || id !== retro.id);
    const useRoms = hasRetro(pickedInOrder(), state.roms);
    if (useRoms) {
      // Retro-Go is inserted according to its chosen order, not forced to the end.
      selected.splice(pickedInOrder().indexOf(retro.id), 0, retro.id);
    }
    const folder = state.lang === 'en' ? '' : state.lang + '/';
    const iconObj = state.app.icon === 'custom' && custom ? Promise.resolve(custom.obj) :
      fetchObj('variants/' + state.app.icon + '-icon.o');
    const [t, base, arena, icon, ...objects] = await Promise.all([
      tools(), fetchObj(folder + 'base.o'), fetchObj(folder + 'arena.o'), iconObj,
      ...selected.map((id) => fetchObj(objectPath(id))),
    ]);
    const files = { 'base.o': base, 'arena.o': arena, 'app.o': appObject(), 'icon.o': icon };
    selected.forEach((id, i) => { files[id + '.o'] = objects[i]; });
    const args = k.link.concat(['-T', 'numplay.ld', 'base.o', 'arena.o', 'app.o', 'icon.o'], selected.map((id) => id + '.o'));
    if (useRoms) { files['roms.o'] = romObject(romTable(state.roms)); args.push('roms.o'); }
    const entries = selected.map((id) => 'KEEP(*(.rodata.np_games.' + String((k.byId[id] || byId[id]).index).padStart(2, '0') + '))').join('\n    ');
    const head = k.script.head.replace('KEEP(*(SORT_BY_NAME(.rodata.np_games.*)))', entries);
    const block = (id) => k.script.blocks[id] || kit.script.blocks[id];
    const index = (id) => (k.byId[id] || byId[id]).index;
    const ldScript = head + selected.slice().sort((a, b) => index(a) - index(b)).map(block).join('') + k.script.tail;
    files['numplay.ld'] = ldScript;
    const link = await run(t.ld, args.concat(['-o', 'out.nwa']), files);
    const strip = await run(t.objcopy, ['--strip-unneeded', 'in.nwa', 'out.nwa'], { 'in.nwa': link.FS.readFile('out.nwa') });
    const nwa = strip.FS.readFile('out.nwa');
    const bytes = installedSize(nwa) + k.runtime;
    if (bytes > limit()) throw new Error('This build needs ' + fmt(bytes - limit()) + ' more than your calculator can hold.');
    return { data: nwa, filename: 'NumPlay.nwa' };
  }
  // ----- calculator-specific final link (the .nwa alone is relocatable) -----
  // Uses the very same local ld/objcopy WebAssembly tools as the customizer.
  // eadk.o is supplied by the NumWorks app uploader. Local copies can be put
  // at ld/eadk.o; the official-runtime mirror is an online fallback.
  let eadkRuntime = null;
  async function getEadkRuntime() {
    if (eadkRuntime) return eadkRuntime;
    eadkRuntime = (async () => {
      try {
        const r = await fetch('ld/eadk.o');
        if (r.ok) {
          const bytes = new Uint8Array(await r.arrayBuffer());
          if (bytes[0] === 0x7f && bytes[1] === 0x45 && bytes[2] === 0x4c && bytes[3] === 0x46) return bytes;
        }
      } catch (_) { /* optional local file */ }
      // Pinned version of the NumWorks uploader's EADK object, hosted separately
      // instead of adding an unlicensed binary blob to NumPlay source control.
      const url = 'https://cdn.jsdelivr.net/gh/elouangrimm/numflash@9beaf363127000e4779c8fd5ee20886805edce0b/js/vendor/eadk-o.js';
      const module = await import(url);
      if (!(module.EADK_O instanceof Uint8Array)) throw new Error('Unable to load the NumWorks app runtime.');
      return module.EADK_O;
    })().catch(e => { eadkRuntime = null; throw new Error('NumWorks app runtime unavailable: ' + (e.message || e)); });
    return eadkRuntime;
  }
  function finalLinkScript(region) {
    const { flashStart: fs, flashLength: fl, ramStart: rs, ramLength: rl, trampolineStart: ts } = region;
    for (const n of [fs, fl, rs, rl, ts]) if (!Number.isSafeInteger(n) || n <= 0) throw new Error('Invalid hardware memory layout.');
    return `MEMORY { FLASH (rx) : ORIGIN = ${fs}, LENGTH = ${fl}\n RAM (rwx) : ORIGIN = ${rs}, LENGTH = ${rl} }
ENTRY(_start);
_userland_trampoline_address = ${ts};
SECTIONS {
 .eadk_app_info ORIGIN(FLASH) : {
  LONG(0xDEC0BEBA); KEEP(*(.rodata.eadk_api_level))
  LONG(ADDR(.rodata.eadk_app_name) - ORIGIN(FLASH));
  LONG(SIZEOF(.rodata.eadk_app_icon));
  LONG(ADDR(.rodata.eadk_app_icon) - ORIGIN(FLASH));
  LONG(_start - ORIGIN(FLASH));
  LONG(_eadk_app_end - ORIGIN(FLASH)); LONG(0xDEC0BEBA);
 } >FLASH
 .rodata.eadk_app_name : { KEEP(*(.rodata.eadk_app_name)) } >FLASH
 .rodata.eadk_app_icon : { KEEP(*(.rodata.eadk_app_icon)) } >FLASH
 .text : { . = ALIGN(4); *(.text) *(.text.*) } >FLASH
 .rodata : { *(.rodata) *(.rodata.*) _eadk_app_end = .; } >FLASH
 .data : { . = ALIGN(4); _data_section_start_flash = LOADADDR(.data);
  _data_section_start_ram = .; *(.data) *(.data.*)
  _data_section_end_ram = .; } >RAM AT >FLASH
 .bss : { . = ALIGN(4); _bss_section_start_ram = .;
  *(.bss) *(.bss.*) _bss_section_end_ram = .; } >RAM
 .heap : { _heap_start = .; . = (ORIGIN(RAM) + LENGTH(RAM)); _heap_end = .; } >RAM
}
ASSERT(SIZEOF(.eadk_app_info) == 32, "Missing EADK API level");
ASSERT(SIZEOF(.rodata.eadk_app_name) > 0, "Missing app name");
ASSERT(SIZEOF(.rodata.eadk_app_icon) > 0, "Missing app icon");
`;
  }
  async function linkForCalculator(nwa, region) {
    if (!(nwa instanceof Uint8Array) || nwa.length < 64) throw new Error('Invalid NumPlay.nwa');
    const [t, eadk] = await Promise.all([tools(), getEadkRuntime()]);
    const elf = await run(t.ld, ['--gc-sections', '--allow-multiple-definition',
      '-T', 'install.ld', 'eadk.o', 'app.nwa', '-o', 'installed.elf'], {
      'install.ld': finalLinkScript(region), 'eadk.o': eadk, 'app.nwa': nwa,
    });
    const obj = await run(t.objcopy,
      ['--output-target', 'binary', 'installed.elf', 'installed.bin'],
      { 'installed.elf': elf.FS.readFile('installed.elf') });
    const raw = obj.FS.readFile('installed.bin');
    const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
    if (raw.length < 64 || view.getUint32(0, true) !== 0xDEC0BEBA || view.getUint32(28, true) !== 0xDEC0BEBA)
      throw new Error('Final app did not pass the NumWorks header check. No flash write started.');
    if (raw.length > region.flashLength) throw new Error('App does not fit the connected calculator firmware.');
    const rounded = Math.ceil(raw.length / 65536) * 65536;
    const withEnd = rounded < region.flashLength ? rounded + 65536 : rounded;
    const image = new Uint8Array(withEnd).fill(0xff);
    image.set(raw);
    return { image, appBytes: raw.length, flashStart: region.flashStart };
  }
  window.NumPlayStudioLink = linkForCalculator;

  function downloadBytes(data, filename) {
    const url = URL.createObjectURL(new Blob([data], { type: 'application/octet-stream' }));
    const a = node('a'); a.href = url; a.download = filename;
    document.body.append(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 3000);
  }
  async function doBuild() {
    if (busy || !kit || !pickedInOrder().length || used() > limit() || pickedInOrder().some(standaloneOnly)) return;
    if (retro && state.picked.has(retro.id) && !state.roms.length) { popup('Add at least one Retro-Go ROM first.'); return; }
    busy = true; render();
    try {
      const out = await build();
      downloadBytes(out.data, out.filename);
      $('install-dialog').showModal();
    } catch (err) {
      console.error(err);
      popup(err.message || 'Build failed. Try again.');
    } finally { busy = false; render(); }
  }
  // ---------- game READMEs ----------
  // The README content is loaded only when an info button is pressed. Prefer
  // files in this repo (works with the local preview), then upstream GitHub.
  // README HTML is filtered through an allowlist before reaching the DOM.
  const readmeCache = new Map();
  let readmeRequest = 0;
  const readmeRepo = 'https://github.com/Mason363/NumPlay/blob/main/';
  const readmeRaw = 'https://raw.githubusercontent.com/Mason363/NumPlay/main/';
  const escapeReadme = (v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  function readmeInline(input) {
    let str = String(input), holds = [];
    const hold = (value) => { const token = `\uE000${holds.length}\uE001`; holds.push(value); return token; };
    // Protect literal HTML tags so the sanitizer can allow only safe ones.
    str = str.replace(/<\/?[a-z][^>]*>/gi, (tag) => hold(tag));
    str = str.replace(/`([^`]+)`/g, (_, content) => hold('<code>' + escapeReadme(content) + '</code>'));
    str = escapeReadme(str);
    str = str.replace(/!\[([^\]]*)\]\(([^\s)]+)(?:\s+"([^"]*)")?\)/g,
      (_, alt, src, title) => '<img src="' + escapeReadme(src) + '" alt="' + escapeReadme(alt) + '"' +
        (title ? ' title="' + escapeReadme(title) + '"' : '') + '>');
    str = str.replace(/\[([^\]]+)\]\(([^\s)]+)(?:\s+"([^"]*)")?\)/g,
      (_, label, href, title) => '<a href="' + escapeReadme(href) + '"' +
        (title ? ' title="' + escapeReadme(title) + '"' : '') + '>' + label + '</a>');
    str = str.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    str = str.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
    str = str.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
    str = str.replace(/(?<!_)_([^_\n]+)_(?!_)/g, '<em>$1</em>');
    str = str.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    return str.replace(/\uE000(\d+)\uE001/g, (_, index) => holds[+index]);
  }
  function readmeMarkdown(source) {
    const lines = String(source).replace(/\r\n?/g, '\n').split('\n'), parts = [];
    const isSpecial = (s) => /^\s*(?:#{1,6}\s|```|~~~|[-*+]\s|\d+\.\s|>\s?|<\/?(?:table|p|div|details|h[1-6]|ul|ol|section|pre|img)\b|\|)/i.test(s) || /^\s*([-*_]\s*){3,}$/.test(s);
    let i = 0;
    while (i < lines.length) {
      const raw = lines[i], line = raw.trim();
      if (!line) { i++; continue; }
      let match;
      if ((match = line.match(/^(```+|~~~+)(.*)$/))) {
        const fence = match[1][0], lang = match[2].trim(); i++;
        const code = [];
        while (i < lines.length && !new RegExp('^\\s*' + fence + '{3,}').test(lines[i])) code.push(lines[i++]);
        if (i < lines.length) i++;
        parts.push('<pre><code' + (lang ? ' class="language-' + escapeReadme(lang.replace(/[^\w-]/g, '')) + '"' : '') + '>' + escapeReadme(code.join('\n')) + '</code></pre>');
        continue;
      }
      if ((match = line.match(/^(#{1,6})\s+(.+?)\s*#*$/))) {
        const level = match[1].length;
        parts.push('<h' + level + '>' + readmeInline(match[2]) + '</h' + level + '>'); i++; continue;
      }
      if (/^([-*_]\s*){3,}$/.test(line)) { parts.push('<hr>'); i++; continue; }
      if (line.startsWith('|') && i + 1 < lines.length && /^\s*\|?[\s:|\-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        const split = (s) => s.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map((s) => s.trim().replace(/\\\|/g, '|'));
        const head = split(lines[i]); i += 2;
        let html = '<table><thead><tr>' + head.map((x) => '<th>' + readmeInline(x) + '</th>').join('') + '</tr></thead><tbody>';
        while (i < lines.length && lines[i].trim().startsWith('|')) {
          html += '<tr>' + split(lines[i]).map((x) => '<td>' + readmeInline(x) + '</td>').join('') + '</tr>'; i++;
        }
        parts.push(html + '</tbody></table>'); continue;
      }
      if (/^>\s?/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''));
        parts.push('<blockquote>' + readmeMarkdown(quote.join('\n')) + '</blockquote>'); continue;
      }
      if (/^\s*(?:[-*+]\s+|\d+\.\s+)/.test(raw)) {
        const numbered = /^\s*\d+\.\s+/.test(raw), listTag = numbered ? 'ol' : 'ul';
        const rows = [];
        while (i < lines.length) {
          const item = lines[i].match(/^\s*(?:[-*+]\s+|\d+\.\s+)(.*)$/);
          if (!item) break;
          i++; const block = [item[1]];
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*(?:[-*+]\s+|\d+\.\s+)/.test(lines[i])) {
            block.push(lines[i++].trim());
          }
          rows.push('<li>' + block.map(readmeInline).join('<br>') + '</li>');
        }
        parts.push('<' + listTag + '>' + rows.join('') + '</' + listTag + '>'); continue;
      }
      // GitHub READMEs frequently use HTML for centered images and tables.
      if (/^<\/?(?:table|p|div|details|summary|h[1-6]|ul|ol|section|pre)\b/i.test(line)) {
        const block = [], start = line.match(/^<([a-z][a-z0-9]*)\b/i);
        const tag = start && start[1].toLowerCase();
        while (i < lines.length) {
          const next = lines[i++]; block.push(next);
          if (tag && new RegExp('</' + tag + '\\s*>', 'i').test(next)) break;
          if (!tag || (i < lines.length && !lines[i].trim())) break;
        }
        parts.push(block.join('\n')); continue;
      }
      if (/^<img\b/i.test(line)) { parts.push('<p>' + raw + '</p>'); i++; continue; }
      const paragraph = [raw]; i++;
      while (i < lines.length && lines[i].trim() && !isSpecial(lines[i].trim())) paragraph.push(lines[i++]);
      parts.push('<p>' + readmeInline(paragraph.join(' ')) + '</p>');
    }
    return parts.join('\n');
  }
  function readmeSafeUrl(input, kind, gid) {
    if (!input || /^\s*(?:javascript|data|vbscript|file):/i.test(input)) return null;
    if (input.startsWith('#')) return kind === 'link' ? input : null;
    try {
      const base = (kind === 'image' ? readmeRaw : readmeRepo) + 'games/' + encodeURIComponent(gid) + '/';
      const url = new URL(input, base);
      return ['https:', 'http:'].includes(url.protocol) || (kind === 'link' && url.protocol === 'mailto:') ? url.href : null;
    } catch (_) { return null; }
  }
  function sanitizedReadme(markup, id) {
    const unsafe = new Set(['script','style','iframe','object','embed','svg','math','form','input','button','meta','link','base','video','audio','source','template']);
    const allowed = new Set(['p','span','div','section','h1','h2','h3','h4','h5','h6','strong','b','em','i','u','del','s','br','hr','blockquote','pre','code','ul','ol','li','a','img','table','thead','tbody','tr','th','td','sub','sup','small','details','summary','kbd']);
    const parser = new DOMParser(), parsed = parser.parseFromString('<main>' + markup + '</main>', 'text/html');
    const target = document.createDocumentFragment();
    const clean = (source, parent) => {
      for (const item of source.childNodes) {
        if (item.nodeType === Node.TEXT_NODE) { parent.append(document.createTextNode(item.textContent)); continue; }
        if (item.nodeType !== Node.ELEMENT_NODE) continue;
        const tag = item.localName.toLowerCase();
        if (unsafe.has(tag)) continue;
        if (!allowed.has(tag)) { clean(item, parent); continue; }
        const out = document.createElement(tag);
        if (tag === 'a') {
          const href = readmeSafeUrl(item.getAttribute('href'), 'link', id);
          if (href) {
            out.href = href;
            if (!href.startsWith('#')) { out.target = '_blank'; out.rel = 'noopener noreferrer'; }
          }
        }
        if (tag === 'img') {
          const src = readmeSafeUrl(item.getAttribute('src'), 'image', id);
          if (!src) continue;
          out.src = src; out.loading = 'lazy'; out.alt = item.getAttribute('alt') || '';
          const width = Number(item.getAttribute('width'));
          if (Number.isFinite(width) && width > 0 && width < 1200) out.width = width;
        }
        if (tag === 'td' || tag === 'th') {
          for (const attr of ['colspan','rowspan']) {
            const value = Number(item.getAttribute(attr));
            if (Number.isInteger(value) && value >= 1 && value <= 12) out.setAttribute(attr, value);
          }
        }
        clean(item, out); parent.append(out);
      }
    };
    clean(parsed.body.firstElementChild, target);
    const headings = target.querySelectorAll('h1,h2,h3,h4,h5,h6');
    const slugCounts = new Map();
    for (const h of headings) {
      const slug = h.textContent.toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-');
      const count = slugCounts.get(slug) || 0;
      slugCounts.set(slug, count + 1);
      h.id = slug + (count ? '-' + count : '');
    }
    return target;
  }
  async function gameReadmeText(id) {
    if (!readmeCache.has(id)) readmeCache.set(id, (async () => {
      const local = '../games/' + encodeURIComponent(id) + '/README.md';
      const remote = readmeRaw + 'games/' + encodeURIComponent(id) + '/README.md';
      for (const location of [local, remote]) {
        try {
          const response = await fetch(location);
          if (!response.ok) continue;
          const content = await response.text();
          if (/^\s*(?:<!doctype html|<html\b)/i.test(content)) continue;
          if (content.trim()) return content;
        } catch (_) { /* Local files may not be deployed; fall back to GitHub. */ }
      }
      throw new Error('README could not be loaded');
    })().catch((e) => { readmeCache.delete(id); throw e; }));
    return readmeCache.get(id);
  }
  async function openGameReadme(g) {
    const dialog = $('readme-dialog'), content = $('readme-content');
    const request = ++readmeRequest;
    $('readme-title').textContent = g.title;
    $('readme-subtitle').textContent = fmt(g.flash) + ' · Game README';
    const icon = $('readme-icon'); icon.src = iconPath(g);
    icon.onerror = () => { icon.onerror = null; icon.src = KIT + 'variants/NumPlay.png'; };
    $('readme-source').href = readmeRepo + 'games/' + encodeURIComponent(g.id) + '/README.md';
    content.textContent = 'Loading README…';
    content.setAttribute('aria-busy', 'true');
    if (!dialog.open) dialog.showModal();
    dialog.scrollTop = 0;
    try {
      const text = await gameReadmeText(g.id);
      if (request !== readmeRequest || !dialog.open) return;
      content.replaceChildren(sanitizedReadme(readmeMarkdown(text), g.id));
    } catch (e) {
      if (request === readmeRequest && dialog.open)
        content.textContent = 'Could not load this README. You can still open the original on GitHub using the link below.';
    } finally {
      if (request === readmeRequest) content.removeAttribute('aria-busy');
    }
  }

  // ---------- events ----------
  function events() {
    $('search').addEventListener('input', (e) => { state.search = e.target.value.trim(); renderGames(); });
    for (const filter of ['all', 'selected']) $('filter-' + filter).addEventListener('click', () => {
      state.filter = filter; renderGames();
    });
    $('sort').addEventListener('change', (e) => { state.sort = e.target.value; renderGames(); });
    $('clear-games').addEventListener('click', () => { state.picked.clear(); render(); });
    for (const id of ['build-button', 'mobile-download']) $(id).addEventListener('click', doBuild);
    if (window.NumPlaySaveVault) window.NumPlaySaveVault.bindInstall(() => build(), () => pickedInOrder(), () => ({ ...space() }));
    $('open-settings').addEventListener('click', () => { syncSettings(); $('settings-dialog').showModal(); });
    $('settings-done').addEventListener('click', () => $('settings-dialog').close());
    $('dark-mode-setting').addEventListener('change', (e) => {
      const theme = e.target.checked ? 'dark' : 'light';
      document.documentElement.dataset.theme = theme;
      store('numplay-studio-theme', theme);
    });
    $('space-setting').addEventListener('change', (e) => setCapacity(e.target.value));
    $('language-setting').addEventListener('change', (e) => setLanguage(e.target.value));
    $('app-name-setting').addEventListener('input', (e) => { state.app.name = e.target.value.slice(0, 20); updateSaved(); });
    $('icon-setting').addEventListener('change', (e) => {
      const prev = state.app.icon;
      state.app.icon = e.target.value;
      if (PRESETS[state.app.icon] && state.app.name === (PRESETS[prev]?.[1] ?? 'NumPlay')) {
        state.app.name = PRESETS[state.app.icon][1];
      }
      if (state.app.icon === 'NumPlay-Matrices') state.app.open = 'matrices';
      syncSettings(); updateSaved();
    });
    $('custom-icon').addEventListener('change', async (e) => {
      const file = e.target.files[0]; e.target.value = ''; if (!file) return;
      try {
        custom = await iconFrom(file);
        const encoded = btoa(Array.from(custom.obj, (x) => String.fromCharCode(x)).join(''));
        store('numplay-icon', JSON.stringify({ url: custom.url, obj: encoded }));
        state.app.icon = 'custom'; settingsUi(); updateSaved();
      } catch (err) { popup('Could not read that image. Try a PNG or JPEG.'); }
    });
    $('open-setting').addEventListener('change', (e) => { state.app.open = e.target.value; syncSettings(); updateSaved(); });
    $('secret-setting').addEventListener('change', (e) => { state.app.secret = Number(e.target.value); updateSaved(); });
    $('hint-setting').addEventListener('change', (e) => { state.app.hint = e.target.checked; updateSaved(); });
    $('install-help').addEventListener('click', () => $('install-dialog').showModal());
    // README anchor links must scroll inside their dialog, not replace Studio's
    // game-selection URL hash (which is also used to save a build).
    $('readme-content').addEventListener('click', (e) => {
      const link = e.target.closest('a[href^="#"]');
      if (!link) return;
      e.preventDefault();
      const anchor = decodeURIComponent(link.getAttribute('href').slice(1));
      const heading = [...$('readme-content').querySelectorAll('[id]')].find((node) => node.id === anchor);
      if (heading) heading.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    $('rom-files').addEventListener('change', async (e) => {
      const files = [...e.target.files]; e.target.value = ''; await addRoms(files);
    });
    const drop = $('rom-drop');
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('dragging'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragging'));
    drop.addEventListener('drop', async (e) => {
      e.preventDefault(); drop.classList.remove('dragging'); await addRoms([...e.dataTransfer.files]);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) &&
          !document.querySelector('dialog[open]')) { e.preventDefault(); $('search').focus(); }
    });
    addEventListener('hashchange', async () => {
      const q = new URLSearchParams(location.hash.slice(1));
      const lang = q.get('lang');
      try { if (lang && (kit.langs || []).includes(lang)) await loadLanguage(lang); } catch (_) { /* fallback */ }
      restore(); settingsUi(); render();
    });
  }
  async function start() {
    try {
      const response = await fetch(KIT + 'kit.json');
      if (!response.ok) throw new Error('HTTP ' + response.status + ' loading kit/kit.json');
      kit = await response.json();
      kit.langs = kit.langs || ['en'];
      kit.byId = Object.fromEntries(kit.games.map((g) => [g.id, g]));
      Object.assign(byId, kit.byId); kits.en = kit;
      retro = kit.games.find((g) => g.roms) || null;
      try {
        const saved = store('numplay-icon');
        if (saved) { const j = JSON.parse(saved); custom = { url: j.url, obj: Uint8Array.from(atob(j.obj), (s) => s.charCodeAt(0)) }; }
      } catch (_) { custom = null; }
      const q = new URLSearchParams(location.hash.slice(1) || store('numplay-studio2') || '');
      if (q.get('lang') && kit.langs.includes(q.get('lang'))) {
        try { await loadLanguage(q.get('lang')); } catch (_) { popup('Could not load your saved language.'); }
      }
      restore();
      settingsUi(); events(); render();
      if (window.NumPlaySaveVault) window.NumPlaySaveVault.init(kit).catch((error) => {
        console.error('Vault initialization failed:', error);
      });
      if (retro) {
        const allowed = Object.keys(EXT).filter((s) => (retro.systems || [1, 2, 3]).includes(EXT[s]));
        $('rom-files').accept = allowed.map((s) => '.' + s).join(',');
        $('rom-types').textContent = '(' + allowed.map((s) => '.' + s).join(', ') + ')';
      }
    } catch (e) {
      console.error(e);
      $('game-count').textContent = 'Could not load the game kit';
      $('build-note').textContent = 'The site needs the generated kit/ files. Use the deployed site or run the existing kit build.';
      popup('Game kit missing. Read the setup instructions.');
    }
  }
  start();
})();
