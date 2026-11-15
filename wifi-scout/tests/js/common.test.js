// Run with:  node --test tests/js/common.test.js
// Covers the sensor maths the phone page uses for walk mode, and the map shading.
const test = require('node:test');
const assert = require('node:assert');
const WS = require('../../wifiscout/web/common.js');

function walk(walker, seconds, stepsPerSecond, amplitude) {
  const hz = 60;
  for (let i = 0; i < seconds * hz; i++) {
    const t = i / hz;
    walker.motion(0.3, 0.2, 9.81 + amplitude * Math.sin(2 * Math.PI * stepsPerSecond * t), t * 1000);
  }
}

test('step counter counts a steady walk', () => {
  const walker = new WS.Walker();
  walk(walker, 10, 1.8, 3.0);
  assert.ok(Math.abs(walker.steps - 18) <= 1, 'counted ' + walker.steps);
});

test('step counter ignores a phone lying still or being nudged', () => {
  const still = new WS.Walker();
  walk(still, 10, 1.8, 0.15);
  assert.strictEqual(still.steps, 0);
  const tremor = new WS.Walker();
  walk(tremor, 5, 12, 3.0);   // faster than anyone walks: capped by the minimum gap between steps
  assert.ok(tremor.steps <= 16, 'counted ' + tremor.steps);
});

test('step counter works whatever way up the phone is held', () => {
  const walker = new WS.Walker();
  for (let i = 0; i < 600; i++) {
    const t = i / 60, g = 9.81 + 3 * Math.sin(2 * Math.PI * 2 * t);
    walker.motion(g * 0.6, g * 0.8, 0, t * 1000);
  }
  assert.ok(Math.abs(walker.steps - 20) <= 1, 'counted ' + walker.steps);
});

test('compass smoothing follows turns and crosses north cleanly', () => {
  const walker = new WS.Walker();
  walker.compass(350);
  for (let i = 0; i < 40; i++) walker.compass(10);
  assert.ok(Math.abs(walker.heading - 10) < 1, 'heading ' + walker.heading);
  for (let i = 0; i < 40; i++) walker.compass(355);
  assert.ok(Math.abs(walker.heading - 355) < 1, 'heading ' + walker.heading);
  walker.compass(NaN);
  assert.ok(!isNaN(walker.heading));
});

test('a step moves the dot along the heading, relative to map-up', () => {
  const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, a + ' vs ' + b);
  let p = WS.stepFrom({ x: 0, y: 0 }, 0, 0, 0.7);        // facing map-up
  close(p.x, 0); close(p.y, -0.7);
  p = WS.stepFrom({ x: 0, y: 0 }, 90, 0, 0.7);           // facing right
  close(p.x, 0.7); close(p.y, 0);
  p = WS.stepFrom({ x: 2, y: 2 }, 250, 250, 1);          // compass says 250, and 250 is map-up
  close(p.x, 2); close(p.y, 1);
});

test('a tap correction teaches rotation and stride', () => {
  // Walked 10 m due north by the compass; on the map that was really 9 m at 20 degrees right of up.
  const actual = { x: 9 * Math.sin(20 * Math.PI / 180), y: -9 * Math.cos(20 * Math.PI / 180) };
  const learned = WS.calibrate({ x: 0, y: 10 }, actual, 0, 1);
  assert.ok(learned);
  assert.ok(Math.abs(learned.offset - 348) < 0.01, 'offset ' + learned.offset);   // 60% of the -20 degree error
  assert.ok(Math.abs(learned.scale - 0.95) < 1e-9);
  // Short walks and wild jumps are not corrections.
  assert.strictEqual(WS.calibrate({ x: 0, y: 1 }, actual, 0, 1), null);
  assert.strictEqual(WS.calibrate({ x: 0, y: 10 }, { x: 9, y: 0 }, 0, 1), null);
  assert.strictEqual(WS.calibrate({ x: 0, y: 10 }, { x: 0, y: -30 }, 0, 1), null);
});

test('repeated corrections converge on the true map rotation', () => {
  let offset = 0, scale = 1;
  const truth = 25;   // compass bearing that is really map-up
  for (let i = 0; i < 6; i++) {
    const rad = -truth * Math.PI / 180;
    const learned = WS.calibrate({ x: 0, y: 8 }, { x: 8 * Math.sin(rad), y: -8 * Math.cos(rad) }, offset, scale);
    offset = learned.offset; scale = learned.scale;
  }
  assert.ok(Math.abs(offset - truth) < 0.5, 'offset ' + offset);
  assert.ok(Math.abs(scale - 1) < 1e-9);
});

test('shading interpolates between readings and stops far from any', () => {
  const points = [{ x: 0, y: 0, v: 90 }, { x: 10, y: 0, v: 20 }];
  const near = WS.interpolate(points, 0.2, 0, 7);
  const middle = WS.interpolate(points, 5, 0, 7);
  assert.ok(near.value > 85);
  assert.ok(middle.value > 40 && middle.value < 70, 'middle ' + middle.value);
  assert.strictEqual(near.fade, 1);
  assert.ok(middle.fade < 1 && middle.fade > 0);
  assert.strictEqual(WS.interpolate(points, 5, 30, 7), null);
  assert.strictEqual(WS.interpolate([], 0, 0, 7), null);
});

test('the shading ramp is one hue that gets steadily darker', () => {
  const luminance = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  let last = Infinity;
  for (let i = 0; i <= 20; i++) {
    const c = WS.rampColor(i / 20, false);
    assert.ok(c[2] >= c[0] && c[2] >= c[1], 'blue stays the dominant channel');
    const lum = luminance(c);
    assert.ok(lum < last, 'step ' + i + ' is darker than the one before');
    last = lum;
  }
  assert.deepStrictEqual(WS.rampColor(0, true), WS.rampColor(1, false));   // flips on a dark page
  assert.deepStrictEqual(WS.rampColor(-3, false), WS.rampColor(0, false));
});

test('median and jitter', () => {
  assert.strictEqual(WS.median([5, 1, 9]), 5);
  assert.strictEqual(WS.median([4, 2]), 3);
  assert.strictEqual(WS.median([]), null);
  assert.strictEqual(WS.jitter([10, 12, 9]), 2.5);
  assert.strictEqual(WS.jitter([10]), 0);
});

test('number formatting', () => {
  assert.strictEqual(WS.fmt.mbps(312.4), '312 Mbps');
  assert.strictEqual(WS.fmt.mbps(6.14), '6.1 Mbps');
  assert.strictEqual(WS.fmt.ms(4.26), '4.3 ms');
  assert.strictEqual(WS.fmt.dbm(-61.4), '-61 dBm');
  assert.strictEqual(WS.fmt.dbm(null), '–');
});
