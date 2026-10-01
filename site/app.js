// What Congress Asked — oversight letters from members' own websites.
//
// One static page over a small index plus one file per member, fetched when
// that member is opened. Nothing is kept outside the URL, so every view is a
// link and the back button works:
//
//   ?view=members                 the members, ranked            (default)
//   ?member=<bioguide>            one member, their letters
//   ?member=<id>&as=list          the same, as a table
//   ?view=recipients              who Congress writes to
//   ?to=<name>                    one recipient
//   ?view=timeline                letters by year
//   ?year=<yyyy>                  one year
//   ?view=search&q=<text>         search
//   ?view=about                   method and caveats

const DATA = './data/';
const WALL = 48;       // letters shown on a member page before "show more"
const CARDS = 60;      // member cards before "show more"
const ROWS = 150;      // table rows before "show more"

let INDEX, MEMBERS;
let byId = new Map();
const memberCache = new Map();
let shown = 0;

// ------------------------------------------------------------------ helpers

const $ = (sel, root = document) => root.querySelector(sel);
const num = n => (n ?? 0).toLocaleString('en-US');

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

// Letters are arrays; the column order comes from index.json so the page and
// the build cannot disagree about it.
const F = {};
const get = (row, field) => row[F[field]];

// The letter as the Wayback Machine serves it. `id_` asks for the original
// bytes rather than a rewritten page, which for a PDF is what you want.
const waybackUrl = row =>
  `https://web.archive.org/web/${get(row, 'timestamp')}id_/${get(row, 'key')}`;

const thumbUrl = row => {
  const digest = get(row, 'digest');
  return digest ? `${INDEX.thumb_base || 'thumbs/'}${digest}.jpg` : null;
};

// A date the slug stated, versus one inferred from when Wayback saw the file.
// The difference matters enough to show: "by 12 Mar 2016" is not a letter date.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function when(row, long = false) {
  const iso = get(row, 'date');
  const precision = get(row, 'precision');
  if (!iso) return '—';
  const [y, m, d] = iso.split('-');
  const month = MONTHS[Number(m) - 1] || '';
  if (precision === 'day') return long ? `${Number(d)} ${month} ${y}` : `${month} ${y}`;
  if (precision === 'month') return `${month} ${y}`;
  return long ? `in the archive by ${Number(d)} ${month} ${y}` : `by ${month} ${y}`;
}

function chamber(m) { return m.chamber === 'sen' ? 'Senate' : 'House'; }

function partyClass(party) {
  const p = (party || '').toLowerCase();
  if (p.startsWith('democrat')) return 'b-dem';
  if (p.startsWith('republican')) return 'b-rep';
  return 'b-ind';
}

function memberBadges(m) {
  return [
    el('span', { class: `badge ${m.chamber === 'sen' ? 'b-sen' : 'b-hou'}` },
      chamber(m)),
    el('span', { class: `badge ${partyClass(m.party)}` },
      (m.party || '').slice(0, 11)),
    el('span', { class: 'chip' },
      m.district != null && m.district !== '' ? `${m.state}-${m.district}` : m.state),
  ];
}

// -------------------------------------------------------------------- state

function params() { return new URLSearchParams(location.search); }

function go(next, replace = false) {
  const url = next.toString() ? `?${next}` : location.pathname;
  history[replace ? 'replaceState' : 'pushState']({}, '', url);
  render();
}

function link(paramsObj, cls, ...kids) {
  const next = new URLSearchParams();
  for (const [k, v] of Object.entries(paramsObj)) if (v != null) next.set(k, v);
  return el('a', {
    class: cls,
    href: `?${next}`,
    onclick: e => {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      shown = 0;
      go(next);
    },
  }, ...kids);
}

async function lettersOf(bioguide) {
  if (memberCache.has(bioguide)) return memberCache.get(bioguide);
  const response = await fetch(`${DATA}member/${bioguide}.json`);
  const rows = response.ok ? await response.json() : [];
  memberCache.set(bioguide, rows);
  return rows;
}

