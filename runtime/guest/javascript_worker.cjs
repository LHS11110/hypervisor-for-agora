// This process is unprivileged inside the offline VM; vm.Context is not a sandbox.
const fs = require('node:fs');
const vm = require('node:vm');
const readline = require('node:readline');
const { createRequire } = require('node:module');
const control = Number(process.argv[2]);
const context = vm.createContext({ console, Buffer, process, URL, URLSearchParams,
  require: createRequire('/workspace/blueprint.js'), __dirname: '/workspace', __filename: '/workspace/blueprint.js',
  setTimeout, clearTimeout, setInterval, clearInterval });
// Keep native lexical declaration semantics: redeclaring const/let reports an error.
// Assignments, objects, closures and function declarations persist without replay.
const reader = readline.createInterface({ input: fs.createReadStream(null, { fd: control, autoClose: false }) });
(async () => {
  for await (const line of reader) {
    let exitCode = 0;
    try { await new vm.Script((process.argv[3] === 'true' ? '"use strict";\n' : '') + JSON.parse(line).code, { filename: '/workspace/blueprint.js' }).runInContext(context); }
    catch (error) { console.error(error.stack || String(error)); exitCode = 1; }
    // Drain both output streams before acknowledging this cell.
    await Promise.all([new Promise(r => process.stdout.write('', r)), new Promise(r => process.stderr.write('', r))]);
    fs.writeSync(control, JSON.stringify({ exitCode }) + '\n');
  }
})();
