import { registerHooks } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import ts from 'typescript';
const root = path.resolve(import.meta.dirname, '..');
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('@/')) specifier = pathToFileURL(path.join(root, 'src', specifier.slice(2))).href;
    if ((specifier.startsWith('file:') || specifier.startsWith('.')) && context.parentURL) {
      const url = new URL(specifier, context.parentURL);
      const filename = fileURLToPath(url);
      if (!existsSync(filename)) {
        for (const suffix of ['.ts', '.tsx']) {
          if (existsSync(filename + suffix)) return { url: pathToFileURL(filename + suffix).href, shortCircuit: true };
        }
      }
    }
    return next(specifier, context);
  },
  load(url, context, next) {
    if (!url.startsWith('file:')) return next(url, context);
    const filename = fileURLToPath(url);
    if (filename.endsWith('.css')) return { format: 'module', source: '', shortCircuit: true };
    if (/\.tsx?$/.test(filename) && !filename.includes('/node_modules/')) {
      const source = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText;
      return { format: 'module', source: filename.endsWith('/vite.config.ts') ? 'const __dirname = import.meta.dirname;\n' + source : source, shortCircuit: true };
    }
    return next(url, context);
  },
});
