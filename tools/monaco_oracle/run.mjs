// Replays keystroke plans in Monaco and compares the buffer with the
// GhostWriter model's prediction after every step.
//
//   python tools/monaco_oracle/export_cases.py > cases.json
//   node tools/monaco_oracle/run.mjs cases.json
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const monacoRoot = process.env.MONACO_ROOT || path.join(here, 'node_modules', 'monaco-editor');
const configs = path.join(here, '..', '..', 'ghostwriter', 'data', 'language_configs.json');
const MIME = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json',
               '.ttf': 'font/ttf' };

const server = http.createServer((req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0]);
  let file;
  if (url === '/') file = path.join(here, 'page.html');
  else if (url === '/configs.json') file = configs;
  else if (url.startsWith('/monaco/')) file = path.join(monacoRoot, url.slice('/monaco/'.length));
  if (!file || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

const KEYS = { enter: 'Enter', tab: 'Tab', backspace: 'Backspace', delete: 'Delete', escape: 'Escape',
               left: 'ArrowLeft', right: 'ArrowRight', up: 'ArrowUp', down: 'ArrowDown', home: 'Home', end: 'End' };
function chord(name) {
  return name.split('+').map(p => ({ ctrl: 'Control', shift: 'Shift' }[p] || KEYS[p] || p.toUpperCase())).join('+');
}

const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const verbose = process.argv.includes('-v');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
await page.goto(`http://127.0.0.1:${port}/`);
await page.evaluate(() => window.ready);

let failed = 0;
for (const c of cases) {
  await page.evaluate(([lang, s]) => window.begin(lang, s), [c.language, c.settings]);
  let bad = null;
  for (let i = 0; i < c.steps.length; i++) {
    const [kind, value, expect] = c.steps[i];
    if (kind === 'type') await page.keyboard.type(value);
    else if (kind === 'key') await page.keyboard.press(chord(value));
    else if (kind === 'paste') await page.evaluate(t => window.paste(t), value);
    else if (kind === 'goto') {
      await page.keyboard.press('Control+G');
      await page.waitForTimeout(250);
      await page.keyboard.type(String(value));
      await page.keyboard.press('Enter');
      await page.waitForTimeout(150);
    }
    if (expect !== null && expect !== undefined) {
      const st = await page.evaluate(() => window.state());
      if (st.text !== expect.text || st.row !== expect.row || st.col !== expect.col) {
        bad = { step: i, action: [kind, value], expect, got: st, prev: c.steps.slice(Math.max(0, i - 6), i).map(s => s.slice(0, 2)) };
        break;
      }
    }
  }
  if (!bad && c.final !== undefined) {
    const st = await page.evaluate(() => window.state());
    if (st.text !== c.final) bad = { step: 'final', expect: { text: c.final }, got: st };
  }
  if (bad) {
    failed++;
    console.log(`FAIL ${c.name} at step ${bad.step} ${JSON.stringify(bad.action || '')}`);
    if (verbose) console.log(JSON.stringify(bad, null, 1));
    else {
      const tail = (t) => JSON.stringify(t.split('\n').slice(-4).join('\n'));
      const e = bad.expect, g = bad.got;
      if (e.row !== undefined) {
        const el = e.text.split('\n'), gl = g.text.split('\n');
        let i = 0; while (i < el.length && i < gl.length && el[i] === gl[i]) i++;
        console.log(`  model : ${JSON.stringify(el.slice(Math.max(0, i - 2), i + 2).join('\n'))} @${e.row},${e.col}`);
        console.log(`  editor: ${JSON.stringify(gl.slice(Math.max(0, i - 2), i + 2).join('\n'))} @${g.row},${g.col}`);
      } else {
        const el = e.text.split('\n'), gl = g.text.split('\n');
        let i = 0; while (i < el.length && i < gl.length && el[i] === gl[i]) i++;
        console.log(`  first differing line ${i + 1}: want ${JSON.stringify(el[i])} got ${JSON.stringify(gl[i])}`);
      }
    }
  } else if (verbose) console.log(`ok   ${c.name} (${c.steps.length} steps)`);
}
console.log(`${cases.length - failed}/${cases.length} cases match the editor`);
await browser.close();
server.close();
process.exit(failed ? 1 : 0);
