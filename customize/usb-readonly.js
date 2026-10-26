/* NumPlay Studio - EXPERIMENTAL read-only NumWorks save-script backup.
 * Uses documented WebUSB / USB DFU requests to read firmware-described RAM storage.
 * The DFU SetAddress command positions the read pointer. Backup requests are
 * read-only. A separate installer module uses an explicitly requested session
 * for writes only after its own preflight and backup checks.
 * No external scripts or services, and no telemetry.
 * Validated with mock DFU responses. Needs verification on a physical calculator.
 */
'use strict';
(() => {
  const VID = 0x0483, PID = 0xA291;
  const MAGIC_SLOT = [0xba, 0xdb, 0xee, 0xef];
  const MAGIC_USERLAND = [0xfe, 0xed, 0xc0, 0xde];
  const MAGIC_STORAGE = [0xba, 0xdd, 0x0b, 0xee];
  const REQUEST = { dnload: 1, upload: 2, status: 3, clear: 4, state: 5, abort: 6 };
  // DfuSe UPLOAD block numbers advance by the device's DFU transfer size.
  // NumWorks Epsilon uses 2048-byte transfers. Requesting only 1024 bytes
  // advances by 2048 anyway, silently skipping half the storage on hardware.
  const blockSize = 2048;
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const equalAt = (bytes, offset, match) => match.every((v, i) => bytes[offset + i] === v);
  function bounded(condition, message) { if (!condition) throw new Error(message); }
  function withTimeout(p, what) {
    let timer;
    const deadline = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(what + ' timed out (USB communication stopped)')), 15000); });
    return Promise.race([p, deadline]).finally(() => clearTimeout(timer));
  }
  const u32 = (bytes, at) => new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(at, true);

  // Only recognize well-understood graphing-calculator models / firmware address families.
  function modelOf(device, descriptor) {
    const bcd = descriptor[12] | descriptor[13] << 8;
    const old = { model: 'N0110 / N0115', ramStart: 0x20000000, ramEnd: 0x20040000,
      flashStart: 0x08000000, flashEnd: 0x08100000, extStart: 0x90000000, extEnd: 0x90800000 };
    if (bcd === 0x110 || bcd === 0x115) return { ...old, model: bcd === 0x115 ? 'N0115' : 'N0110' };
    if (bcd === 0x120) return { model: 'N0120', ramStart: 0x24000000, ramEnd: 0x24050000,
      flashStart: 0x08000000, flashEnd: 0x08080000, extStart: 0x90000000, extEnd: 0x90800000 };
    throw new Error('Unsupported calculator USB model (device version 0x' + bcd.toString(16) + '). Backup stopped without changing anything.');
  }
  function flashAddressAllowed(addr, length, m) {
    return (addr >= m.flashStart && addr + length <= m.flashEnd) ||
      (addr >= m.extStart && addr + length <= m.extEnd);
  }
  function parseStorageRecords(bytes) {
    bounded(bytes.length >= 10, 'Calculator storage read is unexpectedly short; no save data was imported.');
    // Keep independent checks so a physical-device failure tells us whether
    // the RAM address or the transfer progression needs investigation.
    bounded(equalAt(bytes, 0, MAGIC_STORAGE),
      'Calculator storage start signature is missing (firmware storage address or USB transfer mismatch). No save data was imported.');
    bounded(equalAt(bytes, bytes.length - 4, MAGIC_STORAGE),
      'Calculator storage end signature is missing (storage length or USB transfer mismatch). No save data was imported.');
    const result = [];
    const dataEnd = bytes.length - 4;
    let at = 4, terminator = false;
    while (at + 2 <= dataEnd) {
      const n = bytes[at] | bytes[at + 1] << 8;
      if (n === 0) { terminator = true; break; }
      bounded(n >= 4 && at + n <= dataEnd, 'Calculator storage contains an invalid record length.');
      const end = at + n;
      let zero = at + 2;
      while (zero < end && bytes[zero]) zero++;
      bounded(zero > at + 2 && zero < end, 'Calculator storage contains an invalid record name.');
      const name = decoder.decode(bytes.slice(at + 2, zero));
      bounded(name.length < 100, 'Calculator storage record name is unexpectedly long.');
      result.push({ name, value: bytes.slice(zero + 1, end) });
      at = end;
    }
    bounded(terminator, 'Calculator storage has no terminating record.');
    return result;
  }
  function saveScriptsFromRecords(records) {
    const saves = [];
    const nameRx = /^[a-z0-9_.-]+_saves\.py$/i;
    const contentRx = /^#(?:>|=)[a-z0-9_.-]+:(?:[0-9]+:)?[a-z0-9+/=]+\s*$/im;
    for (const record of records) {
      if (!nameRx.test(record.name) || record.name.includes('..') || !record.value.length) continue;
      // Epsilon script records begin with a one-byte auto-import/status flag.
      const body = record.value[0] <= 1 ? record.value.subarray(1) : record.value;
      let text;
      try { text = decoder.decode(body).replace(/\0+$/g, ''); } catch (_) { continue; }
      if (contentRx.test(text)) saves.push({ name: record.name, content: text });
    }
    return saves;
  }
  class Reader {
    constructor(device) {
      this.device = device;
      this.iface = null;
    }
    setup() {
      const found = [];
      for (const config of this.device.configurations) {
        for (const iface of config.interfaces) {
          for (const alt of iface.alternates) {
            if (alt.interfaceClass === 254 && alt.interfaceSubclass === 1 && (alt.interfaceProtocol === 1 || alt.interfaceProtocol === 2))
              found.push({ config: config.configurationValue, number: iface.interfaceNumber, alt: alt.alternateSetting });
          }
        }
      }
      bounded(found.length, 'No DFU interface on this USB device.');
      this.iface = found.find((f) => f.alt === 0) || found[0];
    }
    async open() {
      this.setup();
      await withTimeout(this.device.open(), 'USB open');
      if (!this.device.configuration || this.device.configuration.configurationValue !== this.iface.config)
        await withTimeout(this.device.selectConfiguration(this.iface.config), 'Select USB configuration');
      await withTimeout(this.device.claimInterface(this.iface.number), 'Claim USB interface');
      await this.setAlt(0);
    }
    async setAlt(alt) {
      await withTimeout(this.device.selectAlternateInterface(this.iface.number, alt), 'Select memory area');
    }
    async input(type, size, value = 0) {
      const r = await withTimeout(this.device.controlTransferIn({ requestType: 'class', recipient: 'interface',
        request: type, value, index: this.iface.number }, size), 'DFU input');
      bounded(r?.status === 'ok' && r.data, 'Calculator declined the USB read request.');
      return new Uint8Array(r.data.buffer, r.data.byteOffset, r.data.byteLength);
    }
    async output(type, data, value = 0) {
      const r = await withTimeout(this.device.controlTransferOut({ requestType: 'class', recipient: 'interface',
        request: type, value, index: this.iface.number }, data), 'DFU control');
      bounded(r?.status === 'ok', 'Calculator declined the USB control request.');
    }
    async idle() {
      let s = (await this.input(REQUEST.state, 1))[0];
      if (s === 2) return;
      await this.output(REQUEST.abort, new Uint8Array());
      s = (await this.input(REQUEST.state, 1))[0];
      if (s === 10) { await this.output(REQUEST.clear, new Uint8Array()); s = (await this.input(REQUEST.state, 1))[0]; }
      bounded(s === 2, 'Calculator DFU state is not idle (' + s + ').');
    }
    async seek(address) {
      await this.idle();
      const cmd = new Uint8Array(5); cmd[0] = 0x21;
      new DataView(cmd.buffer).setUint32(1, address, true);
      // DFU block 0, SET_ADDRESS_POINTER: changes only the read pointer, not stored files.
      await this.output(REQUEST.dnload, cmd, 0);
      const deadline = Date.now() + 8000;
      while (true) {
        const r = await this.input(REQUEST.status, 6);
        bounded(r.length === 6 && r[0] === 0, 'Calculator reported a DFU error while setting its read pointer.');
        if (r[4] === 5) break;
        bounded(Date.now() < deadline && (r[4] === 4 || r[4] === 2), 'Calculator could not select a memory address (state ' + r[4] + ').');
        await delay(Math.max(1, Math.min(80, r[1] | r[2] << 8 | r[3] << 16)));
      }
      await this.idle();
    }
    async read(address, count, ram, update = () => {}) {
      bounded(count > 0 && count <= 0x10000, 'Refusing to read unexpected memory size.');
      await this.setAlt(ram ? 1 : 0);
      try {
        await this.seek(address);
        const result = new Uint8Array(count);
        let at = 0;
        for (let block = 2; at < count; block++) {
          const chunk = await this.input(REQUEST.upload, Math.min(blockSize, count - at), block);
          bounded(chunk.length, 'The calculator returned an empty memory block.');
          const n = Math.min(chunk.length, count - at);
          result.set(chunk.subarray(0, n), at);
          at += n;
          update(at / count);
        }
        await this.idle();
        return result;
      } finally {
        try { await this.idle(); } catch (_) {}
        if (ram) try { await this.setAlt(0); } catch (_) {}
      }
    }
    async model() {
      const r = await withTimeout(this.device.controlTransferIn({ requestType: 'standard', recipient: 'device',
        request: 6, value: 0x100, index: 0 }, 18), 'Read calculator model');
      bounded(r?.status === 'ok' && r.data?.byteLength >= 14, 'Unable to identify calculator model.');
      return modelOf(this.device, new Uint8Array(r.data.buffer, r.data.byteOffset, r.data.byteLength));
    }
    async storageInfo(model) {
      const s = await this.read(model.ramStart, 16, true);
      bounded(equalAt(s, 0, MAGIC_SLOT) && equalAt(s, 12, MAGIC_SLOT),
        'Firmware slot header not recognized. This firmware is not supported by experimental USB backup.');
      const userland = u32(s, 8);
      bounded(flashAddressAllowed(userland, 48, model), 'Firmware reported an invalid application-header address.');
      const u = await this.read(userland, 48, false);
      bounded(equalAt(u, 0, MAGIC_USERLAND), 'Firmware application header not recognized.');
      const addr = u32(u, 12), size = u32(u, 16);
      bounded(size > 100 && size <= 0xf000 && addr >= model.ramStart && addr + size + 8 <= model.ramEnd,
        'Firmware storage is outside known calculator RAM bounds.');
      // The extra-apps region and trampoline are described by the SAME verified
      // userland header. Never guess an app address from the calculator model.
      const flashStart = u32(u, 20), flashEnd = u32(u, 24);
      let ramStart = u32(u, 28), ramEnd = u32(u, 32), markerAt = 44;
      if (equalAt(u, 28, MAGIC_USERLAND)) {
        // Older header versions omit the external-apps RAM range.
        ramStart = null; ramEnd = null; markerAt = 28;
      } else if (equalAt(u, 36, MAGIC_USERLAND)) markerAt = 36;
      // A read-only backup only needs storageAddr/size. Do not make it fail
      // just because the extra-app installer header is unfamiliar.
      const recognizedApps = equalAt(u, markerAt, MAGIC_USERLAND);
      const trampolineStart = recognizedApps ? userland + markerAt + 12 : null;
      return { address: addr, size, flashStart, flashEnd,
        ramStart, ramEnd, trampolineStart, userland };
    }
    async close() { if (this.device.opened) try { await this.device.close(); } catch (_) {} }
  }
  async function requestAndRead(onProgress = () => {}) {
    bounded(window.isSecureContext && navigator.usb && typeof navigator.usb.requestDevice === 'function',
      'WebUSB needs Chrome or Edge on HTTPS or localhost, with USB access allowed.');
    // Request permission directly in the click event's user gesture.
    const device = await navigator.usb.requestDevice({ filters: [{ vendorId: VID, productId: PID }] });
    bounded(device.vendorId === VID && device.productId === PID, 'Unsupported USB device selected.');
    const reader = new Reader(device);
    try {
      onProgress('Connecting to calculator…');
      await reader.open();
      const model = await reader.model();
      onProgress('Reading firmware storage information…');
      const storage = await reader.storageInfo(model);
      onProgress('Reading calculator save scripts…');
      const raw = await reader.read(storage.address, storage.size + 8, true, (p) => onProgress('Reading storage… ' + Math.round(p * 100) + '%'));
      const records = parseStorageRecords(raw);
      const files = saveScriptsFromRecords(records);
      return { model: model.model, files, count: records.length };
    } finally { await reader.close(); }
  }
  // Reuse the SAME device after backing up, when the user chooses installation.
  // The caller must close this session, including on errors.
  // A new device object is provided by the browser after a physical replug.
  // Open it without a picker only when WebUSB already granted permission.
  async function openSession(device, onProgress = () => {}) {
    bounded(device && device.vendorId === VID && device.productId === PID,
      'Not an authorized NumWorks calculator.');
    const reader = new Reader(device);
    try {
      onProgress('Connecting to calculator…');
      await reader.open();
      const model = await reader.model();
      onProgress('Reading calculator storage…');
      const storage = await reader.storageInfo(model);
      const raw = await reader.read(storage.address, storage.size + 8, true,
        (p) => onProgress('Reading saves… ' + Math.round(p * 100) + '%'));
      const records = parseStorageRecords(raw);
      return { reader, device, model: model.model, modelInfo: model,
        storage, records, files: saveScriptsFromRecords(records), close: () => reader.close() };
    } catch (error) { await reader.close(); throw error; }
  }
  async function requestSession(onProgress = () => {}) {
    bounded(window.isSecureContext && navigator.usb && typeof navigator.usb.requestDevice === 'function',
      'WebUSB needs Chrome or Edge on HTTPS or localhost.');
    const device = await navigator.usb.requestDevice({ filters: [{ vendorId: VID, productId: PID }] });
    return openSession(device, onProgress);
  }
  window.NumPlayUsbRead = { requestAndRead, requestSession, openSession, Reader,
    parseStorageRecords, saveScriptsFromRecords };
})();
