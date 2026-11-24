/* Переключение расписания без перезагрузки страницы.
   Прогрессивное улучшение: формы и ссылки продолжают работать сами по себе,
   а скрипт перехватывает их и подменяет только блоки 01/03 и навигацию недели
   (сервер отдаёт их по /schedule/fragment/?…&partial=1).

   Правила:
   - параметры запроса — из формы/ссылки, адрес после подмены — канонический
     из заголовка X-Schedule-Url (в нём группа, преподаватель, неделя и
     подгруппа после серверных правил), поэтому ссылку можно скопировать;
   - во время запроса блоки получают класс is-sched-loading (полупрозрачность
     и спиннер только внутри блоков, страница не блокируется);
   - ошибка, таймаут (4 с) или пустой фрагмент — обычный переход по ссылке;
   - фокус и положение скролла сохраняются: после подмены фокус возвращается
     на тот же элемент блока, а блок-якорь остаётся на месте во вьюпорте. */
(() => {
  'use strict';

  const BLOCKS = ['sched-week-nav', 'sched-block-group', 'sched-block-teacher'];
  const TIMEOUT = 4000;
  const script = document.querySelector('script[data-fragment-url]');
  if (!script) return;
  const fragmentUrl = script.getAttribute('data-fragment-url');
  const pageUrl = script.getAttribute('data-page-url') || '';
  let seq = 0;
  let controller = null;

  /* Метка документа: не меняется при подмене блоков и меняется при полной
     перезагрузке — по ней видно, что страница не перезагружалась. */
  const stamp = 'load-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  window.__schedulePageStamp = stamp;
  if (document.body) document.body.dataset.stamp = stamp;

  const blocks = () => BLOCKS.map((id) => document.getElementById(id)).filter(Boolean);

  function setBusy(on) {
    blocks().forEach((block) => {
      block.classList.toggle('is-sched-loading', on);
      block.setAttribute('aria-busy', on ? 'true' : 'false');
    });
  }

  /* Инлайновый onchange="this.form.submit()" нужен только без JS: с JS форма
     уходит фрагментом, иначе браузер перезагрузил бы страницу. */
  function stripInlineSubmit() {
    document.querySelectorAll('form.group-switch select[onchange]').forEach((select) => {
      select.removeAttribute('onchange');
    });
  }

  /* Какой элемент держал фокус: после подмены ищем его аналог в том же блоке. */
  function focusToken(element) {
    if (!element || element === document.body) return null;
    const block = element.closest('[id^="sched-"]');
    if (!block) return null;
    let selector = '';
    if (element.matches('[data-week-arrow]')) {
      selector = '[data-week-arrow="' + element.getAttribute('data-week-arrow') + '"]';
    } else if (element.matches('a.sub-pill')) {
      selector = 'a.sub-pill.is-active';
    } else if (element.closest('.sel')) {
      selector = '.sel__btn';
    } else if (element.matches('select[name]')) {
      selector = 'select[name="' + element.getAttribute('name') + '"]';
    } else if (element.matches('button, a')) {
      selector = element.tagName.toLowerCase();
    }
    return selector ? { block: block.id, selector } : null;
  }

  function restoreFocus(token) {
    if (!token) return;
    const block = document.getElementById(token.block);
    if (!block) return;
    const target = block.querySelector(token.selector) || block.querySelector('.sel__btn, select');
    if (target && typeof target.focus === 'function') target.focus({ preventScroll: true });
  }

  function withParams(base, params) {
    const url = new URL(base, window.location.href);
    params.forEach(([name, value]) => {
      if (String(value).trim()) url.searchParams.set(name, value);
    });
    return url;
  }

  /* Запрос из формы: и адрес фрагмента, и обычный адрес страницы (для fallback). */
  function formRequest(form) {
    const params = [];
    new window.FormData(form).forEach((value, name) => params.push([name, value]));
    const fragment = withParams(fragmentUrl, params);
    fragment.searchParams.set('partial', '1');
    return { fragment: fragment.toString(), page: withParams(window.location.pathname, params).toString() };
  }

  /* Запрос из ссылки: параметры ссылки переносим на фрагментный эндпоинт,
     а сама ссылка остаётся адресом страницы для fallback. */
  function linkRequest(href) {
    const page = new URL(href, window.location.href);
    const fragment = withParams(fragmentUrl, Array.from(page.searchParams.entries()));
    fragment.searchParams.set('partial', '1');
    return { fragment: fragment.toString(), page: page.toString() };
  }

  /* Замена блоков. mode: 'push' — новая запись истории, 'none' — адрес уже
     правильный (кнопка «назад»). false — фрагмент непригоден, тогда работает
     обычный переход. */
  function apply(html, mode, canonicalUrl, anchor) {
    const doc = new window.DOMParser().parseFromString(html, 'text/html');
    if (!doc.getElementById('sched-block-group')) return false;
    const anchorTop = anchor ? anchor.getBoundingClientRect().top : null;
    let replaced = 0;
    blocks().forEach((current) => {
      const fresh = doc.getElementById(current.id);
      if (!fresh) return;  // блока нет в шаблоне — он просто остаётся как был
      current.innerHTML = fresh.innerHTML;
      current.className = fresh.className;
      replaced += 1;
    });
    if (!replaced) return false;

    const canonical = canonicalUrl || window.location.pathname + window.location.search;
    if (mode === 'push') window.history.pushState({ schedule: true }, '', canonical);

    stripInlineSubmit();
    if (window.initSelects) window.initSelects();

    if (anchor && anchorTop !== null) {
      const freshAnchor = document.getElementById(anchor.id);
      if (freshAnchor) {
        const delta = freshAnchor.getBoundingClientRect().top - anchorTop;
        if (Math.abs(delta) > 1) window.scrollBy(0, delta);
      }
    }
    document.dispatchEvent(new window.CustomEvent('schedule:updated', { detail: { url: canonical } }));
    return true;
  }

  async function navigate(request, options) {
    const opts = options || {};
    const mySeq = ++seq;
    const token = focusToken(document.activeElement);
    const anchor = (opts.anchor && opts.anchor.closest('[id^="sched-"]')) || blocks()[0] || null;
    if (controller) controller.abort();
    const mine = new window.AbortController();
    controller = mine;
    const timer = window.setTimeout(() => mine.abort(), TIMEOUT);
    setBusy(true);
    try {
      const response = await window.fetch(request.fragment, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        signal: mine.signal,
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const html = await response.text();
      if (mySeq !== seq) return;
      const canonical = response.headers.get('X-Schedule-Url') || request.page;
      if (!apply(html, opts.mode || 'push', canonical, anchor)) {
        throw new Error('во фрагменте нет блоков');
      }
      restoreFocus(token);
    } catch (error) {
      if (mySeq !== seq) return;  // запрос отменён более свежим действием
      window.location.href = request.page;  // без фрагмента: обычный переход
    } finally {
      window.clearTimeout(timer);
      if (mySeq === seq) setBusy(false);
    }
  }

  /* event.target бывает не элементом (document, текстовый узел) — closest есть
     только у Element. */
  const elementOf = (event) => (event.target instanceof Element ? event.target : null);

  /* Ссылки недели и подгрупп: только левый клик без модификаторов. */
  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const node = elementOf(event);
    const link = node && node.closest('a[data-week-arrow], a.sub-pill');
    if (!link || !link.closest('[id^="sched-"]')) return;
    event.preventDefault();
    navigate(linkRequest(link.href), { mode: 'push', anchor: link });
  });

  /* Формы выбора группы и преподавателя: перехватываем submit и change. */
  function controlForm(node) {
    if (!node) return null;
    const select = node.closest('select[name="group"], select[name="teacher"]');
    return select ? select.closest('form.group-switch') : null;
  }

  document.addEventListener('change', (event) => {
    const form = controlForm(elementOf(event));
    if (!form) return;
    event.preventDefault();
    navigate(formRequest(form), { mode: 'push', anchor: form });
  });

  document.addEventListener('submit', (event) => {
    const node = elementOf(event);
    const form = node && node.closest('form.group-switch');
    if (!form) return;
    event.preventDefault();
    navigate(formRequest(form), { mode: 'push', anchor: form });
  });

  /* Кнопка «назад»: запись истории уже содержит параметры — подменяем блоки. */
  window.addEventListener('popstate', () => {
    navigate(linkRequest(window.location.href), { mode: 'none' });
  });

  /* Первичная подготовка: адрес становится каноническим — с группой и
     преподавателем, сохранёнными в сессии, чтобы ссылка была копируемой. */
  stripInlineSubmit();
  if (
    pageUrl &&
    document.getElementById('sched-block-group') &&
    pageUrl !== window.location.pathname + window.location.search
  ) {
    window.history.replaceState({ schedule: true }, '', pageUrl);
  }

  window.scheduleAjax = {
    stamp,
    refresh: (url) => navigate(linkRequest(url || window.location.href), { mode: 'none' }),
  };
})();
