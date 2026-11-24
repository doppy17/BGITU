/* Блок «02 Учебный план»: бегущая строка для обрезанных названий дисциплин
   и одноразовое плавное появление отрезков семестров.

   Маркиза включается только там, где текст реально не влезает в колонку:
   измеряем ширину текста с точностью до долей пикселя (Range), потому что
   целочисленный scrollWidth на границе даёт ложное «ровно 2 px».
   Внутри маски — трек из двух одинаковых экземпляров текста; он едет только
   влево на -50% (один экземпляр с зазором), поэтому цикл бесшовный и без
   возврата назад. Полное название остаётся в title, колонка sticky. */
(function () {
  'use strict';

  var MARQUEE_SPEED = 20; // px/с: ориентир пользователя 18–25
  var MARQUEE_GAP = 24;   // совпадает с --tl-marquee-gap в cabinet.css

  var reduceQuery = window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)')
    : { matches: false, addEventListener: null };

  var grids = [];
  var clips = [];

  function textWidth(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    var width = range.getBoundingClientRect().width;
    range.detach();
    return width;
  }

  function motionAllowed() {
    // a11y-режим «Скрыть изображения и анимацию» тоже выключает бегущую строку.
    return !reduceQuery.matches &&
      !document.documentElement.classList.contains('a11y-im');
  }

  function clearMarquee(subject) {
    var clip = subject.querySelector('.tl-subject__clip');
    if (clip) {
      var track = clip.querySelector('.tl-subject__track');
      if (track) {
        var text = track.querySelector('.tl-subject__text');
        if (text) clip.appendChild(text); // вернуть единственный экземпляр
        track.remove();                    // здесь же уходит aria-hidden дубль
      }
    }
    subject.classList.remove('is-marquee');
    delete subject.dataset.tlOverflow;
    subject.style.removeProperty('--tl-marquee-dur');
    subject.style.removeProperty('--tl-marquee-gap');
  }

  function buildTrack(clip, text) {
    var track = document.createElement('span');
    track.className = 'tl-subject__track';
    clip.insertBefore(track, text);
    track.appendChild(text);

    var copy = text.cloneNode(true);
    copy.setAttribute('aria-hidden', 'true');
    copy.removeAttribute('id');
    track.appendChild(copy);

    return track;
  }

  function applyMarquee(subject) {
    var clip = subject.querySelector('.tl-subject__clip');
    var text = subject.querySelector('.tl-subject__text');
    if (!clip || !text) return;

    if (!motionAllowed()) {
      clearMarquee(subject);
      return;
    }

    // Ширина маски, а не .tl-subject: у названия есть правый отступ 15px.
    var overflow = textWidth(text) - clip.clientWidth;
    if (overflow <= 2) {
      clearMarquee(subject);
      return;
    }

    var prev = parseFloat(subject.dataset.tlOverflow || '0');
    if (subject.classList.contains('is-marquee') && Math.abs(prev - overflow) < 0.5) {
      return; // уже едет с той же амплитудой — не перезапускаем
    }

    clearMarquee(subject);
    // Перезапуск с нуля, если ширина текста изменилась (например, a11y-fs2).
    void subject.offsetWidth;

    var oneCopy = overflow + clip.clientWidth; // точная ширина текста
    var duration = (oneCopy + MARQUEE_GAP) / MARQUEE_SPEED;

    subject.dataset.tlOverflow = String(overflow);
    subject.style.setProperty('--tl-marquee-gap', MARQUEE_GAP + 'px');
    subject.style.setProperty('--tl-marquee-dur', duration.toFixed(2) + 's');
    buildTrack(clip, text);
    subject.classList.add('is-marquee');
  }

  function measure() {
    for (var i = 0; i < clips.length; i += 1) {
      applyMarquee(clips[i]);
    }
  }

  var measureQueued = false;
  function scheduleMeasure() {
    if (measureQueued) return;
    measureQueued = true;
    window.requestAnimationFrame(function () {
      measureQueued = false;
      measure();
    });
  }

  function init() {
    grids = Array.prototype.slice.call(document.querySelectorAll('.tl-grid'));
    clips = Array.prototype.slice.call(document.querySelectorAll('.tl-subject'));

    // Одноразовый триггер появления отрезков: класс остаётся на сетке,
    // поэтому ресайз и смена темы не перезапускают каскад.
    for (var i = 0; i < grids.length; i += 1) {
      grids[i].classList.add('is-tl-revealed');
    }

    measure();

    window.addEventListener('resize', scheduleMeasure, { passive: true });
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(scheduleMeasure);
    }

    // Крупный шрифт для слабовидящих и режим «без анимации» меняют метрики
    // без resize окна.
    if (window.MutationObserver) {
      new MutationObserver(scheduleMeasure).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['class', 'style', 'data-theme']
      });
    }

    if (window.ResizeObserver && clips.length) {
      var ro = new ResizeObserver(scheduleMeasure);
      for (var j = 0; j < clips.length; j += 1) {
        var clip = clips[j].querySelector('.tl-subject__clip');
        if (clip) ro.observe(clip);
      }
    }

    if (reduceQuery.addEventListener) {
      reduceQuery.addEventListener('change', scheduleMeasure);
    } else if (reduceQuery.addListener) {
      reduceQuery.addListener(scheduleMeasure);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
