/* ============================================================
   БГИТУ — hero3d.js  (Three.js r128, подключается в index.html)

   Hero:   пыль, плывущая по экрану
   Сцены:  ИЭИ (компьютер) → ЛИ (лес) → СИ (высотки)
           Каждая сцена «прикреплена» (sticky) на ~3 экрана высоты.
           Прогресс скролла p∈[0..1] управляет камерой:
             p = 0  — фигура по центру, спокойное движение
             p → 1  — камера влетает в объект (экран / лес / окно),
                      затем затемнение до цвета фона и следующая сцена.
   ============================================================ */
(function () {
    'use strict';
    if (typeof THREE === 'undefined') return;

    /* ── окружение ───────────────────────────────────────── */
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const mobile = window.matchMedia('(max-width: 768px)').matches
                || window.matchMedia('(pointer: coarse)').matches;

    const clamp  = (x, a, b) => Math.min(b, Math.max(a, x));
    const lerp   = (a, b, t) => a + (b - a) * t;
    const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
    const ease   = (p) => p * p * (3 - 2 * p);
    const isDark = () => document.documentElement.getAttribute('data-theme') !== 'light';

    /* ── палитра и общие материалы ───────────────────────── */
    const PAL = {
        dark:  { wire: 0x5b7bff, dim: 0x27306a, dust: 0x9fb2ff },
        light: { wire: 0x2b52ff, dim: 0x9aa8e8, dust: 0x3550c8 },
    };
    const MAT_WIRE = new THREE.LineBasicMaterial({ color: PAL.dark.wire, transparent: true, opacity: 0.95 });
    const MAT_DIM  = new THREE.LineBasicMaterial({ color: PAL.dark.dim,  transparent: true, opacity: 0.85 });
    const dustMats = [];

    function applyTheme() {
        const dark = isDark();
        const p = dark ? PAL.dark : PAL.light;
        MAT_WIRE.color.setHex(p.wire);
        MAT_DIM.color.setHex(p.dim);
        dustMats.forEach(m => {
            m.color.setHex(p.dust);
            m.blending = dark ? THREE.AdditiveBlending : THREE.NormalBlending;
            m.needsUpdate = true;
        });
    }
    new MutationObserver(applyTheme)
        .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    /* ── wireframe-хелперы (рёбра без диагоналей) ────────── */
    const edges = (geo, dim) =>
        new THREE.LineSegments(new THREE.EdgesGeometry(geo, 15), dim ? MAT_DIM : MAT_WIRE);
    const box    = (w, h, d, dim)          => edges(new THREE.BoxGeometry(w, h, d), dim);
    const cyl    = (rt, rb, h, dim, seg=8) => edges(new THREE.CylinderGeometry(rt, rb, h, seg), dim);
    const sphere = (r, dim)                => edges(new THREE.SphereGeometry(r, 10, 8), dim);

    /* Батч: склеивает много рёбер в один LineSegments (быстро) */
    function makeBatch() {
        const pos = [];
        const v = new THREE.Vector3();
        return {
            addGeo(geo, matrix) {
                const e = new THREE.EdgesGeometry(geo, 15);
                const a = e.attributes.position;
                for (let i = 0; i < a.count; i++) {
                    v.fromBufferAttribute(a, i).applyMatrix4(matrix);
                    pos.push(v.x, v.y, v.z);
                }
                e.dispose(); geo.dispose();
            },
            addRect(cx, cy, cz, w, h, plane) {           // рамка окна в плоскости XY или ZY
                const hw = w / 2, hh = h / 2;
                const pts = plane === 'xy'
                    ? [[cx-hw,cy-hh,cz],[cx+hw,cy-hh,cz],[cx+hw,cy+hh,cz],[cx-hw,cy+hh,cz]]
                    : [[cx,cy-hh,cz-hw],[cx,cy-hh,cz+hw],[cx,cy+hh,cz+hw],[cx,cy+hh,cz-hw]];
                for (let i = 0; i < 4; i++) {
                    const a = pts[i], b = pts[(i + 1) % 4];
                    pos.push(a[0],a[1],a[2], b[0],b[1],b[2]);
                }
            },
            build(mat) {
                const g = new THREE.BufferGeometry();
                g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
                return new THREE.LineSegments(g, mat);
            },
        };
    }

    function gridFloor(size, div, y) {
        const pos = [], h = size / 2, step = size / div;
        for (let i = 0; i <= div; i++) {
            const k = -h + i * step;
            pos.push(-h, y, k,  h, y, k,   k, y, -h,  k, y, h);
        }
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
        return new THREE.LineSegments(g, MAT_DIM);
    }

    /* Человечек: ноги, туловище, руки, голова + «реквизит» */
    function makeHuman(scale, prop) {
        const g = new THREE.Group();
        const add = (m, x, y, z, rz) => { m.position.set(x, y, z); if (rz) m.rotation.z = rz; g.add(m); return m; };
        add(cyl(0.07, 0.07, 0.55, true), -0.12, 0.275, 0);
        add(cyl(0.07, 0.07, 0.55, true),  0.12, 0.275, 0);
        add(cyl(0.18, 0.16, 0.62, false), 0, 0.86, 0);
        add(cyl(0.05, 0.05, 0.5, true), -0.28, 0.82, 0,  0.35);
        add(cyl(0.05, 0.05, 0.5, true),  0.28, 0.82, 0, -0.35);
        add(sphere(0.16, false), 0, 1.36, 0);

        if (prop === 'helmet') {
            const h = edges(new THREE.SphereGeometry(0.2, 10, 6, 0, Math.PI * 2, 0, Math.PI / 2), false);
            add(h, 0, 1.4, 0);
        } else if (prop === 'board') {                    // планшет с чертежом
            const b = box(0.42, 0.56, 0.02, false);
            b.rotation.y = -0.4; add(b, 0.38, 0.85, 0.12);
        } else if (prop === 'roll') {                     // рулон чертежей
            const r = cyl(0.05, 0.05, 0.6, false, 6);
            r.rotation.z = Math.PI / 2.6; add(r, 0.4, 0.75, 0.08);
        } else if (prop === 'staff') {                    // прибор / штатив эколога
            add(cyl(0.02, 0.02, 1.5, false, 5), 0.42, 0.75, 0.05);
            add(box(0.16, 0.1, 0.1, false), 0.42, 1.55, 0.05);
        }
        g.scale.setScalar(scale);
        return g;
    }

    /* ── пыль ────────────────────────────────────────────── */
    const dustTex = (function () {
        const c = document.createElement('canvas');
        c.width = c.height = 64;
        const x = c.getContext('2d');
        const g = x.createRadialGradient(32, 32, 0, 32, 32, 30);
        g.addColorStop(0,   'rgba(255,255,255,1)');
        g.addColorStop(0.35,'rgba(255,255,255,0.55)');
        g.addColorStop(1,   'rgba(255,255,255,0)');
        x.fillStyle = g; x.fillRect(0, 0, 64, 64);
        return new THREE.CanvasTexture(c);
    })();

    function makeDust(scene, count, spread, size, opacity) {
        const pos = new Float32Array(count * 3);
        const vel = new Float32Array(count * 2);
        const ph  = new Float32Array(count);
        for (let i = 0; i < count; i++) {
            pos[i*3]   = (Math.random() - 0.5) * spread.x * 2;
            pos[i*3+1] = (Math.random() - 0.5) * spread.y * 2;
            pos[i*3+2] = (Math.random() - 0.5) * spread.z * 2;
            vel[i*2]   = (Math.random() - 0.5) * 0.004;
            vel[i*2+1] = (Math.random() - 0.5) * 0.003 + 0.0006;   // лёгкий подъём
            ph[i]      = Math.random() * Math.PI * 2;
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        const mat = new THREE.PointsMaterial({
            size, sizeAttenuation: true, map: dustTex, transparent: true,
            depthWrite: false, opacity,
        });
        dustMats.push(mat);
        const points = new THREE.Points(geo, mat);
        points.frustumCulled = false;
        scene.add(points);

        return {
            points,
            update(t, dt, push) {
                if (dt === 0) return;
                for (let i = 0; i < count; i++) {
                    const p = ph[i];
                    let x = pos[i*3], y = pos[i*3+1];
                    x += (vel[i*2]   + Math.sin(t * 0.6 + p) * 0.0016) * dt;   // броуновский дрейф
                    y += (vel[i*2+1] + Math.cos(t * 0.45 + p * 1.3) * 0.0012) * dt;
                    if (push) {                                               // мягко отталкиваем от курсора
                        const dx = x - push.x, dy = y - push.y, d2 = dx*dx + dy*dy;
                        if (d2 < 2.5) { x += dx / (d2 + 0.2) * 0.008 * dt; y += dy / (d2 + 0.2) * 0.008 * dt; }
                    }
                    if (x >  spread.x) x = -spread.x; else if (x < -spread.x) x =  spread.x;
                    if (y >  spread.y) y = -spread.y; else if (y < -spread.y) y =  spread.y;
                    pos[i*3] = x; pos[i*3+1] = y;
                }
                geo.attributes.position.needsUpdate = true;
            },
        };
    }

    /* ── stage: renderer + scene + camera + пыль ─────────── */
    function createStage(canvas, opts) {
        let renderer;
        try {
            renderer = new THREE.WebGLRenderer({ canvas, antialias: !mobile, alpha: true });
        } catch (e) {
            canvas.style.display = 'none';
            return null;
        }
        renderer.setClearColor(0x000000, 0);
        const scene  = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(opts.fov, 1, 0.1, 200);
        camera.position.set(0, 0, opts.z);

        const resize = () => {
            const w = canvas.clientWidth, h = canvas.clientHeight;
            if (!w || !h) return;
            renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, mobile ? 1.5 : 2));
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        };
        new ResizeObserver(resize).observe(canvas);
        resize();

        const k = mobile ? 0.5 : 1;
        const dustA = makeDust(scene, Math.round(opts.dust * k),        opts.spread, opts.dustSize,     0.75);
        const dustB = makeDust(scene, Math.round(opts.dust * 0.22 * k), opts.spread, opts.dustSize * 3, 0.22);

        return {
            renderer, scene, camera,
            updateDust(t, dt, push) { dustA.update(t, dt, push); dustB.update(t * 0.8, dt, null); },
            dustGroup: [dustA.points, dustB.points],
        };
    }

    /* ── ввод ────────────────────────────────────────────── */
    const mouse = { x: 0, y: 0 };
    if (!mobile) {
        window.addEventListener('mousemove', (e) => {
            mouse.x =  (e.clientX / window.innerWidth  - 0.5) * 2;
            mouse.y = -(e.clientY / window.innerHeight - 0.5) * 2;
        }, { passive: true });
    }

    /* ══════════════════════════════════════════════════════
       HERO — только пыль
    ══════════════════════════════════════════════════════ */
    const heroSection = document.querySelector('.hero-canvas-section');
    const heroCanvas  = document.getElementById('heroCanvas');
    let hero = null;
    if (heroCanvas) {
        const stage = createStage(heroCanvas, {
            fov: 60, z: 5, dust: 1100, dustSize: 0.035,
            spread: { x: 9, y: 5.5, z: 4 },
        });
        if (stage) {
            hero = { stage, active: true };
            new IntersectionObserver(([e]) => { hero.active = e.isIntersecting; }).observe(heroSection);
        }
    }
    const heroContent = document.querySelector('.hero-content');

    /* ══════════════════════════════════════════════════════
       ОБЩАЯ ЗАГОТОВКА ПИННЕД-СЦЕНЫ
    ══════════════════════════════════════════════════════ */
    const scenes = [];

    function registerScene(id, opts, build) {
        const track  = document.getElementById(id);
        if (!track) return;
        const canvas = track.querySelector('.scene-canvas');
        const fade   = track.querySelector('.scene-fade');
        const hud    = track.querySelector('.scene-hud');
        const stage  = createStage(canvas, opts);
        if (!stage) return;

        const s = {
            track, fade, hud, stage,
            p: 0, mx: 0, my: 0, active: false,
            update: build(stage),            // build возвращает update(p, e, t, s)
        };
        new IntersectionObserver(([en]) => { s.active = en.isIntersecting; },
                                 { rootMargin: '10% 0px 10% 0px' }).observe(track);
        scenes.push(s);
    }

    function trackProgress(track) {
        const r = track.getBoundingClientRect();
        const total = r.height - window.innerHeight;
        const p = total > 0 ? clamp(-r.top / total, 0, 1) : 0;         // внутри пина
        const e = clamp(1 - r.top / window.innerHeight, 0, 1);         // подъезд снизу
        return { p, e };
    }

    /* ══════════════════════════════════════════════════════
       СЦЕНА 1 — ИЭИ: компьютер, на экране ползёт код
    ══════════════════════════════════════════════════════ */
    registerScene('scene-iei', {
        fov: 50, z: 7, dust: 700, dustSize: 0.04,
        spread: { x: 12, y: 7, z: 5 },
    }, (stage) => {
        const { scene, camera } = stage;
        const computer = new THREE.Group();
        scene.add(computer);

        computer.add(gridFloor(40, 40, -1.34));
        const mon = box(3.2, 2.1, 0.12, false);             mon.position.set(0, 0.3, 0);
        const stand = box(0.16, 0.7, 0.16, false);          stand.position.set(0, -0.95, 0);
        const base = box(1.1, 0.08, 0.7, false);            base.position.set(0, -1.3, 0.05);
        const kb = box(2.6, 0.08, 0.9, true);               kb.position.set(0, -1.3, 1.25);
        const sys = box(0.75, 1.3, 0.95, false);            sys.position.set(2.5, -0.69, 0.3);
        computer.add(mon, stand, base, kb, sys);

        /* экран — CanvasTexture с ползущим кодом */
        const W = 1024, H = 640, LH = 30;
        const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
        const cx = cv.getContext('2d');
        const tex = new THREE.CanvasTexture(cv);
        tex.anisotropy = 4;
        const screen = new THREE.Mesh(
            new THREE.PlaneGeometry(2.9, 1.8),
            new THREE.MeshBasicMaterial({ map: tex })
        );
        screen.position.set(0, 0.3, 0.065);
        computer.add(screen);

        const CODE = [
            'class University {',
            '  constructor(name) {',
            '    this.name = name;',
            '    this.institutes = [];',
            '  }',
            '  addInstitute(inst) {',
            '    this.institutes.push(inst);',
            '    return this;',
            '  }',
            '  get students() {',
            '    return this.institutes',
            '      .reduce((s, i) => s + i.count, 0);',
            '  }',
            '}',
            '',
            'const bgitu = new University("БГИТУ");',
            'bgitu.addInstitute({ name: "ИЭИ", count: 3200 })',
            '     .addInstitute({ name: "ЛИ",  count: 2600 })',
            '     .addInstitute({ name: "СИ",  count: 3200 });',
            '',
            '// компиляция...',
            'await build({ target: "future" });',
            'console.log(bgitu.students); // 9000',
            '',
        ];
        let offset = 0;

        function drawScreen() {
            const dark = isDark();
            const bg  = dark ? '#070912' : '#eef0f7';
            const col = {
                kw:  dark ? '#7c9eff' : '#2040bb',
                str: dark ? '#7fffb0' : '#1a7a40',
                cmt: dark ? '#525a70' : '#8090a0',
                txt: dark ? '#e6e8f2' : '#101020',
                num: dark ? '#3a4058' : '#a0a8bb',
            };
            cx.fillStyle = bg; cx.fillRect(0, 0, W, H);

            /* строка-заголовок: название института на экране */
            cx.fillStyle = dark ? '#10142a' : '#dde2f5';
            cx.fillRect(0, 0, W, 54);
            cx.font = '700 24px "Courier New", monospace';
            cx.fillStyle = col.kw;
            cx.fillText('ИЭИ', 24, 36);
            cx.fillStyle = col.txt;
            cx.fillText('// Инженерно-экономический институт', 100, 36);

            cx.font = '22px "Courier New", monospace';
            const first = Math.floor(offset), frac = offset - first;
            const rows = Math.ceil((H - 54) / LH) + 1;
            cx.save();
            cx.beginPath(); cx.rect(0, 54, W, H - 54); cx.clip();
            for (let i = 0; i < rows; i++) {
                const idx  = first + i;
                const line = CODE[idx % CODE.length];
                const y = 54 + 26 + i * LH - frac * LH;
                cx.fillStyle = col.num;
                cx.fillText(String(idx % 99 + 1).padStart(2, ' '), 16, y);
                let c = col.txt;
                if (/\/\//.test(line)) c = col.cmt;
                else if (/"/.test(line)) c = col.str;
                else if (/^\s*(class|const|await|get|return|constructor)/.test(line)) c = col.kw;
                cx.fillStyle = c;
                cx.fillText(line, 62, y);
            }
            cx.restore();
            tex.needsUpdate = true;
        }

        const tmp = new THREE.Vector3();
        return (p, e, t, s) => {
            if (!reduce) offset += 0.03;
            drawScreen();

            const k = ease(p);
            // вращение затухает по мере приближения
            const calm = 1 - smooth(0, 0.55, p);
            computer.rotation.y = lerp(computer.rotation.y, (s.mx * 0.35 + Math.sin(t * 0.35) * 0.18) * calm, 0.05);
            computer.rotation.x = lerp(computer.rotation.x, (-s.my * 0.12 + 0.06) * calm, 0.05);

            // камера подлетает к экрану, чтобы он занял весь кадр
            const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
            const dEnd = Math.min(2.9 / (2 * half * camera.aspect), 1.8 / (2 * half)) * (mobile ? 1.05 : 0.98);
            const zStart = Math.max(7, 4.6 / (half * camera.aspect));
            camera.position.set(0, lerp(0.7, 0.3, k), lerp(zStart, dEnd + 0.07, k));
            tmp.set(0, lerp(0.1, 0.3, k), 0);
            camera.lookAt(tmp);
        };
    });

    /* ══════════════════════════════════════════════════════
       СЦЕНА 2 — ЛИ: лес, архитекторы и экологи
    ══════════════════════════════════════════════════════ */
    registerScene('scene-li', {
        fov: 58, z: 10, dust: 700, dustSize: 0.04,
        spread: { x: 16, y: 8, z: 8 },
    }, (stage) => {
        const { scene, camera } = stage;
        const forest = new THREE.Group();
        scene.add(forest);
        forest.add(gridFloor(60, 60, 0));

        // детерминированный ГПСЧ — лес всегда одинаковый
        let seed = 7;
        const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

        const rows = [];
        const M = new THREE.Matrix4(), T = new THREE.Matrix4(), Q = new THREE.Quaternion();
        const rowsCount = mobile ? 7 : 11;
        for (let r = 0; r < rowsCount; r++) {
            const z = 3 - r * 2.3;
            const bw = makeBatch(), bd = makeBatch();
            for (let x = -17; x <= 17; x += 1.7 + rnd() * 0.9) {
                const px = x + (rnd() - 0.5) * 0.8;
                // коридор для камеры и площадка для людей
                if (Math.abs(px) < 1.5) continue;
                if (z > -1 && z < 3.5 && Math.abs(px) < 5.5) continue;
                const s = 0.85 + rnd() * 0.75;
                M.compose(new THREE.Vector3(px, 0, z + (rnd() - 0.5) * 0.8), Q, new THREE.Vector3(s, s, s));
                const part = (geo, y, batch) => batch.addGeo(geo, M.clone().multiply(T.makeTranslation(0, y, 0)));
                part(new THREE.CylinderGeometry(0.06, 0.09, 0.8, 6),   0.4,  bd);
                part(new THREE.ConeGeometry(0.62, 1.3, 7),             1.25, bw);
                part(new THREE.ConeGeometry(0.48, 1.0, 7),             1.85, bw);
                part(new THREE.ConeGeometry(0.32, 0.8, 6),             2.4,  bw);
            }
            const g = new THREE.Group();
            g.add(bw.build(MAT_WIRE), bd.build(MAT_DIM));
            forest.add(g);
            rows.push(g);
        }

        // люди: слева архитекторы, справа экологи
        const people = new THREE.Group();
        [['board', -4.2, 1.6], ['roll', -3.1, 1.0], ['board', -2.3, 1.8],
         ['staff',  2.4, 1.2], ['helmet',3.2, 1.9], ['staff',  4.1, 1.1]]
            .forEach(([prop, x, z]) => {
                const h = makeHuman(1.05, prop);
                h.position.set(x, 0, z);
                h.rotation.y = x < 0 ? 0.5 : -0.5;
                people.add(h);
            });
        forest.add(people);

        const tmp = new THREE.Vector3();
        return (p, e, t, s) => {
            const k = ease(p);
            rows.forEach((g, i) => { g.rotation.z = Math.sin(t * 0.7 + i * 0.9) * 0.012; });

            // камера идёт вглубь леса между группами людей
            const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
            const zStart = Math.max(10, 12 / (half * camera.aspect));
            const calm = 1 - smooth(0.2, 0.9, p);
            camera.position.set(
                lerp(camera.position.x, s.mx * 0.9 * calm, 0.05),
                lerp(1.6, 1.3, k) + s.my * 0.2 * calm,
                lerp(zStart, -9, k)
            );
            tmp.set(0, lerp(1.4, 1.5, k), lerp(0, -16, k));
            camera.lookAt(tmp);
        };
    });

    /* ══════════════════════════════════════════════════════
       СЦЕНА 3 — СИ: три высотки + бригада, влёт в окно
    ══════════════════════════════════════════════════════ */
    registerScene('scene-si', {
        fov: 50, z: 14, dust: 700, dustSize: 0.045,
        spread: { x: 16, y: 9, z: 7 },
    }, (stage) => {
        const { scene, camera } = stage;
        const city = new THREE.Group();
        scene.add(city);
        city.add(gridFloor(50, 50, 0));

        const B = [
            { x: -4.6, h: 7.0, w: 1.5 },
            { x:  0,   h: 9.5, w: 1.7 },
            { x:  4.6, h: 6.0, w: 1.4 },
        ];
        const ROW = 0.55;
        B.forEach(b => {
            const wall = makeBatch(), win = makeBatch();
            wall.addGeo(new THREE.BoxGeometry(b.w, b.h, b.w), new THREE.Matrix4().makeTranslation(0, b.h / 2, 0));
            const floors = Math.floor(b.h / ROW) - 1;
            const hw = b.w / 2 + 0.01;
            for (let f = 0; f < floors; f++) {
                const y = f * ROW + 0.55;
                for (let c = -1; c <= 1; c++) {
                    const o = c * (b.w / 3.6);
                    win.addRect(o,  y,  hw, 0.22, 0.28, 'xy');
                    win.addRect(o,  y, -hw, 0.22, 0.28, 'xy');
                    win.addRect( hw, y, o,  0.22, 0.28, 'zy');
                    win.addRect(-hw, y, o,  0.22, 0.28, 'zy');
                }
            }
            const g = new THREE.Group();
            g.add(wall.build(MAT_WIRE), win.build(MAT_DIM));
            g.position.x = b.x;
            city.add(g);
        });

        // башенный кран
        const tower = box(0.14, 8.4, 0.14, false); tower.position.set(2.5, 4.2, -1.6);
        const jib   = box(4.6, 0.12, 0.12, false); jib.position.set(3.4, 8.3, -1.6);
        const cw    = box(0.5, 0.4, 0.3, true);    cw.position.set(0.9, 8.1, -1.6);
        const hook  = box(0.03, 2.2, 0.03, true);  hook.position.set(5.0, 7.15, -1.6);
        city.add(tower, jib, cw, hook);

        // бригада
        [[-3.4, 2.2], [-2.4, 3.0], [2.3, 2.6], [3.3, 1.9], [4.2, 3.0], [-6.2, 2.4]]
            .forEach(([x, z]) => {
                const h = makeHuman(0.75, 'helmet');
                h.position.set(x, 0, z);
                h.rotation.y = (x < 0 ? 0.6 : -0.6);
                city.add(h);
            });

        // окно центральной высотки, в которое влетаем
        const WIN_Y = 9 * ROW + 0.55;
        let spin = 0;
        const tmpPos = new THREE.Vector3(), tmpLook = new THREE.Vector3();
        const TAU = Math.PI * 2;

        return (p, e, t, s) => {
            const k = ease(p);
            if (!reduce && p < 0.04) spin += 0.004;          // спокойно вращается на месте
            const target = Math.round(spin / TAU) * TAU;      // при скролле разворачивается «лицом»
            city.rotation.y = lerp(spin, target, smooth(0.02, 0.32, p)) + s.mx * 0.12 * (1 - k);

            const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
            const zStart = Math.max(14, 7.6 / (half * camera.aspect));
            tmpPos.set(0, lerp(3.6, WIN_Y, k), lerp(zStart, 1.05, k));
            tmpLook.set(0, lerp(4.4, WIN_Y, k), lerp(0, 0.9, k));
            camera.position.copy(tmpPos);
            camera.lookAt(tmpLook);
        };
    });

    /* ══════════════════════════════════════════════════════
       ГЛАВНЫЙ ЦИКЛ
    ══════════════════════════════════════════════════════ */
    applyTheme();
    let last = performance.now(), t = 0;

    function frame(now) {
        requestAnimationFrame(frame);
        const dt = reduce ? 0 : Math.min((now - last) / 16.667, 3);
        last = now;
        t += 0.016 * dt;

        /* hero */
        if (hero && hero.active) {
            const { stage } = hero;
            const sy = window.scrollY;
            stage.dustGroup.forEach(g => { g.position.y = sy * 0.004; });
            stage.camera.position.x = lerp(stage.camera.position.x, mouse.x * 0.3, 0.03);
            stage.camera.position.y = lerp(stage.camera.position.y, mouse.y * 0.2, 0.03);
            stage.camera.lookAt(0, 0, 0);
            const push = mobile ? null : { x: mouse.x * 5, y: mouse.y * 3.2 - sy * 0.004 };
            stage.updateDust(t, dt, push);
            stage.renderer.render(stage.scene, stage.camera);
            if (heroContent) {
                const f = 1 - clamp(sy / (window.innerHeight * 0.55), 0, 1);
                heroContent.style.opacity = f;
                heroContent.style.transform = 'translateY(' + (sy * 0.15) + 'px)';
            }
        }

        /* сцены */
        for (let i = 0; i < scenes.length; i++) {
            const s = scenes[i];
            if (!s.active) continue;

            const { p, e } = trackProgress(s.track);
            const pp = reduce ? 0 : p;
            s.p = lerp(s.p, pp, reduce ? 1 : 0.14);          // сглаживание скролла
            s.mx = lerp(s.mx, mouse.x, 0.05);
            s.my = lerp(s.my, mouse.y, 0.05);

            s.update(s.p, e, t, s);
            s.stage.updateDust(t, dt, null);
            s.stage.renderer.render(s.stage.scene, s.stage.camera);

            // затемнение: въезд (fade-in) и выезд в цвет фона (переход к следующей сцене)
            const fadeIn  = reduce ? 0 : 1 - smooth(0.35, 1.0, e);
            const fadeOut = reduce ? 0 : smooth(mobile ? 0.8 : 0.86, 1.0, s.p);
            if (s.fade) s.fade.style.opacity = Math.max(fadeIn, fadeOut).toFixed(3);
            if (s.hud && !reduce) {
                const o = 1 - smooth(0.06, 0.32, s.p);
                s.hud.style.opacity = o.toFixed(3);
                s.hud.style.visibility = o < 0.03 ? 'hidden' : 'visible';
            }
        }
    }
    requestAnimationFrame(frame);
})();
