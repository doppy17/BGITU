(function () {
    const root = document.documentElement;
    const btn  = document.getElementById('themeToggle');
    const KEY  = 'bgitu-theme';

    // Тёмная тема по умолчанию
    const saved = localStorage.getItem(KEY) || 'dark';
    root.setAttribute('data-theme', saved);

    if (btn) {
        btn.addEventListener('click', () => {
            const current = root.getAttribute('data-theme');
            const next    = current === 'dark' ? 'light' : 'dark';
            root.setAttribute('data-theme', next);
            localStorage.setItem(KEY, next);
        });
    }
})();
