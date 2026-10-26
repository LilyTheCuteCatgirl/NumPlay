/* NumPlay Studio - experimental direct NumWorks USB installer.
 * Flash operations are never entered until the caller has successfully
 * backed up the calculator and produced a verified, device-linked binary.
 * Uses the firmware-reported app region, not hardcoded flash addresses.
 */
'use strict';
(() => {
  const magic = [0xba, 0xdd, 0x0b, 0xee];
  const sector = 65536, blockSize = 2048;
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  const ensure = (ok, msg) => { if (!ok) throw new Error(msg); };
  const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
  const hex = (n) => '0x' + Number(n).toString(16).toUpperCase();
  const inFlash = (addr, m) => (addr >= m.flashStart && addr + 4 <= m.flashEnd) ||
    (addr >= m.extStart && addr + 4 <= m.extEnd);


  async function statusUntil(reader, target, label) {
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      const s = await reader.input(3, 6);
      ensure(s.length === 6 && s[0] === 0, label + ': calculator DFU status error.');
      if (s[4] === target) return;
      ensure(s[4] === 4 || s[4] === 2 || s[4] === 5,
        label + ': unexpected DFU state ' + s[4]);
      await pause(Math.min(250, Math.max(1, s[1] | (s[2] << 8) | (s[3] << 16))));
    }
    throw new Error(label + ': calculator did not respond in time.');
  }
  async function writeBlock(reader, address, bytes, alt) {
    await reader.setAlt(alt);
    await reader.seek(address);
    await reader.output(1, bytes, 2);
    await statusUntil(reader, 5, 'USB write');
    await reader.idle();
  }
  async function eraseSector(reader, address) {
    await reader.setAlt(0);
    await reader.idle();
    const cmd = new Uint8Array(5);
    cmd[0] = 0x41;
    new DataView(cmd.buffer).setUint32(1, address, true);
    await reader.output(1, cmd, 0);
    await statusUntil(reader, 5, 'Flash erase');
    await reader.idle();
  }
  // The kit has four supported app-memory layouts. Nwagra uses the *unused*
  // second firmware slot by patching the NumWorks site's USB header response;
  // our USB reader deliberately returns the actual, unmodified header.
  // Hence, only an explicit XL selection may expand beyond the reported end.
  const supportedSpaces = {
    std:  { start: 0x90200000, end: 0x903F0000 },
    e26:  { start: 0x90180000, end: 0x903F0000 },
    xl:   { start: 0x90200000, end: 0x90800000 },
    xl26: { start: 0x90180000, end: 0x90800000 },
  };
  function flashRegion(session, selectedSpace) {
    const info = session.storage;
    const model = session.modelInfo;
    const f = info.flashStart, e = info.flashEnd, a = info.ramStart, b = info.ramEnd;
    ensure(Number.isInteger(f) && Number.isInteger(e) && f % sector === 0 && e % sector === 0 &&
      f >= model.extStart && e <= model.extEnd && e > f,
      'Firmware extra-apps flash region is not supported. Nothing will be written.');
    ensure(Number.isInteger(a) && Number.isInteger(b) && a >= model.ramStart && b <= model.ramEnd && b > a &&
      b - a >= 0x12000 && b - a <= 0x50000,
      'Cannot identify firmware app RAM. Nothing will be written.');

    // Prefer the Studio *selected* limit, but refuse an unknown kit layout.
    // If no kit is supplied (e.g. read-only diagnostics), only use the
    // firmware-reported limits. No guessed extra app space.
    const chosen = selectedSpace || null;
    const canonical = chosen && supportedSpaces[chosen.id];
    if (chosen) ensure(canonical && chosen.start === canonical.start &&
      chosen.end === canonical.end && chosen.size === canonical.end - canonical.start,
      'The Studio memory setting does not match a supported NumPlay kit layout. Nothing will be written.');
    if (chosen) ensure(canonical.start === f,
      'Your calculator reports that apps begin at ' + hex(f) +
      ', but the selected ' + chosen.id + ' mode starts at ' + hex(canonical.start) +
      '. Choose the memory mode matching your firmware; nothing was written.');
    const end = canonical ? canonical.end : e;
    ensure(end > f && end <= model.extEnd && end - f <= 0x700000,
      'Selected calculator storage extends beyond the recognized physical flash. Nothing will be written.');
    // Never shrink or enlarge an unknown firmware into the hidden slot.
    const extended = end > e;
    if (extended) ensure((chosen.id === 'xl' || chosen.id === 'xl26') &&
      e === 0x903F0000 && end === 0x90800000,
      'The firmware-reported app region cannot be safely expanded with Nwagra. Nothing was written.');

    const t = info.trampolineStart;
    ensure(Number.isInteger(t) && (t & 3) === 0 && inFlash(t, model) &&
      !(t >= f && t < end),
      'Unsupported firmware trampoline location (' + (Number.isInteger(t) ? hex(t) : 'unknown') +
      '). Nothing will be written.');
    return { flashStart: f, flashLength: end - f, ramStart: a,
      ramLength: b - a, trampolineStart: t, extended,
      reportedEnd: e, setting: chosen ? chosen.id : 'firmware' };
  }

  // Nwagra writes into the inactive secondary OS slot. Confirm from the
  // *real* running calculator that neither active kernel nor userland
  // header points into the extended app region; refuse if they do.
  async function verifyNwagraSlot(session, region) {
    if (!region.extended) return;
    const m = session.modelInfo;
    const slots = await session.reader.read(m.ramStart, 16, true);
    const marker = [0xba, 0xdb, 0xee, 0xef];
    ensure(slots.length === 16 && same(slots.subarray(0, 4), marker) &&
      same(slots.subarray(12, 16), marker),
      'Could not verify the active firmware slot. Nwagra installation was stopped before writing.');
    const view = new DataView(slots.buffer, slots.byteOffset, slots.byteLength);
    const kernel = view.getUint32(4, true), userland = view.getUint32(8, true);
    ensure(userland === session.storage.userland,
      'Active firmware changed since the backup. Nwagra install was stopped.');
    for (const [label, addr] of [['kernel', kernel], ['userland', userland]]) {
      ensure(inFlash(addr, m) && !(addr >= region.flashStart &&
        addr < region.flashStart + region.flashLength),
        'Active ' + label + ' firmware at ' + hex(addr) +
        ' overlaps the selected Nwagra app area. Nothing was written.');
    }
  }

  function scriptRecord(name, content) {
    const text = new TextEncoder().encode(content);
    const bytes = new Uint8Array(text.length + 2);
    bytes[0] = 0; // Epsilon Python script auto-import flag (disabled)
    bytes.set(text, 1);
    bytes[bytes.length - 1] = 0;
    return { name, value: bytes };
  }
  function mergeScriptRecords(liveRecords, selectedScripts) {
    const byName = new Map(liveRecords.map((r) => [r.name, { name: r.name, value: new Uint8Array(r.value) }]));
    for (const file of selectedScripts) {
      ensure(/^[a-z0-9_.-]+_saves\.py$/i.test(file.name) &&
        typeof file.content === 'string' && file.content.length < 100000,
        'Invalid saved script in vault: ' + file.name);
      // Selected games use the freshest shared backup assembled from BOTH the
      // calculator and the archive; unrelated files are retained verbatim.
      byName.set(file.name, scriptRecord(file.name, file.content));
    }
    return [...byName.values()];
  }
  function packStorage(records, capacity) {
    const encoder = new TextEncoder();
    const out = new Uint8Array(capacity);
    let off = 0;
    for (const r of records) {
      ensure(/^[a-z0-9_.-]{1,99}$/i.test(r.name) && !r.name.includes('..'), 'Unsafe calculator filename.');
      const name = encoder.encode(r.name), bytes = r.value;
      const len = 3 + name.length + bytes.length;
      ensure(len <= 65535 && off + len + 2 <= out.length,
        'Calculator Python storage is full. Saves were retained in the browser, but automatic restoration cannot fit them.');
      out[off] = len & 255; out[off + 1] = len >> 8;
      out.set(name, off + 2);
      out[off + 2 + name.length] = 0;
      out.set(bytes, off + 3 + name.length);
      off += len;
    }
    return out;
  }
  async function restore(reader, storage, records, progress, overwriteExisting = false) {
    const before = await reader.read(storage.address, storage.size + 8, true);
    ensure(same(before.subarray(0, 4), magic) && same(before.subarray(before.length - 4), magic),
      'Calculator storage signatures changed after restart; refusing restoration.');
    const current = window.NumPlayUsbRead.parseStorageRecords(before);
    // The calculator may have been used since the backup. Its live records
    // take priority on automatic restoration: never silently overwrite progress.
    const map = new Map(current.map((r) => [r.name, r]));
    for (const r of records) {
      if (!map.has(r.name) || overwriteExisting) map.set(r.name, r);
    }
    const merged = [...map.values()];
    const wanted = packStorage(merged, storage.size);
    // If the selected copies survived the reboot, VERIFY without rewriting
    // the same data. Staged saves may be cleared on restart; that is why this
    // function is called both before installation and after reconnecting.
    if (same(before.subarray(4, 4 + storage.size), wanted)) {
      progress('All saved files are already present and verified.', 1);
      return merged.length;
    }
    // Refuse to touch RAM until the entire storage image fits.
    for (let i = 0; i < wanted.length; i += blockSize) {
      await writeBlock(reader, storage.address + 4 + i, wanted.subarray(i, i + blockSize), 1);
      progress('Restoring saved games…', (i + Math.min(blockSize, wanted.length - i)) / wanted.length);
    }
    await reader.setAlt(0);
    const checked = await reader.read(storage.address, storage.size + 8, true);
    ensure(same(checked.subarray(0, 4), magic) && same(checked.subarray(checked.length - 4), magic),
      'Storage signatures failed verification after restoration.');
    ensure(same(checked.subarray(4, storage.size + 4), wanted),
      'Calculator did not retain the restored files. Keep your browser backups.');
    return merged.length;
  }
  // Staging is a RAM-storage operation, NOT an app flash. We stage archived
  // scripts before flashing so that the calculator has a full selection before
  // its restart. Many Epsilon builds clear RAM storage on restart, however, so
  // this never replaces the post-restart verification/restoration step.
  async function stageBeforeFlash(session, scripts, onProgress) {
    const storage = session.storage;
    const reader = session.reader;
    const before = await reader.read(storage.address, storage.size + 8, true);
    ensure(same(before.subarray(0, 4), magic) && same(before.subarray(before.length - 4), magic),
      'Cannot verify calculator storage before installation. No flash changes were made.');
    const wanted = packStorage(scripts, storage.size);
    if (same(before.subarray(4, storage.size + 4), wanted)) {
      onProgress('All selected game saves already staged.', 1);
      return { wrote: false, files: scripts.length };
    }
    await restore(reader, storage, scripts,
      (label, fraction) => onProgress('Staging selected game saves…', fraction), true);
    onProgress('Selected saves staged and verified.', 1);
    return { wrote: true, files: scripts.length };
  }
  async function flash(session, binary, onProgress, info) {
    ensure(info && info.flashLength > 0, 'Missing verified flash layout.');
    const { image, appBytes } = binary;
    ensure(image instanceof Uint8Array && image.length > 0 &&
      image.length <= info.flashLength && image.length % sector === 0,
      'Linked image is not a valid set of full flash sectors.');
    ensure(image[0] === 0xba && image[1] === 0xbe && image[2] === 0xc0 && image[3] === 0xde,
      'Final app header is not valid.');
    const reader = session.reader, sectors = image.length / sector;
    for (let i = 0; i < sectors; i++) {
      await eraseSector(reader, info.flashStart + i * sector);
      onProgress('Installing NumPlay…', (i + 0.15) / sectors);
      // An all-FF sector is the *end marker* and only needs to be erased.
      if (!image.subarray(i * sector, (i + 1) * sector).every((x) => x === 255)) {
        for (let at = 0; at < sector; at += blockSize)
          await writeBlock(reader, info.flashStart + i * sector + at,
            image.subarray(i * sector + at, i * sector + at + blockSize), 0);
      }
      onProgress('Installing NumPlay…', (i + 0.8) / sectors);
    }
    // Verify all bytes by reading them back BEFORE reboot.
    for (let i = 0; i < sectors; i++) {
      const actual = await reader.read(info.flashStart + i * sector, sector, false);
      ensure(same(actual, image.subarray(i * sector, (i + 1) * sector)),
        'Installation verification failed in sector ' + (i + 1) + '. Leave calculator connected and use recovery if needed.');
      onProgress('Verifying installed app…', (i + 1) / sectors);
    }
    onProgress('Restarting calculator…', 1);
    // DFU manifest may disconnect WebUSB before returning a status response.
    try {
      await reader.setAlt(0);
      await reader.seek(info.flashStart);
      await reader.output(1, new Uint8Array(), 2);
      await statusUntil(reader, 7, 'Calculator restart');
    } catch (err) {
      // A physical disconnect following successful readback is expected.
      if (reader.device.opened) {
        try { await reader.close(); } catch (_) { /* The calculator is rebooting. */ }
      }
    }
    return { appBytes, region: info };
  }
  // The userland header stores a pointer to the firmware's EADK trampoline.
  // Check that pointer BEFORE any write, using the same read-only USB path as backup.
  async function verifyFirmwareTrampoline(session, region) {
    const slot = region.trampolineStart;
    const raw = await session.reader.read(slot, 4, false);
    ensure(raw.length === 4, 'Could not read the firmware trampoline pointer. Nothing was written.');
    const target = new DataView(raw.buffer, raw.byteOffset, raw.byteLength).getUint32(0, true);
    // Thumb entry points have the low bit set; the underlying function must
    // belong to firmware flash, not the replaceable external-apps region.
    const actual = target - (target & 1);  // avoid signed 32-bit bitwise truncation for 0x900...
    ensure((target & 1) === 1 && inFlash(actual, session.modelInfo) &&
      !(actual >= region.flashStart && actual < region.flashStart + region.flashLength),
      'Firmware trampoline pointer ' + hex(target) + ' at ' + hex(slot) +
      ' is not a valid firmware entry point. Nothing was written.');
  }
  // Before committing an install, verify the linked region is exactly the
  // firmware's flash layout; an unrelated app or memory region is never erased.
  async function directInstall(session, nwa, selectedScripts, onProgress, selectedSpace, beforeFlash) {
    const region = flashRegion(session, selectedSpace);
    onProgress('Checking firmware trampoline…', 0.01);
    await verifyFirmwareTrampoline(session, region);
    onProgress('Checking active firmware slot…', 0.015);
    await verifyNwagraSlot(session, region);
    ensure(window.NumPlayStudioLink, 'NumPlay linker was not loaded.');
    onProgress('Linking NumPlay for your calculator…', 0.02);
    const linked = await window.NumPlayStudioLink(nwa, region);
    onProgress('Final app ready: ' + Math.round(linked.appBytes / 1024) + ' KB', 0.04);
    const current = session.records;
    // Preserve every live record, not just its Python copy: a .sav may be
    // newer than its _saves.py snapshot when a game was just played.
    const scripts = mergeScriptRecords(current, selectedScripts);
    // Preflight for the storage footprint on the actual firmware.
    packStorage(scripts, session.storage.size);
    // In Nwagra mode the inactive firmware slot can be overwritten. This risk
    // is disclosed inside Studio's install panel before the flash begins,
    // rather than asking for another native browser confirmation dialog.
    // verifyNwagraSlot still rejects unknown/active firmware layouts.
    // Save the exact restore plan durably BEFORE erasing any flash. The page
    // may be closed or the USB device may re-enumerate on restart.
    if (typeof beforeFlash === 'function') await beforeFlash(scripts);
    // NumWorks' installation reboot clears RAM-backed file storage, including
    // the scripts written by stageBeforeFlash. Hardware testing confirmed that
    // pre-staging alone DOES NOT survive. Do not overwrite live files pointlessly.
    // The persistent browser restore plan above is the source of truth until a
    // fresh post-reboot USB connection restores and verifies the records.
    onProgress('Backup verified. Installing NumPlay…', 0.1);
    await flash(session, linked, onProgress, region);
    return { scripts, staged: { wrote: false, files: 0 }, model: session.model, appBytes: linked.appBytes,
      flashStart: region.flashStart, storage: session.storage };
  }
  const USB_VENDOR = 0x0483, USB_PRODUCT = 0xA291;
  function matchesCalculator(device, original) {
    if (device.vendorId !== USB_VENDOR || device.productId !== USB_PRODUCT) return false;
    // The USB object is *not* stable across reboot. The serial is the
    // meaningful identity; skip devices with a known different serial.
    return !(original?.serialNumber && device.serialNumber &&
      original.serialNumber !== device.serialNumber);
  }
  // The device disconnects during a successful firmware reboot. Every retry
  // must ask WebUSB for a fresh USBDevice, not reopen the original stale one.
  async function reconnectAndRestore(original, scripts, progress, maxAttempts = 6) {
    ensure(navigator.usb && typeof navigator.usb.getDevices === 'function',
      'WebUSB is unavailable; use Restore saved games in the Backed up games tab.');
    let last, encountered = false;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      await pause(attempt === 0 ? 2000 : 1500);
      let devices;
      try { devices = (await navigator.usb.getDevices()).filter((d) => matchesCalculator(d, original)); }
      catch (err) { last = err; continue; }
      if (!devices.length) {
        progress('Waiting for NumWorks USB to reconnect…', (attempt + 1) / maxAttempts);
        continue;
      }
      // Don't choose a possibly wrong calculator without a user prompt.
      if (devices.length > 1) throw new Error(
        'Multiple authorized calculators detected. Use Restore saved games to choose yours manually.');
      for (const fresh of devices) {
        const reader = new window.NumPlayUsbRead.Reader(fresh);
        try {
          // Reading firmware/storage also confirms this is a supported calculator.
          await reader.open();
          const model = await reader.model();
          if (model.model !== 'N0120' && model.model !== 'N0110' && model.model !== 'N0115')
            throw new Error('Reconnected device is not a supported NumWorks calculator.');
          const storage = await reader.storageInfo(model);
          const count = await restore(reader, storage, scripts, (label, p) => progress(label, p));
          return count;
        } catch (err) {
          last = err;
          encountered = true;
          // Storage format / capacity issues won't be fixed by waiting.
          if (/full|too large|signatures|unsafe|not retain|verification/i.test(String(err.message)))
            throw err;
        } finally { await reader.close().catch(() => {}); }
      }
      progress('Waiting for restarted calculator…', Math.min(0.95, (attempt + 1) / maxAttempts));
    }
    throw new Error('Chrome could not reconnect automatically after restart' +
      (encountered && last ? ': ' + last.message : '.') +
      ' Click Reconnect & finish saves in this window to grant USB permission again. Do not reinstall NumPlay.');
  }
  function differingRecords(live, backed) {
    const map = new Map(live.map((r) => [r.name, r.value]));
    return backed.filter((r) => {
      const have = map.get(r.name);
      return have && !same(have, r.value);
    }).map((r) => r.name);
  }
  // Existing authorized USB session: write only the storage records, never flash.
  async function restoreSession(session, scripts, progress, overwriteExisting = false) {
    return restore(session.reader, session.storage, scripts, progress, overwriteExisting);
  }
  window.NumPlayUsbInstall = { flashRegion, verifyFirmwareTrampoline, verifyNwagraSlot,
    mergeScriptRecords, packStorage, stageBeforeFlash, directInstall, reconnectAndRestore,
    differingRecords, restoreSession };
})();
