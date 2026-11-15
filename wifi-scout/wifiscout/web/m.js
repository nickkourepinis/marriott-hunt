/* Phone survey page: walk the home, measure each spot, see the coverage map fill in. */
(function () {
  'use strict';
  var h = WS.h, fmt = WS.fmt, api = WS.api;
  var ROOMS = ['Living room', 'Kitchen', 'Bedroom', 'Office', 'Hallway', 'Bathroom', 'Dining room', 'Kids room',
    'Basement', 'Garage', 'Patio', 'Yard'];
  var main = document.getElementById('main');
  var floorSelect = document.getElementById('floor');

  var query = new URLSearchParams(location.search);
  var key = query.get('k') || '';
  try {
    if (key) sessionStorage.setItem('wsk', key);
    else key = sessionStorage.getItem('wsk') || '';
  } catch (e) { /* private mode: keep the key from the address */ }
  api.key = key;

  var S = {
    data: null, floor: null, you: null, selected: null, busy: false, tab: 'survey', progress: '',
    last: null, name: '', analysis: null, plans: {}, metric: 'overall',
    walk: { on: false, offset: 0, scale: 1, anchor: null, walked: { x: 0, y: 0 }, alive: false }
  };
  var map = null, walker = null, toastTimer = null, pollTimer = null;

  function toast(message, ms) {
    var old = document.querySelector('.toast');
    if (old) old.remove();
    var el = h('div', { class: 'toast', role: 'status', text: message });
    document.body.appendChild(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.remove(); }, ms || 3500);
  }

  // ------------------------------------------------------------- data
  function floorSpots() {
    return (S.data ? S.data.spots : []).filter(function (s) { return s.floor === S.floor; });
  }
  function load(silent) {
    return api.call('/api/state').then(function (data) {
      var changed = !S.data || S.data.version !== data.version;
      S.data = data;
      if (!S.floor || data.floors.indexOf(S.floor) < 0) S.floor = data.floors[0];
      if (changed || !silent) { fillFloors(); render(); }
      loadPlan();
    }).catch(function (error) {
      if (error.status === 403) {
        WS.clear(main).appendChild(h('div', { class: 'view' }, h('div', { class: 'panel stack' },
          h('h2', { text: 'Scan the QR code again' }),
          h('p', { class: 'muted', text: 'This page needs the link shown on the computer running WiFi Scout. Open the camera and scan the code on its screen.' }))));
      } else if (!silent) {
        toast('Cannot reach the computer running WiFi Scout. Is this phone on the home Wi-Fi?', 6000);
      }
    });
  }
  function loadPlan() {
    var meta = S.data && S.data.plans ? S.data.plans[S.floor] : null;
    var cached = S.plans[S.floor];
    if (!meta) { if (cached) { delete S.plans[S.floor]; drawMap(); } return; }
    if (cached && cached.rev === meta.rev) return;
    api.call('/api/plan?floor=' + encodeURIComponent(S.floor)).then(function (plan) {
      S.plans[S.floor] = { image: plan.image, width_m: plan.width_m, rev: meta.rev };
      drawMap();
    }).catch(function () {});
  }
  function fillFloors() {
    WS.clear(floorSelect);
    S.data.floors.forEach(function (name) {
      floorSelect.appendChild(h('option', { value: name, text: name, selected: name === S.floor }));
    });
  }
  floorSelect.addEventListener('change', function () {
    S.floor = floorSelect.value; S.you = S.walk.on ? { x: 0, y: 0 } : null; S.selected = null;
    resetAnchor(); render(); loadPlan();
  });

  // --------------------------------------------------------- measuring
  function headers(extra) {
    var out = { 'X-WS-Key': key };
    if (extra) Object.keys(extra).forEach(function (k) { out[k] = extra[k]; });
    return out;
  }
  function pingTest(count) {
    var times = [], fails = 0, index = 0;
    function next() {
      if (index >= count || (index >= 5 && !times.length)) {   // nothing answered: do not keep the user waiting
        var kept = times.length;
        return {
          rtt_ms: kept ? Math.round(WS.median(times) * 10) / 10 : null,
          jitter_ms: kept ? Math.round(WS.jitter(times) * 10) / 10 : null,
          loss_pct: Math.round(1000 * fails / Math.max(1, kept + fails)) / 10
        };
      }
      var i = index++;
      var controller = new AbortController();
      var timer = setTimeout(function () { controller.abort(); }, 1500);
      var start = performance.now();
      return fetch('/api/ping?i=' + i + '&t=' + Date.now(), { cache: 'no-store', signal: controller.signal, headers: headers() })
        .then(function (r) { return r.text(); })
        .then(function () { if (i >= 2) times.push(performance.now() - start); })
        .catch(function () { if (i >= 2) fails++; })
        .then(function () { clearTimeout(timer); return next(); });
    }
    return next();
  }
  function downTest(ms) {
    var controller = new AbortController();
    var start = performance.now(), bytes = 0, warmAt = null, warmBytes = 0;
    var guard = setTimeout(function () { controller.abort(); }, ms + 1500);
    function finish() {
      clearTimeout(guard);
      try { controller.abort(); } catch (e) { /* already closed */ }
      var end = performance.now();
      if (warmAt !== null && end - warmAt > 300) return (bytes - warmBytes) * 8 / ((end - warmAt) / 1000) / 1e6;
      return end > start && bytes ? bytes * 8 / ((end - start) / 1000) / 1e6 : null;
    }
    return fetch('/api/down?bytes=400000000&t=' + Date.now(), { cache: 'no-store', signal: controller.signal, headers: headers() })
      .then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        if (!response.body || !response.body.getReader) {
          return response.arrayBuffer().then(function (buffer) { bytes = buffer.byteLength; return finish(); });
        }
        var reader = response.body.getReader();
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) return finish();
            var now = performance.now();
            bytes += chunk.value.length;
            if (warmAt === null && now - start > 500) { warmAt = now; warmBytes = bytes; }
            if (now - start > ms) return finish();
            return pump();
          });
        }
        return pump();
      }).catch(function () { return finish(); });
  }
  var uploadBlock = null;
  function upTest(ms) {
    if (!uploadBlock) {
      uploadBlock = new Uint8Array(4 * 1024 * 1024);
      if (window.crypto && crypto.getRandomValues) {
        for (var o = 0; o < uploadBlock.length; o += 65536) crypto.getRandomValues(uploadBlock.subarray(o, o + 65536));
      }
    }
    var size = 256 * 1024, start = performance.now(), counted = 0, countFrom = null, firstBytes = 0, firstMs = 0;
    function next() {
      var now = performance.now();
      if (now - start >= ms) {
        if (countFrom !== null && counted > 0) return counted * 8 / ((now - countFrom) / 1000) / 1e6;
        return firstBytes ? firstBytes * 8 / (firstMs / 1000) / 1e6 : null;
      }
      var body = new Blob([uploadBlock.subarray(0, size)]);
      var began = performance.now();
      return fetch('/api/up?t=' + Date.now(), { method: 'POST', body: body, cache: 'no-store',
        headers: headers({ 'Content-Type': 'application/octet-stream' }) })
        .then(function (r) { return r.text(); })
        .then(function () {
          var took = performance.now() - began;
          if (countFrom === null) { countFrom = performance.now(); firstBytes = size; firstMs = took; }
          else counted += size;
          if (took < 400 && size < uploadBlock.length) size = Math.min(uploadBlock.length, size * 2);
          return next();
        });
    }
    return next().catch(function () { return null; });
  }

  function measure(existing) {
    if (S.busy) return;
    S.busy = true;
    var result = {};
    var position = S.you ? { x: S.you.x, y: S.you.y } : null;
    setProgress('Checking response time…');
    pingTest(14).then(function (ping) {
      Object.assign(result, ping);
      if (ping.rtt_ms == null) throw new Error('offline');
      setProgress('Measuring download…');
      return downTest(3500);
    }).then(function (down) {
      result.down_mbps = down == null ? null : Math.round(down * 10) / 10;
      setProgress('Measuring upload…');
      return upTest(3500);
    }).then(function (up) {
      result.up_mbps = up == null ? null : Math.round(up * 10) / 10;
      if (result.rtt_ms == null && result.down_mbps == null) throw new Error('offline');
      var body = Object.assign({ floor: S.floor, source: 'phone' }, result);
      if (existing) body.id = existing.id;
      else {
        body.name = (S.name || '').trim() || null;
        if (position) { body.x = position.x; body.y = position.y; }
      }
      if (walker && S.walk.on) { body.heading = walker.heading == null ? null : Math.round(walker.heading); body.steps = walker.steps; }
      setProgress('Saving…');
      return api.call('/api/spot', { method: 'POST', body: body });
    }).then(function (saved) {
      S.last = saved.spot; S.selected = saved.spot.id; S.name = '';
      if (!S.walk.on) S.you = null; else resetAnchor();
      S.busy = false; S.progress = '';
      return load();
    }).then(function () {
      if (S.last && S.last.rssi == null && !S.toldAboutSignal) {
        S.toldAboutSignal = true;
        toast('Saved. For a sharper map, add the signal strength: see the reading below, or Setup.', 5000);
      }
    }).catch(function () {
      S.busy = false; S.progress = '';
      render();
      toast('The measurement failed. This spot may have no usable Wi-Fi: move one step back and try again, or save it as a dead zone below.', 7000);
      S.failedHere = true; render();
    });
  }
  function saveDeadZone() {
    var body = { floor: S.floor, source: 'manual', name: (S.name || '').trim() || null, rssi: -92, rtt_ms: 300, loss_pct: 25,
      down_mbps: 0.1, note: 'Marked as dead zone: the test could not run here' };
    if (S.you) { body.x = S.you.x; body.y = S.you.y; }
    api.call('/api/spot', { method: 'POST', body: body }).then(function (saved) {
      S.last = saved.spot; S.selected = saved.spot.id; S.failedHere = false; S.name = '';
      return load();
    }).catch(function () { toast('Still offline here. Walk back toward the router and tap the button again.', 6000); });
  }
  function setProgress(text) { S.progress = text; var el = document.getElementById('progress'); if (el) el.textContent = text; var b = document.getElementById('measure'); if (b) b.disabled = true; }

  // -------------------------------------------------------- walk mode
  function resetAnchor() { S.walk.anchor = S.you ? { x: S.you.x, y: S.you.y } : null; S.walk.walked = { x: 0, y: 0 }; }
  function onStep(w) {
    if (!S.walk.on || !S.you || w.heading == null) return;
    var stride = (S.data ? S.data.stride_m : 0.7);
    S.you = WS.stepFrom(S.you, w.heading, S.walk.offset, stride * S.walk.scale);
    var rad = w.heading * Math.PI / 180;
    S.walk.walked.x += stride * Math.sin(rad); S.walk.walked.y += stride * Math.cos(rad);
    drawMap(); updateWalkStatus();
  }
  function onMotion(event) {
    var a = event.accelerationIncludingGravity;
    if (!a) return;
    S.walk.alive = true;
    walker.motion(a.x, a.y, a.z, event.timeStamp || performance.now());
  }
  function onOrientation(event) {
    var heading = null;
    if (typeof event.webkitCompassHeading === 'number' && !isNaN(event.webkitCompassHeading)) heading = event.webkitCompassHeading;
    else if (event.absolute && typeof event.alpha === 'number') heading = (360 - event.alpha) % 360;
    if (heading == null) return;
    var first = walker.heading == null;
    walker.compass(heading);
    if (first && S.walk.pendingFace) { S.walk.offset = walker.heading; S.walk.pendingFace = false; }
    if (S.walk.on) { drawMap(); updateWalkStatus(); }
  }
  function startWalk() {
    if (!window.isSecureContext) {
      toast('Motion sensors only work on the secure address. Open the https link shown in Setup.', 7000);
      S.tab = 'setup'; render(); return;
    }
    var asks = [];
    try {
      if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') asks.push(DeviceMotionEvent.requestPermission());
      if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') asks.push(DeviceOrientationEvent.requestPermission());
    } catch (e) { asks = [Promise.reject(e)]; }
    Promise.all(asks).then(function (answers) {
      if (answers.some(function (a) { return a !== 'granted'; })) throw new Error('denied');
      if (!walker) {
        walker = new WS.Walker(onStep);
        window.addEventListener('devicemotion', onMotion);
        window.addEventListener('deviceorientationabsolute', onOrientation);
        window.addEventListener('deviceorientation', onOrientation);
      }
      S.walk.on = true; S.walk.alive = false; S.walk.scale = 1;
      if (walker.heading != null) S.walk.offset = walker.heading; else S.walk.pendingFace = true;
      if (!S.you) {
        var here = floorSpots().filter(function (s) { return typeof s.x === 'number'; });
        var lastSpot = here.length ? here[here.length - 1] : null;
        S.you = lastSpot ? { x: lastSpot.x, y: lastSpot.y } : { x: 0, y: 0 };
      }
      resetAnchor(); render();
      setTimeout(function () {
        if (S.walk.on && !S.walk.alive) toast('No motion data arrived. In iPhone Settings > Apps > Safari, allow Motion & Orientation Access, then reload.', 8000);
      }, 2500);
    }).catch(function () {
      toast('Motion access was not allowed. Reload the page and tap Allow when asked.', 6000);
    });
  }
  function stopWalk() { S.walk.on = false; render(); }
  function faceUp() {
    if (walker && walker.heading != null) { S.walk.offset = walker.heading; resetAnchor(); drawMap(); toast('Map-up now points the way you are facing.'); }
  }
  function updateWalkStatus() {
    var el = document.getElementById('walk-status');
    if (el && walker) el.textContent = walker.steps + ' steps · facing ' + (walker.heading == null ? '–' : Math.round(walker.heading) + '°');
  }
  function mapHeading() {
    if (!S.walk.on || !walker || walker.heading == null) return null;
    return (walker.heading - S.walk.offset + 360) % 360;
  }

  // -------------------------------------------------------------- map
  function drawMap() {
    if (!map) return;
    map.setData({ spots: floorSpots(), you: S.you, heading: mapHeading(), selected: S.selected, metric: S.metric,
      plan: S.plans[S.floor] || null });
  }
  function onMapTap(event) {
    var rect = map.canvas.getBoundingClientRect();
    var px = event.clientX - rect.left, py = event.clientY - rect.top;
    var hit = map.spotAt(px, py, 22);
    if (hit) { S.selected = S.selected === hit.id ? null : hit.id; render(); return; }
    var world = map.toWorld(px, py);
    if (S.walk.on && S.walk.anchor) {
      var learned = WS.calibrate(S.walk.walked, { x: world.x - S.walk.anchor.x, y: world.y - S.walk.anchor.y }, S.walk.offset, S.walk.scale);
      if (learned) { S.walk.offset = learned.offset; S.walk.scale = learned.scale; }
    }
    S.you = { x: world.x, y: world.y };
    resetAnchor();
    S.failedHere = false;
    render();
  }

  // ----------------------------------------------------------- render
  function readingTiles(spot) {
    return h('div', { class: 'reading' },
      h('div', null, h('b', { class: 'num', text: spot.rssi == null ? '–' : Math.round(spot.rssi) }), h('span', { text: 'signal dBm' })),
      h('div', null, h('b', { class: 'num', text: spot.down_mbps == null ? '–' : fmt.num(spot.down_mbps) }), h('span', { text: 'down Mbps' })),
      h('div', null, h('b', { class: 'num', text: spot.up_mbps == null ? '–' : fmt.num(spot.up_mbps) }), h('span', { text: 'up Mbps' })),
      h('div', null, h('b', { class: 'num', text: spot.rtt_ms == null ? '–' : fmt.num(spot.rtt_ms) }), h('span', { text: 'delay ms' })));
  }
  function gradeChip(spot) {
    var grade = WS.GRADE[(spot.scores || {}).grade];
    return grade ? WS.statusChip(grade) : h('span', { class: 'muted small', text: 'no reading' });
  }
  function signalPanel(spot) {
    var box = h('div', { class: 'stack', style: { gap: '8px' } });
    box.appendChild(h('p', { class: 'small muted', text: 'Add the real signal strength for ' + (spot.name || 'this spot') + ':' }));
    var input = h('input', { class: 'input', id: 'rssi-' + spot.id, inputmode: 'numeric', placeholder: 'e.g. -62', style: { maxWidth: '110px' }, 'aria-label': 'Signal in dBm' });
    box.appendChild(h('div', { class: 'row' },
      h('a', { class: 'btn', href: 'shortcuts://run-shortcut?name=' + encodeURIComponent((S.data.server || {}).shortcut_name || 'WiFi Scout') }, 'Read it with the Shortcut'),
      h('span', { class: 'muted small', text: 'or type it' }), input,
      h('button', { class: 'btn', onclick: function () {
        var value = parseFloat(String(input.value).replace('−', '-'));
        if (isNaN(value)) return;
        if (value > 0) value = -value;
        api.call('/api/spot', { method: 'POST', body: { id: spot.id, rssi: value } }).then(function () { return load(); });
      } }, 'Save')));
    return box;
  }
  function renderSurvey(view) {
    var data = S.data, spots = floorSpots();
    var mapWrap = h('div', { class: 'map-wrap' });
    var canvas = h('canvas', { id: 'map', 'aria-label': 'Coverage map of ' + S.floor });
    mapWrap.appendChild(canvas);
    mapWrap.appendChild(h('div', { class: 'map-hint', text: S.walk.on ? 'Walk mode: the blue dot follows you. Tap to correct it.' : (S.you ? 'Blue dot = you. Tap Measure.' : 'Tap the map where you are standing') }));
    view.appendChild(mapWrap);
    view.appendChild(h('div', { class: 'legend' },
      h('span', { text: 'weak' }), h('span', { class: 'ramp', style: { background: WS.rampCss(WS.isDark()) } }), h('span', { text: 'strong' }),
      h('span', { class: 'grow' }),
      h('label', { class: 'row small' }, h('input', { type: 'checkbox', id: 'heat', checked: map ? map.showHeat : true, onchange: function (e) { map.showHeat = e.target.checked; drawMap(); } }), 'shading')));
    map = new WS.CoverageMap(canvas, {});
    canvas.addEventListener('click', onMapTap);
    drawMap();

    var panel = h('div', { class: 'panel stack' });
    var nameInput = h('input', { class: 'input', id: 'spot-name', placeholder: 'Name this spot (e.g. Kitchen)', value: S.name, autocomplete: 'off',
      oninput: function (e) { S.name = e.target.value; } });
    panel.appendChild(h('button', { class: 'btn primary big', id: 'measure', disabled: S.busy, onclick: function () { measure(null); } }, 'Measure here'));
    panel.appendChild(h('div', { class: 'progress', id: 'progress', role: 'status', text: S.progress || (S.you || S.walk.on ? 'Takes about 8 seconds. Stand still. Name the spot while it runs.' : 'Tip: tap the map first so the reading lands in the right place.') }));
    panel.appendChild(nameInput);
    var used = {};
    (data.spots || []).forEach(function (s) { used[s.name] = true; });
    panel.appendChild(h('div', { class: 'chips one-line' }, ROOMS.filter(function (r) { return !used[r]; }).map(function (room) {
      return h('button', { class: 'chip', type: 'button', 'aria-pressed': S.name === room ? 'true' : 'false',
        onclick: function () { S.name = room; var el = document.getElementById('spot-name'); if (el) el.value = room;
          Array.prototype.forEach.call(document.querySelectorAll('.chips.one-line .chip'), function (c) { c.setAttribute('aria-pressed', c.textContent === room ? 'true' : 'false'); }); } }, room);
    })));
    if (S.failedHere) {
      panel.appendChild(h('button', { class: 'btn', onclick: saveDeadZone }, 'Save this spot as a dead zone'));
    }
    view.appendChild(panel);

    var selected = (data.spots || []).filter(function (s) { return s.id === S.selected; })[0];
    if (selected) {
      var card = h('div', { class: 'panel stack' });
      card.appendChild(h('div', { class: 'row' }, h('h3', { class: 'grow', text: selected.name || 'Spot' }), gradeChip(selected)));
      card.appendChild(readingTiles(selected));
      if (selected.channel || selected.bssid || selected.tx_rate) {
        card.appendChild(h('p', { class: 'small muted', text: [selected.channel ? 'channel ' + selected.channel : null,
          selected.band ? selected.band + ' GHz' : null, selected.tx_rate ? 'link ' + selected.tx_rate + ' Mbps' : null,
          selected.bssid ? 'access point …' + selected.bssid.slice(-8) : null].filter(Boolean).join(' · ') }));
      }
      if (selected.rssi == null) card.appendChild(signalPanel(selected));
      card.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn', disabled: S.busy, onclick: function () { measure(selected); } }, 'Measure again'),
        S.you ? h('button', { class: 'btn', onclick: function () {
          api.call('/api/spot', { method: 'POST', body: { id: selected.id, x: S.you.x, y: S.you.y, floor: S.floor } }).then(function () { if (!S.walk.on) S.you = null; return load(); });
        } }, 'Move to blue dot') : null,
        h('button', { class: 'btn', onclick: function () {
          var name = window.prompt ? window.prompt('Name for this spot', selected.name || '') : null;
          if (name) api.call('/api/spot', { method: 'POST', body: { id: selected.id, name: name } }).then(function () { return load(); });
        } }, 'Rename'),
        h('button', { class: 'btn danger', onclick: function () {
          api.call('/api/spot/delete', { method: 'POST', body: { id: selected.id } }).then(function () { S.selected = null; return load(); });
        } }, 'Delete')));
      view.appendChild(card);
    }

    var walk = h('div', { class: 'panel stack' });
    walk.appendChild(h('div', { class: 'row' }, h('h3', { class: 'grow', text: 'Walk mode' }),
      h('button', { class: 'btn' + (S.walk.on ? '' : ' primary'), onclick: S.walk.on ? stopWalk : startWalk }, S.walk.on ? 'Turn off' : 'Turn on')));
    if (S.walk.on) {
      walk.appendChild(h('p', { class: 'num', id: 'walk-status', text: '' }));
      walk.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn', onclick: faceUp }, 'I am facing map-up'),
        h('button', { class: 'btn', onclick: function () { changeStride(-0.05); } }, 'Shorter steps'),
        h('button', { class: 'btn', onclick: function () { changeStride(0.05); } }, 'Longer steps')));
      walk.appendChild(h('p', { class: 'small muted', text: 'Hold the phone flat, pointing where you walk. Stride ' + (data.stride_m || 0.7).toFixed(2) + ' m. If the dot drifts, tap your real position: it learns from each correction.' }));
    } else {
      walk.appendChild(h('p', { class: 'small muted', text: 'Uses the step counter and compass in this iPhone to move the blue dot as you walk, so you do not have to tap the map for every reading.' }));
    }
    view.appendChild(walk);

    var list = h('div', { class: 'panel' });
    list.appendChild(h('div', { class: 'row', style: { marginBottom: '4px' } }, h('h3', { class: 'grow', text: 'Readings on ' + S.floor }), h('span', { class: 'muted small', text: spots.length + (spots.length === 1 ? ' spot' : ' spots') })));
    if (!spots.length) list.appendChild(h('p', { class: 'muted small', text: 'Nothing measured on this floor yet. Start next to the router, then work outward to the far corners and anywhere Wi-Fi feels slow.' }));
    spots.slice().sort(function (a, b) { return ((a.scores || {}).overall || 0) - ((b.scores || {}).overall || 0); }).forEach(function (spot) {
      var values = [spot.rssi == null ? null : fmt.dbm(spot.rssi), spot.down_mbps == null ? null : fmt.mbps(spot.down_mbps) + ' down',
        spot.rtt_ms == null ? null : fmt.ms(spot.rtt_ms), typeof spot.x === 'number' ? null : 'not on the map'].filter(Boolean).join(' · ');
      list.appendChild(h('div', { class: 'spot-row', role: 'button', tabindex: '0', 'aria-current': spot.id === S.selected ? 'true' : 'false',
        onclick: function () { S.selected = S.selected === spot.id ? null : spot.id; render(); } },
        h('b', { text: spot.name || 'Spot' }), gradeChip(spot), h('div', { class: 'vals num', text: values })));
    });
    view.appendChild(list);
    updateWalkStatus();
  }
  function changeStride(delta) {
    var next = Math.round(((S.data.stride_m || 0.7) + delta) * 100) / 100;
    api.call('/api/settings', { method: 'POST', body: { stride_m: next } }).then(function () { return load(); });
  }

  function renderResults(view) {
    var a = S.analysis;
    if (!a) { view.appendChild(h('p', { class: 'muted', text: 'Working out the results…' })); return; }
    var head = h('div', { class: 'panel stack' });
    if (a.score != null) {
      head.appendChild(h('div', { class: 'row', style: { alignItems: 'flex-end' } },
        h('span', { class: 'score', text: a.score }), h('span', { class: 'muted', text: 'out of 100' })));
    }
    head.appendChild(h('p', { text: a.verdict }));
    if (a.coverage && a.coverage.coverage_pct != null) {
      head.appendChild(h('p', { class: 'small muted', text: a.coverage.coverage_pct + '% of ' + a.coverage.spots + ' measured spots have usable Wi-Fi.' }));
    }
    view.appendChild(head);
    if (a.plan && a.plan.length) {
      view.appendChild(h('h2', { text: 'What to do, in order' }));
      a.plan.slice(0, 8).forEach(function (item) {
        view.appendChild(h('details', { class: 'fix' },
          h('summary', null, item.step + '. ' + item.title, h('span', { class: 'muted small', text: '  ' + (item.cost === 'free' ? 'free' : 'cost ' + item.cost) + ' · ' + item.effort })),
          h('p', { class: 'small', style: { marginTop: '8px' }, text: item.why }),
          item.notes && item.notes.length ? h('ul', null, item.notes.map(function (n) { return h('li', { text: n }); })) : null,
          h('ol', null, item.steps.map(function (s) { return h('li', { text: s }); })),
          item.products && item.products.length ? h('p', { class: 'small muted', style: { marginTop: '6px' }, text: 'Examples: ' + item.products.join('; ') }) : null));
      });
    }
    var findings = h('div', { class: 'panel' });
    (a.findings || []).forEach(function (f) {
      findings.appendChild(h('div', { class: 'finding' },
        h('div', { class: 'row' }, WS.statusChip(WS.SEVERITY[f.severity] || WS.SEVERITY.info)),
        h('h3', { style: { marginTop: '6px' }, text: f.title }),
        f.detail ? h('p', { class: 'small', style: { marginTop: '4px', color: 'var(--ink-2)' }, text: f.detail }) : null,
        f.evidence && f.evidence.length ? h('ul', null, f.evidence.map(function (e) { return h('li', { text: e }); })) : null));
    });
    view.appendChild(h('h2', { text: 'What was found' }));
    view.appendChild(findings);
    view.appendChild(h('p', { class: 'small muted', text: 'The full report with every chart is on the computer running WiFi Scout.' }));
  }

  function renderSetup(view) {
    var data = S.data, server = data.server || {}, profile = data.profile || {};
    if (!window.isSecureContext && server.https_url) {
      view.appendChild(h('div', { class: 'banner stack' },
        h('b', { text: 'Walk mode needs the secure address' }),
        h('span', { text: 'iPhone only shares the step counter and compass with secure pages. Open this link, tap "Show Details", then "visit this website". The warning appears because the certificate was made by your own computer.' }),
        h('a', { href: server.https_url, text: server.https_url.split('?')[0] })));
    }
    var shortcut = h('div', { class: 'panel stack' });
    shortcut.appendChild(h('h3', { text: 'Real signal strength: the one-time Shortcut' }));
    shortcut.appendChild(h('p', { class: 'small muted', text: 'Web pages cannot read Wi-Fi signal strength on iPhone. Apple’s Shortcuts app can. Build this once (about two minutes), then tap "Read it with the Shortcut" after each measurement.' }));
    shortcut.appendChild(h('ol', { class: 'steps-list' },
      h('li', { text: 'Open the Shortcuts app, tap +, and name the shortcut exactly: ' + (server.shortcut_name || 'WiFi Scout') }),
      h('li', { text: 'Add the action "Get Network Details". Tap "Network Name" in it and choose RSSI.' }),
      h('li', null, 'Add the action "Get Contents of URL". In its address box enter this, ending with the = sign:', h('br'), h('code', { id: 'radio-url', text: (server.radio_url || '') + '&rssi=' })),
      h('li', { text: 'With the cursor right after the = sign, tap "Network Details" in the bar above the keyboard. It appears as a blue bubble at the end of the address.' }),
      h('li', { text: 'Tap the play button once and choose Allow when it asks to connect.' })));
    if (server.stable_radio_url) {
      shortcut.appendChild(h('p', { class: 'small' }, 'Better on a Mac: this form of the address keeps working if the computer\u2019s number changes: ',
        h('code', { text: server.stable_radio_url + '&rssi=' })));
    } else {
      shortcut.appendChild(h('p', { class: 'small muted', text: 'If the Shortcut stops working later, the computer\u2019s address has changed: come back here for the current one.' }));
    }
    shortcut.appendChild(h('p', { class: 'small muted', text: 'Optional, for channel and access-point detail: add the same pair of actions again with "Channel Number" and an address ending &ch= , then "BSSID" with &bssid= , "TX Rate" with &tx= and "Noise" with &noise= .' }));
    shortcut.appendChild(h('button', { class: 'btn', onclick: function () {
      var text = (server.radio_url || '') + '&rssi=';
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { toast('Address copied.'); }, function () { toast('Press and hold the address to copy it.'); });
      else toast('Press and hold the address to copy it.');
    } }, 'Copy the address'));
    shortcut.appendChild(h('p', { class: 'small muted', text: 'No Shortcut? Apple’s free AirPort Utility app shows the same number: enable Wi-Fi Scanner in Settings > Apps > AirPort Utility, scan, and type the dBm value into the reading.' }));
    view.appendChild(shortcut);

    var home = h('div', { class: 'panel stack' });
    home.appendChild(h('h3', { text: 'About this home' }));
    var kind = h('select', { class: 'input', id: 'p-home' }, ['', 'house', 'townhouse', 'apartment', 'condo'].map(function (v) {
      return h('option', { value: v, selected: (profile.home || '') === v, text: v ? v.charAt(0).toUpperCase() + v.slice(1) : 'Choose…' });
    }));
    var sqft = h('input', { class: 'input', id: 'p-sqft', inputmode: 'numeric', value: profile.sqft || '', placeholder: 'e.g. 1800' });
    var down = h('input', { class: 'input', id: 'p-down', inputmode: 'decimal', value: profile.plan_down || '', placeholder: 'e.g. 500' });
    var up = h('input', { class: 'input', id: 'p-up', inputmode: 'decimal', value: profile.plan_up || '', placeholder: 'e.g. 20' });
    home.appendChild(h('label', { class: 'field' }, h('span', { text: 'Type of home' }), kind));
    home.appendChild(h('label', { class: 'field' }, h('span', { text: 'Size in square feet' }), sqft));
    home.appendChild(h('div', { class: 'row' },
      h('label', { class: 'field grow' }, h('span', { text: 'Plan download (Mbps)' }), down),
      h('label', { class: 'field grow' }, h('span', { text: 'Plan upload (Mbps)' }), up)));
    home.appendChild(h('button', { class: 'btn primary', onclick: function () {
      api.call('/api/profile', { method: 'POST', body: { home: kind.value, sqft: sqft.value, plan_down: down.value, plan_up: up.value } })
        .then(function () { toast('Saved.'); return load(); });
    } }, 'Save'));
    view.appendChild(home);

    var floors = h('div', { class: 'panel stack' });
    floors.appendChild(h('h3', { text: 'Floors' }));
    floors.appendChild(h('p', { class: 'small muted', text: data.floors.join(' · ') }));
    var floorInput = h('input', { class: 'input', placeholder: 'e.g. Upstairs', 'aria-label': 'New floor name' });
    floors.appendChild(h('div', { class: 'row' }, h('div', { class: 'grow' }, floorInput), h('button', { class: 'btn', onclick: function () {
      if (!floorInput.value.trim()) return;
      api.call('/api/floors', { method: 'POST', body: { floors: data.floors.concat([floorInput.value.trim()]) } }).then(function () {
        S.floor = floorInput.value.trim(); return load();
      });
    } }, 'Add floor')));
    view.appendChild(floors);

    var plan = h('div', { class: 'panel stack' });
    plan.appendChild(h('h3', { text: 'Floor plan for ' + S.floor + ' (optional)' }));
    plan.appendChild(h('p', { class: 'small muted', text: 'A photo or sketch of the floor, shown under the map. Enter how wide the pictured area is so distances come out right.' }));
    var width = h('input', { class: 'input', inputmode: 'decimal', value: (data.plans[S.floor] || {}).width_m || 12, style: { maxWidth: '110px' }, 'aria-label': 'Width in metres' });
    var file = h('input', { type: 'file', accept: 'image/*', 'aria-label': 'Floor plan image' });
    plan.appendChild(file);
    plan.appendChild(h('div', { class: 'row' }, h('span', { class: 'small', text: 'Width of the picture in metres' }), width,
      h('button', { class: 'btn', onclick: function () { uploadPlan(file, parseFloat(width.value) || 12); } }, 'Use picture'),
      data.plans[S.floor] ? h('button', { class: 'btn danger', onclick: function () {
        api.call('/api/plan', { method: 'POST', body: { floor: S.floor, image: null } }).then(function () { delete S.plans[S.floor]; return load(); });
      } }, 'Remove') : null));
    view.appendChild(plan);

    view.appendChild(h('div', { class: 'panel stack' },
      h('h3', { text: 'How the readings work' }),
      h('p', { class: 'small muted', text: 'Each measurement sends data back and forth between this phone and the computer running WiFi Scout, entirely inside your home. It shows how good the Wi-Fi link is at that spot, separate from your internet speed. ' +
        (server.collector_via === 'wifi' ? 'That computer is on Wi-Fi too, so speeds read lower than they are: plug it into the router with a cable for true numbers.' : 'That computer is cabled to the network, so the speeds are true.') })));
  }
  function uploadPlan(input, width) {
    var chosen = input.files && input.files[0];
    if (!chosen) { toast('Choose a picture first.'); return; }
    var reader = new FileReader();
    reader.onload = function () {
      var img = new Image();
      img.onload = function () {
        var limit = 1400, scale = Math.min(1, limit / Math.max(img.naturalWidth, img.naturalHeight));
        var canvas = document.createElement('canvas');
        canvas.width = Math.round(img.naturalWidth * scale); canvas.height = Math.round(img.naturalHeight * scale);
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
        api.call('/api/plan', { method: 'POST', body: { floor: S.floor, image: canvas.toDataURL('image/jpeg', 0.72), width_m: width } })
          .then(function () { toast('Floor plan added.'); delete S.plans[S.floor]; S.tab = 'survey'; setTabs(); return load(); })
          .catch(function () { toast('That picture could not be saved. Try a smaller one.'); });
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(chosen);
  }

  function render() {
    if (!S.data) return;
    var keepScroll = main.scrollTop;
    var active = document.activeElement && document.activeElement.id;
    var view = h('div', { class: 'view' });
    WS.clear(main).appendChild(view);
    if (S.tab === 'survey') renderSurvey(view);
    else if (S.tab === 'results') renderResults(view);
    else renderSetup(view);
    main.scrollTop = keepScroll;
    if (active === 'spot-name') { var el = document.getElementById('spot-name'); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }
  }
  function setTabs() {
    Array.prototype.forEach.call(document.querySelectorAll('#tabs button'), function (b) {
      b.setAttribute('aria-selected', b.getAttribute('data-tab') === S.tab ? 'true' : 'false');
    });
  }
  document.getElementById('tabs').addEventListener('click', function (event) {
    var button = event.target.closest('button');
    if (!button) return;
    S.tab = button.getAttribute('data-tab'); setTabs(); main.scrollTop = 0; render();
    if (S.tab === 'results') {
      S.analysis = null; render();
      api.call('/api/analysis').then(function (a) { S.analysis = a; render(); }).catch(function () { toast('Could not load the results.'); });
    }
  });

  function poll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(function () {
      if (document.visibilityState === 'visible' && !S.busy && S.tab === 'survey' && document.activeElement.tagName !== 'INPUT') {
        load(true).then(poll, poll);
      } else poll();
    }, 5000);
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && !S.busy) load(true);
  });

  if (!key) {
    WS.clear(main).appendChild(h('div', { class: 'view' }, h('div', { class: 'panel stack' },
      h('h2', { text: 'Scan the QR code' }),
      h('p', { class: 'muted', text: 'Open this page by scanning the code shown on the computer running WiFi Scout.' }))));
  } else {
    load().then(poll);
  }
})();
