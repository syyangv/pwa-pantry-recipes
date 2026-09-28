---
modified_at: 2026-09-27
tags:
cssclasses:
  - hide-frontmatter
---
![[noteNav]]
![[genTOC]]
![[pantry_controls]]

```dataviewjs
// Pantry overview — total count + total 💵 cash value, with per-section breakdown
const NAMES = { '1': '冰箱', '2': '干货', '3': '早餐', '4': '零食', '5': '饮料', '6': '日用' };
const page = dv.page('Logistics/库存/Pantry.md');
const _tasks = page?.file?.tasks ?? [];
const _pMap = {}, _pHasUnits = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
for (const t of _tasks) { _pMap[t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _pHasUnits.add(t.parent); }
const sec = {};
let N = 0, T = 0;
for (const t of _tasks) {
  if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
  if (_pHasUnits.has(t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
  const h = (t.header?.subpath ?? t.section?.subpath ?? '').trim();
  const m = h.match(/^([1-6])/);
  if (!m) continue;
  const s = m[1];
  (sec[s] = sec[s] || { n: 0, t: 0 }).n++;
  N++;
  let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
  let v = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
  if (!v && t.parent != null && /\d+\/\d+/.test(t.text)) {
    const p = _pMap[t.parent]; if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) v = parseFloat(pp[1].replace(/,/g, '')); }
  }
  if (v) { sec[s].t += v; T += v; }
}
dv.paragraph(`### 📦 库存总览 · 📋 ${N} 项 · 💵 $${T.toFixed(2)}`);
dv.table(['区', '数量', '💵 小计'],
  Object.keys(NAMES).map(s => [NAMES[s], (sec[s]?.n ?? 0) + ' 项', '$' + (sec[s]?.t ?? 0).toFixed(2)]));
