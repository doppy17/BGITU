/* Портал БГИТУ — минимальный клиентский слой без зависимостей. */
(function () {
  'use strict';

  var burger = document.getElementById('burger');
  var nav = document.getElementById('cabNav');
  var backdrop = document.getElementById('navBackdrop');
  var navClose = document.getElementById('navClose');

  function setMenu(open) {
    if (!burger || !nav || !backdrop) return;
    burger.setAttribute('aria-expanded', String(open));
    nav.classList.toggle('is-open', open);
    backdrop.classList.toggle('is-open', open);
    backdrop.hidden = !open;
    document.body.classList.toggle('nav-open', open);
    if (open && navClose) navClose.focus();
  }

  if (burger && nav && backdrop) {
    burger.addEventListener('click', function () {
      setMenu(!nav.classList.contains('is-open'));
    });
    backdrop.addEventListener('click', function () {
      setMenu(false);
      burger.focus();
    });
    if (navClose) {
      navClose.addEventListener('click', function () {
        setMenu(false);
        burger.focus();
      });
    }
    nav.addEventListener('click', function (event) {
      if (event.target.closest('a') && window.matchMedia('(max-width: 1000px)').matches) {
        setMenu(false);
      }
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && nav.classList.contains('is-open')) {
        setMenu(false);
        burger.focus();
      }
    });
  }

  var head = document.getElementById('siteHead');
  if (head) {
    var onScroll = function () {
      head.classList.toggle('is-stuck', window.scrollY > 24);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  // Док-эффект левого меню (как в macOS): пункт под курсором увеличивается,
  // соседние разъезжаются наружу с сохранением зазора.
  (function () {
    var dockNav = document.getElementById('cabNav');
    if (!dockNav || !window.matchMedia('(hover: hover)').matches) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var dockItems = Array.prototype.slice.call(dockNav.querySelectorAll('a'));
    if (dockItems.length < 2) return;

    var AMP = 0.13;      // максимум увеличения
    var SIGMA = 95;      // радиус влияния, px
    var base = null;     // базовые позиции пунктов (без трансформаций)

    function measure() {
      base = dockItems.map(function (el) {
        el.style.transform = '';
        var r = el.getBoundingClientRect();
        return { center: r.top + r.height / 2, h: r.height };
      });
    }

    function apply(clientY) {
      var dists = base.map(function (b) { return Math.abs(b.center - clientY); });
      var h = dists.indexOf(Math.min.apply(Math, dists));
      var scales = dists.map(function (d) {
        return 1 + AMP * Math.exp(-(d * d) / (2 * SIGMA * SIGMA));
      });
      // раздвижка: якорь — пункт под курсором, соседи едут наружу от него
      var ty = new Array(dockItems.length).fill(0);
      for (var j = h + 1; j < dockItems.length; j++) {
        ty[j] = ty[j - 1] + (base[j - 1].h * (scales[j - 1] - 1) + base[j].h * (scales[j] - 1)) / 2;
      }
      for (var k = h - 1; k >= 0; k--) {
        ty[k] = ty[k + 1] - (base[k].h * (scales[k] - 1) + base[k + 1].h * (scales[k + 1] - 1)) / 2;
      }
      dockItems.forEach(function (el, i) {
        el.style.transform = 'translateY(' + ty[i].toFixed(2) + 'px) scale(' + scales[i].toFixed(3) + ')';
      });
    }

    dockNav.addEventListener('mouseenter', function () { measure(); });
    // Прямой расчёт без requestAnimationFrame: в фоновых вкладках rAF
    // троттлится, а расчёт здесь дешёвый (десяток пунктов).
    dockNav.addEventListener('mousemove', function (event) {
      if (!base) return;
      apply(event.clientY);
    });
    dockNav.addEventListener('mouseleave', function () {
      base = null;
      dockItems.forEach(function (el) { el.style.transform = ''; });
    });
  })();

  // Сообщения не мешают работе: плавно убираем через 7 секунд.
  var flashes = document.querySelectorAll('.flash');
  if (flashes.length) {
    window.setTimeout(function () {
      flashes.forEach(function (node) {
        node.style.transition = 'opacity .4s ease';
        node.style.opacity = '0';
        window.setTimeout(function () { node.remove(); }, 420);
      });
    }, 7000);
  }
})();
