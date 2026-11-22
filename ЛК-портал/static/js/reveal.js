/* Появление текста при скролле:
   .reveal            — fade + slide-up блока
   [data-split]       — заголовок разбивается на слова, слова выезжают по очереди */
(function () {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // скрываем блоки только если скрипт действительно работает: без JS контент виден
    document.documentElement.classList.add('js-reveal');

    document.querySelectorAll('[data-split]').forEach(el => {
        const text = el.textContent.trim().split(/\s+/);
        el.setAttribute('aria-label', el.textContent.trim());
        el.textContent = '';
        text.forEach((word, i) => {
            const outer = document.createElement('span');
            outer.className = 'split-word';
            outer.setAttribute('aria-hidden', 'true');
            const inner = document.createElement('span');
            inner.className = 'split-word__inner';
            inner.style.transitionDelay = (i * 60) + 'ms';
            inner.textContent = word;
            outer.appendChild(inner);
            el.appendChild(outer);
            el.appendChild(document.createTextNode(' '));
        });
        el.classList.add('split-ready');
    });

    const els = document.querySelectorAll('.reveal, .split-ready');
    if (!els.length) return;

    if (reduce || !('IntersectionObserver' in window)) {
        els.forEach(el => el.classList.add('visible'));
        return;
    }

    const io = new IntersectionObserver((entries) => {
        entries.forEach(e => {
            if (e.isIntersecting) {
                e.target.classList.add('visible');
                io.unobserve(e.target);
            }
        });
    }, { threshold: 0.15, rootMargin: '0px 0px -8% 0px' });

    els.forEach(el => io.observe(el));
    // страховка: если observer не сработал (headless/старый браузер) — показываем всё
    setTimeout(() => {
        if (!document.querySelector('.reveal.visible')) {
            els.forEach(el => el.classList.add('visible'));
        }
    }, 1200);
})();
