// Known-answer tests for the redirect script inside 404.html.
// Run: node tests/redirect.test.js   (no packages needed)
//
// The script is taken out of 404.html and run against a fake location and
// document, so the test covers the real code, not a copy of it.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ORIGIN = 'https://niratorn.github.io';
const html = fs.readFileSync(path.join(__dirname, '..', '404.html'), 'utf8');

let passed = 0;
let failed = 0;
function check(name, cond, detail) {
  if (cond) { passed += 1; return; }
  failed += 1;
  console.log('FAIL ' + name + (detail === undefined ? '' : '  ' + JSON.stringify(detail)));
}

// ---------- take the script and the site list out of the page ----------
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
check('exactly one inline script', scripts.length === 1, scripts.length);
const script = scripts[0] || '';

const listMatch = /var SITES = (\[[^\]]*\]);/.exec(script);
check('SITES list found', !!listMatch);
const SITES = listMatch ? JSON.parse(listMatch[1].replace(/'/g, '"')) : [];
check('SITES is not empty', SITES.length > 0);
check('OBdating is in SITES', SITES.includes('OBdating'), SITES);

// Run the page script the way a browser would for one address.
function open(pathname, search = '', hash = '') {
  const seen = { replaced: null, href: null, timers: [] };
  const root = { className: '' };
  const context = {
    location: { pathname, search, hash, replace(url) { seen.replaced = url; } },
    document: {
      documentElement: root,
      getElementById(id) {
        if (id !== 'go') { throw new Error('unexpected element ' + id); }
        return { setAttribute(name, value) { if (name === 'href') { seen.href = value; } } };
      },
    },
    setTimeout(fn, ms) { seen.timers.push({ fn, ms }); },
  };
  vm.runInNewContext(script, context);
  return {
    target: seen.replaced,
    href: seen.href,
    cls: root.className,
    timers: seen.timers,
    // what the page looks like once the slow-network timer has fired
    afterTimers() { seen.timers.forEach((t) => t.fn()); return root.className; },
  };
}

// Every upper/lower case spelling of a name, e.g. 256 of them for "obdating".
function caseVariants(name) {
  let out = [''];
  for (const ch of name) {
    const lo = ch.toLowerCase();
    const up = ch.toUpperCase();
    const next = [];
    for (const prefix of out) {
      next.push(prefix + lo);
      if (up !== lo) { next.push(prefix + up); }
    }
    out = next;
  }
  return out;
}

// ---------- 1. the two addresses the owner asked for ----------
for (const typed of ['/obdating', '/obdating/', '/OBDATING', '/OBDATING/']) {
  const r = open(typed);
  check('asked for: ' + typed, r.target === '/OBdating/', r.target);
}

// ---------- 2. every spelling of every site name ----------
for (const real of SITES) {
  check('site name has no slash: ' + real, !real.includes('/') && real.length > 0);
  const variants = caseVariants(real);
  check('variants include the real name: ' + real, variants.includes(real));
  let moved = 0;
  for (const v of variants) {
    for (const tail of ['', '/']) {
      const r = open('/' + v + tail);
      if (v === real) {
        // exact spelling that still reached the 404 page: stay, never loop
        check('exact name stays put: /' + v + tail, r.target === null && r.cls === 'notfound', r);
      } else {
        moved += 1;
        check('variant goes to the real name: /' + v + tail, r.target === '/' + real + '/', r.target);
      }
    }
  }
  check('all other spellings moved: ' + real, moved === (variants.length - 1) * 2, moved);
}
check('obdating has 256 spellings', caseVariants('obdating').length === 256);

// ---------- 3. the rest of the address is kept as typed ----------
const kept = [
  ['/obdating/index.html', '', '', '/OBdating/index.html'],
  ['/OBDATING/CLAUDE.md', '', '', '/OBdating/CLAUDE.md'],
  ['/obdating/', '?v=ac4df28', '', '/OBdating/?v=ac4df28'],
  ['/obdating', '?v=ac4df28', '#rules', '/OBdating/?v=ac4df28#rules'],
  ['/Obdating/', '', '#rules', '/OBdating/#rules'],
  ['/OBDATING/fonts/fonts.css', '?a=1&b=2', '', '/OBdating/fonts/fonts.css?a=1&b=2'],
  ['/obdating/a/b/c/', '', '', '/OBdating/a/b/c/'],
];
for (const [p, s, h, want] of kept) {
  const r = open(p, s, h);
  check('kept: ' + p + s + h, r.target === want, r.target);
  check('manual link matches: ' + p + s + h, r.href === want, r.href);
}

// ---------- 4. addresses that must NOT be redirected ----------
const stay = [
  '/', '/index.html', '/404.html', '/favicon.ico', '/robots.txt',
  '/obdatin', '/obdating2', '/xobdating', '/ob-dating', '/ob/dating', '/obdating.html',
  '/a/obdating', '/a/obdating/', '/nst-reader/', '/nst-reader/obdating',
  '/OBdating', '/OBdating/', '/OBdating/missing.html', '/OBdating/obdating/',
  '//obdating', '//evil.example/obdating', '',
];
for (const p of stay) {
  const r = open(p, '?x=1', '#y');
  check('stays: ' + JSON.stringify(p), r.target === null && r.cls === 'notfound' && r.href === null, r);
  check('no timer when staying: ' + JSON.stringify(p), r.timers.length === 0, r.timers.length);
}

// ---------- 5. a redirect never leaves this website and never loops ----------
const nasty = [
  ['/obdating//evil.example/x', '', ''],
  ['/obdating/\\evil.example', '', ''],
  ['/obdating/%2F%2Fevil.example', '', ''],
  ['/obdating/..//evil.example', '', ''],
  ['/obdating/', '?next=https://evil.example', ''],
  ['/obdating/', '', '#//evil.example'],
  ['/obdating/javascript:alert(1)', '', ''],
  ['/OBDATING', '?//evil.example', '#javascript:alert(1)'],
];
const allMoved = [...kept.map(([p, s, h]) => [p, s, h]), ...nasty];
for (const [p, s, h] of allMoved) {
  const r = open(p, s, h);
  const label = p + s + h;
  check('moved: ' + label, typeof r.target === 'string', r.target);
  if (typeof r.target !== 'string') { continue; }
  check('target starts with /OBdating/: ' + label, r.target.startsWith('/OBdating/'), r.target);
  const url = new URL(r.target, ORIGIN);
  check('target stays on this website: ' + label, url.origin === ORIGIN, url.href);
  check('target is still inside the app folder: ' + label,
    url.pathname === '/OBdating' || url.pathname.startsWith('/OBdating/') || p.includes('..'), url.pathname);
  // the corrected address, opened again, must not be moved a second time
  const again = open(url.pathname, url.search, url.hash);
  check('no second hop: ' + label, again.target === null, again.target);
}

// ---------- 6. the slow-network message ----------
{
  const r = open('/obdating', '?v=1', '#rules');
  check('nothing shown at first', r.cls === '', r.cls);
  check('one timer of 1.5 s', r.timers.length === 1 && r.timers[0].ms === 1500, r.timers.map((t) => t.ms));
  check('timer shows the moving message', r.afterTimers() === 'moving');
}

// ---------- 7. the page itself ----------
check('page is kept out of search results', /<meta name="robots" content="noindex, nofollow">/.test(html));
check('page loads nothing from other websites', !/(src|href)\s*=\s*["']?(https?:)?\/\//i.test(html));
check('no-JavaScript fallback shows the not-found text', /<noscript><style>#notfound \{ display: block; \}<\/style><\/noscript>/.test(html));
check('page is larger than 512 bytes', Buffer.byteLength(html, 'utf8') > 512, Buffer.byteLength(html, 'utf8'));
check('no en dash or em dash', !new RegExp('[' + String.fromCharCode(0x2013, 0x2014) + ']').test(html));

console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed === 0 ? 0 : 1);
