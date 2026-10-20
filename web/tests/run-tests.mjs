import { spawnSync } from 'node:child_process';
import { lstatSync, mkdtempSync, readdirSync, realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const root = realpathSync(path.resolve(import.meta.dirname, '..'));
function entryExists(filename) {
  try { lstatSync(filename); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}
const configured = process.env.CRAFT_RELEASE_TEST_OUTPUT;
const output = configured === undefined
  ? path.join(mkdtempSync(path.join(realpathSync(tmpdir()), 'craft-release-test-')), 'dist')
  : configured;
if (!path.isAbsolute(output)) throw new Error('CRAFT_RELEASE_TEST_OUTPUT must be an absolute path.');
if (entryExists(output)) throw new Error('CRAFT_RELEASE_TEST_OUTPUT must not exist.');
// Resolve existing ancestors so a symlink cannot route output into this source tree.
let ancestor = path.dirname(output);
while (!entryExists(ancestor)) ancestor = path.dirname(ancestor);
const canonicalOutput = path.resolve(realpathSync(ancestor), path.relative(ancestor, output));
const relative = path.relative(root, canonicalOutput);
if (relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative))) {
  throw new Error('CRAFT_RELEASE_TEST_OUTPUT must be outside the source tree.');
}
const tests = readdirSync(path.join(root, 'tests'), { withFileTypes: true })
  .filter(entry => entry.isFile() && entry.name.endsWith('.test.mjs'))
  .map(entry => path.join(root, 'tests', entry.name)).sort();
if (!tests.length) throw new Error('No tests/*.test.mjs files found.');
console.info('Production artifact test output: ' + output);
const result = spawnSync(process.execPath, [
  '--import', path.join(root, 'tests/typescript-loader.mjs'), '--test', ...tests,
], { cwd: root, env: { ...process.env, CRAFT_RELEASE_TEST_OUTPUT: output }, stdio: 'inherit' });
if (result.error) console.error(result.error);
if (result.signal) console.error('Test process ended with signal ' + result.signal);
process.exitCode = result.status ?? 1;
