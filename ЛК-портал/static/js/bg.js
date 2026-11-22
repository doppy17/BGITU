/* БГИТУ — фон кабинета: звёздная пыль и линии-«нити», как на основном сайте.
   Цвета берутся из токенов темы (--brand-accent/--brand-aux/--brand-ink) и
   обновляются по событию bgitu:palette. Цикл на setTimeout (~30 кадров/с) —
   работает и там, где requestAnimationFrame заморожен (фоновые вкладки). */
(function () {
  'use strict';

  var cv = document.querySelector('.bg-canvas');
  if (!cv) return;
  var ctx = cv.getContext('2d');
  if (!ctx) return;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var W = 0, H = 0;
  var ACC = '255,90,46', AUX = '255,154,60', INK = '242,242,242';

  function hex(v) {
    v = v.trim();
    if (v[0] === '#') {
      v = v.slice(1);
      if (v.length === 3) v = v[0] + v[0] + v[1] + v[1] + v[2] + v[2];
      return parseInt(v.slice(0, 2), 16) + ',' + parseInt(v.slice(2, 4), 16) + ',' + parseInt(v.slice(4, 6), 16);
    }
    return ACC;
  }

  function palette() {
    var cs = getComputedStyle(document.documentElement);
    try {
      ACC = hex(cs.getPropertyValue('--brand-accent'));
      AUX = hex(cs.getPropertyValue('--brand-aux'));
      INK = hex(cs.getPropertyValue('--brand-ink'));
    } catch (e) {}
  }

  var rgba = function (v, a) { return 'rgba(' + v + ',' + a + ')'; };

  var PARTS = [];
  function resize() {
    var dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = window.innerWidth;
    H = window.innerHeight;
    cv.width = W * dpr;
    cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    PARTS = [];
  }

  function seedParts() {
    var n = Math.min(80, Math.max(24, Math.round(W * H / 24000)));
    PARTS = [];
    for (var i = 0; i < n; i++) {
      var z = 0.25 + Math.random() * 0.75;
      var c = Math.random();
      PARTS.push({
        x: Math.random() * W,
        y: Math.random() * H,
        z: z,
        r: 0.5 + z * 1.3,
        a: 0.08 + 0.3 * z,
        vx: (Math.random() - 0.5) * 0.06,
        vy: -(0.03 + Math.random() * 0.14) * z,
        ph: Math.random() * 6.28,
        col: c < 0.1 ? 'acc' : (c < 0.24 ? 'aux' : 'ink'),
        glow: c < 0.16,
      });
    }
  }

  var scrollY = 0;
  addEventListener('scroll', function () { scrollY = window.scrollY; }, { passive: true });

  function drawLines(t) {
    var ampH = H * 0.16;
    for (var l = 0; l < 2; l++) {
      var y0 = H * (0.28 + 0.35 * l) + Math.sin(t * 0.11 + l * 2.1) * H * 0.03;
      ctx.beginPath();
      for (var x = 0; x <= W; x += 24) {
        var y = y0 + Math.sin(x * 0.004 + l * 2.4 + t * 0.35) * ampH;
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      var grad = ctx.createLinearGradient(0, 0, W, 0);
      var c = l === 0 ? ACC : AUX;
      grad.addColorStop(0, rgba(c, 0));
      grad.addColorStop(0.22, rgba(c, 0.09));
      grad.addColorStop(0.5, rgba(c, 0.045));
      grad.addColorStop(0.78, rgba(c, 0.09));
      grad.addColorStop(1, rgba(c, 0));
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }
  }

  function frame(t) {
    ctx.clearRect(0, 0, W, H);
    drawLines(t);
    if (!PARTS.length) seedParts();
    var span = H + 40;
    for (var i = 0; i < PARTS.length; i++) {
      var p = PARTS[i];
      var tt = reduce ? 0 : t;
      var px = p.x + p.vx * 60 * tt + Math.sin(tt * 0.3 + p.ph) * 3.2;
      var py = p.y + p.vy * 60 * tt;
      var x = ((px % (W + 20)) + W + 20) % (W + 20) - 10;
      var y = (((py - scrollY * 0.25 * p.z) % span) + span) % span - 20;
      var a = p.a * (0.65 + 0.35 * Math.sin(t * 0.7 + p.ph));
      var col = p.col === 'acc' ? ACC : (p.col === 'aux' ? AUX : INK);
      if (p.glow) {
        ctx.fillStyle = rgba(col, a * 0.18);
        ctx.beginPath();
        ctx.arc(x, y, p.r * 4, 0, 7);
        ctx.fill();
      }
      ctx.fillStyle = rgba(col, a);
      ctx.beginPath();
      ctx.arc(x, y, p.r, 0, 7);
      ctx.fill();
    }
  }

  // Футер: подсветка надписи «БГИТУ» следует за курсором, размеры пятен дышат.
  var fw = document.querySelector('.footer__word');
  if (fw) {
    var spots = [
      { x: '--x0', y: '--y0' },
      { x: '--x1', y: '--y1' },
      { x: '--x2', y: '--y2' },
    ];
    addEventListener('pointermove', function (e) {
      var r = fw.getBoundingClientRect();
      if (e.clientY < r.top - 140 || e.clientY > r.bottom + 80) return;
      var px = ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%';
      var py = ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%';
      fw.style.setProperty(spots[0].x, px);
      fw.style.setProperty(spots[0].y, py);
      setTimeout(function () {
        fw.style.setProperty(spots[1].x, px);
        fw.style.setProperty(spots[1].y, py);
      }, 140);
      setTimeout(function () {
        fw.style.setProperty(spots[2].x, px);
        fw.style.setProperty(spots[2].y, py);
      }, 300);
    });
    var breathe = function () {
      var t = reduce ? 0 : performance.now() / 1000;
      fw.style.setProperty('--f0', (0.22 + 0.2 * (1 + Math.sin(t * 0.5))).toFixed(2));
      fw.style.setProperty('--f1', (0.28 + 0.26 * (1 + Math.sin(t * 0.5 + 2.1))).toFixed(2));
      fw.style.setProperty('--f2', (0.18 + 0.16 * (1 + Math.sin(t * 0.5 + 4.2))).toFixed(2));
    };
    breathe();
    if (!reduce) setInterval(breathe, 800);
  }

  var t0 = performance.now();
  var timer = 0;
  function loop() {
    frame((performance.now() - t0) / 1000);
    timer = setTimeout(loop, 33);
  }

  palette();
  resize();
  addEventListener('resize', resize);
  document.addEventListener('bgitu:palette', function () { palette(); });
  if (reduce) {
    frame(0);
  } else {
    loop();
  }
})();
