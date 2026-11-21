/**
 * Behaviour tests for the inline script in index.html.
 *
 * The page ships one small inline script and it has never been run under test.
 * There is no jsdom here and no package to install, so this file builds the
 * smallest DOM the script actually touches and executes the script inside it.
 * That is enough to catch the two things that were wrong:
 *
 *   1. The scroll spy walked a hard-coded list of section ids whose order did
 *      not match the document, so the last id in the list always won and the
 *      nav could never highlight "services" or "about".
 *   2. The marquee clones the work cards and marks the copies aria-hidden, but
 *      the copies are still <a href> elements, so every card appeared twice in
 *      the tab order.
 *
 * Run: node --test tests/
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const HTML = readFileSync(join(ROOT, 'index.html'), 'utf8');

function inlineScript() {
  const match = HTML.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/);
  assert.ok(match, 'index.html has no inline script');
  return match[1];
}

/** Every id="..." in the document, in the order the browser would see them. */
function documentIds() {
  return [...HTML.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);
}

/** The nav entries, in order. */
function navHrefs() {
  const nav = HTML.match(/<nav class="nav-left">([\s\S]*?)<\/nav>/);
  assert.ok(nav, 'no .nav-left nav');
  return [...nav[1].matchAll(/href="#([^"]+)"/g)].map((m) => m[1]);
}

// ---------------------------------------------------------------------------
// a DOM small enough to be obvious, large enough to run the script
// ---------------------------------------------------------------------------

function element({ id = null, href = null, top = 0, classes = [], children = [] } = {}) {
  const classSet = new Set(classes);
  const attrs = new Map();
  if (id) attrs.set('id', id);
  if (href) attrs.set('href', href);
  const el = {
    id,
    children,
    _top: top,
    _classes: classSet,
    _attrs: attrs,
    getBoundingClientRect: () => ({ top: el._top }),
    addEventListener: () => {},
    getAttribute: (name) => (attrs.has(name) ? attrs.get(name) : null),
    setAttribute: (name, value) => attrs.set(name, value),
    appendChild: (child) => el.children.push(child),
    cloneNode: () => {
      const copy = element({
        id,
        href,
        top,
        classes: [...classSet],
        children: children.map((c) => c.cloneNode()),
      });
      for (const [k, v] of attrs) copy.setAttribute(k, v);
      return copy;
    },
    classList: {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
      toggle: (c, on) => (on ? classSet.add(c) : classSet.delete(c)),
    },
  };
  return el;
}

function build({ tops = {} } = {}) {
  const ids = documentIds();
  const sections = ids
    .filter((id) => ['projects', 'stack', 'services', 'about', 'connect'].includes(id))
    .map((id) => element({ id, top: tops[id] ?? 10_000 }));

  const navLinks = navHrefs().map((target) => element({ href: '#' + target }));

  // the work track: two cards, each an <a href>
  const cards = [
    element({ href: 'https://github.com/EVM0x/splitmon' }),
    element({ href: 'https://github.com/EVM0x/poker-bot' }),
  ];
  const track = element({ id: 'wt', children: cards });

  const byId = new Map(sections.map((s) => [s.id, s]));
  byId.set('wt', track);

  const scrollHandlers = [];
  const document = {
    getElementById: (id) => byId.get(id) ?? null,
    querySelectorAll: (selector) => {
      if (selector === '.nav-left a') return navLinks;
      if (selector === '.sec[id]') return sections;
      return [];
    },
  };
  const window = {
    innerHeight: 800,
    addEventListener: (type, fn) => {
      if (type === 'scroll') scrollHandlers.push(fn);
    },
    dispatchEvent: (event) => {
      if (event.type === 'scroll') scrollHandlers.forEach((fn) => fn());
    },
  };

  const context = vm.createContext({
    document,
    window,
    setTimeout: (fn) => fn(),
    Event: class Event {
      constructor(type) {
        this.type = type;
      }
    },
    Array,
    Map,
    Set,
    Math,
    console,
  });
  vm.runInContext(inlineScript(), context, { filename: 'index.html <script>' });

  return { sections, navLinks, track, window, scrollTo };
}

/** Move the page so each section sits at its given offset, then fire scroll. */
function scrollTo(dom, tops) {
  for (const section of dom.sections) section._top = tops[section.id] ?? 10_000;
  dom.window.dispatchEvent({ type: 'scroll' });
}

function active(dom) {
  return dom.navLinks.filter((a) => a.classList.contains('active')).map((a) => a.getAttribute('href'));
}

// ---------------------------------------------------------------------------
// the scroll spy
// ---------------------------------------------------------------------------

// The nav deliberately reads Projects, Services, About, Stack while the page
// reads projects, stack, services, about. That mismatch is what broke the old
// spy, so the tests below check the behaviour directly rather than the order.

test('scrolling to services highlights services', () => {
  const dom = build();
  scrollTo(dom, { projects: -3000, stack: -2000, services: -500, about: 900, connect: 3000 });
  assert.deepEqual(active(dom), ['#services']);
});

test('scrolling to about highlights about', () => {
  const dom = build();
  scrollTo(dom, { projects: -4000, stack: -3000, services: -2000, about: -100, connect: 2500 });
  assert.deepEqual(active(dom), ['#about']);
});

test('scrolling to stack highlights stack', () => {
  const dom = build();
  scrollTo(dom, { projects: -2000, stack: -300, services: 800, about: 1900, connect: 4000 });
  assert.deepEqual(active(dom), ['#stack']);
});

test('scrolling to projects highlights projects', () => {
  const dom = build();
  scrollTo(dom, { projects: 100, stack: 2000, services: 3000, about: 4000, connect: 5000 });
  assert.deepEqual(active(dom), ['#projects']);
});

test('at most one nav entry is active at a time', () => {
  const dom = build();
  for (const offset of [-5000, -3000, -1000, 0, 1000, 5000]) {
    scrollTo(dom, {
      projects: offset,
      stack: offset + 1000,
      services: offset + 2000,
      about: offset + 3000,
      connect: offset + 4000,
    });
    assert.ok(active(dom).length <= 1, `got ${JSON.stringify(active(dom))}`);
  }
});

test('above every section nothing is highlighted', () => {
  const dom = build();
  scrollTo(dom, { projects: 5000, stack: 6000, services: 7000, about: 8000, connect: 9000 });
  assert.deepEqual(active(dom), []);
});

// ---------------------------------------------------------------------------
// the marquee clones
// ---------------------------------------------------------------------------

test('the work track is doubled for the seamless loop', () => {
  const dom = build();
  assert.equal(dom.track.children.length, 4, 'two cards should become four');
});

test('the clones are hidden from assistive technology', () => {
  const dom = build();
  for (const clone of dom.track.children.slice(2)) {
    assert.equal(clone.getAttribute('aria-hidden'), 'true');
  }
});

test('the clones are not in the tab order', () => {
  // An aria-hidden element that is still focusable is a real keyboard trap:
  // every work card shows up twice when tabbing through the page.
  const dom = build();
  for (const clone of dom.track.children.slice(2)) {
    assert.equal(
      clone.getAttribute('tabindex'),
      '-1',
      'a duplicated card is still reachable by keyboard',
    );
  }
});

test('the originals keep their place in the tab order', () => {
  const dom = build();
  for (const original of dom.track.children.slice(0, 2)) {
    assert.equal(original.getAttribute('tabindex'), null);
    assert.ok(original.getAttribute('href'));
  }
});
