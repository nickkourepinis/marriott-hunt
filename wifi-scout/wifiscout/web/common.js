/* Shared by the phone survey page and the report. No dependencies. */
(function (root) {
  'use strict';

  // Build DOM safely: every string becomes a text node, never markup.
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value == null || value === false) return;
        if (key === 'class') el.className = value;
        else if (key === 'text') el.textContent = value;
        else if (key === 'style') Object.assign(el.style, value);
        else if (key.slice(0, 2) === 'on') el.addEventListener(key.slice(2), value);
        else if (value === true) el.setAttribute(key, '');
        else el.setAttribute(key, value);
      });
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, child) {
    if (child == null || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); return; }
    el.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

  var fmt = {
    num: function (v, digits) {
      if (v == null || isNaN(v)) return '–';
      return Number(v).toFixed(digits == null ? (Math.abs(v) < 10 && v % 1 ? 1 : 0) : digits);
    },
    dbm: function (v) { return v == null ? '–' : Math.round(v) + ' dBm'; },
    mbps: function (v) { return v == null ? '–' : (v >= 100 ? Math.round(v) : Number(v).toFixed(v < 10 ? 1 : 0)) + ' Mbps'; },
    ms: function (v) { return v == null ? '–' : (v >= 10 ? Math.round(v) : Number(v).toFixed(1)) + ' ms'; },
    pct: function (v) { return v == null ? '–' : Math.round(v) + '%'; },
    clock: function (t) {
      if (!t) return '–';
      var d = new Date(t * 1000);
      return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }
  };

  var GRADE = {
    strong: { status: 'good', glyph: '✓', label: 'Strong' },
    fair: { status: 'warning', glyph: '–', label: 'Fair' },
    weak: { status: 'serious', glyph: '!', label: 'Weak' },
    dead: { status: 'critical', glyph: '✕', label: 'Dead zone' }
  };
  var SEVERITY = {
    critical: { status: 'critical', glyph: '✕', label: 'Critical' },
    major: { status: 'serious', glyph: '!', label: 'Problem' },
    minor: { status: 'warning', glyph: '–', label: 'Could be better' },
    info: { status: 'info', glyph: 'i', label: 'Note' },
    good: { status: 'good', glyph: '✓', label: 'Good' }
  };
  function statusChip(spec, label) {
    return h('span', { class: 'status ' + spec.status }, h('i', { 'aria-hidden': 'true', text: spec.glyph }), label || spec.label);
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function isDark() {
    var c = cssVar('--plane').replace('#', '');
    if (c.length !== 6) return false;
    return parseInt(c.slice(0, 2), 16) + parseInt(c.slice(2, 4), 16) + parseInt(c.slice(4, 6), 16) < 384;
  }

  // One-hue sequential ramp (light to dark). More signal = more ink; on a dark page it flips.
  var RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf',
    '#1c5cab', '#184f95', '#104281', '#0d366b'].map(function (hex) {
    return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
  });
  function rampColor(t, dark) {
    t = Math.max(0, Math.min(1, t));
    if (dark) t = 1 - t;
    var pos = t * (RAMP.length - 1), i = Math.min(RAMP.length - 2, Math.floor(pos)), f = pos - i;
    var a = RAMP[i], b = RAMP[i + 1];
    return [Math.round(a[0] + (b[0] - a[0]) * f), Math.round(a[1] + (b[1] - a[1]) * f), Math.round(a[2] + (b[2] - a[2]) * f)];
  }
  function rampCss(dark) {
    var stops = [];
    for (var i = 0; i <= 6; i++) { var c = rampColor(i / 6, dark); stops.push('rgb(' + c.join(',') + ')'); }
    return 'linear-gradient(90deg,' + stops.join(',') + ')';
  }

  // Inverse-distance-weighted value at a point. Returns null when no reading is near enough to trust.
  function interpolate(points, x, y, reach) {
    var sumW = 0, sumV = 0, nearest = Infinity;
    for (var i = 0; i < points.length; i++) {
      var p = points[i], dx = p.x - x, dy = p.y - y, d2 = dx * dx + dy * dy;
      if (d2 < nearest) nearest = d2;
      var w = 1 / (d2 + 0.35);
      sumW += w * w; sumV += w * w * p.v;
    }
    if (!points.length) return null;
    var d = Math.sqrt(nearest);
    if (d > reach) return null;
    return { value: sumV / sumW, fade: d <= reach * 0.4 ? 1 : 1 - (d - reach * 0.4) / (reach * 0.6) };
  }

  // Step counter + compass smoothing for walk mode (pedestrian dead reckoning).
  function Walker(onStep) {
    this.gravity = 9.81; this.level = 0; this.above = false; this.lastStep = -1e9;
    this.steps = 0; this.heading = null; this.onStep = onStep || function () {};
    this.samples = 0;
  }
  Walker.prototype.motion = function (ax, ay, az, timeMs) {
    if (ax == null || ay == null || az == null) return;
    this.samples++;
    var mag = Math.sqrt(ax * ax + ay * ay + az * az);
    this.gravity += (mag - this.gravity) * 0.02;
    this.level += ((mag - this.gravity) - this.level) * 0.35;
    if (!this.above && this.level > 1.1 && timeMs - this.lastStep > 320) {
      this.above = true; this.lastStep = timeMs; this.steps++; this.onStep(this);
    } else if (this.above && this.level < 0.3) {
      this.above = false;
    }
  };
  Walker.prototype.compass = function (degrees) {
    if (degrees == null || isNaN(degrees)) return;
    if (this.heading == null) { this.heading = degrees; return; }
    var delta = ((degrees - this.heading + 540) % 360) - 180;
    this.heading = (this.heading + delta * 0.25 + 360) % 360;
  };
  // Move a map position one stride along a compass heading. offset = the compass bearing of map-up.
  function stepFrom(pos, headingDeg, offsetDeg, stride) {
    var theta = (headingDeg - offsetDeg) * Math.PI / 180;
    return { x: pos.x + stride * Math.sin(theta), y: pos.y - stride * Math.cos(theta) };
  }
  // Learn map rotation and stride scale from a correction: the walker thought it moved `walked`
  // (metres, compass frame: x east, y north) while the user says the true move on the map was `actual`.
  function calibrate(walked, actual, offsetDeg, scale) {
    var lw = Math.sqrt(walked.x * walked.x + walked.y * walked.y);
    var la = Math.sqrt(actual.x * actual.x + actual.y * actual.y);
    if (lw < 3 || la < 1.5) return null;
    var bearingWalked = Math.atan2(walked.x, walked.y) * 180 / Math.PI;          // compass bearing walked
    var bearingMap = Math.atan2(actual.x, -actual.y) * 180 / Math.PI;            // bearing on the map (up = 0)
    var wantOffset = bearingWalked - bearingMap;
    var delta = ((wantOffset - offsetDeg + 540) % 360) - 180;
    var ratio = la / lw;
    if (Math.abs(delta) > 35 || ratio < 0.6 || ratio > 1.6) return null;
    return { offset: (offsetDeg + delta * 0.6 + 360) % 360, scale: scale * 0.5 + ratio * 0.5 };
  }

  function median(values) {
    if (!values.length) return null;
    var s = values.slice().sort(function (a, b) { return a - b; }), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function jitter(values) {
    if (values.length < 2) return 0;
    var total = 0;
    for (var i = 1; i < values.length; i++) total += Math.abs(values[i] - values[i - 1]);
    return total / (values.length - 1);
  }

  // ---------------------------------------------------------------- map
  function CoverageMap(canvas, options) {
    this.canvas = canvas; this.ctx = canvas.getContext('2d');
    this.options = options || {};
    this.spots = []; this.metric = 'overall'; this.you = null; this.heading = null; this.selected = null;
    this.plan = null; this.planImage = null; this.view = null; this.showHeat = true;
    var self = this;
    this._redraw = function () { self.draw(); };
    if (window.ResizeObserver) new ResizeObserver(this._redraw).observe(canvas);
    else window.addEventListener('resize', this._redraw);
    if (window.matchMedia) {
      var mq = window.matchMedia('(prefers-color-scheme: dark)');
      if (mq.addEventListener) mq.addEventListener('change', this._redraw);
    }
    if (window.MutationObserver) {   // a host page may switch theme by attribute
      new MutationObserver(this._redraw).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    }
  }
  CoverageMap.prototype.setData = function (data) {
    var self = this;
    ['spots', 'metric', 'you', 'heading', 'selected', 'showHeat'].forEach(function (key) {
      if (key in data) self[key] = data[key];
    });
    if ('plan' in data) {
      var plan = data.plan;
      if (!plan) { this.plan = null; this.planImage = null; }
      else if (!this.plan || this.plan.image !== plan.image) {
        this.plan = plan; this.planImage = null;
        var img = new Image();
        img.onload = function () { self.planImage = img; self.draw(); };
        img.src = plan.image;
      } else { this.plan = plan; }
    }
    this.draw();
  };
  CoverageMap.prototype.placed = function () {
    return this.spots.filter(function (s) { return typeof s.x === 'number' && typeof s.y === 'number'; });
  };
  CoverageMap.prototype.planRect = function () {
    if (!this.plan || !this.planImage) return null;
    var w = this.plan.width_m || 12;
    return { x: 0, y: 0, w: w, h: w * this.planImage.naturalHeight / this.planImage.naturalWidth };
  };
  CoverageMap.prototype.computeView = function (width, height) {
    var xs = [], ys = [];
    this.placed().forEach(function (s) { xs.push(s.x); ys.push(s.y); });
    if (this.you) { xs.push(this.you.x); ys.push(this.you.y); }
    var rect = this.planRect();
    if (rect) { xs.push(rect.x, rect.x + rect.w); ys.push(rect.y, rect.y + rect.h); }
    if (!xs.length) { xs = [-5, 5]; ys = [-5, 5]; }
    var pad = rect ? 0.8 : 2.5;
    var x0 = Math.min.apply(null, xs) - pad, x1 = Math.max.apply(null, xs) + pad;
    var y0 = Math.min.apply(null, ys) - pad, y1 = Math.max.apply(null, ys) + pad;
    var minSpan = 10;
    if (x1 - x0 < minSpan) { var cx = (x0 + x1) / 2; x0 = cx - minSpan / 2; x1 = cx + minSpan / 2; }
    if (y1 - y0 < minSpan) { var cy = (y0 + y1) / 2; y0 = cy - minSpan / 2; y1 = cy + minSpan / 2; }
    var scale = Math.min(width / (x1 - x0), height / (y1 - y0));
    return { scale: scale, ox: (width - (x1 - x0) * scale) / 2 - x0 * scale, oy: (height - (y1 - y0) * scale) / 2 - y0 * scale,
      width: width, height: height };
  };
  CoverageMap.prototype.toScreen = function (x, y) {
    return { x: x * this.view.scale + this.view.ox, y: y * this.view.scale + this.view.oy };
  };
  CoverageMap.prototype.toWorld = function (px, py) {
    return { x: (px - this.view.ox) / this.view.scale, y: (py - this.view.oy) / this.view.scale };
  };
  CoverageMap.prototype.spotAt = function (px, py, radius) {
    var best = null, bestD = (radius || 24) * (radius || 24), self = this;
    this.placed().forEach(function (s) {
      var p = self.toScreen(s.x, s.y), d = (p.x - px) * (p.x - px) + (p.y - py) * (p.y - py);
      if (d <= bestD) { bestD = d; best = s; }
    });
    return best;
  };
  CoverageMap.prototype.value = function (spot) {
    var scores = spot.scores || {};
    var v = scores[this.metric];
    return v == null ? null : v;
  };
  CoverageMap.prototype.draw = function () {
    var canvas = this.canvas, ctx = this.ctx;
    var width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height) return;
    var dpr = Math.min(window.devicePixelRatio || 1, 3);
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var dark = isDark();
    var surface = cssVar('--raised') || '#fff', hairline = cssVar('--hairline') || '#ddd', axis = cssVar('--axis') || '#bbb';
    var ink = cssVar('--ink') || '#000', ink2 = cssVar('--ink-2') || '#444', accent = cssVar('--accent') || '#2a78d6';
    ctx.fillStyle = surface; ctx.fillRect(0, 0, width, height);
    var view = this.view = this.computeView(width, height);
    var self = this;

    var rect = this.planRect();
    if (rect) {
      var a = this.toScreen(rect.x, rect.y);
      ctx.globalAlpha = dark ? 0.55 : 0.8;
      ctx.drawImage(this.planImage, a.x, a.y, rect.w * view.scale, rect.h * view.scale);
      ctx.globalAlpha = 1;
    }

    // heat field
    var points = [];
    this.placed().forEach(function (s) { var v = self.value(s); if (v != null) points.push({ x: s.x, y: s.y, v: v }); });
    if (this.showHeat && points.length) {
      var cell = 6, cols = Math.ceil(width / cell), rows = Math.ceil(height / cell);
      var off = this._heat || (this._heat = document.createElement('canvas'));
      off.width = cols; off.height = rows;
      var octx = off.getContext('2d'), image = octx.createImageData(cols, rows), data = image.data;
      for (var r = 0; r < rows; r++) {
        for (var c = 0; c < cols; c++) {
          var world = this.toWorld((c + 0.5) * cell, (r + 0.5) * cell);
          var sample = interpolate(points, world.x, world.y, 7);
          var index = (r * cols + c) * 4;
          if (!sample) { data[index + 3] = 0; continue; }
          var color = rampColor(sample.value / 100, dark);
          data[index] = color[0]; data[index + 1] = color[1]; data[index + 2] = color[2];
          data[index + 3] = Math.round(255 * 0.82 * sample.fade);
        }
      }
      octx.putImageData(image, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(off, 0, 0, cols, rows, 0, 0, cols * cell, rows * cell);
    }

    // survey grid: 1 m and 5 m lines
    var step = view.scale >= 14 ? 1 : 5;
    var w0 = this.toWorld(0, 0), w1 = this.toWorld(width, height);
    ctx.lineWidth = 1;
    for (var gx = Math.floor(w0.x / step) * step; gx <= w1.x; gx += step) {
      var sx = Math.round(gx * view.scale + view.ox) + 0.5;
      ctx.strokeStyle = (Math.round(gx) % 5 === 0) ? axis : hairline;
      ctx.globalAlpha = (Math.round(gx) % 5 === 0) ? 0.55 : 0.45;
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, height); ctx.stroke();
    }
    for (var gy = Math.floor(w0.y / step) * step; gy <= w1.y; gy += step) {
      var sy = Math.round(gy * view.scale + view.oy) + 0.5;
      ctx.strokeStyle = (Math.round(gy) % 5 === 0) ? axis : hairline;
      ctx.globalAlpha = (Math.round(gy) % 5 === 0) ? 0.55 : 0.45;
      ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(width, sy); ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // scale bar
    var barMetres = view.scale * 5 > width * 0.5 ? 1 : 5;
    ctx.strokeStyle = ink2; ctx.fillStyle = ink2; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(12, height - 14); ctx.lineTo(12 + barMetres * view.scale, height - 14); ctx.stroke();
    ctx.font = '11px ' + (cssVar('--font') || 'sans-serif'); ctx.textBaseline = 'bottom'; ctx.textAlign = 'left';
    ctx.fillText(barMetres + ' m', 12, height - 18);

    // spots
    var statusColor = { good: cssVar('--good'), warning: cssVar('--warning'), serious: cssVar('--serious'), critical: cssVar('--critical') };
    var boxes = [];
    var ordered = this.placed().slice().sort(function (a, b) {
      return ((b.scores || {}).overall || 0) - ((a.scores || {}).overall || 0);
    });
    ordered.forEach(function (s) {
      var p = self.toScreen(s.x, s.y), grade = GRADE[(s.scores || {}).grade];
      var radius = 9, selected = self.selected === s.id;
      ctx.beginPath(); ctx.arc(p.x, p.y, radius + 2, 0, Math.PI * 2); ctx.fillStyle = surface; ctx.fill();
      ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = grade ? statusColor[grade.status] : cssVar('--quiet'); ctx.fill();
      if (selected) { ctx.lineWidth = 2; ctx.strokeStyle = ink; ctx.beginPath(); ctx.arc(p.x, p.y, radius + 4, 0, Math.PI * 2); ctx.stroke(); }
      if (grade) {
        ctx.fillStyle = grade.status === 'warning' ? '#0b0b0b' : '#ffffff';
        ctx.font = '700 11px ' + (cssVar('--font') || 'sans-serif'); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(grade.glyph, p.x, p.y + 0.5);
      }
    });
    // labels after all markers, weakest first so problem spots keep their names
    ctx.font = '600 11.5px ' + (cssVar('--font') || 'sans-serif'); ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ordered.slice().reverse().forEach(function (s) {
      var name = s.name || ''; if (!name) return;
      if (name.length > 18) name = name.slice(0, 17) + '…';
      var p = self.toScreen(s.x, s.y), tw = ctx.measureText(name).width;
      var box = { x: p.x - tw / 2 - 3, y: p.y + 12, w: tw + 6, h: 15 };
      if (box.x < 2) box.x = 2;
      if (box.x + box.w > width - 2) box.x = width - 2 - box.w;
      var clash = boxes.some(function (b) { return box.x < b.x + b.w && box.x + box.w > b.x && box.y < b.y + b.h && box.y + box.h > b.y; });
      if (clash && self.selected !== s.id) return;
      boxes.push(box);
      ctx.lineWidth = 3; ctx.strokeStyle = surface; ctx.lineJoin = 'round';
      ctx.strokeText(name, box.x + box.w / 2, box.y + 1);
      ctx.fillStyle = ink; ctx.fillText(name, box.x + box.w / 2, box.y + 1);
    });

    // "you are here"
    if (this.you) {
      var y = this.toScreen(this.you.x, this.you.y);
      if (this.heading != null) {
        var theta = this.heading * Math.PI / 180;
        ctx.beginPath(); ctx.moveTo(y.x, y.y);
        ctx.arc(y.x, y.y, 26, theta - Math.PI / 2 - 0.45, theta - Math.PI / 2 + 0.45); ctx.closePath();
        ctx.fillStyle = accent; ctx.globalAlpha = 0.28; ctx.fill(); ctx.globalAlpha = 1;
      }
      ctx.beginPath(); ctx.arc(y.x, y.y, 9, 0, Math.PI * 2); ctx.fillStyle = surface; ctx.fill();
      ctx.beginPath(); ctx.arc(y.x, y.y, 6, 0, Math.PI * 2); ctx.fillStyle = accent; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = accent; ctx.beginPath(); ctx.arc(y.x, y.y, 11, 0, Math.PI * 2); ctx.stroke();
    }
  };

  var api = {
    key: '',
    call: function (path, options) {
      options = options || {};
      var headers = { 'X-WS-Key': api.key };
      var init = { method: options.method || 'GET', headers: headers, cache: 'no-store', signal: options.signal };
      if (options.body !== undefined) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(options.body); }
      return fetch(path, init).then(function (response) {
        if (!response.ok) {
          var error = new Error('HTTP ' + response.status); error.status = response.status; throw error;
        }
        return response.json();
      });
    }
  };

  var WS = { h: h, clear: clear, fmt: fmt, GRADE: GRADE, SEVERITY: SEVERITY, statusChip: statusChip, cssVar: cssVar,
    isDark: isDark, rampColor: rampColor, rampCss: rampCss, interpolate: interpolate, Walker: Walker,
    stepFrom: stepFrom, calibrate: calibrate, median: median, jitter: jitter, CoverageMap: CoverageMap, api: api };
  if (typeof module !== 'undefined' && module.exports) module.exports = WS;
  else root.WS = WS;
})(typeof window !== 'undefined' ? window : this);