function tally(rows, field) {
  const counts = new Map();
  for (const row of rows) {
    const v = get(row, field);
    if (v) counts.set(v, (counts.get(v) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function yearsOf(rows) {
  const counts = new Map();
  for (const row of rows) {
    const iso = get(row, 'date');
    if (!iso) continue;
    const y = Number(iso.slice(0, 4));
    counts.set(y, (counts.get(y) || 0) + 1);
  }
  return counts;
}

// ---------------------------------------------------------------- fragments

function sparkline(counts, from, to) {
  const span = to - from + 1;
  if (span <= 0 || !counts.size) return el('span');
  const max = Math.max(...counts.values());
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'spark');
  svg.setAttribute('viewBox', `0 0 ${span * 3} 22`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  for (const [year, n] of counts) {
    const h = Math.max(1.5, (n / max) * 22);
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', (year - from) * 3);
    rect.setAttribute('y', 22 - h);
    rect.setAttribute('width', 2.2);
    rect.setAttribute('height', h);
    svg.append(rect);
  }
  return svg;
}

function roleTag(role) {
  if (!role) return null;
  const cls = role === 'leads' ? 'r-leads' : role === 'joins' ? 'r-joins' : '';
  return el('span', { class: `role ${cls}` }, role);
}

// The letter itself. For most of these the scan IS the document — only 24% of
// the PDFs carry a text layer — so the page is the primary thing to show.
function shot(row) {
  const src = thumbUrl(row);
  const subject = get(row, 'subject');
  const recipient = get(row, 'recipient');
  const caption = subject || recipient || get(row, 'key').split('/').pop();
  return el('a', {
    class: 'shot',
    href: waybackUrl(row),
    target: '_blank',
    rel: 'noopener',
    title: get(row, 'key'),
  },
    el('span', { class: 'frame' },
      src
        ? el('img', { src, loading: 'lazy', alt: '', width: 170, height: 220 })
        : el('span', { class: 'none' },
          get(row, 'kind') === 'pdf' ? 'PDF — not yet rendered' : 'Press release')),
    el('span', { class: 'cap' }, caption),
    el('span', { class: 'when' }, when(row),
      recipient && subject ? ` · ${recipient}` : ''),
  );
}

function wall(rows) {
  const limit = shown || WALL;
  const box = el('div', { class: 'wall' },
    ...rows.slice(0, limit).map(shot));
  const out = el('div', {}, box);
  if (rows.length > limit) {
    out.append(el('button', {
      class: 'more',
      onclick: () => { shown = limit + WALL; render(); },
    }, `Show more — ${num(rows.length - limit)} more letters`));
  }
  return out;
}

function table(rows, opts = {}) {
  const limit = shown || ROWS;
  const body = el('tbody');
  for (const row of rows.slice(0, limit)) {
    const member = opts.member || byId.get(opts.lookup?.(row));
    body.append(el('tr', {},
      el('td', { class: 'wh' }, when(row)),
      opts.showMember
        ? el('td', { class: 'who' },
          member ? link({ member: member.id }, null, member.name) : '—')
        : null,
      el('td', { class: 'who' },
        get(row, 'recipient')
          ? link({ to: get(row, 'recipient') }, null, get(row, 'recipient'))
          : el('span', { class: 'faint' }, 'not named')),
      el('td', { class: 'sub2' },
        get(row, 'subject') || el('span', { class: 'faint' }, '—'),
        get(row, 'role') ? ' ' : null, roleTag(get(row, 'role'))),
      el('td', {},
        el('a', {
          href: waybackUrl(row), target: '_blank', rel: 'noopener',
          class: 'small',
        }, get(row, 'kind') === 'pdf' ? 'PDF' : 'page')),
    ));
  }
  const out = el('div', {},
    el('table', { class: 'letters' },
      el('thead', {}, el('tr', {},
        el('th', {}, 'Date'),
        opts.showMember ? el('th', {}, 'Member') : null,
        el('th', {}, 'Written to'),
        el('th', {}, 'Subject, from the filename'),
        el('th', {}, ''))),
      body));
  if (rows.length > limit) {
    out.append(el('button', {
      class: 'more',
      onclick: () => { shown = limit + ROWS; render(); },
    }, `Show more — ${num(rows.length - limit)} more letters`));
  }
  return out;
}

function rankList(entries, makeLink, limit = 14) {
  const max = entries.length ? entries[0][1] : 1;
  const list = el('ul', { class: 'rank' });
  for (const [name, n] of entries.slice(0, limit)) {
    list.append(el('li', {},
      el('span', { class: 'nm' }, makeLink(name)),
      el('span', { class: 'track' },
        el('i', { style: `width:${Math.max(4, (n / max) * 100)}%` })),
      el('span', { class: 'n tab' }, num(n)),
    ));
  }
  return list;
}

// -------------------------------------------------------------------- views

function viewMembers() {
  const p = params();
  const q = (p.get('q') || '').toLowerCase();
  const ch = p.get('chamber') || '';
  const party = p.get('party') || '';
  const sort = p.get('sort') || 'letters';

  let list = MEMBERS.filter(m => {
    if (q && !m.name.toLowerCase().includes(q)
        && !(m.state || '').toLowerCase().includes(q)) return false;
    if (ch && m.chamber !== ch) return false;
    if (party && !(m.party || '').startsWith(party)) return false;
    return true;
  });
  if (sort === 'name') list = [...list].sort((a, b) => a.last.localeCompare(b.last));
  else if (sort === 'state') list = [...list].sort((a, b) =>
    (a.state || '').localeCompare(b.state || '') || a.last.localeCompare(b.last));
  else list = [...list].sort((a, b) => b.letters - a.letters);

  const setParam = (key, value) => {
    const next = params();
    if (value) next.set(key, value); else next.delete(key);
    go(next, true);
  };

  const controls = el('div', { class: 'controls' },
    el('input', {
      type: 'search', value: p.get('q') || '',
      placeholder: 'Filter by name or state',
      oninput: e => {
        const next = params();
        if (e.target.value) next.set('q', e.target.value); else next.delete('q');
        history.replaceState({}, '', `?${next}`);
        render();
        const box = $('.controls input[type=search]');
        if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
      },
    }),
    el('select', { onchange: e => setParam('chamber', e.target.value) },
      el('option', { value: '', selected: !ch }, 'Both chambers'),
      el('option', { value: 'sen', selected: ch === 'sen' }, 'Senate'),
      el('option', { value: 'rep', selected: ch === 'rep' }, 'House')),
    el('select', { onchange: e => setParam('party', e.target.value) },
      el('option', { value: '', selected: !party }, 'Any party'),
      el('option', { value: 'Democrat', selected: party === 'Democrat' }, 'Democrat'),
      el('option', { value: 'Republican', selected: party === 'Republican' }, 'Republican'),
      el('option', { value: 'Independent', selected: party === 'Independent' }, 'Independent')),
    el('select', { onchange: e => setParam('sort', e.target.value) },
      el('option', { value: 'letters', selected: sort === 'letters' }, 'Most letters'),
      el('option', { value: 'name', selected: sort === 'name' }, 'By surname'),
      el('option', { value: 'state', selected: sort === 'state' }, 'By state')),
    el('span', { class: 'count tab' },
      `${num(list.length)} of ${num(MEMBERS.length)} members`),
  );

  const limit = shown || CARDS;
  const grid = el('div', { class: 'grid' });
  const [from, to] = INDEX.span || [2000, 2026];
  for (const m of list.slice(0, limit)) {
    grid.append(link({ member: m.id }, 'card',
      el('span', { class: 'top' },
        el('span', { class: 'nm' }, m.name),
        el('span', { class: 'ct tab' }, num(m.letters))),
      el('span', { class: 'meta' }, memberBadges(m),
        m.span ? el('span', {}, `${m.span[0]}–${m.span[1]}`) : null),
      m.top.length
        ? el('span', { class: 'with' }, `wrote to ${m.top.slice(0, 2).join(', ')}`)
        : el('span', { class: 'with faint' }, 'no recipients named in filenames'),
    ));
  }

  const out = el('div', {},
    el('p', { class: 'hero' },
      'What has your member of Congress demanded, and of whom?'),
    el('p', { class: 'sub' },
      el('b', {}, num(INDEX.letters)), ' letters published on ',
      el('b', {}, num(INDEX.members_with_letters)),
      ' members’ own websites and preserved by the Wayback Machine, ',
      `${(INDEX.span || [])[0]}–${(INDEX.span || [])[1]}. `,
      'Member sites are wiped when a seat changes hands, so for many of these the archive is the only copy left.'),
    controls, grid);

  if (list.length > limit) {
    out.append(el('button', {
      class: 'more',
      onclick: () => { shown = limit + CARDS; render(); },
    }, `Show more — ${num(list.length - limit)} more members`));
  }
  if (!list.length) out.append(el('div', { class: 'empty' }, 'No members match those filters.'));
  return out;
}

async function viewMember(id) {
  const m = byId.get(id);
  if (!m) return el('div', { class: 'empty' }, 'No such member.');
  const rows = await lettersOf(id);
  if (!rows.length) {
    return el('div', {},
      link({ view: 'members' }, 'back', '← All members'),
      el('div', { class: 'panel' },
        el('div', { class: 'dhead' }, el('h2', {}, m.name),
          el('span', { class: 'meta' }, memberBadges(m)))),
      el('div', { class: 'empty' },
        'No letters found on this member’s site. ',
        el('a', { href: m.site, target: '_blank', rel: 'noopener' }, m.host)));
  }

  const p = params();
  const asList = p.get('as') === 'list';
  const recipients = tally(rows, 'recipient');
  const counts = yearsOf(rows);
  const years = [...counts.keys()].sort();
  const leads = rows.filter(r => get(r, 'role') === 'leads').length;
  const joins = rows.filter(r => get(r, 'role') === 'joins').length;
  const dated = rows.filter(r => get(r, 'precision') === 'day'
    || get(r, 'precision') === 'month').length;

  const switchTo = next => {
    const q = params();
    if (next === 'list') q.set('as', 'list'); else q.delete('as');
    shown = 0;
    go(q);
  };

  return el('div', {},
    link({ view: 'members' }, 'back', '← All members'),
    el('div', { class: 'panel' },
      el('div', { class: 'dhead' },
        el('h2', {}, m.name),
        el('span', { class: 'meta' }, memberBadges(m)),
        el('a', {
          href: m.site, target: '_blank', rel: 'noopener',
          class: 'small', style: 'margin-left:auto',
        }, m.host)),
      el('div', { class: 'stats' },
        el('div', {}, el('span', { class: 'v tab' }, num(rows.length)),
          el('span', { class: 'k' }, 'Letters')),
        el('div', {}, el('span', { class: 'v tab' }, num(recipients.length)),
          el('span', { class: 'k' }, 'Named recipients')),
        el('div', {}, el('span', { class: 'v tab' },
          years.length ? `${years[0]}–${years[years.length - 1]}` : '—'),
          el('span', { class: 'k' }, 'Span')),
        leads || joins
          ? el('div', {}, el('span', { class: 'v tab' }, `${num(leads)} / ${num(joins)}`),
            el('span', { class: 'k' }, 'Led / joined'))
          : null,
      )),
    el('div', { class: 'cols' },
      el('div', {},
        el('div', { class: 'controls' },
          el('div', { class: 'toggle' },
            el('button', {
              'aria-pressed': String(!asList),
              onclick: () => switchTo('wall'),
            }, 'The letters'),
            el('button', {
              'aria-pressed': String(asList),
              onclick: () => switchTo('list'),
            }, 'As a list')),
          el('span', { class: 'count tab' },
            `${num(dated)} of ${num(rows.length)} carry a date in the filename`)),
        asList ? table(rows, { member: m }) : wall(rows)),
      el('div', {},
        el('div', { class: 'panel' },
          el('h3', {}, 'Wrote to'),
          recipients.length
            ? rankList(recipients, name => link({ to: name }, null, name))
            : el('p', { class: 'faint small' },
              'No recipients are named in this site’s filenames.')),
        years.length > 1
          ? el('div', { class: 'panel' },
            el('h3', {}, 'By year'),
            rankList([...counts.entries()].sort((a, b) => b[0] - a[0]),
              y => link({ year: y }, null, String(y)), 12))
          : null)),
  );
}

function viewRecipients() {
  const p = params();
  const q = (p.get('q') || '').toLowerCase();
  const all = INDEX.recipients || [];
  const list = q ? all.filter(([name]) => name.toLowerCase().includes(q)) : all;
  const limit = shown || CARDS;
  const max = all.length ? all[0][1] : 1;

  const grid = el('div', { class: 'grid' });
  for (const [name, n] of list.slice(0, limit)) {
    grid.append(link({ to: name }, 'card',
      el('span', { class: 'top' },
        el('span', { class: 'nm' }, name),
        el('span', { class: 'ct tab' }, num(n))),
      el('span', { class: 'track', style: 'width:100%;height:.55rem;background:var(--tint);border-radius:3px;overflow:hidden' },
        el('i', {
          style: `display:block;height:100%;width:${Math.max(3, (n / max) * 100)}%;`
            + 'background:var(--active);opacity:.5',
        })),
    ));
  }

  const out = el('div', {},
    el('p', { class: 'hero' }, 'Who Congress writes to.'),
    el('p', { class: 'sub' },
      el('b', {}, num(all.length)),
      ' agencies, officials and companies named in the filenames of these letters. ',
      'Agency abbreviations and the officials who ran them are folded together, so ',
      'letters to Scott Pruitt and letters to the EPA count once.'),
    el('div', { class: 'controls' },
      el('input', {
        type: 'search', value: p.get('q') || '', placeholder: 'Filter recipients',
        oninput: e => {
          const next = params();
          if (e.target.value) next.set('q', e.target.value); else next.delete('q');
          history.replaceState({}, '', `?${next}`);
          render();
          const box = $('.controls input[type=search]');
          if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
        },
      }),
      el('span', { class: 'count tab' }, `${num(list.length)} recipients`)),
    grid);
  if (list.length > limit) {
    out.append(el('button', {
      class: 'more', onclick: () => { shown = limit + CARDS; render(); },
    }, `Show more — ${num(list.length - limit)} more`));
  }
  return out;
}

async function viewRecipient(name) {
  // The index says which members wrote to whom; their letters have to be
  // fetched to list them. Only members whose top recipients include this one
  // are loaded, which keeps a recipient page to a handful of requests.
  const candidates = MEMBERS.filter(m => m.top.includes(name));
  const loaded = await Promise.all(candidates.map(async m =>
    [m, (await lettersOf(m.id)).filter(r => get(r, 'recipient') === name)]));
  const pairs = loaded.filter(([, rows]) => rows.length)
    .sort((a, b) => b[1].length - a[1].length);
  const total = pairs.reduce((sum, [, rows]) => sum + rows.length, 0);
  const indexCount = (INDEX.recipients || []).find(([n]) => n === name)?.[1] ?? 0;

  return el('div', {},
    link({ view: 'recipients' }, 'back', '← All recipients'),
    el('div', { class: 'panel' },
      el('div', { class: 'dhead' }, el('h2', {}, name)),
      el('div', { class: 'stats' },
        el('div', {}, el('span', { class: 'v tab' }, num(indexCount)),
          el('span', { class: 'k' }, 'Letters in total')),
        el('div', {}, el('span', { class: 'v tab' }, num(pairs.length)),
          el('span', { class: 'k' }, 'Members shown')),
      )),
    total < indexCount
      ? el('p', { class: 'note' },
        `Showing ${num(total)} of ${num(indexCount)} letters — those from members for `
        + 'whom this is a top-three recipient. The rest are on their own pages.')
      : null,
    pairs.length
      ? el('div', {}, ...pairs.map(([m, rows]) =>
        el('div', { class: 'panel' },
          el('h3', {}, link({ member: m.id }, null, m.name), ` · ${num(rows.length)}`),
          el('div', { class: 'wall' }, ...rows.slice(0, 12).map(shot)))))
      : el('div', { class: 'empty' }, 'No letters loaded for this recipient.'));
}

function viewTimeline() {
  const years = INDEX.years || {};
  const entries = Object.entries(years).map(([y, n]) => [Number(y), n])
    .sort((a, b) => a[0] - b[0]);
  if (!entries.length) return el('div', { class: 'empty' }, 'No dated letters.');
  const from = entries[0][0], to = entries[entries.length - 1][0];
  const max = Math.max(...entries.map(e => e[1]));

  const bars = el('div', { class: 'bars' });
  for (let y = from; y <= to; y++) {
    const n = years[String(y)] || 0;
    bars.append(el('button', {
      style: `height:${n ? Math.max(2, (n / max) * 100) : 0}%`,
      title: `${y}: ${num(n)} letters`,
      'aria-label': `${y}, ${n} letters`,
      onclick: () => { const q = params(); q.set('year', y); q.delete('view'); shown = 0; go(q); },
    }));
  }

  return el('div', {},
    el('p', { class: 'hero' }, 'Letters by year.'),
    el('p', { class: 'sub' },
      'Most of these dates come from the filename. Where the filename gave none, the letter is '
      + 'placed at the first Wayback capture that saw it, which is an upper bound rather than a date — '
      + `${num((INDEX.precisions || {})['captured-by'] || 0)} of `
      + `${num(INDEX.letters)} letters.`),
    el('div', { class: 'panel' }, bars,
      el('div', { class: 'axis' },
        ...[from, Math.round((from + to) / 2), to].map((y, i) => el('span', {
          style: `position:absolute;left:${((y - from) / Math.max(1, to - from)) * 100}%;`
            + `transform:translateX(${i === 0 ? 0 : i === 2 ? -100 : -50}%)`,
        }, y)))));
}

async function viewYear(yearText) {
  const year = Number(yearText);
  const candidates = MEMBERS.filter(m => m.span
    && m.span[0] <= year && year <= m.span[1]);
  const loaded = await Promise.all(candidates.map(async m =>
    [m, (await lettersOf(m.id)).filter(r =>
      (get(r, 'date') || '').startsWith(String(year)))]));
  const pairs = loaded.filter(([, rows]) => rows.length)
    .sort((a, b) => b[1].length - a[1].length);
  const rows = pairs.flatMap(([, r]) => r);
  const total = (INDEX.years || {})[String(year)] || 0;

  return el('div', {},
    link({ view: 'timeline' }, 'back', '← Timeline'),
    el('div', { class: 'panel' },
      el('div', { class: 'dhead' }, el('h2', {}, year)),
      el('div', { class: 'stats' },
        el('div', {}, el('span', { class: 'v tab' }, num(total)),
          el('span', { class: 'k' }, 'Letters')),
        el('div', {}, el('span', { class: 'v tab' }, num(pairs.length)),
          el('span', { class: 'k' }, 'Members')),
      )),
    el('div', { class: 'cols' },
      el('div', { class: 'panel' }, wall(rows)),
      el('div', { class: 'panel' },
        el('h3', {}, 'Busiest that year'),
        rankList(pairs.map(([m, r]) => [m.id, r.length]),
          id => link({ member: id }, null, byId.get(id)?.name || '—'), 14))));
}

async function viewSearch(query) {
  const needle = (query || '').trim().toLowerCase();
  if (!needle) return el('div', { class: 'empty' }, 'Type something to search for.');

  // Members whose name or state matches, plus a scan of the loaded members'
  // letters. The index does not carry every subject line, so this searches
  // what has been opened plus the recipient vocabulary.
  const nameHits = MEMBERS.filter(m =>
    m.name.toLowerCase().includes(needle));
  const recipientHits = (INDEX.recipients || [])
    .filter(([name]) => name.toLowerCase().includes(needle));

  const scanned = [...memberCache.keys()];
  const letterHits = [];
  for (const id of scanned) {
    for (const row of memberCache.get(id) || []) {
      const haystack = `${get(row, 'subject') || ''} ${get(row, 'recipient') || ''} `
        + `${get(row, 'key')}`;
      if (haystack.toLowerCase().includes(needle)) letterHits.push([id, row]);
    }
  }

  return el('div', {},
    el('p', { class: 'hero' }, `“${query}”`),
    el('p', { class: 'sub' },
      el('b', {}, num(nameHits.length)), ' members, ',
      el('b', {}, num(recipientHits.length)), ' recipients, and ',
      el('b', {}, num(letterHits.length)),
      ' letters among the members opened so far. ',
      el('span', { class: 'faint' },
        'Letter search covers filenames, not the text of the letters — only 24% of these '
        + 'PDFs have a text layer at all.')),
    nameHits.length
      ? el('div', { class: 'panel' }, el('h3', {}, 'Members'),
        el('div', { class: 'grid' }, ...nameHits.slice(0, 12).map(m =>
          link({ member: m.id }, 'card',
            el('span', { class: 'top' }, el('span', { class: 'nm' }, m.name),
              el('span', { class: 'ct tab' }, num(m.letters))),
            el('span', { class: 'meta' }, memberBadges(m))))))
      : null,
    recipientHits.length
      ? el('div', { class: 'panel' }, el('h3', {}, 'Recipients'),
        rankList(recipientHits, name => link({ to: name }, null, name), 12))
      : null,
    letterHits.length
      ? el('div', { class: 'panel' }, el('h3', {}, 'Letters'),
        el('div', { class: 'wall' }, ...letterHits.slice(0, 36).map(([, row]) => shot(row))))
      : null,
    !nameHits.length && !recipientHits.length && !letterHits.length
      ? el('div', { class: 'empty' }, 'Nothing matched.')
      : null);
}

function viewAbout() {
  const precisions = INDEX.precisions || {};
  const kinds = INDEX.kinds || {};
  const captured = precisions['captured-by'] || 0;
  return el('div', { class: 'prose' },
    el('p', { class: 'hero' }, 'How this was built, and what it misses.'),

    el('h3', {}, 'Where the letters come from'),
    el('p', {}, `Every sitting member of Congress has an official website, and most of them
      publish the oversight letters they send — to agencies, to companies, to the President.
      This explorer indexes those letters as the Wayback Machine preserved them:
      ${num(INDEX.letters)} of them across ${num(INDEX.members_with_letters)} members.`),
    el('p', {}, `That archive is not a convenience. A member's site is replaced wholesale when
      the seat changes hands — the address survives, the content does not. Two sites checked
      while building this, portman.senate.gov and braun.senate.gov, no longer resolve at all.
      Their letters are still in the Wayback Machine.`),
    el('p', {}, 'The index is Wayback’s CDX API, queried per member site. Archive-It is not a '
      + 'source for this: its "Congress" collection returned no captures at all for '
      + 'warren.senate.gov or grassley.senate.gov, because it archives political campaigns '
      + 'rather than official member sites.'),

    el('h3', {}, 'Why the filename is the description'),
    el('p', {}, `Of 21 letter PDFs fetched and tested, five yielded extractable text —
      about a quarter. The rest are scans of the signed original, which is what a letter is.
      So the document cannot be read by a machine without OCR, and the URL has to carry the
      meaning instead.`),
    el('p', {}, 'It turns out to be good at it, because the people publishing these files named '
      + 'them for people. A slug like '),
    el('p', {}, el('code', {},
      'pressley-warren-markey-letter-to-hhs-re-racial-disparities-in-vaccine-distribution')),
    el('p', {}, 'states the co-signers, the recipient and the subject. That is where every '
      + 'recipient and subject on this site comes from: the filename, not the letter.'),

    el('h3', {}, 'Dates'),
    el('p', {}, `${num(INDEX.letters - captured)} letters state a date in the filename.
      The remaining ${num(captured)} do not, and are placed at the first Wayback capture that
      saw them — which is a real upper bound, not a date. Those are shown as
      "by March 2016" rather than as a day, everywhere they appear.`),

    el('h3', {}, 'What it does not reach'),
    el('p', {}, 'Only sitting members. A former member’s letters are in the archive too, but '
      + 'mapping former members to the websites they once had is a separate problem, and this '
      + 'does not attempt it yet. That material is the most at risk and the least replaceable.'),
    el('p', {}, 'Only letters whose filename or URL says "letter". A letter published as an '
      + 'untitled attachment, or named by topic alone, is not found by this method.'),
    el('p', {}, `Recipients are named in ${Math.round(((INDEX.recipients || []).reduce(
      (s, [, n]) => s + n, 0) / Math.max(1, INDEX.letters)) * 100)}% of these filenames.
      Where a site names files by topic — "2022 crab disaster letter" — there is no recipient
      to extract, and none is invented.`),
    el('p', {}, 'Newsletters are excluded, which matters more than it sounds: "newsletter" '
      + 'contains "letter", and for one senator 784 of 1,204 matching URLs were newsletters.'),

    el('h3', {}, 'The corpus'),
    el('table', {}, el('tbody', {},
      el('tr', {}, el('td', {}, 'Letters indexed'),
        el('td', { class: 'n' }, num(INDEX.letters))),
      el('tr', {}, el('td', {}, 'Members with letters'),
        el('td', { class: 'n' },
          `${num(INDEX.members_with_letters)} of ${num(INDEX.members_total)}`)),
      el('tr', {}, el('td', {}, 'Documents (PDFs)'),
        el('td', { class: 'n' }, num(kinds.pdf || 0))),
      el('tr', {}, el('td', {}, 'Press-release pages'),
        el('td', { class: 'n' }, num(kinds.page || 0))),
      el('tr', {}, el('td', {}, 'Distinct recipients'),
        el('td', { class: 'n' }, num((INDEX.recipients || []).length))),
      el('tr', {}, el('td', {}, 'First pages rendered'),
        el('td', { class: 'n' }, num(INDEX.thumbs || 0))),
      el('tr', {}, el('td', {}, 'Built'), el('td', { class: 'n' }, INDEX.built)))),
  );
}

// -------------------------------------------------------------------- shell

const TABS = [
  ['members', 'Members'],
  ['recipients', 'Recipients'],
  ['timeline', 'Timeline'],
  ['search', 'Search'],
  ['about', 'About'],
];

function currentView(p) {
  if (p.get('member')) return 'members';
  if (p.get('to')) return 'recipients';
  if (p.get('year')) return 'timeline';
  return p.get('view') || 'members';
}

async function render() {
  const p = params();
  const view = currentView(p);

  $('#tabs').replaceChildren(...TABS.map(([key, label]) =>
    el('button', {
      'aria-current': view === key ? 'page' : null,
      onclick: () => {
        const next = new URLSearchParams();
        if (key !== 'members') next.set('view', key);
        if (key === 'search' && p.get('q')) next.set('q', p.get('q'));
        shown = 0;
        go(next);
      },
    }, label)));

  let body;
  try {
    if (p.get('member')) body = await viewMember(p.get('member'));
    else if (p.get('to')) body = await viewRecipient(p.get('to'));
    else if (p.get('year')) body = await viewYear(p.get('year'));
    else if (view === 'recipients') body = viewRecipients();
    else if (view === 'timeline') body = viewTimeline();
    else if (view === 'search') body = await viewSearch(p.get('q'));
    else if (view === 'about') body = viewAbout();
    else body = viewMembers();
  } catch (err) {
    body = el('div', { class: 'empty' }, 'Something went wrong. ',
      el('span', { class: 'small' }, String(err.message || err)));
  }
  $('#main').replaceChildren(body);

  const box = $('#q');
  if (box && view === 'search') box.value = p.get('q') || '';
  document.title = p.get('member')
    ? `${byId.get(p.get('member'))?.name || 'Member'} · What Congress Asked`
    : p.get('to') ? `${p.get('to')} · What Congress Asked`
      : 'What Congress Asked · Internet Archive';
}

async function boot() {
  const response = await fetch(`${DATA}index.json`);
  if (!response.ok) throw new Error(`index.json: HTTP ${response.status}`);
  INDEX = await response.json();
  MEMBERS = INDEX.members;
  INDEX.columns.forEach((name, i) => { F[name] = i; });
  byId = new Map(MEMBERS.map(m => [m.id, m]));

  $('#topsearch').addEventListener('submit', e => {
    e.preventDefault();
    const next = new URLSearchParams();
    next.set('view', 'search');
    next.set('q', $('#q').value);
    shown = 0;
    go(next);
  });
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-view]');
    if (!a) return;
    e.preventDefault();
    const next = new URLSearchParams();
    next.set('view', a.dataset.view);
    shown = 0;
    go(next);
  });
  window.addEventListener('popstate', () => { shown = 0; render(); });
  await render();
}

boot().catch(err => {
  $('#main').replaceChildren(
    el('div', { class: 'empty' }, 'Could not load the letters. ',
      el('span', { class: 'small' }, String(err.message || err))));
});