```

```dataviewjs
// Pantry $ flow line chart — switch between weekly and monthly views.
// Both views use the same added(➕)/done(✅)/cancelled(❌) events, summed by 💵.
// Monthly view overlays matching months from the previous year as dashed lines.
// Both views plot 现有库存 snapshots recorded every Sunday 3am in
// Logistics/库存/Pantry 快照.md — untagged stock only, so they line up with the
// 无标签 chip (use ↻ 刷新快照 after the weekly job runs). The weekly view plots
// each snapshot; the monthly view plots the average of that month's snapshots.
try {
  const SOURCES = dv.pages('"Logistics/库存/Pantry" or "Archive"').file.tasks
    .where(t => t.path.includes('Logistics/库存/Pantry') || /archive\/pantry/i.test(t.path));
  const _tbl = {}; const _hasUnits = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
  for (const t of SOURCES) { _tbl[t.path + ':' + t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _hasUnits.add(t.path + ':' + t.parent); }
  const wkKey = d => d.weekYear + '-W' + String(d.weekNumber).padStart(2, '0');
  const periodStart = (d, view) => view === 'week' ? d.startOf('week') : d.startOf('month');
  const periodKey = (d, view) => view === 'week' ? wkKey(d) : d.toFormat('yyyy-MM');
  const events = [];
  let minDt = null;
  for (const t of SOURCES) {
    if (_hasUnits.has(t.path + ':' + t.line)) continue;
    let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
    let amt = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
    if (!amt && t.parent != null && /\d+\/\d+/.test(t.text)) {
      const p = _tbl[t.path + ':' + t.parent];
      if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) amt = parseFloat(pp[1].replace(/,/g, '')); }
    }
    if (!amt) continue;
    for (const [emoji, key] of [['➕', 'add'], ['✅', 'done'], ['❌', 'canc']]) {
      const m = t.text.match(new RegExp(emoji + '\\s*(\\d{4}-\\d{2}-\\d{2})'));
      if (!m) continue;
      const d = dv.luxon.DateTime.fromISO(m[1]);
      events.push({ date: d, key, amt });
      if (!minDt || d < minDt) minDt = d;
    }
  }
  // --- inventory rows + tag filtering -------------------------------------
  // Mirrors the PWA pantry tab: All / Untagged / #tag chips filter the
  // 现有库存 figure (the shade + its legend). The $ flow series above is a
  // purchase/consumption history and is deliberately not tag-filtered. The
  // Sunday snapshots in 快照.md are the untagged (无标签) total, so the snapshot
  // line lines up with the 无标签 chip.
  const UNTAGGED = '#untagged';
  const TAG_RE = /(?:^|[ \t])#([^\s#]+)/g;
  const tagsOf = text => [...text.matchAll(TAG_RE)].map(m => m[1]);
  const matchesFilter = (tags, filter) =>
    filter === null ? true : filter === UNTAGGED ? tags.length === 0 : tags.includes(filter);
  const inventoryRows = [];
  for (const t of SOURCES) {
    if (!t.path.includes('Logistics/库存/Pantry')) continue;
    if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
    if (_hasUnits.has(t.path + ':' + t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
    let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
    let amt = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
    if (!amt && t.parent != null && /\d+\/\d+/.test(t.text)) {
      const p = _tbl[t.path + ':' + t.parent];
      if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) amt = parseFloat(pp[1].replace(/,/g, '')); }
    }
    inventoryRows.push({ tags: tagsOf(t.text), amt });
  }
  const inventoryFor = filter => {
    let count = 0, total = 0;
    for (const row of inventoryRows) {
      if (!matchesFilter(row.tags, filter)) continue;
      count++;
      total += row.amt;
    }
    return { count, total };
  };
  const availableTags = [...new Set(inventoryRows.flatMap(r => r.tags))].sort();
  const SNAP_PATH = 'Logistics/库存/Pantry 快照.md';
  let snapByWeek = new Map();
  let snapByMonth = new Map();
  // One 现有库存 snapshot per week, written every Sunday 3am by
  // Helper/scripts/pantry_snapshot.py. Weeks without a snapshot stay null so the
  // line breaks instead of dropping to zero. snapByMonth keeps the running sum
  // and count so the monthly view can plot the average of that month's snapshots.
  const loadSnapshots = async () => {
    snapByWeek = new Map();
    snapByMonth = new Map();
    let text = null;
    try { text = await dv.io.load(SNAP_PATH); } catch (e) { text = null; }
    for (const m of (text ?? '').matchAll(/^-\s*(\d{4}-\d{2}-\d{2})[^$\n]*\$\s*([\d,]+(?:\.\d+)?)/gm)) {
      const value = parseFloat(m[2].replace(/,/g, ''));
      const d = dv.luxon.DateTime.fromISO(m[1]);
      snapByWeek.set(wkKey(d), value);
      const mk = d.toFormat('yyyy-MM');
      const bucket = (snapByMonth.get(mk) || { sum: 0, n: 0 });
      bucket.sum += value; bucket.n++;
      snapByMonth.set(mk, bucket);
    }
  };
  await loadSnapshots();

  const controls = dv.container.createEl('div');
  controls.style.marginBottom = '6px';
  const chart = dv.container.createEl('div');
  const buttons = {};
  let activeView = 'week';
  for (const [view, label] of [['week', '周'], ['month', '月']]) {
    const button = controls.createEl('button', { text: label });
    button.type = 'button';
    button.style.marginRight = '6px';
    button.addEventListener('click', () => render(view));
    buttons[view] = button;
  }
  const refreshBtn = controls.createEl('button', { text: '↻ 刷新快照' });
  refreshBtn.type = 'button';
  refreshBtn.addEventListener('click', async () => {
    refreshBtn.disabled = true;
    await loadSnapshots();
    render(activeView);
    refreshBtn.disabled = false;
  });

  // Tag chips for the 现有库存 figure — same semantics as the PWA pantry tab.
  const tagBar = controls.createEl('div');
  tagBar.style.margin = '6px 0 2px';
  const tagChips = [];
  let activeTagFilter = null;   // null = All
  const addChip = (label, value) => {
    const chip = tagBar.createEl('button', { text: label });
    chip.type = 'button';
    chip.style.marginRight = '6px';
    chip.addEventListener('click', () => { activeTagFilter = value; render(activeView); });
    tagChips.push({ chip, value });
  };
  addChip('全部分类', null);
  addChip('无标签', UNTAGGED);
  for (const tag of availableTags) addChip('#' + tag, tag);
  const filterLabel = filter => filter === null ? '全部分类' : filter === UNTAGGED ? '无标签' : '#' + filter;
  const syncChips = () => {
    tagChips.forEach(({ chip, value }) => {
      chip.setAttribute('aria-pressed', String(value === activeTagFilter));
      chip.style.fontWeight = value === activeTagFilter ? '600' : '400';
    });
  };
  const tagNote = tagBar.createEl('span');
  tagNote.style.fontSize = '0.85em';
  tagNote.style.opacity = '0.7';
  tagNote.textContent = '现有库存与快照按分类筛选（快照=无标签）· 金额曲线为全部';
  syncChips();

  const render = view => {
    activeView = view;
    syncChips();
    const { count: inventoryCount, total: existingPantryValue } = inventoryFor(activeTagFilter);
    Object.entries(buttons).forEach(([name, button]) => {
      const selected = name === view;
      button.setAttribute('aria-pressed', String(selected));
      button.style.fontWeight = selected ? '600' : '400';
    });
    if (!minDt) {
      chart.textContent = '暂无带 💵 金额的记录';
      return;
    }

    const end = dv.luxon.DateTime.now().startOf(view === 'week' ? 'week' : 'month');
    const curYear = view === 'week' ? end.weekYear : end.year;
    const prevYearStart = dv.luxon.DateTime.fromObject({ weekYear: curYear - 1, weekNumber: 1 });
    const periods = [];
    let cur = view === 'month' ? end.startOf('year') : periodStart(minDt, view);
    if (view === 'week' && cur < prevYearStart) cur = prevYearStart;   // show only previous year + current year
    while (cur <= end) {
      periods.push({
        key: periodKey(cur, view),
        date: cur,
        num: cur.weekNumber,
        year: view === 'week' ? cur.weekYear : cur.year,
      });
      cur = cur.plus(view === 'week' ? { weeks: 1 } : { months: 1 });
    }

    const bucket = {};
    const get = k => (bucket[k] = bucket[k] || { add: 0, done: 0, canc: 0 });
    for (const event of events) get(periodKey(event.date, view))[event.key] += event.amt;

    const n = periods.length, denom = Math.max(1, n - 1);
    const series = [
      { name: '新增', color: '#60a5fa', f: 'add' },
      { name: '完成', color: '#4ade80', f: 'done' },
      { name: '取消', color: '#f87171', f: 'canc' },
    ];
    const data = series.map(s => periods.map(p => (bucket[p.key]?.[s.f]) || 0));
    const lastYearData = view === 'month'
      ? series.map(s => periods.map(p => (bucket[periodKey(p.date.minus({ years: 1 }), view)]?.[s.f]) || 0))
      : null;
    // 现有库存 snapshot line: weekly view plots each Sunday's snapshot, monthly
    // view plots the average of the snapshots that fall in that month. Months
    // with no snapshot stay null so the line breaks instead of dropping to zero.
    const snapshotData = view === 'week'
      ? periods.map(p => (snapByWeek.has(p.key) ? snapByWeek.get(p.key) : null))
      : periods.map(p => {
        const b = snapByMonth.get(p.key);
        return b && b.n ? b.sum / b.n : null;
      });
    const drawSeries = [
      ...series.map((s, si) => ({ name: s.name, color: s.color, values: data[si] })),
      {
        name: view === 'week' ? '现有库存快照' : '现有库存月均',
        color: '#f59e0b',
        values: snapshotData,
        snapshot: true,
      },
    ];
    const maxVal = Math.max(
      1, existingPantryValue,
      ...data.concat(lastYearData ?? []).flat(),
      ...snapshotData.filter(v => v != null));
    const yMax = Math.ceil(maxVal * 1.15 / 5) * 5 || 5;
    const stepX = view === 'month' ? 72 : 48;
    const pad = { top: 46, right: 20, bottom: 58, left: 60 };
    const plotW = Math.max(220, denom * stepX), plotH = 372 - pad.top - pad.bottom;
    const W = pad.left + pad.right + plotW, H = 372;
    const x = i => pad.left + (plotW * i) / denom;
    const y = v => pad.top + plotH - (v / yMax) * plotH;
    const pantryColor = '#f59e0b';
    const pantryBlock = existingPantryValue > 0
      ? `<rect x="${pad.left}" y="${y(existingPantryValue).toFixed(1)}" width="${plotW.toFixed(1)}" height="${(pad.top + plotH - y(existingPantryValue)).toFixed(1)}" fill="${pantryColor}" fill-opacity="0.08"/>`
      : '';
    let grid = '';
    for (let i = 0; i <= 5; i++) {
      const val = (yMax / 5) * i, yy = y(val);
      grid += `<text x="${pad.left - 6}" y="${yy + 4}" text-anchor="end" font-size="13" fill="#888">$${val.toFixed(0)}</text>`;
      grid += `<line x1="${pad.left}" y1="${yy}" x2="${pad.left + plotW}" y2="${yy}" stroke="#ccc" stroke-dasharray="2,3"/>`;
    }
    const lblStep = Math.max(1, Math.ceil(n / (view === 'month' ? 12 : 14)));
    let xlab = '', shownYear = null;
    periods.forEach((p, i) => {
      if (i % lblStep === 0 || i === n - 1) {
        const txt = view === 'week'
          ? ((p.year !== shownYear) ? `'${String(p.year).slice(2)} W${p.num}` : `W${p.num}`)
          : p.date.toFormat('M月');
        shownYear = p.year;
        xlab += `<text x="${x(i).toFixed(1)}" y="${pad.top + plotH + 20}" text-anchor="middle" font-size="13" fill="#888">${txt}</text>`;
      }
    });
    let lines = '';
    drawSeries.forEach((s, si) => {
      const drawLine = (values, dashed) => {
        for (let i = 0; i < n - 1; i++) {
          if (values[i] == null || values[i + 1] == null) continue;   // snapshot gap
          const dash = dashed || (view === 'week' && periods[i].year < curYear) ? ' stroke-dasharray="4,3"' : '';
          lines += `<line x1="${x(i).toFixed(1)}" y1="${y(values[i]).toFixed(1)}" x2="${x(i + 1).toFixed(1)}" y2="${y(values[i + 1]).toFixed(1)}" stroke="${s.color}" stroke-width="${s.snapshot ? 2.5 : 2}"${dash}/>`;
        }
        values.forEach((v, i) => {
          if (v == null) return;
          lines += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="${s.snapshot ? 3.5 : (v > 0 ? 3 : 1.5)}" fill="${s.color}"/>`;
        });
      };
      drawLine(s.values, false);
      if (lastYearData && lastYearData[si]) drawLine(lastYearData[si], true);
    });
    // legend laid out left to right so an extra series never overlaps the labels
    let legend = '', lx = pad.left;
    const legendItem = (mark, text, advance) => {
      legend += mark(lx);
      legend += `<text x="${lx + 20}" y="${H - 10}" font-size="12" fill="#888">${text}</text>`;
      lx += advance;
    };
    const step = text => 20 + text.length * 13 + 22;
    drawSeries.forEach(s => legendItem(x0 => `<line x1="${x0}" y1="${H - 15}" x2="${x0 + 16}" y2="${H - 15}" stroke="${s.color}" stroke-width="2.5"/>`, s.name, step(s.name)));
    legendItem(() => '', '虚线=上年', step('虚线=上年'));
    if (existingPantryValue > 0) {
      legendItem(x0 => `<rect x="${x0}" y="${H - 19}" width="16" height="10" fill="${pantryColor}" fill-opacity="0.08"/>`,
        `现有库存 $${existingPantryValue.toFixed(2)} · ${inventoryCount} 项 · ${filterLabel(activeTagFilter)}`, 0);
    }
    const W2 = Math.max(W, lx + 24);
    const svg = `<div style="overflow-x:auto; padding:8px 0;">
  <svg width="${W2}" height="${H}" style="font-family:var(--font-interface);">
    <text x="${W2 / 2}" y="26" text-anchor="middle" font-size="17" font-weight="600" fill="var(--text-normal)">Pantry ${view === 'week' ? '周' : '月'}金额趋势 ($)</text>
    ${pantryBlock}
    ${grid}
    <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + plotH}" stroke="#999"/>
    <line x1="${pad.left}" y1="${pad.top + plotH}" x2="${pad.left + plotW}" y2="${pad.top + plotH}" stroke="#999"/>
    ${lines}${xlab}${legend}
  </svg>
</div>`;
    chart.innerHTML = svg;
  };
  render('week');
} catch (e) { dv.paragraph('⚠️ Error: ' + e.message); }
```

<!-- Weee order 93907772 delivered 2026-05-26; total units 23; ingested by wholefoods-to-pantry -->
<!-- Amazon Whole Foods order 113-0315086-6943470 purchased 2026-06-03; 6 lines / 7 units; ingested by wholefoods-to-pantry -->
<!-- Amazon Whole Foods order 113-4031032-0327467 purchased 2026-06-30; 8 lines / 8 units; ingested by wholefoods-to-pantry -->
<!-- Weee order 95124902 delivered 2026-06-14; total units 15; ingested by wholefoods-to-pantry -->
<!-- Amazon Whole Foods order 111-8028384-5518617 delivered 2026-07-08; 7 lines / 10 units; ingested by wholefoods-to-pantry -->
<!-- Weee order 96747996 delivered 2026-07-15; total units 13; ingested by wholefoods-to-pantry -->
<!-- Amazon Whole Foods order 112-6235651-2141010 delivered 2026-07-30; 10 lines / 14 units; ingested by wholefoods-to-pantry -->
<!-- Weee order 98140011 delivered 2026-08-06; total units 23; ingested by wholefoods-to-pantry -->
<!-- Amazon Whole Foods order 112-4223078-9769858 delivered 2026-08-14; 7 lines / 8 units; ingested by wholefoods-to-pantry -->
<!-- Weee order 99245413 delivered 2026-08-24; total units 22; ingested by wholefoods-to-pantry -->
<!-- Amazon Whole Foods order 112-1709064-0564204 delivered 2026-09-07; 9 lines / 12 units (3 White Peach out of stock/refunded + 2 Poland Spring units skipped); ingested by wholefoods-to-pantry -->
<!-- Weee order 100171172 delivered 2026-09-10; total units 19; ingested by wholefoods-to-pantry -->
<!-- Amazon Whole Foods order 112-7397647-5020235 delivered 2026-09-17; 8 lines / 10 units (4 Poland Spring units skipped); ingested by wholefoods-to-pantry -->
<!-- Weee order 101057684 delivered 2026-09-22; total units 17; ingested by wholefoods-to-pantry -->
# 1 冰箱

```dataviewjs
const SEC = '1';
const page = dv.page('Logistics/库存/Pantry.md');
const _ts = page?.file?.tasks ?? [];
const _pm = {}, _hu = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
for (const t of _ts) { _pm[t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _hu.add(t.parent); }
let n = 0, total = 0;
for (const t of _ts) {
  if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
  if (_hu.has(t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
  const h = (t.header?.subpath ?? t.section?.subpath ?? '').trim();
  if (!(h.startsWith(SEC + '.') || h.startsWith(SEC + ' ') || h === SEC)) continue;
  n++;
  let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
  let v = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
  if (!v && t.parent != null && /\d+\/\d+/.test(t.text)) { const p = _pm[t.parent]; if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) v = parseFloat(pp[1].replace(/,/g, '')); } }
  if (v) total += v;
}
dv.paragraph(`**📋 ${n} 项 · 💵 $${total.toFixed(2)}**`);
```

## 1.1 冷藏
```tasks
filename includes Nini's
not done
description does not include #❄️ 
```
- [ ] Chobani® 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz 💵 $2.19 ✍️ 2026-09-07 ➕ 2026-09-07
- [ ] Icelandic Provisions, Extra Creamy Skyr, Cold Brew Coffee, 4.4 Ounce 💵 $2.09 ✍️ 2026-09-07 ➕ 2026-09-07
- [ ] 中华 玉子豆腐 日式豆腐 245 克 💵 $2.99 ✍️ 2026-09-10 ➕ 2026-09-10
  - [ ] 1/2 ✍️ 2026-09-10 ➕ 2026-09-10
  - [ ] 2/2 ✍️ 2026-09-10 ➕ 2026-09-10
- [/] Chicken Wings 💵 $8.00 ✍️ 2026-09-20 ➕ 2026-09-20 🛫 2026-09-27
- [x] 绿豆煎饼 225 克 💵 $6.99 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-27 ✅ 2026-09-27
- [/] 辣拌鱿鱼丝 130 克 💵 $6.88 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-23
### 1.1.1 水果
- [x] 优质白桃礼盒 4 磅 💵 $10.99 ✍️ 2026-09-10 ➕ 2026-09-10 🛫 2026-09-11 ✅ 2026-09-23
- [x] Banana Conventional, 1 Each 💵 $0.81 ✍️ 2026-09-18 ➕ 2026-09-17 🛫 2026-09-20 ✅ 2026-09-21
- [ ] 超大号爆浆蓝莓 9.8 盎司 💵 $5.99 ✍️ 2026-09-22 ➕ 2026-09-22
- [ ] Orri 蜜橘 2.8-3.2 磅 💵 $4.88 ✍️ 2026-09-22 ➕ 2026-09-22
### 1.1.2 冷藏饮料
- [x] MOOALA Organic Bananamilk, 48 FZ 💵 $4.87 ✍️ 2026-07-30 ➕ 2026-07-30 🛫 2026-09-16 ✅ 2026-09-24
- [/] 365 By Whole Foods Market, Organic 1% Milk, 32 Fl Oz 💵 $2.99 ✍️ 2026-09-18 ➕ 2026-09-17 🛫 2026-09-18
### 1.1.3 蔬菜
- [-] 空心菜嫩苗 0.95-1.05 磅 💵 $2.99 ✍️ 2026-09-10 ➕ 2026-09-10 🛫 2026-09-13 ❌ 2026-09-22
- [x] Forward Greens Micro Broccoli, 2 OZ 💵 $4.79 ✍️ 2026-09-18 ➕ 2026-09-17 🛫 2026-09-22 ✅ 2026-09-24
- [/] 空心菜嫩苗 0.95-1.05 磅 💵 $3.99 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-27
### 1.1.4 调料
- [ ] 李锦记 蒸鱼豉油 14 盎司 💵 $3.79 ✍️ 2026-09-22 ➕ 2026-09-22
## 1.2 冷冻
```tasks
filename includes Nini's
not done
description includes #❄️ 
```
- [/] 柴米 薄百叶（干豆腐皮） 冷冻 227 克 ✍️ 2026-05-18 ➕ 2026-05-10 🛫 2026-08-18
- [ ] 柴米 蒜香蒸茄子 300 克 ✍️ 2026-05-26 ➕ 2026-05-26
- [/] 禾苑 速冻包浆豆腐 330 克 #SF 💵 $3.49 ✍️ 2026-08-24 ➕ 2026-08-24 🛫 2026-08-25
- [/] 365 by Whole Foods Market, Organic Mozzarella String Cheese, 8 Ounce 💵 $5.99 ✍️ 2026-09-18 ➕ 2026-09-17 🛫 2026-09-20
### 1.2.1 冷冻肉&海鲜
- [ ] Frozen Mackerel Boneless Protion-cut 5P 10.58 盎司 ✍️ 2026-05-18 ➕ 2026-04-26
- [/] 江船长&Yaba 优质天然冷冻小鲍鱼肉 30-35个/盒 135 克 ✍️ 2026-05-26 ➕ 2026-05-26 🛫 2026-08-18
- [/] 田螺肉 14 盎司 💵 $3.99 ✍️ 2026-06-14 ➕ 2026-06-14
- [ ] 禾苑 蟹粉鱼肉狮子头 冷冻 280 克 💵 $4.59 ✍️ 2026-07-16 ➕ 2026-07-15
- [/] 味圈 台湾爆汁香肠 冷冻 1 磅 💵 $8.79 ✍️ 2026-08-07 ➕ 2026-08-06 🛫 2026-08-14
- [ ] YABA 花蛤肉 冷冻 12 盎司 #SF 💵 $6.29 ✍️ 2026-08-24 ➕ 2026-08-24
- [ ] Fusipim 芝士鱼豆腐 冷冻 500 克 💵 $7.49 ✍️ 2026-09-10 ➕ 2026-09-10
### 1.2.2 冷冻蔬果
- [/] 365 by Whole Foods Market, Organic Broccoli Florets, 16 oz, (Frozen) 💵 $3.49 ✍️ 2026-07-30 ➕ 2026-07-30 🛫 2026-09-20
### 1.2.3 冷冻主食&点心
- [ ] Trader Joe's 4 Chocolate Croissants ✍️ 2026-05-24 ➕ 2026-02-01
- [/] 小巷口 老上海蟹壳黄 葱香馅 冷冻 360 克 💵 $6.49 ✍️ 2026-09-10 ➕ 2026-09-10 🛫 2026-09-23
- [ ] Love Me Sweet 港式酥皮蛋挞 12颗 （原味*4， 芋头*4， 麻薯*4） 冷冻 25.4 盎司 💵 $18.47 ✍️ 2026-09-10 ➕ 2026-09-10
### 1.2.4 冷冻甜品
- [x] 盒马 敲敲杯焦糖巧克力口味牛乳冰淇淋 90 克 💵 $3.99 ✍️ 2026-09-10 ➕ 2026-09-10 🛫 2026-09-20 ✅ 2026-09-20
  - [x] 1/2 ✍️ 2026-09-10 ➕ 2026-09-10 ✅ 2026-09-20
  - [x] 2/2 ✍️ 2026-09-10 ➕ 2026-09-10 ✅ 2026-09-20
# 2 干货

```dataviewjs
const SEC = '2';
const page = dv.page('Logistics/库存/Pantry.md');
const _ts = page?.file?.tasks ?? [];
const _pm = {}, _hu = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
for (const t of _ts) { _pm[t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _hu.add(t.parent); }
let n = 0, total = 0;
for (const t of _ts) {
  if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
  if (_hu.has(t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
  const h = (t.header?.subpath ?? t.section?.subpath ?? '').trim();
  if (!(h.startsWith(SEC + '.') || h.startsWith(SEC + ' ') || h === SEC)) continue;
  n++;
  let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
  let v = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
  if (!v && t.parent != null && /\d+\/\d+/.test(t.text)) { const p = _pm[t.parent]; if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) v = parseFloat(pp[1].replace(/,/g, '')); } }
  if (v) total += v;
}
dv.paragraph(`**📋 ${n} 项 · 💵 $${total.toFixed(2)}**`);
```

- [ ] Manukora Manuka Honey MGO 50+ ✍️ 2026-05-18 ➕ 2026-04-13
- [/] Beekeeper's Naturals Nootropic Brain Supplement ➕ 2026-04-13
- [/] JAYONE Matcha Green Tea Powder 100 克 💵 $5.99 ✍️ 2026-05-18 ➕ 2026-05-10 🛫 2026-09-25
- [ ] Mushroom Dried Morel Mushrooms ✍️ 2026-05-24 ➕ 2026-02-01 📅 2026-06-15
- [/] 白象 汤好喝 金汤花胶鸡味面 570 克 💵 $4.99 ✍️ 2026-06-14 ➕ 2026-06-14 🛫 2026-06-22
- [/] 超禾 有机黑米 16 盎司 💵 $3.99 ✍️ 2026-08-07 ➕ 2026-08-06 🛫 2026-09-13
	- [x] 1/2 ✍️ 2026-08-07 ➕ 2026-08-06 🛫 2026-09-13 ✅ 2026-09-14
	- [ ] 2/2 ✍️ 2026-08-07 ➕ 2026-08-06
- [/] 饭匹兄弟 原味糯米笋 248 克 #SF 💵 $3.99 ✍️ 2026-08-24 ➕ 2026-08-24 🛫 2026-08-25
	- [ ] 1/2 #SF ✍️ 2026-08-24 ➕ 2026-08-24
	- [x] 2/2 #SF ✍️ 2026-08-24 ➕ 2026-08-24 🛫 2026-08-25 ✅ 2026-08-31
- [/] Surasang 韩国年糕片 1.43 磅 #SF 💵 $4.49 ✍️ 2026-08-24 ➕ 2026-08-24 🛫 2026-08-30
- [/] 八道 高级牛骨汤面 125g*4 500 克 #SF 💵 $7.79 ✍️ 2026-08-24 ➕ 2026-08-24 🛫 2026-08-25
- [ ] 必品阁 紫菜汤(2人份) 500 克 #SF 💵 $5.49 ✍️ 2026-08-24 ➕ 2026-08-24
- [ ] 饭匹兄弟 原味糯米笋 248 克 💵 $3.99 ✍️ 2026-09-10 ➕ 2026-09-10
- [ ] 柴米 传统工艺 软心皮蛋 6枚装 65 克 💵 $3.29 ✍️ 2026-09-22 ➕ 2026-09-22
# 3 早餐

```dataviewjs
const SEC = '3';
const page = dv.page('Logistics/库存/Pantry.md');
const _ts = page?.file?.tasks ?? [];
const _pm = {}, _hu = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
for (const t of _ts) { _pm[t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _hu.add(t.parent); }
let n = 0, total = 0;
for (const t of _ts) {
  if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
  if (_hu.has(t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
  const h = (t.header?.subpath ?? t.section?.subpath ?? '').trim();
  if (!(h.startsWith(SEC + '.') || h.startsWith(SEC + ' ') || h === SEC)) continue;
  n++;
  let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
  let v = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
  if (!v && t.parent != null && /\d+\/\d+/.test(t.text)) { const p = _pm[t.parent]; if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) v = parseFloat(pp[1].replace(/,/g, '')); } }
  if (v) total += v;
}
dv.paragraph(`**📋 ${n} 项 · 💵 $${total.toFixed(2)}**`);
```

- [/] Matchaful Original Matcha Granola 8oz ✍️ 2026-05-24 ➕ 2026-05-24 🛫 2026-09-26
- [ ] Chocolate Crepe (30 servings) 💵 $8.79 ✍️ 2026-09-20 ➕ 2026-09-20
# 4 零食

```dataviewjs
const SEC = '4';
const page = dv.page('Logistics/库存/Pantry.md');
const _ts = page?.file?.tasks ?? [];
const _pm = {}, _hu = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
for (const t of _ts) { _pm[t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _hu.add(t.parent); }
let n = 0, total = 0;
for (const t of _ts) {
  if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
  if (_hu.has(t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
  const h = (t.header?.subpath ?? t.section?.subpath ?? '').trim();
  if (!(h.startsWith(SEC + '.') || h.startsWith(SEC + ' ') || h === SEC)) continue;
  n++;
  let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
  let v = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
  if (!v && t.parent != null && /\d+\/\d+/.test(t.text)) { const p = _pm[t.parent]; if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) v = parseFloat(pp[1].replace(/,/g, '')); } }
  if (v) total += v;
}
dv.paragraph(`**📋 ${n} 项 · 💵 $${total.toFixed(2)}**`);
```
- [ ] 盒马 十蔬米饼 清新黄瓜味 【健康低卡 含膳食纤维】 118 克 💵 $3.99 ✍️ 2026-09-10 ➕ 2026-09-10
- [x] 鲍师傅 奶黄流心月饼 4入 1 盒 💵 $16.00 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-23 ✅ 2026-09-26
- [x] 家乡味 有机甘栗仁 100 克 💵 $2.49 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-23 ✅ 2026-09-27
  - [x] 1/2 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-23 ✅ 2026-09-23
  - [x] 2/2 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-27 ✅ 2026-09-27
- [x] 良品铺子 特好剥碧根果 奶香味 120 克 💵 $4.99 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-23 ✅ 2026-09-26
  - [x] 1/3 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-23 ✅ 2026-09-23
  - [x] 2/3 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-25 ✅ 2026-09-25
  - [x] 3/3 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-25 ✅ 2026-09-26
- [ ] 香甜麻花 2根 400 克 💵 $4.88 ✍️ 2026-09-22 ➕ 2026-09-22
- [/] Wang Korea 有机去壳甘栗仁 60g*5 300 克 💵 $6.48 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-24
  - [x] 1/2 ✍️ 2026-09-22 ➕ 2026-09-22 🛫 2026-09-24 ✅ 2026-09-26
  - [ ] 2/2 ✍️ 2026-09-22 ➕ 2026-09-22
# 5 饮料

```dataviewjs
const SEC = '5';
const page = dv.page('Logistics/库存/Pantry.md');
const _ts = page?.file?.tasks ?? [];
const _pm = {}, _hu = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
for (const t of _ts) { _pm[t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _hu.add(t.parent); }
let n = 0, total = 0;
for (const t of _ts) {
  if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
  if (_hu.has(t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
  const h = (t.header?.subpath ?? t.section?.subpath ?? '').trim();
  if (!(h.startsWith(SEC + '.') || h.startsWith(SEC + ' ') || h === SEC)) continue;
  n++;
  let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
  let v = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
  if (!v && t.parent != null && /\d+\/\d+/.test(t.text)) { const p = _pm[t.parent]; if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) v = parseFloat(pp[1].replace(/,/g, '')); } }
  if (v) total += v;
}
dv.paragraph(`**📋 ${n} 项 · 💵 $${total.toFixed(2)}**`);
```

- [/] Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack ✍️ 2026-05-19 🛫 2026-05-13
- [x] POM Wonderful 100% Pomegranate Juice, 16 Ounce Bottle 💵 $4.04 ✍️ 2026-09-07 ➕ 2026-09-07 🛫 2026-09-11 ✅ 2026-09-20
  - [x] 1/2 ✍️ 2026-09-07 ➕ 2026-09-07 🛫 2026-09-11 ✅ 2026-09-15
  - [x] 2/2 ✍️ 2026-09-07 ➕ 2026-09-07 🛫 2026-09-15 ✅ 2026-09-20
- [/] POM Wonderful 100% Pomegranate Juice, 16 Ounce Bottle 💵 $4.04 ✍️ 2026-09-18 ➕ 2026-09-17 🛫 2026-09-26
- [ ] POM Wonderful 100% Pomegranate Juice, 48 Fl Oz 💵 $10.39 ✍️ 2026-09-20 ➕ 2026-09-20
# 6 日用

```dataviewjs
const SEC = '6';
const page = dv.page('Logistics/库存/Pantry.md');
const _ts = page?.file?.tasks ?? [];
const _pm = {}, _hu = new Set();   // a unit parent's 💵 is the per-unit price; its 1/N subtasks are the real units
for (const t of _ts) { _pm[t.line] = t; if (t.parent != null && /\d+\/\d+/.test(t.text)) _hu.add(t.parent); }
let n = 0, total = 0;
for (const t of _ts) {
  if (t.completed || t.status === '>' || t.status === '-') continue;   // done / forwarded / cancelled
  if (_hu.has(t.line)) continue;   // unit parent — counted via its 1/N subtasks instead
  const h = (t.header?.subpath ?? t.section?.subpath ?? '').trim();
  if (!(h.startsWith(SEC + '.') || h.startsWith(SEC + ' ') || h === SEC)) continue;
  n++;
  let pm = t.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/);
  let v = pm ? parseFloat(pm[1].replace(/,/g, '')) : 0;
  if (!v && t.parent != null && /\d+\/\d+/.test(t.text)) { const p = _pm[t.parent]; if (p) { const pp = p.text.match(/💵\s*\$?([\d,]+(?:\.\d+)?)/); if (pp) v = parseFloat(pp[1].replace(/,/g, '')); } }
  if (v) total += v;
}
dv.paragraph(`**📋 ${n} 项 · 💵 $${total.toFixed(2)}**`);
```
