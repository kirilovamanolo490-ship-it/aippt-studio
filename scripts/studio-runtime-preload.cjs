// CommonJS counterpart of the ESM loader; bare require() must obey the same
// managed dependency roots instead of discovering an old Work cache.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const original = Module._resolveFilename;
const roots = JSON.parse(process.env.AIPPT_NODE_MODULE_ROOTS || '[]').map(p => fs.realpathSync(p));
const within = (p, root) => p === root || p.startsWith(root + path.sep);
const historical = p => /[/\\]work[/\\]\d+[/\\].*[/\\]node_modules(?:[/\\]|$)/i.test(p);
const parents = roots.map(root => {
  const parent = new Module(path.join(root, '.aippt-runtime.cjs'));
  parent.filename = parent.id;
  parent.paths = Module._nodeModulePaths(root);
  return parent;
});

Module._resolveFilename = function(specifier, parent, isMain, options) {
  if (Module.isBuiltin(specifier)) return original.call(this, specifier, parent, isMain, options);
  if (path.isAbsolute(specifier) || specifier.startsWith('.')) {
    const target = original.call(this, specifier, parent, isMain, options);
    if (historical(fs.realpathSync(target))) throw new Error('Historical Work node_modules cannot supply task dependencies');
    return target;
  }
  const candidates = [...parents];
  let parentPath;
  if (parent?.filename) {
    try { parentPath = fs.realpathSync(parent.filename); }
    catch { parentPath = path.resolve(parent.filename); } // createRequire virtual parent
  }
  if (parentPath && roots.some(root => within(parentPath, root))) candidates.unshift(parent);
  for (const candidate of candidates) {
    let target;
    try { target = fs.realpathSync(original.call(this, specifier, candidate, false, options)); } catch { continue; }
    if (!historical(target) && roots.some(root => within(target, root))) return target;
  }
  throw new Error(`Package ${specifier} is not in the verified Studio runtime`);
};
