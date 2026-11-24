/* Редактирование блока 01 «Данные студента» прямо в профиле.
   Прогрессивное улучшение: без JS кнопка «Изменить» — обычная ссылка на
   /cabinet/profile/edit/. С JS она раскрывает форму внутри блока, а сохранение
   уходит fetch-ом на тот же адрес с X-Requested-With: XMLHttpRequest.

   Сервер при успехе отдаёт JSON с готовым HTML блока 01 — им блок и
   заменяется, страница не перезагружается. Ошибки валидации показываются
   у полей и блок остаётся в режиме правки. Любая другая ошибка, таймаут или
   неожиданный ответ — обычная отправка формы (fallback), как без JS. */
(() => {
  'use strict';

  const TIMEOUT = 10000;

  /* Метка документа: живёт до полной перезагрузки — по ней видно, что
     сохранение прошло без перезагрузки. */
  window.__profileInline = 'load-' + Date.now().toString(36);

  const elementOf = (event) => (event.target instanceof Element ? event.target : null);
  const rootOf = (node) => (node instanceof Element ? node.closest('[data-profile-facts]') : null);
  const all = (root, selector) => Array.from(root.querySelectorAll(selector));

  function status(root, text) {
    const out = root.querySelector('[data-profile-status]');
    if (out) out.textContent = text || '';
  }

  function clearErrors(root) {
    all(root, '[data-profile-error]').forEach((node) => {
      node.textContent = '';
      node.hidden = true;
    });
    status(root, '');
  }

  function focusField(form, name) {
    const field = form.querySelector('[name="' + name + '"]');
    if (!field) return;
    const custom = field.closest('.sel');
    const target = custom ? custom.querySelector('.sel__btn') : field;
    if (target && typeof target.focus === 'function') target.focus({ preventScroll: true });
  }

  function showErrors(root, form, payload) {
    const fields = (payload && payload.fields) || {};
    let first = null;
    Object.keys(fields).forEach((name) => {
      const box = form.querySelector('[data-profile-error="' + name + '"]');
      if (!box) return;
      box.textContent = (fields[name] || []).join(' ');
      box.hidden = false;
      if (!first) first = name;
    });
    status(root, (payload && payload.message) || 'Проверьте выделенные поля.');
    if (first) {
      focusField(form, first);
    } else {
      const field = form.querySelector('select, input:not([type="hidden"])');
      if (field) field.focus({ preventScroll: true });
    }
  }

  function open(root) {
    const view = root.querySelector('[data-profile-view]');
    const form = root.querySelector('[data-profile-form]');
    if (!view || !form || root.classList.contains('is-editing')) return;
    clearErrors(root);
    view.hidden = true;
    form.hidden = false;
    root.classList.add('is-editing');
    const openButton = root.querySelector('[data-profile-edit-open]');
    if (openButton) openButton.setAttribute('aria-expanded', 'true');
    if (window.initSelects) window.initSelects();
    const field = form.querySelector('select, input:not([type="hidden"])');
    const target = field && field.closest('.sel') ? field.closest('.sel').querySelector('.sel__btn') : field;
    if (target && typeof target.focus === 'function') target.focus({ preventScroll: true });
  }

  function close(root, returnFocus) {
    const view = root.querySelector('[data-profile-view]');
    const form = root.querySelector('[data-profile-form]');
    if (!view || !form) return;
    form.hidden = true;
    view.hidden = false;
    root.classList.remove('is-editing', 'is-saving');
    const openButton = root.querySelector('[data-profile-edit-open]');
    if (openButton) {
      openButton.setAttribute('aria-expanded', 'false');
      if (returnFocus !== false) openButton.focus({ preventScroll: true });
    }
    clearErrors(root);
  }

  /* Студенческий билет показывает курс и направление группы: после правки
     группы они меняются — обновляем подписи без перезагрузки. */
  function syncTicket(profile) {
    if (!profile) return;
    const course = document.querySelector('[data-ticket-course]');
    if (course && profile.course !== null && profile.course !== undefined) {
      course.textContent = String(profile.course);
    }
    const direction = document.querySelector('[data-ticket-direction]');
    if (direction && profile.direction) direction.textContent = profile.direction;
    const years = document.querySelector('[data-ticket-years]');
    if (years && profile.enrollment_year && profile.graduation_year) {
      years.textContent = profile.enrollment_year + '–' + profile.graduation_year;
    }
  }

  /* Шапка профиля: «ИСТ-101 · 1 курс · институт» — тоже из вычисленных полей. */
  function syncHero(profile) {
    if (!profile) return;
    const group = document.querySelector('[data-hero-group]');
    if (group) group.textContent = profile.group || '';
    const course = document.querySelector('[data-hero-course]');
    if (course) course.textContent = profile.course ? profile.course + ' курс' : '';
    const subgroup = document.querySelector('[data-hero-subgroup]');
    if (subgroup) {
      subgroup.textContent = profile.subgroup ? 'подгруппа «' + profile.subgroup + '»' : '';
    }
  }

  /* Замена блока готовым HTML из ответа: скролл компенсируется, чтобы блок
     остался на прежнем месте во вьюпорте. */
  function apply(root, html, message) {
    const holder = document.createElement('div');
    holder.innerHTML = String(html).trim();
    const fresh = holder.firstElementChild;
    if (!fresh || !fresh.matches('[data-profile-facts]')) throw new Error('в ответе нет блока 01');
    const top = root.getBoundingClientRect().top;
    root.replaceWith(fresh);
    const delta = fresh.getBoundingClientRect().top - top;
    if (Math.abs(delta) > 1) window.scrollBy(0, delta);
    if (window.initSelects) window.initSelects();

    const note = fresh.querySelector('[data-profile-note]');
    if (note && message) {
      note.textContent = message;
      window.setTimeout(() => {
        if (note.isConnected) note.textContent = '';
      }, 5000);
    }
    const openButton = fresh.querySelector('[data-profile-edit-open]');
    if (openButton) openButton.focus({ preventScroll: true });
  }

  async function save(form) {
    const root = rootOf(form);
    if (!root) return;
    const data = new window.FormData(form);
    const submitButton = form.querySelector('[type="submit"]');
    const controller = new window.AbortController();
    const timer = window.setTimeout(() => controller.abort(), TIMEOUT);

    root.classList.add('is-saving');
    form.setAttribute('aria-busy', 'true');
    if (submitButton) submitButton.disabled = true;
    status(root, 'Сохраняем…');

    try {
      const response = await window.fetch(form.action, {
        method: 'POST',
        body: data,
        credentials: 'same-origin',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
        },
        signal: controller.signal,
      });

      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }

      if (!response.ok || !payload || payload.ok !== true) {
        if (payload && payload.ok === false) {
          showErrors(root, form, payload);
          return;
        }
        throw new Error('HTTP ' + response.status);
      }
      if (!payload.html) throw new Error('пустой ответ');
      apply(root, payload.html, payload.message);
      syncTicket(payload.profile);
      syncHero(payload.profile);
    } catch (error) {
      // Обычный POST: сервер сохранит те же поля и вернёт в профиль.
      if (form.isConnected) form.submit();
    } finally {
      window.clearTimeout(timer);
      root.classList.remove('is-saving');
      form.removeAttribute('aria-busy');
      if (submitButton) submitButton.disabled = false;
    }
  }

  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const node = elementOf(event);
    if (!node) return;

    const openLink = node.closest('[data-profile-edit-open]');
    if (openLink) {
      const root = rootOf(openLink);
      if (!root) return;
      event.preventDefault();
      open(root);
      return;
    }

    const cancel = node.closest('[data-profile-cancel]');
    if (cancel) {
      const root = rootOf(cancel);
      if (!root) return;
      event.preventDefault();
      close(root, true);
    }
  });

  document.addEventListener('submit', (event) => {
    const node = elementOf(event);
    const form = node && node.closest('[data-profile-form]');
    if (!form) return;
    event.preventDefault();
    save(form);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const node = elementOf(event);
    const form = node && node.closest('[data-profile-form]');
    if (!form) return;
    const root = rootOf(form);
    if (!root || !root.classList.contains('is-editing')) return;
    event.preventDefault();
    close(root, true);
  });

  /* Год окончания — только чтение: показываем результат прямо при вводе. */
  document.addEventListener('input', (event) => {
    const node = elementOf(event);
    if (!node) return;
    const input = node.closest('[data-profile-form] input[name="enrollment_year"]');
    if (!input) return;
    const root = rootOf(input);
    const out = root && root.querySelector('[data-profile-graduation]');
    if (!out) return;
    const term = parseInt(root.getAttribute('data-study-term'), 10) || 4;
    const year = parseInt(input.value, 10);
    const valid = Number.isFinite(year) && year >= 2000 && year <= 2100;
    out.textContent = valid ? String(year + term) : '—';
  });
})();
