/* NumPlay Studio Save Vault: local IndexedDB backups, imports, and export.
 * USB backup and experimental direct installation are supported.
 * Keep previous snapshots on every install attempt.
 */
'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const DB_NAME = 'numplay-studio-save-vault';
  const DB_VERSION = 2;
  const MAX_SCRIPT_BYTES = 600 * 1024;
  const safeName = (name) => typeof name === 'string' && /^[a-z0-9_.-]{1,90}\.py$/i.test(name) && !name.includes('..');
  const saveLinePattern = /^#(?:>|=)[a-z0-9_.-]+:(?:[0-9]+:)?[A-Za-z0-9+/=]+\s*$/gim;
  const fmtDate = (t) => t ? new Date(t).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Not available';
  let db = null, kit = null, gameMap = new Map(), stored = [], ready = false;
  let launchDownload = null, currentSelection = () => [];
  let chosenCapacity = () => null;

  const make = (tag, className, txt) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (txt !== undefined) el.textContent = txt;
    return el;
  };
  const status = (text) => { $('vault-status').textContent = text; };
  const notifier = (message) => {
    const toast = $('toast');
    if (toast) { toast.textContent = message; toast.classList.add('show'); setTimeout(() => toast.classList.remove('show'), 4000); }
  };

  function request(store, mode, fn) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, mode);
      const obj = tx.objectStore(Array.isArray(store) ? store[0] : store);
      let result;
      try { result = fn(obj, tx); } catch (e) { tx.abort(); reject(e); return; }
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error || new Error('Local backup database error'));
      tx.onabort = () => reject(tx.error || new Error('Local backup was cancelled'));
    });
  }
  function openDb() {
    return new Promise((resolve, reject) => {
      if (!window.indexedDB) { reject(new Error('IndexedDB is unavailable in this browser')); return; }
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const d = req.result;
        if (!d.objectStoreNames.contains('files')) d.createObjectStore('files', { keyPath: 'name' });
        if (!d.objectStoreNames.contains('history')) d.createObjectStore('history', { keyPath: 'id', autoIncrement: true });
        if (!d.objectStoreNames.contains('install_backups')) d.createObjectStore('install_backups', { keyPath: 'id' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('Could not open local save vault'));
      req.onblocked = () => reject(new Error('Close other Studio tabs and try again'));
    });
  }
  async function allFiles() {
    return new Promise((resolve, reject) => {
      const tx = db.transaction('files', 'readonly');
      const req = tx.objectStore('files').getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }
  async function history() {
    return new Promise((resolve, reject) => {
      const tx = db.transaction('history', 'readonly');
      const req = tx.objectStore('history').getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }
  // A freshly retrieved numplay_saves.py can omit an uninstalled game's data.
  // Merge its records rather than discarding archival copies from the vault.
  function preserveMissingSharedRecords(previous, incoming) {
    if (!previous) return incoming;
    const lineRx = /^#(?:>|=)([a-z0-9_.-]+):(?:[0-9]+:)?[A-Za-z0-9+/=]+\s*$/i;
    const present = new Set(incoming.split(/\r?\n/).map((line) => line.match(lineRx)?.[1]).filter(Boolean));
    const keep = [];
    for (const line of previous.split(/\r?\n/)) {
      const name = line.match(lineRx)?.[1];
      if (name && !present.has(name)) { keep.push(line); present.add(name); }
    }
    return keep.length ? incoming.replace(/\s*$/, '') + '\n' + keep.join('\n') + '\n' : incoming;
  }
  async function addCopy(name, content, source) {
    if (!safeName(name)) throw new Error('Not a safe Python script filename: ' + name);
    if (typeof content !== 'string' || !content.trim() || new Blob([content]).size > MAX_SCRIPT_BYTES)
      throw new Error(name + ': script is empty or too large');
    if (!content.match(saveLinePattern)) throw new Error(name + ': no NumPlay backup records found');
    const old = stored.find((f) => f.name === name);
    if (name === 'numplay_saves.py') content = preserveMissingSharedRecords(old?.content, content);
    if (old?.content === content) return 'unchanged';
    const now = Date.now();
    await request(['files', 'history'], 'readwrite', (_unused, tx) => {
      if (old) tx.objectStore('history').add({ name: old.name, content: old.content, savedAt: old.savedAt, source: old.source });
      tx.objectStore('files').put({ name, content, savedAt: now, source });
    });
    stored = await allFiles();
    return old ? 'updated' : 'added';
  }
  async function loadGameMap() {
    // The live NumPlay repository maintains the authoritative list of game save record names.
    const paths = ['../games/games.json', 'https://raw.githubusercontent.com/Mason363/NumPlay/main/games/games.json'];
    for (const path of paths) {
      try {
        const resp = await fetch(path, { cache: 'no-store' });
        if (!resp.ok) continue;
        const obj = await resp.json();
        if (!Array.isArray(obj)) continue;
        for (const g of obj) if (g.id && Array.isArray(g.records)) gameMap.set(g.id, g.records);
        if (gameMap.size) return;
      } catch (_) { /* Working offline: names of specific scripts still identify games. */ }
    }
  }
  function gameByRecord(rec) {
    for (const [id, records] of gameMap.entries()) {
      if (records.some((test) => test.endsWith('*') ? rec.startsWith(test.slice(0, -1)) : test === rec)) return id;
    }
    // Fallback covers common save names when games.json is not reachable.
    return (kit?.games || []).find((g) => rec.toLowerCase().startsWith(g.id.toLowerCase() + '.'))?.id || null;
  }
  function ownedGames(file) {
    const ids = new Set();
    if (file.name !== 'numplay_saves.py') {
      for (const g of kit?.games || []) {
        if (file.name.toLowerCase() === g.id.toLowerCase() + '_saves.py' || (gameMap.get(g.id) || []).includes(file.name)) ids.add(g.id);
      }
    }
    const entries = [...file.content.matchAll(/^#(?:>|=)([a-z0-9_.-]+):/gim)];
    for (const match of entries) { const id = gameByRecord(match[1]); if (id) ids.add(id); }
    return { ids: [...ids], entries: entries.length };
  }
  function groups() {
    const groups = new Map();
    for (const file of stored) {
      const { ids, entries } = ownedGames(file);
      for (const id of ids.length ? ids : ['unknown']) {
        if (!groups.has(id)) groups.set(id, { id, files: new Set(), entries: 0, updated: 0 });
        const g = groups.get(id);
        g.files.add(file.name);
        g.entries += entries;
        g.updated = Math.max(g.updated, file.savedAt);
      }
    }
    return [...groups.values()].sort((a, b) => (b.updated - a.updated) || a.id.localeCompare(b.id));
  }
  function view(which) {
    const backed = which === 'backups';
    $('select-view').hidden = backed;
    $('backups-view').hidden = !backed;
    $('view-select').classList.toggle('active', !backed);
    $('view-backups').classList.toggle('active', backed);
    $('view-select').setAttribute('aria-pressed', String(!backed));
    $('view-backups').setAttribute('aria-pressed', String(backed));
    $('mobile-dock').hidden = backed;
    document.body.classList.toggle('showing-backups', backed);
    if (backed) refresh();
  }
  function title(id) { return (kit?.games || []).find((g) => g.id === id)?.title || (id === 'unknown' ? 'Unmatched save records' : id); }
  function render() {
    if (!ready) return;
    const list = $('vault-list'); list.replaceChildren();
    const gs = groups();
    $('vault-tab-count').textContent = gs.length ? String(gs.length) : '';
    $('vault-export').disabled = !stored.length;
    status(stored.length ? `${stored.length} saved Python script${stored.length === 1 ? '' : 's'} · ${gs.length} game group${gs.length === 1 ? '' : 's'}` : 'No saves in this browser yet. Import your calculator’s Python backup scripts to get started.');
    if (!gs.length) {
      const empty = make('div', 'vault-empty');
      empty.append(make('h2', '', 'Your save library is empty'), make('p', 'muted', 'When you import game backup scripts, your saves will appear here. They remain stored even if the game is no longer selected.'));
      const btn = make('button', 'button button-outline', 'Import saved Python scripts');
      btn.type = 'button'; btn.addEventListener('click', () => $('vault-import-file').click());
      empty.append(btn); list.append(empty); return;
    }
    for (const g of gs) {
      const row = make('div', 'vault-item');
      const icon = make('div', 'vault-icon');
      const image = (kit?.games || []).find((it) => it.id === g.id);
      if (image) {
        const img = make('img'); img.src = image.icon ? 'kit/icons/' + g.id + '.png' : 'kit/variants/NumPlay.png'; img.alt = '';
        img.addEventListener('error', () => { img.src = 'kit/variants/NumPlay.png'; }, { once: true });
        icon.append(img);
      } else icon.textContent = '📁';
      const details = make('div', 'vault-details');
      details.append(make('strong', '', title(g.id)), make('p', 'muted', [...g.files].join(' · ')));
      const meta = make('div', 'vault-meta');
      meta.append(make('strong', '', 'Backed up'), make('span', '', fmtDate(g.updated)));
      row.append(icon, details, meta); list.append(row);
    }
  }
  function refresh() { if (!ready) return; render(); }
  async function ingestFiles(files) {
    if (!ready) { status('The save library is not ready.'); return; }
    let added = 0, updated = 0, unchanged = 0, skipped = [];
    status('Importing saves…');
    for (const file of files) {
      try {
        const text = await file.text();
        if (file.name.toLowerCase().endsWith('.json')) {
          const backup = JSON.parse(text);
          if (backup.type !== 'numplay-save-vault' || backup.version !== 1 || !Array.isArray(backup.files))
            throw new Error('Not a NumPlay Save Vault export');
          for (const rec of backup.files) {
            const result = await addCopy(rec.name, rec.content, 'Imported vault');
            if (result === 'added') added++; else if (result === 'updated') updated++; else unchanged++;
          }
          // Preserve the exported revision history too, with deduplication.
          if (Array.isArray(backup.previousVersions)) {
            const seen = new Set((await history()).map((r) => r.name + '\0' + r.content + '\0' + r.savedAt));
            for (const previous of backup.previousVersions) {
              if (!safeName(previous?.name) || typeof previous.content !== 'string' ||
                  !previous.content.match(saveLinePattern) || new Blob([previous.content]).size > MAX_SCRIPT_BYTES) continue;
              const marker = previous.name + '\0' + previous.content + '\0' + previous.savedAt;
              if (seen.has(marker)) continue;
              await request('history', 'readwrite', (store) => {
                store.add({ name: previous.name, content: previous.content,
                  savedAt: Number(previous.savedAt) || Date.now(), source: previous.source || 'Imported vault revision' });
              });
              seen.add(marker);
            }
          }
        } else {
          const result = await addCopy(file.name, text, 'Imported Python script');
          if (result === 'added') added++; else if (result === 'updated') updated++; else unchanged++;
        }
      } catch (e) { skipped.push(file.name + ': ' + (e?.message || String(e))); }
    }
    refresh();
    const message = `${added} new, ${updated} updated, ${unchanged} unchanged` + (skipped.length ? `; ${skipped.length} skipped` : '');
    notifier(message);
    if (skipped.length) status('Import finished, but: ' + skipped.join(' | ').slice(0, 250));
  }
  async function exportVault() {
    if (!ready || !stored.length) { notifier('No saves are backed up in this browser yet.'); return; }
    const snapshots = await history();
    const payload = { type: 'numplay-save-vault', version: 1, exportedAt: new Date().toISOString(), files: stored, previousVersions: snapshots };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = make('a'); a.href = url; a.download = 'NumPlay-Save-Vault-' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.append(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 3000);
  }

  // ----- Prepare an install package: all processing is local, USB reads ONLY -----
  // Small ZIP implementation, no dependencies. Entries are uncompressed (method 0).
  // The package intentionally includes a full independent vault snapshot so an
  // installation never depends only on browser site storage surviving.
  const u8 = (n) => n & 255;
  const utf8 = new TextEncoder();
  const crcTable = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (c & 1 ? 0xEDB88320 : 0);
    crcTable[n] = c >>> 0;
  }
  function crc32(bytes) {
    let c = 0xffffffff;
    for (const b of bytes) c = (c >>> 8) ^ crcTable[(c ^ b) & 255];
    return (c ^ 0xffffffff) >>> 0;
  }
  function packageZip(entries) {
    let offset = 0;
    const local = [], central = [];
    for (const [name, input] of entries) {
      if (!/^[a-z0-9_./-]{1,180}$/i.test(name) || name.includes('..') || name.startsWith('/'))
        throw new Error('Unsafe archive filename');
      const filename = utf8.encode(name);
      const data = typeof input === 'string' ? utf8.encode(input) : input;
      if (!(data instanceof Uint8Array)) throw new Error('Invalid ZIP input');
      const checksum = crc32(data);
      const lh = new Uint8Array(30 + filename.length), ld = new DataView(lh.buffer);
      ld.setUint32(0, 0x04034b50, true);
      ld.setUint16(4, 20, true); ld.setUint16(6, 0x800, true);
      ld.setUint16(8, 0, true); ld.setUint32(14, checksum, true);
      ld.setUint32(18, data.length, true); ld.setUint32(22, data.length, true);
      ld.setUint16(26, filename.length, true); lh.set(filename, 30);
      local.push(lh, data);
      const ch = new Uint8Array(46 + filename.length), cd = new DataView(ch.buffer);
      cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 20, true); cd.setUint16(6, 20, true);
      cd.setUint16(8, 0x800, true); cd.setUint32(16, checksum, true);
      cd.setUint32(20, data.length, true); cd.setUint32(24, data.length, true);
      cd.setUint16(28, filename.length, true); cd.setUint32(42, offset, true); ch.set(filename, 46);
      central.push(ch); offset += lh.length + data.length;
    }
    const centralSize = central.reduce((n, x) => n + x.length, 0);
    const footer = new Uint8Array(22), f = new DataView(footer.buffer);
    f.setUint32(0, 0x06054b50, true);
    f.setUint16(8, entries.length, true); f.setUint16(10, entries.length, true);
    f.setUint32(12, centralSize, true); f.setUint32(16, offset, true);
    return new Blob([...local, ...central, footer], { type: 'application/zip' });
  }
  function saveDownload(blob, name) {
    const url = URL.createObjectURL(blob), a = make('a');
    a.href = url; a.download = name; document.body.append(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 30000);
  }
  async function usbBackupIntoVault(onProgress) {
    if (!ready) throw new Error('The browser save vault is not available.');
    if (!window.NumPlayUsbRead) throw new Error('USB module missing. Refresh the site.');
    const result = await window.NumPlayUsbRead.requestAndRead(onProgress);
    // No automatic deletion: removed games do not overwrite vault history.
    if (!result.files.length) throw new Error('No NumPlay save scripts were detected. Nothing changed in the vault.');
    const tally = { added: 0, updated: 0, unchanged: 0 }, errors = [];
    for (const f of result.files) {
      try { tally[await addCopy(f.name, f.content, 'USB backup · ' + result.model)]++; }
      catch (err) { errors.push(f.name + ': ' + String(err?.message || err)); }
    }
    refresh();
    // Partial backups should not be treated as verified. The successfully
    // imported copies remain stored, but stop this installation preflight.
    if (errors.length) throw new Error('Some saves could not be stored: ' + errors.join('; ').slice(0, 350));
    return { model: result.model, count: result.files.length, tally };
  }
  function scriptsForSelection(selectedIds) {
    const ids = new Set(selectedIds);
    const scripts = [];
    for (const file of stored) {
      const membership = ownedGames(file).ids;
      if (file.name === 'numplay_saves.py' || membership.some((id) => ids.has(id)))
        scripts.push(file);
    }
    return scripts;
  }
  // ----- Real one-click backup / install progress UI -----
  let usbBusy = false, retryScripts = null, retryDevice = null;
  // A physical unplug/replug is required on the user's N0120 to expose fresh
  // USB storage after the installation reboot. Never mark completion beforehand.
  let awaitingPhysicalReplug = false;
  let replugAcknowledged = false;

  function clearHandoff() {
    const panel = $('vault-install-replug');
    if (panel) { panel.hidden = true; panel.classList.remove('ready'); }
    $('vault-install-unplugged').hidden = false;
    $('vault-install-unplugged').disabled = false;
    $('vault-replug-step-1').classList.remove('finished');
    $('vault-replug-step-2').classList.remove('finished');
    $('vault-replug-step-3').classList.add('not-yet');
    $('vault-replug-step-4').classList.add('not-yet');
    replugAcknowledged = false;
  }
  function showHandoff() {
    clearHandoff();
    // Firmware caveats were displayed during the pre-flash phase. At 90%,
    // give the entire dialog to the cable handoff so it's readable on phones.
    $('vault-install-dialog').querySelector('.work-intro').hidden = true;
    $('vault-install-nwagra-note').hidden = true;
    $('vault-install-dialog').querySelector('button.button-wide.work-close').hidden = true;
    $('vault-install-dialog').querySelector('.work-bottom-note').hidden = true;
    $('vault-install-replug').hidden = false;
    $('vault-replug-help').textContent = 'The app is already installed. This step restores your saves. Do not reinstall NumPlay.';
    $('vault-install-retry').hidden = true;
  }
  function acknowledgeUnplug() {
    if (!awaitingPhysicalReplug || usbBusy || replugAcknowledged) return;
    replugAcknowledged = true;
    $('vault-install-replug').classList.add('ready');
    $('vault-replug-step-1').classList.add('finished');
    $('vault-replug-step-2').classList.add('finished');
    $('vault-replug-step-3').classList.remove('not-yet');
    $('vault-replug-step-4').classList.remove('not-yet');
    $('vault-install-unplugged').hidden = true;
    $('vault-replug-help').textContent = 'IMPORTANT: Fully restart the calculator, wait for the language selection screen, and select your language BEFORE plugging USB back in. Once connected, Studio will restore the backed-up saves. If nothing happens, click Reconnect & finish saves below.';
    workUpdate('vault-install', 'Step 3 of 4: Finish restarting and select your language. Only then plug USB back in.', 90);
    $('vault-install-retry').hidden = false;
  }
  // Exceptional save conflicts still require a real user choice, but the
  // choice lives inside Studio, never in a native browser confirm popup.
  function chooseInStudio(heading, message, safeText, otherText) {
    const panel = $('vault-install-decision');
    $('vault-decision-heading').textContent = heading;
    $('vault-decision-message').textContent = message;
    const safe = $('vault-decision-safe'), other = $('vault-decision-other');
    safe.textContent = safeText; other.textContent = otherText;
    panel.hidden = false;
    return new Promise((resolve) => {
      const choose = (yes) => {
        safe.onclick = null; other.onclick = null;
        panel.hidden = true;
        resolve(yes);
      };
      safe.onclick = () => choose(false);
      other.onclick = () => choose(true);
    });
  }

  const namesOf = (files) => files.map((f) => (groups().find((g) => g.files.has(f.name))?.id ||
    f.name.replace(/_saves\.py$/i, '')).replace(/_/g, ' '));
  const beginWork = (dialogId, title) => {
    const dialog = $(dialogId);
    const heading = dialog.querySelector('h2'); if (heading) heading.textContent = title;
    $('vault-install-retry').hidden = true;
    dialog.querySelectorAll('.work-close').forEach((b) => b.disabled = true);
    if (!dialog.open) dialog.showModal();
    return dialog;
  };
  function workUpdate(prefix, title, percent) {
    const statusEl = $(prefix + '-progress');
    const fill = $(prefix + '-bar-fill');
    const meter = $(prefix + '-bar');
    const p = Math.round(Math.max(0, Math.min(100, percent)));
    statusEl.textContent = title;
    fill.style.width = p + '%';
    meter.setAttribute('aria-valuenow', String(p));
    $(prefix + '-percent').textContent = p + '%';
  }
  function doneWork(dialog) { dialog.querySelectorAll('.work-close').forEach((b) => b.disabled = false); }
  const usbStatus = (text) => {
    const match = text.match(/(\d+)%/);
    workUpdate('vault-usb', text.replace(/\s*\d+%$/, ''),
      text.startsWith('Reading storage') ? 8 + (match ? Number(match[1]) * 0.72 : 0) : 5);
  };
  async function saveUsbFiles(files, model, label) {
    const tally = { added: 0, updated: 0, unchanged: 0 }, changes = [], skipped = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      try {
        const outcome = await addCopy(file.name, file.content, 'USB backup · ' + model);
        tally[outcome]++;
        changes.push({ name: file.name, outcome });
      } catch (e) { skipped.push(file.name + ': ' + (e.message || e)); }
      if (label === 'vault-usb') workUpdate(label, 'Saving ' + file.name + '…', 80 + Math.round((i + 1) / files.length * 20));
    }
    refresh();
    if (skipped.length) throw new Error('Some scripts could not be saved: ' + skipped.join('; '));
    return { tally, changes };
  }
  function renderResult(listId, files, emptyText = 'No game-save scripts were found.') {
    const list = $(listId); list.replaceChildren();
    const rows = new Map();
    for (const file of files) {
      const full = stored.find((entry) => entry.name === file.name);
      const ids = full ? ownedGames(full).ids : [];
      const labels = ids.length ? ids.map(title) : [file.name.replace(/_saves\.py$/i, '').replace(/_/g, ' ')];
      for (const label of labels) {
        const previous = rows.get(label);
        if (!previous || (previous === 'unchanged' && file.outcome !== 'unchanged'))
          rows.set(label, file.outcome);
      }
    }
    if (!rows.size) { list.append(make('li', '', emptyText)); return; }
    for (const [name, outcome] of rows) {
      const row = make('li');
      const state = outcome === 'unchanged' ? 'Already backed up' :
        outcome === 'updated' ? 'Updated' : outcome === 'added' ? 'New backup' : 'Restored';
      row.append(make('span', '', name), make('small', '', state));
      list.append(row);
    }
  }
  async function runBackup() {
    if (usbBusy) return;
    usbBusy = true;
    const dialog = beginWork('vault-usb-dialog', 'Back up calculator');
    const btn = $('vault-backup-usb'); btn.disabled = true;
    $('vault-usb-results').replaceChildren();
    workUpdate('vault-usb', 'Choose your NumWorks in the USB window…', 0);
    try {
      if (!ready) throw new Error('Save Vault is not ready.');
      if (!window.NumPlayUsbRead) throw new Error('USB reader could not load.');
      // No awaits before requesting USB permission: preserve the user gesture.
      const result = await window.NumPlayUsbRead.requestAndRead(usbStatus);
      workUpdate('vault-usb', 'Organizing saved games…', 80);
      const { tally, changes } = await saveUsbFiles(result.files, result.model, 'vault-usb');
      renderResult('vault-usb-results', changes);
      workUpdate('vault-usb', 'Backup complete · ' + tally.added + ' new, ' +
        tally.updated + ' updated, ' + tally.unchanged + ' unchanged', 100);
      notifier('Calculator game saves backed up.');
    } catch (error) {
      workUpdate('vault-usb', 'Backup stopped: ' + (error?.message || error), 0);
    } finally { usbBusy = false; btn.disabled = false; doneWork(dialog); }
  }
  async function getInstallBackup(id) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction('install_backups', 'readonly');
      const req = tx.objectStore('install_backups').get(id);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error || new Error('Could not read stored recovery plan'));
    });
  }
  async function saveRestorePlan(session, scripts, selected) {
    const plan = { id: 'pending-restore', model: session.model,
      serial: session.device.serialNumber || '', selected: [...selected],
      savedAt: Date.now(), completed: false,
      scripts: scripts.map((r) => ({ name: r.name, value: new Uint8Array(r.value) })) };
    await request('install_backups', 'readwrite', (store) => store.put(plan));
    const checked = await getInstallBackup('pending-restore');
    if (!checked || checked.scripts.length !== plan.scripts.length ||
      checked.scripts.some((r, i) => r.name !== plan.scripts[i].name ||
        !r.value.every((byte, j) => byte === plan.scripts[i].value[j])))
      throw new Error('Could not verify the save-restore plan. No flash changes were made.');
    return plan;
  }
  async function saveInstallSnapshot(session) {
    // A full, persistent RAW storage snapshot survives a failed operation,
    // including live .sav records newer than the game's Python copy.
    const scripts = session.records
      .map((r) => ({ name: r.name, value: new Uint8Array(r.value) }));
    await request('install_backups', 'readwrite', (store) => store.put({
      id: 'latest', model: session.model, serial: session.device.serialNumber || '',
      selected: currentSelection(), savedAt: Date.now(), scripts,
    }));
    const checked = await new Promise((resolve, reject) => {
      const req = db.transaction('install_backups', 'readonly').objectStore('install_backups').get('latest');
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    if (!checked || checked.scripts.length !== scripts.length ||
      checked.scripts.some((f, i) => f.name !== scripts[i].name ||
        f.value.length !== scripts[i].value.length ||
        f.value.some((byte, j) => byte !== scripts[i].value[j])))
      throw new Error('Could not verify the safety snapshot in this browser. No flashing started.');
    return scripts.length;
  }
  async function runDirectInstall() {
    if (usbBusy) return;
    usbBusy = true;
    const dialog = beginWork('vault-install-dialog', 'Install NumPlay');
    const desc = dialog.querySelector('.work-intro');
    if (desc) { desc.hidden = false; desc.textContent = 'Studio backs up all calculator files, installs your selected NumPlay and saves a verified restoration plan. After flashing, unplug USB, finish the full calculator restart and choose your language before reconnecting to restore game progress. Other installed extra apps may be replaced.'; }
    const btn = $('install-calculator'); btn.disabled = true;
    $('vault-install-results').replaceChildren();
    workUpdate('vault-install', 'Choose your NumWorks in the USB window…', 0);
    retryScripts = null; retryDevice = null; awaitingPhysicalReplug = false;
    clearHandoff();
    $('vault-install-dialog').querySelector('button.button-wide.work-close').hidden = false;
    $('vault-install-dialog').querySelector('.work-bottom-note').hidden = false;
    $('vault-install-decision').hidden = true;
    let session, flashed = false;
    const originalConfig = location.hash;
    const selected = currentSelection();
    // Snapshot the chosen calculator capacity before connecting over USB.
    const chosen = chosenCapacity();
    // The risks are visible in Studio, without a native OK/Cancel prompt.
    $('vault-install-nwagra-note').hidden = !(chosen && ['xl', 'xl26'].includes(chosen.id));
    try {
      if (!ready) throw new Error('Save Vault is not ready.');
      if (!selected.length) throw new Error('Select at least one game first.');
      if (!window.NumPlayUsbRead?.requestSession || !window.NumPlayUsbInstall)
        throw new Error('USB installation module did not load. Refresh Studio.');
      // Get permission directly from the Install click; do not await a modal.
      session = await window.NumPlayUsbRead.requestSession((message) =>
        workUpdate('vault-install', message, 3));
      workUpdate('vault-install', 'Saving current game progress…', 15);
      await saveUsbFiles(session.files, session.model, 'vault-install');
      await saveInstallSnapshot(session);
      workUpdate('vault-install', 'Backing up complete. Building NumPlay…', 23);
      const result = await launchDownload(); // builds but does NOT download
      if (!(result?.data instanceof Uint8Array) || !result.data.length)
        throw new Error('NumPlay app build failed. Nothing was written.');
      if (location.hash !== originalConfig || currentSelection().join(',') !== selected.join(',') ||
        JSON.stringify(chosenCapacity()) !== JSON.stringify(chosen))
        throw new Error('Your selected games changed. Please retry installation.');
      const restoreFiles = scriptsForSelection(selected);
      workUpdate('vault-install', 'Preparing calculator-specific app…', 35);
      const onWrite = (message, fraction) => {
        const percent = message.startsWith('Verifying') ? 75 + fraction * 11 :
          message.startsWith('Installing') ? 51 + fraction * 24 :
          message.startsWith('Restarting') ? 87 : 40;
        workUpdate('vault-install', message, percent);
      };
      const installed = await window.NumPlayUsbInstall.directInstall(session, result.data,
        restoreFiles, onWrite, chosen,
        (plan) => saveRestorePlan(session, plan, selected));
      flashed = true;
      retryScripts = installed.scripts;
      retryDevice = session.device;
      // Reboot disconnects WebUSB, and NumWorks can wipe Python scripts during
      // the reset. A pre-flash write cannot make them survive it. Real hardware
      // required physical unplug/replug; do NOT retry a stale USB handle.
      await session.close(); session = null;
      awaitingPhysicalReplug = true;
      workUpdate('vault-install', 'NumPlay installed and verified. Follow these four steps to finish restoring your game saves.', 90);
      showHandoff();
      // Leave pending-restore INCOMPLETE until a new USB session restores and
      // verifies the Python scripts. A refresh still has the verified snapshot.
    } catch (error) {
      const detail = error?.message || String(error);
      if (flashed && retryScripts) {
        awaitingPhysicalReplug = true;
        workUpdate('vault-install', 'NumPlay was written, but saves still need restoring: ' + detail, 90);
        showHandoff();
      } else workUpdate('vault-install', 'Installation stopped: ' + detail, 0);
    } finally {
      if (session) await session.close().catch(() => {});
      usbBusy = false;
      btn.disabled = false;
      doneWork(dialog);
    }
  }
  // This is recovery-only: it NEVER calls the app linker or flashes any sector.
  // It intentionally gets a NEW WebUSB device from a fresh permission picker.
  async function recoverSaves(reconnectedDevice = null) {
    if (usbBusy) return;
    awaitingPhysicalReplug = false;
    replugAcknowledged = false;
    usbBusy = true;
    const dialog = beginWork('vault-install-dialog', 'Restore saved games');
    $('vault-install-replug').hidden = true;
    $('vault-install-decision').hidden = true;
    // A restore is not "Done" until the reconnected calculator is verified.
    $('vault-install-dialog').querySelector('button.button-wide.work-close').hidden = true;
    $('vault-install-dialog').querySelector('.work-bottom-note').hidden = true;
    const desc = dialog.querySelector('.work-intro');
    if (desc) { desc.hidden = false; desc.textContent = 'Reconnect your calculator to restore the backed-up saves. This only writes calculator file storage; it does not reinstall NumPlay.'; }
    $('vault-install-nwagra-note').hidden = true;
    $('vault-install-results').replaceChildren();
    $('vault-install-retry').hidden = true;
    workUpdate('vault-install', 'Choose your NumWorks in the USB window…', 0);
    let session;
    try {
      if (!ready) throw new Error('The Save Vault is not ready.');
      // Picker-free sessions are ONLY used for actual physical USB reconnect
      // events on an already-authorized calculator. Manual clicks open the picker.
      const connection = reconnectedDevice
        ? window.NumPlayUsbRead.openSession(reconnectedDevice, (message) =>
          workUpdate('vault-install', message, 8))
        : window.NumPlayUsbRead.requestSession((message) =>
          workUpdate('vault-install', message, 8));
      session = await connection;
      workUpdate('vault-install', 'Checking saved recovery files…', 16);
      const pending = await getInstallBackup('pending-restore');
      const snapshot = await getInstallBackup('latest');
      const plan = pending && !pending.completed ? pending : snapshot;
      const connectedSerial = session.device.serialNumber || '';
      let scripts;
      if (plan && Array.isArray(plan.scripts) && plan.scripts.length) {
        if (plan.model !== session.model) throw new Error(
          'Backup is for ' + plan.model + ', not ' + session.model + '. Refusing restoration.');
        if (plan.serial && connectedSerial && plan.serial !== connectedSerial)
          throw new Error('This is a different calculator from the backed-up device. No files were written.');
        if (!plan.serial || !connectedSerial) {
          const proceed = await chooseInStudio('Confirm calculator identity',
            'The backup was made for an ' + plan.model + ', but its unique USB serial number cannot be checked. Restore to this connected calculator?',
            'Cancel restoration', 'Restore to this calculator');
          if (!proceed) throw new Error('Restore cancelled. No files were written.');
        }
        const selected = plan.selected?.length ? plan.selected : currentSelection();
        scripts = pending && !pending.completed ? pending.scripts :
          window.NumPlayUsbInstall.mergeScriptRecords(plan.scripts, scriptsForSelection(selected));
      } else {
        // Portable recovery after a browser reset / different origin. Unlike
        // the raw snapshot, vault JSON only has backed-up .py game scripts.
        const archived = scriptsForSelection(currentSelection());
        if (!archived.length) throw new Error('No local install snapshot and no backed-up saves for selected games. Import your exported Save Vault JSON first.');
        const proceed = await chooseInStudio('Partial backup available',
          'Studio found ' + archived.length + ' game-save script(s) in the vault, but not a full calculator snapshot. Other files cannot be recovered from this backup.',
          'Cancel restoration', 'Restore game saves only');
        if (!proceed) throw new Error('Restore cancelled. No files were written.');
        scripts = window.NumPlayUsbInstall.mergeScriptRecords([], archived);
      }
      // During the *unfinished installation of this same calculator*, the
      // verified pre-flash recovery plan must replace any newly-created default
      // saves. Those defaults are NOT more recent game progress. Do not ask a
      // confusing conflict question for this recovery path.
      //
      // A standalone manual restore is different: the calculator might have
      // been played since the backup, so preserve it unless the user chooses
      // to replace its files. Identity checks above apply to BOTH paths.
      const installingNow = !!(pending && !pending.completed);
      const conflicts = window.NumPlayUsbInstall.differingRecords(session.records, scripts);
      let overwrite = installingNow;
      if (conflicts.length && !installingNow) {
        const preview = conflicts.slice(0, 7).join(', ') + (conflicts.length > 7 ? '...' : '');
        overwrite = await chooseInStudio('Different saves found',
          conflicts.length + ' existing calculator file(s) differ from your backed-up copies:\n' +
          preview + '\n\nKeeping the calculator copies protects any newer progress. Replacing them restores the older backups.',
          'Keep calculator saves', 'Replace with backup');
      }
      workUpdate('vault-install', installingNow
        ? 'Restoring your pre-install saves over new game defaults...'
        : 'Restoring saved games...', 25);
      const n = await window.NumPlayUsbInstall.restoreSession(session, scripts,
        (label, fraction) => workUpdate('vault-install', label, 28 + fraction * 70), overwrite);
      workUpdate('vault-install', 'Save restoration verified · ' + n + ' storage files present.', 100);
      $('vault-install-dialog').querySelector('button.button-wide.work-close').hidden = false;
      renderResult('vault-install-results', scripts.filter((r) => /_saves\.py$/i.test(r.name))
        .map((r) => ({ name: r.name })));
      if (pending && !pending.completed) await request('install_backups', 'readwrite', (store) =>
        store.put({ ...pending, completed: true }));
      retryScripts = null; retryDevice = null; awaitingPhysicalReplug = false;
      clearHandoff();
      notifier('Calculator saves restored and verified.');
    } catch (e) {
      // No flash operations exist on this path, even if restoration fails.
      workUpdate('vault-install', 'Save restoration is still pending: ' + (e.message || e) + '. Your browser backup is preserved. If the calculator was not reconnected, unplug and plug its USB cable back in, then click Reconnect & finish saves.', 90);
      $('vault-install-retry').hidden = false;
    } finally {
      if (session) await session.close().catch(() => {});
      usbBusy = false; doneWork(dialog);
    }
  }
  async function retryRestore() { await recoverSaves(); }

  async function init(initialKit) {
    kit = initialKit;
    $('view-select').addEventListener('click', () => view('select'));
    $('view-backups').addEventListener('click', () => view('backups'));
    $('vault-import').addEventListener('click', () => $('vault-import-file').click());
    $('vault-import-file').addEventListener('change', async (e) => {
      const files = [...e.target.files]; e.target.value = ''; await ingestFiles(files);
    });
    $('vault-export').addEventListener('click', () => exportVault().catch((e) => status(e.message)));
    $('vault-backup-usb').addEventListener('click', runBackup);
    // Permanently accessible recovery button, including after a refresh.
    const recoverButton = make('button', 'button button-outline', 'Restore saved games');
    recoverButton.id = 'vault-restore-usb'; recoverButton.type = 'button';
    const backupButton = $('vault-backup-usb');
    backupButton.insertAdjacentElement('afterend', recoverButton);
    recoverButton.addEventListener('click', () => recoverSaves());
    $('vault-install-retry').textContent = 'Reconnect & finish saves';
    $('vault-install-retry').addEventListener('click', retryRestore);
    $('vault-install-unplugged').addEventListener('click', acknowledgeUnplug);
    $('vault-install-dialog').addEventListener('cancel', (event) => {
      if (usbBusy) event.preventDefault();
    });
    // After the reboot the calculator may not re-enumerate until physically
    // unplugged/replugged. WebUSB's 'connect' event gives us a NEW USBDevice.
    // If Chrome has kept authorization for this *same* device, finish restoring
    // without another click. Otherwise keep the manual picker fallback.
    if (navigator.usb && typeof navigator.usb.addEventListener === 'function') {
      navigator.usb.addEventListener('connect', async (event) => {
        if (!awaitingPhysicalReplug || !replugAcknowledged || usbBusy || !event.device) return;
        const device = event.device;
        if (device.vendorId !== 0x0483 || device.productId !== 0xA291) return;
        let plan;
        try { plan = await getInstallBackup('pending-restore'); } catch (_) { return; }
        if (!awaitingPhysicalReplug || !replugAcknowledged || usbBusy || !plan || plan.completed) return;
        // Auto-write only when both USB and snapshot have a matching serial.
        // The manual picker still permits explicit user confirmation otherwise.
        if (!plan.serial || !device.serialNumber || plan.serial !== device.serialNumber) return;
        await new Promise((resolve) => setTimeout(resolve, 1100));
        if (!awaitingPhysicalReplug || !replugAcknowledged || usbBusy) return;
        recoverSaves(device);
      });
    }

    try {
      db = await openDb();
      stored = await allFiles();
      ready = true;
      refresh();
      // A functioning library must not require a request to GitHub.
      loadGameMap().then(refresh);
    } catch (e) {
      $('vault-import').disabled = true;
      $('vault-export').disabled = true;
      status('The browser could not open its local save library: ' + (e?.message || e));
    }
  }
  function bindInstall(buildFn, selectionFn, capacityFn) {
    launchDownload = buildFn;
    currentSelection = selectionFn;
    chosenCapacity = capacityFn;
    $('install-calculator').addEventListener('click', runDirectInstall);
  }
  function updateInstallAvailability(ok) {
    const button = $('install-calculator');
    if (button) button.disabled = !ok;
  }
  window.NumPlaySaveVault = { init, bindInstall, updateInstallAvailability };
})();
