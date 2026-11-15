/* Report page: renders the audit bundle. Works as a saved file and as the live dashboard. */
(function () {
  'use strict';
  var h = WS.h, fmt = WS.fmt;
  var payload = JSON.parse(document.getElementById('ws-data').textContent);
  var B = payload.bundle, LIVE = payload.live || null, PLANS = payload.plans || {};
  var page = document.getElementById('page');
  var UI = { floor: null, metric: 'overall', heat: true, selected: null };
  var NS = 'http://www.w3.org/2000/svg';
  var mounts = [], observers = [];
  var AREAS = { coverage: 'Coverage', interference: 'Interference and channels', speed: 'Wi-Fi speed',
    latency: 'Responsiveness', backhaul: 'Mesh links', isp: 'Internet line', config: 'Router settings',
    devices: 'Devices', security: 'Security' };
  var METRICS = { overall: 'Overall score', signal: 'Signal strength', speed: 'Speed', latency: 'Responsiveness' };

  function s(tag, attrs) {
    var el = document.createElementNS(NS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { if (attrs[k] != null) el.setAttribute(k, attrs[k]); });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null) continue;
      el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  function command() { return (B.meta && B.meta.command) || 'python3 -m wifiscout'; }
  function section(title, lede) {
    var el = h('section', null, h('h2', { text: title }));
    if (lede) el.appendChild(h('p', { class: 'lede', text: lede }));
    return el;
  }
  function tile(label, value, unit, sub) {
    return h('div', { class: 'tile' }, h('div', { class: 'label', text: label }),
      h('div', { class: 'value' }, value, unit ? h('small', { text: ' ' + unit }) : null),
      sub ? h('div', { class: 'sub', text: sub }) : null);
  }
  function facts(rows) {
    var dl = h('dl', { class: 'facts' });
    rows.forEach(function (row) { if (row && row[1] != null && row[1] !== '') { dl.appendChild(h('dt', { text: row[0] })); dl.appendChild(h('dd', { text: row[1] })); } });
    return dl;
  }
  function tableOf(headers, rows) {
    var table = h('table', null, h('thead', null, h('tr', null, headers.map(function (head) {
      return h('th', { class: head.num ? 'num' : null, text: head.label });
    }))));
    var body = h('tbody');
    rows.forEach(function (row) {
      body.appendChild(h('tr', null, row.map(function (cell, i) {
        return h('td', { class: headers[i].num ? 'num' : null }, cell == null ? '–' : cell);
      })));
    });
    table.appendChild(body);
    return h('div', { class: 'scroll-x' }, table);
  }
  function tipFor(wrap) {
    var tip = h('div', { class: 'tip', hidden: true });
    wrap.appendChild(tip);
    return {
      show: function (x, y, lines) {
        WS.clear(tip);
        lines.forEach(function (line, i) { tip.appendChild(i === 0 ? h('b', { text: line }) : h('div', { text: line })); });
        tip.hidden = false;
        var w = wrap.clientWidth, tw = tip.offsetWidth;
        tip.style.left = Math.max(4, Math.min(w - tw - 4, x + 12)) + 'px';
        tip.style.top = Math.max(4, y - tip.offsetHeight - 10) + 'px';
      },
      hide: function () { tip.hidden = true; }
    };
  }
  function onResize(wrap, draw) {
    mounts.push(function () {
      var last = 0;
      function go() { var w = wrap.clientWidth; if (w && w !== last) { last = w; draw(w); } }
      go();
      if (window.ResizeObserver) { var ro = new ResizeObserver(go); ro.observe(wrap); observers.push(ro); }
    });
  }
  function niceMax(v) {
    var steps = [1, 2, 4, 5, 10, 20, 40, 50, 100, 200, 400, 500, 1000, 2000];
    for (var i = 0; i < steps.length; i++) if (v <= steps[i]) return steps[i];
    return Math.ceil(v / 1000) * 1000;
  }
  function barPath(x, y, w, hgt, r, horizontal) {
    r = Math.min(r, w / 2, hgt);
    if (horizontal) {
      r = Math.min(r, hgt / 2, w);
      return 'M' + x + ' ' + y + 'h' + (w - r) + 'a' + r + ' ' + r + ' 0 0 1 ' + r + ' ' + r + 'v' + (hgt - 2 * r) +
        'a' + r + ' ' + r + ' 0 0 1 ' + (-r) + ' ' + r + 'h' + (-(w - r)) + 'z';
    }
    return 'M' + x + ' ' + (y + hgt) + 'v' + (-(hgt - r)) + 'a' + r + ' ' + r + ' 0 0 1 ' + r + ' ' + (-r) + 'h' + (w - 2 * r) +
      'a' + r + ' ' + r + ' 0 0 1 ' + r + ' ' + r + 'v' + (hgt - r) + 'z';
  }

  // ------------------------------------------------------------ top
  function masthead() {
    var meta = B.meta || {}, bits = [];
    if (meta.generated) bits.push('Measured ' + fmt.clock(meta.generated));
    var speed = B.speed || {};
    if (speed.isp) bits.push(speed.isp);
    var ctx = (B.analysis || {}).context || {};
    if (ctx.brand) bits.push(ctx.brand + (ctx.is_mesh ? ' mesh, ' + ctx.access_points + ' access points' : ' router'));
    if (meta.demo) bits.push('SAMPLE DATA, not your network');
    return h('header', { class: 'masthead' }, h('h1', { text: 'WiFi Scout report' }), h('span', { class: 'meta', text: bits.join(' · ') }));
  }
  function verdictPanel() {
    var a = B.analysis || {}, k = a.kpis || {}, speed = B.speed || {}, cov = a.coverage || {}, ctx = a.context || {};
    var hero = h('div', { class: 'hero' });
    hero.appendChild(h('div', { class: 'eyebrow', text: 'Network health' }));
    if (a.score != null) hero.appendChild(h('div', { class: 'figure' }, h('b', { text: a.score }), h('span', { text: 'out of 100' })));
    hero.appendChild(h('p', { text: a.verdict || '' }));
    if (a.score_note) hero.appendChild(h('p', { class: 'small muted', text: a.score_note }));
    var c = a.counts || {};
    var chips = h('div', { class: 'row' });
    ['critical', 'major', 'minor'].forEach(function (name) {
      if (c[name]) chips.appendChild(WS.statusChip(WS.SEVERITY[name], c[name] + ' ' + WS.SEVERITY[name].label.toLowerCase()));
    });
    if (c.good) chips.appendChild(WS.statusChip(WS.SEVERITY.good, c.good + ' working well'));
    hero.appendChild(chips);
    var tiles = h('div', { class: 'tiles' });
    var profile = B.profile || {};
    if (k.down_mbps != null) tiles.appendChild(tile('Download', fmt.num(k.down_mbps), 'Mbps', profile.plan_down ? 'plan ' + profile.plan_down : (speed.via === 'wifi' ? 'tested over Wi-Fi' : 'tested by cable')));
    if (k.up_mbps != null) tiles.appendChild(tile('Upload', fmt.num(k.up_mbps), 'Mbps', profile.plan_up ? 'plan ' + profile.plan_up : null));
    if (k.internet_ms != null) tiles.appendChild(tile('Internet delay', fmt.num(k.internet_ms), 'ms', k.router_ms != null ? 'router hop ' + fmt.num(k.router_ms) + ' ms' : null));
    if (k.bloat_grade) tiles.appendChild(tile('Lag under load', k.bloat_grade, 'grade', speed.bloat_ms != null ? '+' + fmt.num(speed.bloat_ms) + ' ms when busy' : null));
    if (k.coverage_pct != null) tiles.appendChild(tile('Usable coverage', k.coverage_pct, '%', cov.spots + ' spots measured'));
    if (k.devices) tiles.appendChild(tile('Devices', k.devices, null, ctx.access_points ? ctx.access_points + (ctx.access_points === 1 ? ' access point' : ' access points') : null));
    if (k.neighbors != null) tiles.appendChild(tile('Neighbour networks', k.neighbors, null, ((B.wifi || {}).channels || {}).density ? B.wifi.channels.density.level + ' crowding' : null));
    if (!tiles.childNodes.length) tiles.appendChild(h('p', { class: 'muted', text: 'Run "' + command() + '" on a computer in your home to fill this in.' }));
    return h('div', { class: 'panel verdict' }, hero, tiles);
  }
  function livePanel() {
    if (!LIVE || !LIVE.phone_url) return null;
    var qr = h('div', { class: 'qr', 'aria-label': 'QR code for the phone survey' });
    qr.innerHTML = LIVE.qr_svg || '';  // generated by this program, not from the network
    var steps = h('ol', null,
      h('li', { text: 'Join this home Wi-Fi on the iPhone, open the Camera and point it at the code.' }),
      LIVE.secure ? h('li', { text: 'Safari warns that the connection is not private, because your own computer made the certificate. Tap Show Details, then "visit this website".' }) : null,
      h('li', { text: 'Stand next to the router, tap the map where you are, and tap Measure. Repeat in every room and every spot that feels slow.' }),
      h('li', { text: 'This page updates by itself as readings arrive.' }));
    return h('div', { class: 'panel live' }, qr, h('div', { class: 'stack', style: { gap: '6px' } },
      h('h2', { text: 'Map the home with your iPhone' }),
      h('p', { class: 'muted small', text: 'The computer only knows the signal where it sits. The phone measures every room.' }),
      steps,
      h('p', { class: 'small' }, 'No camera? Type this in Safari: ', h('code', { text: LIVE.phone_url })),
      LIVE.secure ? null : h('p', { class: 'small muted', text: 'Secure mode is off, so walk mode (step counter and compass) is unavailable. Tapping the map works.' })));
  }
  function planSection() {
    var plan = (B.analysis || {}).plan || [];
    if (!plan.length) return null;
    var sec = section('What to do, in order', 'Worst problem first, free fixes before paid ones. Re-run the scan after each change to see what it did.');
    var list = h('div', { class: 'plan' });
    plan.forEach(function (item, index) {
      var body = h('div', { class: 'body' });
      body.appendChild(h('p', { text: item.why }));
      (item.notes || []).forEach(function (note) { body.appendChild(h('div', { class: 'note', text: note })); });
      body.appendChild(h('ol', null, item.steps.map(function (step) { return h('li', { text: step }); })));
      if (item.products && item.products.length) {
        body.appendChild(h('div', null, h('div', { class: 'eyebrow', text: 'Examples' }), h('ul', null, item.products.map(function (p) { return h('li', { text: p }); }))));
      }
      body.appendChild(h('p', { class: 'for', text: 'Addresses: ' + item['for'].join(' · ') }));
      list.appendChild(h('details', { class: 'fix', id: 'fix-' + item.id, open: index === 0 },
        h('summary', null, h('span', { class: 'n', text: item.step + '.' }), h('span', { class: 't', text: item.title }),
          h('span', { class: 'tags', text: (item.cost === 'free' ? 'free' : 'cost ' + item.cost) + ' · ' + item.effort })),
        body));
    });
    sec.appendChild(list);
    return sec;
  }

  // ------------------------------------------------------- coverage
  function scoreIndex() {
    var index = {};
    ((B.analysis || {}).spots || []).forEach(function (row) { index[row.id] = row.scores; });
    return index;
  }
  function coverageSection() {
    var survey = B.survey || {}, spots = survey.spots || [], a = B.analysis || {}, cov = a.coverage || {};
    var sec = section('Coverage map', spots.length ? (WS.isDark() ? 'Brighter' : 'Darker') + ' blue means better Wi-Fi at that spot. Where the blue fades out, coverage fades out. Markers show each reading’s verdict.' : null);
    if (!spots.length) {
      sec.appendChild(h('div', { class: 'empty stack' },
        h('h3', { text: 'No rooms measured yet' }),
        h('p', { text: LIVE ? 'Scan the code above with your iPhone and take a reading in each room. The map draws itself here.' : 'Run "' + command() + ' survey", scan the QR code with your iPhone and take a reading in each room.' })));
      return sec;
    }
    var scores = scoreIndex();
    spots.forEach(function (spot) { spot.scores = scores[spot.id] || {}; });
    var floors = survey.floors || ['Main floor'];
    if (!UI.floor || floors.indexOf(UI.floor) < 0) {
      UI.floor = floors.filter(function (f) { return spots.some(function (sp) { return sp.floor === f; }); })[0] || floors[0];
    }
    var filters = h('div', { class: 'filters' });
    if (floors.length > 1) {
      filters.appendChild(h('div', { class: 'chips', role: 'group', 'aria-label': 'Floor' }, floors.map(function (floor) {
        return h('button', { class: 'chip', type: 'button', 'aria-pressed': floor === UI.floor ? 'true' : 'false', onclick: function () { UI.floor = floor; UI.selected = null; render(); } }, floor);
      })));
    }
    var metric = h('select', { class: 'input', id: 'metric', style: { width: 'auto' }, 'aria-label': 'What the shading shows', onchange: function (e) { UI.metric = e.target.value; render(); } },
      Object.keys(METRICS).map(function (key) { return h('option', { value: key, selected: key === UI.metric, text: 'Shading: ' + METRICS[key] }); }));
    filters.appendChild(metric);
    filters.appendChild(h('label', { class: 'row small heat' }, h('input', { type: 'checkbox', id: 'heat', checked: UI.heat, onchange: function (e) { UI.heat = e.target.checked; render(); } }), 'Show shading'));
    sec.appendChild(filters);

    var here = spots.filter(function (sp) { return sp.floor === UI.floor; });
    var wrap = h('div', { class: 'map-wrap' });
    var canvas = h('canvas', { role: 'img', 'aria-label': 'Coverage map of ' + UI.floor + '. The table beside it lists every reading.' });
    wrap.appendChild(canvas);
    var tip = tipFor(wrap);
    var map = null;
    mounts.push(function () {
      map = new WS.CoverageMap(canvas, {});
      map.setData({ spots: here, metric: UI.metric, selected: UI.selected, showHeat: UI.heat, plan: planFor(UI.floor, function () { render(); }) });
      canvas.addEventListener('pointermove', function (event) {
        var rect = canvas.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
        var hit = map.spotAt(px, py, 26);
        if (!hit) { tip.hide(); return; }
        var grade = WS.GRADE[(hit.scores || {}).grade];
        tip.show(px, py, [[hit.rssi != null ? fmt.dbm(hit.rssi) : null, hit.down_mbps != null ? fmt.mbps(hit.down_mbps) : null, hit.rtt_ms != null ? fmt.ms(hit.rtt_ms) : null].filter(Boolean).join(' · ') || 'No values',
          (hit.name || 'Spot') + (grade ? ': ' + grade.label + ' (' + hit.scores.overall + ')' : '')]);
      });
      canvas.addEventListener('pointerleave', tip.hide);
      canvas.addEventListener('click', function (event) {
        var rect = canvas.getBoundingClientRect();
        var hit = map.spotAt(event.clientX - rect.left, event.clientY - rect.top, 26);
        UI.selected = hit ? hit.id : null; map.setData({ selected: UI.selected });
      });
    });
    var left = h('div', { class: 'stack' }, wrap, h('div', { class: 'legend' },
      h('span', { text: 'weak' }), h('span', { class: 'ramp', style: { background: WS.rampCss(WS.isDark()) } }), h('span', { text: 'strong' }),
      h('span', { class: 'grow' }),
      ['strong', 'fair', 'weak', 'dead'].map(function (g) { return WS.statusChip(WS.GRADE[g]); })));
    var unplaced = here.filter(function (sp) { return typeof sp.x !== 'number'; }).length;
    if (unplaced) left.appendChild(h('p', { class: 'small muted', text: unplaced + ' reading' + (unplaced === 1 ? ' is' : 's are') + ' not on the map: on the phone, select it, tap its position, then "Move to blue dot".' }));

    var nodeLabel = {};
    (cov.nodes || []).forEach(function (node) { nodeLabel[node.key] = node.label.replace('Access point ', ''); });
    var rows = here.slice().sort(function (x, y) { return ((x.scores || {}).overall || 0) - ((y.scores || {}).overall || 0); }).map(function (sp) {
      var grade = WS.GRADE[(sp.scores || {}).grade];
      var key = sp.bssid ? sp.bssid.slice(3, 14) : null;
      return [h('b', { text: sp.name || 'Spot' }), grade ? WS.statusChip(grade) : '–', sp.rssi == null ? null : Math.round(sp.rssi),
        sp.down_mbps == null ? null : fmt.num(sp.down_mbps), sp.rtt_ms == null ? null : fmt.num(sp.rtt_ms), key && nodeLabel[key] ? nodeLabel[key] : null];
    });
    var right = h('div', { class: 'panel stack' },
      h('div', { class: 'row' }, h('h3', { class: 'grow', text: 'Readings on ' + UI.floor }), h('span', { class: 'muted small', text: 'weakest first' })),
      tableOf([{ label: 'Spot' }, { label: 'Verdict' }, { label: 'dBm', num: true }, { label: 'Mbps', num: true }, { label: 'ms', num: true }, { label: 'AP' }], rows));
    if ((cov.by_floor || []).length > 1) {
      right.appendChild(h('div', null, h('div', { class: 'eyebrow', text: 'By floor' }), cov.by_floor.map(function (f) {
        return h('div', { class: 'row small', style: { marginTop: '6px' } }, h('span', { style: { width: '110px' }, text: f.floor }),
          h('span', { class: 'meter grow' }, h('span', { style: { width: Math.max(2, f.mean) + '%' } })), h('span', { class: 'num', text: f.mean }));
      })));
    }
    if ((cov.nodes || []).length) {
      right.appendChild(h('div', null, h('div', { class: 'eyebrow', text: 'Which access point served where' }), cov.nodes.map(function (node) {
        return h('p', { class: 'small', style: { marginTop: '4px' }, text: node.label + (node.avg_rssi != null ? ' (average ' + node.avg_rssi + ' dBm): ' : ': ') + node.spots.filter(Boolean).join(', ') });
      })));
    }
    sec.appendChild(h('div', { class: 'cover' }, left, right));
    return sec;
  }
  var planCache = {};
  function planFor(floor, done) {
    var meta = ((B.survey || {}).plans || {})[floor];
    if (PLANS[floor]) return PLANS[floor];
    if (!meta || !LIVE) return null;
    var hit = planCache[floor];
    if (hit && hit.rev === meta.rev) return hit.plan;
    if (!hit || hit.rev !== meta.rev) {
      planCache[floor] = { rev: meta.rev, plan: null };
      WS.api.call('/api/plan?floor=' + encodeURIComponent(floor)).then(function (plan) {
        planCache[floor] = { rev: meta.rev, plan: { image: plan.image, width_m: plan.width_m } };
        done();
      }).catch(function () {});
    }
    return null;
  }

  // ------------------------------------------------------- findings
  function findingsSection() {
    var a = B.analysis || {}, findings = a.findings || [];
    if (!findings.length) return null;
    var planIndex = {};
    (a.plan || []).forEach(function (item) { planIndex[item.id] = item; });
    var sec = section('What was found');
    var grid = h('div', { class: 'findings' });
    var problems = findings.filter(function (f) { return f.severity !== 'good'; });
    var order = [], seen = {};
    problems.forEach(function (f) { if (!seen[f.area]) { seen[f.area] = true; order.push(f.area); } });
    order.forEach(function (area) {
      grid.appendChild(h('div', { class: 'eyebrow area-title', text: AREAS[area] || area }));
      problems.filter(function (f) { return f.area === area; }).forEach(function (f) {
        var card = h('div', { class: 'panel finding' }, h('div', null, WS.statusChip(WS.SEVERITY[f.severity] || WS.SEVERITY.info)), h('h3', { text: f.title }));
        if (f.detail) card.appendChild(h('p', { text: f.detail }));
        if (f.evidence && f.evidence.length) card.appendChild(h('ul', null, f.evidence.map(function (e) { return h('li', { text: e }); })));
        var fixes = (f.fixes || []).map(function (id) { return planIndex[id]; }).filter(Boolean).sort(function (x, y) { return x.step - y.step; });
        if (fixes.length) {
          var line = h('p', { class: 'fixes' }, 'Fix: ');
          fixes.forEach(function (item, i) {
            if (i) line.appendChild(document.createTextNode(' · '));
            line.appendChild(h('a', { href: '#fix-' + item.id, onclick: function () { var d = document.getElementById('fix-' + item.id); if (d) d.open = true; } }, 'step ' + item.step + ', ' + item.title.charAt(0).toLowerCase() + item.title.slice(1)));
          });
          card.appendChild(line);
        }
        grid.appendChild(card);
      });
    });
    var good = findings.filter(function (f) { return f.severity === 'good'; });
    if (good.length) {
      grid.appendChild(h('div', { class: 'eyebrow area-title', text: 'Working well' }));
      good.forEach(function (f) {
        grid.appendChild(h('div', { class: 'panel finding' }, h('div', null, WS.statusChip(WS.SEVERITY.good)), h('h3', { text: f.title }), f.detail ? h('p', { text: f.detail }) : null));
      });
    }
    sec.appendChild(grid);
    return sec;
  }

  // ---------------------------------------------------------- charts
  function channelChart(band) {
    var rows = band.scores || [];
    var wrap = h('div', { class: 'chart' });
    var svgEl = s('svg', { role: 'img', 'aria-label': 'Competition on each 2.4 GHz channel' });
    wrap.appendChild(svgEl);
    var tip = tipFor(wrap);
    onResize(wrap, function (width) {
      WS.clear(svgEl);
      var H = 190, m = { l: 30, r: 6, t: 24, b: 24 };
      svgEl.setAttribute('height', H); svgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + H);
      var top = niceMax(Math.max.apply(null, rows.map(function (r) { return r.score; }).concat([1])));
      var plotH = H - m.t - m.b, bandW = (width - m.l - m.r) / rows.length, barW = Math.max(4, Math.min(24, bandW - 4));
      [0, top / 2, top].forEach(function (tick) {
        var y = m.t + plotH - plotH * tick / top;
        svgEl.appendChild(s('line', { x1: m.l, x2: width - m.r, y1: y + 0.5, y2: y + 0.5, stroke: tick === 0 ? 'var(--axis)' : 'var(--hairline)', 'stroke-width': 1 }));
        svgEl.appendChild(s('text', { x: m.l - 6, y: y + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--ink-3)' }, tick));
      });
      rows.forEach(function (row, i) {
        var cx = m.l + bandW * (i + 0.5), hgt = Math.max(row.score > 0 ? 2 : 0, plotH * row.score / top), y = m.t + plotH - hgt;
        var mine = band.current === row.channel, best = band.recommended === row.channel && band.advice === 'move';
        if (hgt > 0) svgEl.appendChild(s('path', { d: barPath(cx - barW / 2, y, barW, hgt, 4), fill: mine ? 'var(--accent)' : 'var(--quiet)' }));
        var showLabel = bandW >= 24 || row.channel === 1 || row.channel === 6 || row.channel === 11 || mine;
        if (showLabel) svgEl.appendChild(s('text', { x: cx, y: H - 7, 'text-anchor': 'middle', 'font-size': 11.5, 'font-weight': (row.channel === 1 || row.channel === 6 || row.channel === 11) ? 650 : 400, fill: 'var(--ink-2)' }, row.channel));
        if (mine || best) svgEl.appendChild(s('text', { x: cx, y: Math.max(12, y - 6), 'text-anchor': 'middle', 'font-size': 11.5, 'font-weight': 650, fill: 'var(--ink)' }, mine ? 'you' : 'clearest'));
        var hit = s('rect', { x: m.l + bandW * i, y: m.t, width: bandW, height: plotH + m.b, fill: 'transparent', tabindex: 0, role: 'img',
          'aria-label': 'Channel ' + row.channel + ': competition ' + row.score + ', ' + row.networks + ' networks' });
        function show() { tip.show(cx, y, [row.score.toFixed(1) + ' competition', 'Channel ' + row.channel + ' · ' + row.networks + (row.networks === 1 ? ' network' : ' networks') + ' centred here' + (mine ? ' · yours' : '')]); }
        hit.addEventListener('pointermove', show); hit.addEventListener('focus', show);
        hit.addEventListener('pointerleave', tip.hide); hit.addEventListener('blur', tip.hide);
        svgEl.appendChild(hit);
      });
    });
    return h('div', null,
      h('div', { class: 'chart-title', text: '2.4 GHz channels' }),
      h('div', { class: 'chart-sub', text: 'Taller bar = more neighbouring Wi-Fi to share that channel with. Only 1, 6 and 11 do not overlap each other.' }),
      wrap,
      h('details', { class: 'table-view' }, h('summary', { text: 'Show as table' }),
        tableOf([{ label: 'Channel', num: true }, { label: 'Competition', num: true }, { label: 'Networks', num: true }, { label: '' }],
          rows.map(function (r) { return [r.channel, r.score.toFixed(1), r.networks, band.current === r.channel ? 'yours' : (band.recommended === r.channel && band.advice === 'move' ? 'clearest' : '')]; }))));
  }
  function blockChart(band) {
    var rows = (band.blocks || []).filter(function (b) { return b.name !== '165'; });
    var wrap = h('div', { class: 'chart' });
    var svgEl = s('svg', { role: 'img', 'aria-label': 'Competition on each group of 5 GHz channels' });
    wrap.appendChild(svgEl);
    var tip = tipFor(wrap);
    onResize(wrap, function (width) {
      WS.clear(svgEl);
      var rowH = 26, labelW = Math.min(150, Math.round(width * 0.42)), valueW = 96, H = rows.length * rowH + 6;
      svgEl.setAttribute('height', H); svgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + H);
      var top = Math.max.apply(null, rows.map(function (r) { return r.score; }).concat([1]));
      var plotW = Math.max(40, width - labelW - valueW);
      svgEl.appendChild(s('line', { x1: labelW + 0.5, x2: labelW + 0.5, y1: 0, y2: H - 4, stroke: 'var(--axis)', 'stroke-width': 1 }));
      rows.forEach(function (row, i) {
        var y = i * rowH + 4, mine = band.current_block === row.name, barW = plotW * row.score / top;
        var label = s('text', { x: 0, y: y + 13, 'font-size': 12, fill: 'var(--ink-2)', 'font-weight': mine ? 650 : 400 }, row.name + (row.dfs ? ' · radar-shared' : ''));
        svgEl.appendChild(label);
        if (barW >= 1) svgEl.appendChild(s('path', { d: barPath(labelW + 1, y + 2, Math.max(3, barW), 14, 4, true), fill: mine ? 'var(--accent)' : 'var(--quiet)' }));
        svgEl.appendChild(s('text', { x: labelW + Math.max(3, barW) + 8, y: y + 13, 'font-size': 11.5, fill: 'var(--ink-2)' },
          row.networks + (row.networks === 1 ? ' network' : ' networks') + (mine ? ' · you' : '')));
        var hit = s('rect', { x: 0, y: y - 2, width: width, height: rowH, fill: 'transparent', tabindex: 0, role: 'img', 'aria-label': 'Channels ' + row.name + ': ' + row.networks + ' networks, competition ' + row.score });
        function show() { tip.show(labelW + barW, y + 6, [row.score.toFixed(1) + ' competition', 'Channels ' + row.name + ' · ' + row.networks + ' networks' + (row.dfs ? ' · shared with radar' : '')]); }
        hit.addEventListener('pointermove', show); hit.addEventListener('focus', show);
        hit.addEventListener('pointerleave', tip.hide); hit.addEventListener('blur', tip.hide);
        svgEl.appendChild(hit);
      });
    });
    return h('div', null,
      h('div', { class: 'chart-title', text: '5 GHz channel groups' }),
      h('div', { class: 'chart-sub', text: 'Each row is one 80 MHz slice of the band. Longer bar = busier.' }),
      wrap,
      h('details', { class: 'table-view' }, h('summary', { text: 'Show as table' }),
        tableOf([{ label: 'Channels' }, { label: 'Competition', num: true }, { label: 'Networks', num: true }, { label: '' }],
          rows.map(function (r) { return [r.name + (r.dfs ? ' (radar-shared)' : ''), r.score.toFixed(1), r.networks, band.current_block === r.name ? 'yours' : '']; }))));
  }
  function latencyChart(series) {
    var wrap = h('div', { class: 'chart' });
    var svgEl = s('svg', { role: 'img', 'aria-label': 'Internet delay over time' });
    wrap.appendChild(svgEl);
    var tip = tipFor(wrap);
    onResize(wrap, function (width) {
      WS.clear(svgEl);
      var H = 200, m = { l: 40, r: 10, t: 12, b: 26 };
      svgEl.setAttribute('height', H); svgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + H);
      var t0 = series[0].t, t1 = series[series.length - 1].t || t0 + 1, span = Math.max(1, t1 - t0);
      var values = series.map(function (p) { return p.ms; }).filter(function (v) { return v != null; });
      var top = niceMax(Math.max.apply(null, values.concat([20])));
      var plotW = width - m.l - m.r, plotH = H - m.t - m.b;
      function X(t) { return m.l + plotW * (t - t0) / span; }
      function Y(v) { return m.t + plotH - plotH * Math.min(v, top) / top; }
      [0, top / 2, top].forEach(function (tick) {
        svgEl.appendChild(s('line', { x1: m.l, x2: width - m.r, y1: Y(tick) + 0.5, y2: Y(tick) + 0.5, stroke: tick === 0 ? 'var(--axis)' : 'var(--hairline)', 'stroke-width': 1 }));
        svgEl.appendChild(s('text', { x: m.l - 6, y: Y(tick) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--ink-3)' }, tick + (tick === top ? ' ms' : '')));
      });
      var step = series.length > 1 ? (series[1].t - series[0].t) : 10;
      series.forEach(function (p) {
        if (p.down) svgEl.appendChild(s('rect', { x: X(p.t), y: m.t, width: Math.max(2, plotW * step / span), height: plotH, fill: 'var(--critical)', opacity: 0.22 }));
      });
      var d = '', pen = false;
      series.forEach(function (p) {
        if (p.ms == null) { pen = false; return; }
        d += (pen ? 'L' : 'M') + X(p.t).toFixed(1) + ' ' + Y(p.ms).toFixed(1); pen = true;
      });
      svgEl.appendChild(s('path', { d: d, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      [0, 0.5, 1].forEach(function (f) {
        var t = t0 + span * f, dte = new Date(t * 1000);
        svgEl.appendChild(s('text', { x: X(t), y: H - 7, 'text-anchor': f === 0 ? 'start' : (f === 1 ? 'end' : 'middle'), 'font-size': 11, fill: 'var(--ink-3)' },
          dte.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })));
      });
      var cross = s('line', { y1: m.t, y2: m.t + plotH, stroke: 'var(--ink-3)', 'stroke-width': 1, visibility: 'hidden' });
      var dot = s('circle', { r: 4, fill: 'var(--accent)', stroke: 'var(--surface)', 'stroke-width': 2, visibility: 'hidden' });
      svgEl.appendChild(cross); svgEl.appendChild(dot);
      var hit = s('rect', { x: m.l, y: m.t, width: plotW, height: plotH, fill: 'transparent' });
      hit.addEventListener('pointermove', function (event) {
        var rect = svgEl.getBoundingClientRect(), t = t0 + span * (event.clientX - rect.left - m.l) / plotW;
        var nearest = series[0];
        series.forEach(function (p) { if (Math.abs(p.t - t) < Math.abs(nearest.t - t)) nearest = p; });
        var x = X(nearest.t);
        cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
        if (nearest.ms != null) { dot.setAttribute('cx', x); dot.setAttribute('cy', Y(nearest.ms)); dot.setAttribute('visibility', 'visible'); } else dot.setAttribute('visibility', 'hidden');
        tip.show(x, nearest.ms != null ? Y(nearest.ms) : m.t + 20, [nearest.ms != null ? fmt.ms(nearest.ms) : 'Offline', fmt.clock(nearest.t) + (nearest.down ? ' · connection dropped' : '')]);
      });
      hit.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); });
      svgEl.appendChild(hit);
    });
    return wrap;
  }

  // ---------------------------------------------------------- details
  function wifiSection() {
    var wifi = B.wifi;
    if (!wifi) return null;
    var link = wifi.link || {}, ch = wifi.channels || {}, bands = ch.bands || {};
    var sec = section('Wi-Fi where this computer sits');
    var left = h('div', { class: 'panel stack' });
    if (link.connected) {
      left.appendChild(facts([
        ['Network', link.ssid || 'name hidden by the operating system'],
        ['Band', (link.band ? link.band + ' GHz' : '') + (link.channel ? ', channel ' + link.channel : '') + (link.width ? ', ' + link.width + ' MHz wide' : '')],
        ['Signal', link.rssi != null ? link.rssi + ' dBm' + (link.noise != null ? ', noise ' + link.noise + ' dBm' : '') + (link.snr != null ? ' (signal-to-noise ' + link.snr + ' dB)' : '') : null],
        ['Standard', (link.generation || '') + (link.phy ? ' (' + link.phy + ')' : '')],
        ['Link rate', link.tx_rate ? link.tx_rate + ' Mbps' : null],
        ['Security', link.security]]));
    } else {
      left.appendChild(h('p', { class: 'muted', text: (B.network || {}).via === 'ethernet' ? 'This computer is on a cable, so it has no Wi-Fi link of its own to report.' : 'No Wi-Fi link details were available on this computer.' }));
    }
    var density = ch.density;
    if (density) left.appendChild(h('p', { class: 'small', text: density.visible + ' other networks in range, ' + density.strong + ' of them strong. Crowding: ' + density.level + '.' }));
    (wifi.notes || []).forEach(function (note) { left.appendChild(h('p', { class: 'small muted', text: note })); });
    var neighbours = (wifi.neighbors || []).slice().sort(function (x, y) { return (y.rssi || -200) - (x.rssi || -200); });
    if (neighbours.length) {
      left.appendChild(h('details', { class: 'table-view' }, h('summary', { text: 'All ' + neighbours.length + ' networks heard' }),
        tableOf([{ label: 'Network' }, { label: 'Band' }, { label: 'Ch', num: true }, { label: 'dBm', num: true }, { label: 'Security' }],
          neighbours.map(function (n) { return [(n.ssid || '(hidden name)') + (n.mine ? ' · yours' : ''), n.band ? n.band + ' GHz' : null, n.channel, n.rssi, n.security]; }))));
    }
    var right = h('div', { class: 'panel stack' });
    if (bands['2.4'] && bands['2.4'].scores) right.appendChild(channelChart(bands['2.4']));
    if (bands['5'] && bands['5'].blocks) right.appendChild(blockChart(bands['5']));
    if (bands['6']) right.appendChild(h('p', { class: 'small', text: '6 GHz: ' + bands['6'].networks + ' other networks heard. This band is rarely crowded.' }));
    if (!right.childNodes.length) right.appendChild(h('p', { class: 'muted', text: 'No neighbouring networks could be listed from this computer.' }));
    sec.appendChild(h('div', { class: 'grid2' }, left, right));
    return sec;
  }
  function hopFacts(title, stats) {
    if (!stats) return null;
    return h('div', { class: 'tile' }, h('div', { class: 'label', text: title }),
      h('div', { class: 'value' }, stats.median == null ? 'no reply' : fmt.num(stats.median), stats.median == null ? null : h('small', { text: ' ms' })),
      h('div', { class: 'sub', text: stats.median == null ? '' : 'jitter ' + fmt.num(stats.jitter) + ' ms · loss ' + fmt.num(stats.loss_pct) + '%' }));
  }
  function speedSection() {
    var lat = B.latency, dns = B.dns, speed = B.speed;
    if (!lat && !dns && !speed) return null;
    var sec = section('Speed and delay', 'The router hop shows the health of your own network. The internet hop adds the provider’s part.');
    var left = h('div', { class: 'panel stack' });
    var hops = h('div', { class: 'tiles' });
    if (lat) { var a = hopFacts('This computer to the router', lat.gateway), b = hopFacts('This computer to the internet', lat.internet); if (a) hops.appendChild(a); if (b) hops.appendChild(b); }
    left.appendChild(hops);
    if (speed) {
      left.appendChild(facts([
        ['Download', speed.down_mbps != null ? fmt.mbps(speed.down_mbps) : null], ['Upload', speed.up_mbps != null ? fmt.mbps(speed.up_mbps) : null],
        ['Delay when idle', speed.idle_ms != null ? fmt.ms(speed.idle_ms) : null],
        ['Delay when busy', speed.loaded_down_ms != null || speed.loaded_up_ms != null ? fmt.ms(Math.max(speed.loaded_down_ms || 0, speed.loaded_up_ms || 0)) : null],
        ['Tested with', (speed.server || '') + (speed.method === 'networkQuality' ? ' (macOS networkQuality)' : '') + (speed.via ? ', computer on ' + (speed.via === 'wifi' ? 'Wi-Fi' : speed.via) : '')],
        ['Provider', speed.isp], ['Data used', speed.bytes_down ? Math.round((speed.bytes_down + speed.bytes_up) / 1e6) + ' MB' : null],
        ['Note', speed.note]]));
    } else {
      left.appendChild(h('p', { class: 'muted small', text: 'The internet speed test was skipped.' }));
    }
    var right = h('div', { class: 'panel stack' });
    right.appendChild(h('h3', { text: 'DNS lookup time' }));
    if (dns && (dns.system.length || dns.public.length)) {
      right.appendChild(tableOf([{ label: 'Resolver' }, { label: 'Address' }, { label: 'Average ms', num: true }, { label: 'Failed', num: true }],
        dns.system.concat(dns.public).map(function (row) { return [row.label, row.server, row.avg_ms == null ? null : fmt.num(row.avg_ms), row.failures + ' of ' + row.samples]; })));
      right.appendChild(h('p', { class: 'small muted', text: 'Every page load starts with these lookups. Under 30 ms is good.' }));
    } else right.appendChild(h('p', { class: 'muted small', text: 'Not measured.' }));
    var router = B.router;
    if (router && router.gateway) {
      right.appendChild(h('h3', { text: 'Path to the internet' }));
      right.appendChild(facts([['Router', [router.label || router.brand, router.model].filter(Boolean).join(' · ') || router.gateway],
        ['First hops', (router.hops || []).map(function (hop) { return hop || '(no reply)'; }).join('  →  ')],
        ['Double NAT', router.double_nat == null ? 'could not tell' : (router.double_nat ? 'yes' : 'no')],
        ['Carrier-grade NAT', router.cgnat == null ? 'could not tell' : (router.cgnat ? 'yes' : 'no')]]));
    }
    sec.appendChild(h('div', { class: 'grid2' }, left, right));
    return sec;
  }
  function devicesSection() {
    var scan = B.scan;
    if (!scan || !(scan.devices || []).length) return null;
    var devices = scan.devices.slice().sort(function (x, y) {
      function rank(d) { return d.gateway ? 0 : d.infra ? 1 : d.self ? 2 : d.kind === 'unknown' ? 4 : 3; }
      return rank(x) - rank(y) || String(x.type).localeCompare(String(y.type)) || String(x.name).localeCompare(String(y.name));
    });
    var summary = scan.summary || {};
    var sec = section('Devices on the network', devices.length + ' found' + (summary.private_addresses ? ', ' + summary.private_addresses + ' using a private Wi-Fi address (phones and laptops do this, so the maker cannot be read)' : '') + '.');
    var rows = devices.map(function (d) {
      var link = d.self ? 'this computer' : d.loss_pct == null ? null : (d.loss_pct >= 100 ? 'no reply' : fmt.num(d.rtt_ms) + ' ms' + (d.loss_pct ? ', ' + fmt.num(d.loss_pct) + '% loss' : ''));
      return [h('b', { text: d.name || d.label }), d.label && d.label !== d.name ? d.label : d.type, d.vendor || (d.random_mac ? 'private address' : null), d.ip, link];
    });
    sec.appendChild(h('div', { class: 'panel' }, tableOf([{ label: 'Name' }, { label: 'What it is' }, { label: 'Maker' }, { label: 'Address' }, { label: 'Link from this computer' }], rows)));
    return sec;
  }
  function modemSection() {
    var modem = B.modem;
    if (!modem || modem.status !== 'ok') return null;
    var sec = section('Modem line quality', 'Read from the modem itself' + (modem.model ? ' (' + modem.model + ')' : '') + '.');
    var panel = h('div', { class: 'panel stack' });
    if (modem.kind === 'cellular') {
      var cell = modem.cellular || {};
      panel.appendChild(tableOf([{ label: 'Radio' }, { label: 'Power (RSRP) dBm', num: true }, { label: 'Quality (SINR) dB', num: true }, { label: 'Bands' }],
        ['5g', '4g'].filter(function (k) { return cell[k]; }).map(function (k) { return [k.toUpperCase(), cell[k].rsrp, cell[k].sinr, (cell[k].bands || []).join(', ')]; })));
      panel.appendChild(h('p', { class: 'small muted', text: 'Good: power above -90 dBm and quality above 13 dB.' }));
    } else {
      var m = modem.summary || {};
      var tiles = h('div', { class: 'tiles' });
      tiles.appendChild(tile('Downstream power', m.down_power_min == null ? '–' : fmt.num(m.down_power_min, 1) + ' to ' + fmt.num(m.down_power_max, 1), 'dBmV', 'healthy: -10 to +10'));
      tiles.appendChild(tile('Signal-to-noise', m.snr_min == null ? '–' : fmt.num(m.snr_min, 1), 'dB lowest', 'healthy: 33 or more'));
      tiles.appendChild(tile('Upstream power', m.up_power_max == null ? '–' : fmt.num(m.up_power_max, 1), 'dBmV highest', 'healthy: 35 to 49'));
      tiles.appendChild(tile('Uncorrectable errors', m.uncorrected == null ? '–' : m.uncorrected.toLocaleString(), null, 'since the modem restarted'));
      tiles.appendChild(tile('Channels', (m.downstream_channels || 0) + ' down / ' + (m.upstream_channels || 0) + ' up', null, m.docsis ? 'DOCSIS ' + m.docsis : null));
      panel.appendChild(tiles);
      if ((modem.downstream || []).length) {
        panel.appendChild(h('details', { class: 'table-view' }, h('summary', { text: 'Every downstream channel' }),
          tableOf([{ label: 'Channel' }, { label: 'MHz', num: true }, { label: 'Power dBmV', num: true }, { label: 'SNR dB', num: true }, { label: 'Corrected', num: true }, { label: 'Uncorrectable', num: true }],
            modem.downstream.map(function (c) { return [c.channel, c.frequency, c.power, c.snr, c.corrected, c.uncorrected]; }))));
      }
    }
    sec.appendChild(panel);
    return sec;
  }
  function eeroSection() {
    var eero = B.eero;
    if (!eero || !(eero.nodes || []).length) return null;
    var sec = section('eero mesh', 'From your eero account. The link column is each unit’s own connection back toward the gateway.');
    var panel = h('div', { class: 'panel stack' });
    panel.appendChild(tableOf([{ label: 'eero' }, { label: 'Model' }, { label: 'Link to the mesh' }, { label: 'Devices', num: true }, { label: 'Status' }],
      eero.nodes.map(function (n) {
        var link = n.gateway ? 'gateway (connected to the modem)' : n.wired ? 'wired' : (n.mesh_bars != null ? 'wireless, ' + n.mesh_bars + ' of 5 bars' : 'wireless');
        return [h('b', { text: n.name }), n.model, link, n.clients, n.status];
      })));
    var weak = (eero.clients || []).filter(function (c) { return c.connected && c.wireless && c.rssi != null; }).sort(function (x, y) { return x.rssi - y.rssi; }).slice(0, 12);
    if (weak.length) {
      panel.appendChild(h('h3', { text: 'Weakest wireless devices' }));
      panel.appendChild(h('p', { class: 'small muted', text: 'Each one is a fixed sensor: a weak reading marks thin coverage where that device sits.' }));
      panel.appendChild(tableOf([{ label: 'Device' }, { label: 'dBm', num: true }, { label: 'Band' }, { label: 'Connected to' }],
        weak.map(function (c) { return [c.name, c.rssi, c.band ? c.band + ' GHz' : null, c.node]; })));
    }
    sec.appendChild(panel);
    return sec;
  }
  function stabilitySection() {
    var mon = B.monitor, series = B.monitor_series || [];
    if (!mon || !mon.samples) return null;
    var sec = section('Stability over time', 'From the monitor log. Red bands mark moments the connection dropped.');
    var panel = h('div', { class: 'panel stack' });
    var tiles = h('div', { class: 'tiles' });
    tiles.appendChild(tile('Watched for', mon.duration_min >= 60 ? fmt.num(mon.duration_min / 60, 1) : fmt.num(mon.duration_min), mon.duration_min >= 60 ? 'hours' : 'minutes'));
    tiles.appendChild(tile('Online', fmt.num(mon.uptime_pct, mon.uptime_pct >= 99.95 ? 0 : 2), '%'));
    tiles.appendChild(tile('Drop-outs', (mon.outages || []).length, null, mon.local_outages + ' in the home, ' + mon.upstream_outages + ' at the provider'));
    tiles.appendChild(tile('Typical delay', fmt.num(mon.net_ms), 'ms', 'slowest 5%: ' + fmt.num(mon.net_p95_ms) + ' ms'));
    panel.appendChild(tiles);
    if (series.length > 2) panel.appendChild(latencyChart(series));
    if ((mon.outages || []).length) {
      panel.appendChild(h('details', { class: 'table-view' }, h('summary', { text: 'Every drop-out' }),
        tableOf([{ label: 'When' }, { label: 'Seconds', num: true }, { label: 'Where the fault was' }],
          mon.outages.map(function (o) { return [fmt.clock(o.start), Math.round(o.seconds), o.scope === 'local' ? 'inside the home (Wi-Fi or router)' : 'modem or provider']; }))));
    }
    sec.appendChild(panel);
    return sec;
  }
  function toolsSection() {
    var tools = (B.analysis || {}).tools || [];
    if (!tools.length) return null;
    var sec = section('Other tools worth knowing', 'Free or built in. Useful for a second opinion or a closer look.');
    sec.appendChild(h('div', { class: 'panel' }, h('ul', { class: 'tools' }, tools.map(function (t) {
      return h('li', null, h('b', { text: t.name }), h('span', { text: t.use }));
    }))));
    return sec;
  }
  function footer() {
    var el = h('footer', null);
    var meta = B.meta || {};
    ((B.scan || {}).notes || []).concat(B.errors || []).forEach(function (note) { el.appendChild(h('span', { text: 'Note: ' + note })); });
    if ((B.analysis || {}).context && B.analysis.context.home_guessed) el.appendChild(h('span', { text: 'Home type was guessed as an apartment from how many networks are in range. Set it in the phone page under Setup.' }));
    el.appendChild(h('span', { text: 'WiFi Scout ' + (meta.version || '') + '. Everything here was measured on your own network and stays on your computer.' }));
    return el;
  }

  function render() {
    var open = {};
    Array.prototype.forEach.call(document.querySelectorAll('details[id]'), function (d) { open[d.id] = d.open; });
    var scroll = window.scrollY;
    observers.forEach(function (o) { o.disconnect(); }); observers = []; mounts = [];
    WS.clear(page);
    [masthead(), verdictPanel(), livePanel(), planSection(), coverageSection(), findingsSection(), wifiSection(), speedSection(),
      devicesSection(), modemSection(), eeroSection(), stabilitySection(), toolsSection(), footer()].forEach(function (el) { if (el) page.appendChild(el); });
    Object.keys(open).forEach(function (id) { var d = document.getElementById(id); if (d) d.open = open[id]; });
    mounts.forEach(function (fn) { fn(); });
    window.scrollTo(0, scroll);
  }

  render();
  if (LIVE) {
    WS.api.key = LIVE.key;
    var signature = JSON.stringify(B);
    setInterval(function () {
      if (document.visibilityState !== 'visible') return;
      WS.api.call('/api/bundle').then(function (fresh) {
        var next = JSON.stringify(fresh.bundle);
        if (next === signature) return;
        signature = next; B = fresh.bundle; LIVE = fresh.live;
        if (document.activeElement && /^(SELECT|INPUT)$/.test(document.activeElement.tagName)) return;
        render();
      }).catch(function () {});
    }, 6000);
  }
})();
