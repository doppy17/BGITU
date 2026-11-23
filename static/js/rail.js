/* Рельс кабинета: раскрытый рельс закрывается кликом вне его или по Esc.
   Раскрытие/сворачивание кнопкой-стрелкой и мобильным бургером остаётся
   в app.js; здесь только «клик снаружи». Слушатели не перехватывают событие
   (никаких preventDefault/stopPropagation), поэтому ссылки, поиск и
   поповеры темы/доступности продолжают работать как обычно. */
(function () {
  'use strict';

  var nav = document.querySelector('[data-rail]') || document.getElementById('cabNav');
  if (!nav) return;

  var toggle = nav.querySelector('[data-rail-toggle]') || document.getElementById('cabToggle');
  var backdrop = document.querySelector('[data-rail-backdrop]') || document.getElementById('navBackdrop');
  var tip = document.querySelector('[data-rail-tip]') || document.getElementById('navTip');
  var burger = document.getElementById('burger');

  function isOpen() {
    return nav.classList.contains('is-expanded') || nav.classList.contains('is-open');
  }

  function closeRail() {
    var expanded = nav.classList.contains('is-expanded');
    var open = nav.classList.contains('is-open');
    if (!expanded && !open) return false;

    // Фокус внутри рельса после закрытия станет невидимым — вернём его
    // на кнопку-открыватель (стрелку на десктопе, бургер на мобильном).
    var focusInside = nav.contains(document.activeElement);

    if (expanded) {
      nav.classList.remove('is-expanded');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
    }
    if (open) {
      nav.classList.remove('is-open');
      if (backdrop) {
        backdrop.classList.remove('is-open');
        backdrop.hidden = true;
      }
      document.body.classList.remove('nav-open');
      if (burger) burger.setAttribute('aria-expanded', 'false');
    }
    // Подсказка свёрнутого рельса не должна зависать после сворачивания.
    if (tip) {
      tip.classList.remove('is-on');
      tip.setAttribute('aria-hidden', 'true');
    }

    if (focusInside) {
      var target = open && burger ? burger : toggle;
      if (target) target.focus({ preventScroll: true });
    }
    return true;
  }

  function isInsideRail(target) {
    if (!target || typeof target.closest !== 'function') return false;
    return nav.contains(target) || !!(tip && tip.contains(target));
  }

  document.addEventListener('click', function (event) {
    if (!isOpen()) return;
    var target = event.target;
    if (isInsideRail(target)) return;
    if (!target || typeof target.closest !== 'function') return;
    // Кнопки-открыватели: их клик всплывает до document и не должен
    // закрывать только что открытый рельс.
    if (target.closest('[data-rail-toggle], #burger')) return;
    closeRail();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || !isOpen()) return;
    closeRail();
  });
})();
