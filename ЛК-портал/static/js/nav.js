(function () {
    const burger  = document.getElementById('burgerBtn');
    const menu    = document.getElementById('mobileMenu');
    const trigger = document.querySelector('.site-nav__dropdown-trigger');

    if (burger && menu) {
        const setOpen = (open) => {
            menu.classList.toggle('open', open);
            burger.classList.toggle('active', open);
            burger.setAttribute('aria-expanded', String(open));
            menu.setAttribute('aria-hidden', String(!open));
        };
        burger.addEventListener('click', () => setOpen(!menu.classList.contains('open')));
        menu.querySelectorAll('a').forEach(a => a.addEventListener('click', () => setOpen(false)));
    }

    if (trigger) {
        const setDrop = (open) => trigger.setAttribute('aria-expanded', String(open));
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            setDrop(trigger.getAttribute('aria-expanded') !== 'true');
        });
        document.addEventListener('click', () => setDrop(false));
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setDrop(false); });
    }
})();
