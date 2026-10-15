import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
const config = JSON.parse(fs.readFileSync(new URL('./browser-paths.json', import.meta.url), 'utf8'));

const CPU = {arm64: 0x100000c, x64: 0x1000007, ia32: 7};

function historicalRuntime(candidate) {
  let resolved;
  try { resolved = fs.realpathSync(candidate); } catch { return true; }
  const parts = resolved.split(path.sep);
  return parts.some((part, index) => part.toLowerCase() === 'work' && /^\d+$/.test(parts[index + 1] || ''));
}

function hostBinaryCompatible(candidate) {
  // Chrome's Linux launchers can be scripts.  A script is only accepted after
  // a bounded `--version` invocation confirms it can start on this host.
  let header;
  try {
    const descriptor = fs.openSync(candidate, 'r');
    try { header = Buffer.alloc(4096); fs.readSync(descriptor, header, 0, header.length, 0); }
    finally { fs.closeSync(descriptor); }
  } catch { return false; }
  if (header.subarray(0, 2).toString('utf8') === '#!') {
    try { execFileSync(candidate, ['--version'], {stdio: 'ignore', timeout: 5000, windowsHide: true}); return true; }
    catch { return false; }
  }
  if (process.platform === 'win32') return header.subarray(0, 2).toString('ascii') === 'MZ';
  if (process.platform === 'darwin') {
    const magic = header.subarray(0, 4).toString('hex');
    if (magic === 'cffaedfe' || magic === 'cefaedfe') return header.readUInt32LE(4) === CPU[process.arch];
    if (magic === 'feedfacf' || magic === 'feedface') return header.readUInt32BE(4) === CPU[process.arch];
    if (magic !== 'cafebabe' && magic !== 'cafebabf') return false;
    const count = Math.min(header.readUInt32BE(4), 32);
    const stride = magic === 'cafebabf' ? 32 : 20;
    for (let index = 0; index < count; index += 1) {
      const offset = 8 + index * stride;
      if (offset + 4 <= header.length && header.readUInt32BE(offset) === CPU[process.arch]) return true;
    }
    return false;
  }
  if (header.subarray(0, 4).toString('hex') !== '7f454c46') return false;
  const machine = header[5] === 1 ? header.readUInt16LE(18) : header.readUInt16BE(18);
  return (process.arch === 'arm64' && machine === 183) || (process.arch === 'x64' && machine === 62) || (process.arch === 'ia32' && machine === 3);
}

function usable(candidate) {
  if (!candidate) return false;
  try {
    fs.accessSync(candidate, process.platform === 'win32' ? fs.constants.F_OK : fs.constants.X_OK);
    return fs.statSync(candidate).isFile() && !historicalRuntime(candidate) && hostBinaryCompatible(candidate);
  } catch { return false; }
}
export function chromiumExecutable() {
  if (process.env.AIPPT_CHROMIUM) {
    if (!usable(process.env.AIPPT_CHROMIUM)) throw new Error('AIPPT_CHROMIUM is not a compatible host Chromium executable');
    return process.env.AIPPT_CHROMIUM;
  }
  const candidates = (config[process.platform] || []).filter(p => !p.startsWith('$LOCALAPPDATA') || process.env.LOCALAPPDATA)
    .map(p => p.replace('$LOCALAPPDATA', process.env.LOCALAPPDATA || '').replace(/^~\//, os.homedir() + path.sep));
  for (const candidate of candidates) if (usable(candidate)) return candidate;
  if (process.platform !== 'win32') for (const name of ['google-chrome', 'chromium', 'chromium-browser', 'microsoft-edge']) {
    try { const candidate = execFileSync('which', [name], {encoding: 'utf8'}).trim(); if (usable(candidate)) return candidate; } catch {}
  }
  throw new Error('Chromium is unavailable; set AIPPT_CHROMIUM to a compatible Chrome/Edge executable');
}
