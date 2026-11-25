// Подсказка «таблица шире окна» показывается только когда таблица реально прокручивается.
(function () {
  function sync() {
    document.querySelectorAll('.table-wrap').forEach(function (wrap) {
      var hint = wrap.previousElementSibling;
      if (!hint || !hint.classList.contains('scroll-hint')) return;
      var scrolls = wrap.scrollWidth - wrap.clientWidth > 2;
      hint.classList.toggle('is-on', scrolls);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sync);
  } else {
    sync();
  }
  window.addEventListener('resize', sync);
  window.addEventListener('load', sync);
})();

// Шапка «закреплена» — включаем тень под ней только когда содержимое реально уехало.
(function () {
  function mark() {
    document.querySelectorAll('.table-wrap--tall').forEach(function (wrap) {
      wrap.classList.toggle('is-stuck', wrap.scrollTop > 0);
    });
  }
  document.addEventListener('scroll', mark, true);
  window.addEventListener('load', mark);
  window.addEventListener('resize', mark);
})();
