// Resolve bare imports through verified managed roots, never a previous Work's
// node_modules. No task dependency links or package trees are modified.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire, isBuiltin} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const roots = JSON.parse(process.env.AIPPT_NODE_MODULE_ROOTS || '[]').map(p => fs.realpathSync(p));
const historical = p => /[/\\]work[/\\]\d+[/\\].*[/\\]node_modules(?:[/\\]|$)/i.test(p);
const within = (p, root) => p === root || p.startsWith(root + path.sep);

export async function resolve(specifier, context, nextResolve) {
  if (isBuiltin(specifier) || /^(node|data):/.test(specifier)) return nextResolve(specifier, context);
  if (specifier.startsWith('.') || specifier.startsWith('/') || specifier.startsWith('file:')) {
    const result = await nextResolve(specifier, context);
    if (result.url.startsWith('file:') && historical(fs.realpathSync(fileURLToPath(result.url)))) {
      throw new Error('Historical Work node_modules cannot supply native task dependencies');
    }
    return result;
  }
  const candidates = [];
  if (context.parentURL?.startsWith('file:')) {
    const parent = fs.realpathSync(fileURLToPath(context.parentURL));
    if (roots.some(root => within(parent, root))) candidates.push(createRequire(context.parentURL));
  }
  for (const root of roots) candidates.push(createRequire(path.join(root, '.aippt-runtime.cjs')));
  for (const require of candidates) {
    let target;
    try { target = fs.realpathSync(require.resolve(specifier)); } catch { continue; }
    if (historical(target) || !roots.some(root => within(target, root))) continue;
    return nextResolve(pathToFileURL(target).href, context);
  }
  throw new Error(`Package ${specifier} is not in the verified Studio runtime; run runtime selection or add a reviewed host dependency`);
}
