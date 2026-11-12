import { build } from 'esbuild';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { readFileSync } from 'node:fs';
const require = createRequire(import.meta.url);
const base = dirname(require.resolve('@automerge/automerge'));
await build({
	entryPoints: [new URL('crdt.cjs', import.meta.url).pathname],
	outfile: new URL('../guest/workspace-crdt.cjs', import.meta.url).pathname,
	bundle: true, platform: 'node', format: 'cjs', target: 'node22',
	alias: { '@automerge/automerge': resolve(base, 'fullfat_base64.cjs') },
	banner: { js: `/* Bundled @automerge/automerge 3.5.0\n${readFileSync(resolve(base, '../../LICENSE'), 'utf8')} */` },
});
