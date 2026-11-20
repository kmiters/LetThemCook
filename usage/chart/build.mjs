import {build} from 'esbuild';
import {readFileSync, readdirSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';

const licenseFile = '../chart-LICENSES.txt';
await build({
  entryPoints:['dashboard.jsx'], outfile:'../usage-chart.js', bundle:true, minify:true,
  format:'iife', target:'es2022', jsx:'automatic', legalComments:'inline',
  alias:{'@':resolve('evilcharts')}, define:{'process.env.NODE_ENV':'"production"'},
  banner:{js:'/*! Third-party notices: chart-LICENSES.txt */'},
});

// Ship copyrights and license text alongside the compiled bundle, including the
// individual d3 notices bundled by victory-vendor. Native esbuild binaries are build-only.
const lock = JSON.parse(readFileSync('package-lock.json','utf8'));
const notices = ['Evil Charts\n' + readFileSync('evilcharts/LICENSE','utf8')];
function include(directory, label) {
  const files = readdirSync(directory).filter(f => /^(license|licence|notice|copyright)(\.|$|notice)/i.test(f)).sort();
  if (!files.length) throw new Error('Missing license for ' + label);
  notices.push(label + '\n' + files.map(f=>readFileSync(directory+'/'+f,'utf8')).join('\n'));
}
for (const [dir,info] of Object.entries(lock.packages).sort(([a],[b])=>a.localeCompare(b,'en'))) {
  if (!dir || dir.includes('/@esbuild/')) continue;
  include(dir, dir.split('node_modules/').at(-1) + ' ' + info.version);
}
for (const name of readdirSync('node_modules/victory-vendor/lib-vendor').sort()) {
  include('node_modules/victory-vendor/lib-vendor/'+name, 'victory-vendor / '+name);
}
const normalized = notices.map(notice => notice.split('\n')
  .map(line => line.trimEnd()).join('\n').trimEnd());
writeFileSync(licenseFile, normalized.join('\n\n----------\n\n')+'\n');
