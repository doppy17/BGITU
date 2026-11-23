/* Портал БГИТУ — минимальный клиентский слой без зависимостей. */
(function () {
  'use strict';

  var burger = document.getElementById('burger');
  var nav = document.getElementById('cabNav');
  var backdrop = document.getElementById('navBackdrop');

  // Мобильное меню (общее для сайта и кабинета: <nav class="menu" id="menu">)
  var mobileNav = document.getElementById('menu') || document.getElementById('siteNavMobile');
  if (burger && mobileNav) {
    burger.addEventListener('click', function () {
      var open = mobileNav.hasAttribute('hidden');
      if (mobileNav.id === 'menu') {
        mobileNav.hidden = !open;
        burger.setAttribute('aria-expanded', String(open));
        burger.setAttribute('aria-label', open ? 'Закрыть меню' : 'Меню');
        document.documentElement.classList.toggle('menu-open', open);
      } else {
        mobileNav.classList.toggle('is-open', open);
        burger.setAttribute('aria-expanded', String(open));
        document.body.classList.toggle('is-nav-open', open);
      }
    });
    mobileNav.addEventListener('click', function (event) {
      if (event.target.tagName === 'A') {
        if (mobileNav.id === 'menu') {
          mobileNav.hidden = true;
          burger.setAttribute('aria-expanded', 'false');
          document.documentElement.classList.remove('menu-open');
        } else {
          mobileNav.classList.remove('is-open');
          burger.setAttribute('aria-expanded', 'false');
          document.body.classList.remove('is-nav-open');
        }
      }
    });
  }
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
      if (event.target.closest('.cab-nav__item') && window.matchMedia('(max-width: 1000px)').matches) {
        setMenu(false);
      }
    });

    // Пункты навбара — кнопки с data-href (не <a>: у браузера не показывается
    // адрес в статусной строке при наведении).
    document.addEventListener('click', function (event) {
      var item = event.target.closest('.cab-nav__item');
      if (item && item.dataset.href) {
        window.location.href = item.dataset.href;
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

  // Раскрытие/сворачивание рельса — только кнопкой-стрелкой.
  // Док-магнификация убрана (требование юзера): пункты стоят на местах.
  (function () {
    var dockNav = document.getElementById('cabNav');
    var toggle = document.getElementById('cabToggle');
    var tip = document.getElementById('navTip');
    if (!dockNav) return;
    var tipItem = null;       // пункт, чья подсказка сейчас показана
    var showTimer = 0;        // задержка показа (антидребезг при проезде мышью)
    var hideTimer = 0;        // задержка скрытия (переезд с иконки на пузырёк)

    function isCollapsed() {
      return !dockNav.classList.contains('is-expanded') && !dockNav.classList.contains('is-open');
    }

    function clearTimers() {
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
      showTimer = 0;
      hideTimer = 0;
    }

    // Позиция считается один раз на показ: пузырёк не дёргается от ресайза
    // подложки и стоит ровно по центру иконки.
    function placeTip(item) {
      var rect = item.getBoundingClientRect();
      tip.classList.remove('is-left');
      tip.classList.add('is-on');
      tip.setAttribute('aria-hidden', 'false');
      var box = tip.getBoundingClientRect();
      var left = rect.right + 10;
      // не вылезаем за правый край окна — уходим влево от рельса
      if (left + box.width > window.innerWidth - 8) {
        left = Math.max(8, rect.left - box.width - 10);
        tip.classList.add('is-left');
      }
      var top = rect.top + (rect.height - box.height) / 2;
      top = Math.max(8, Math.min(top, window.innerHeight - box.height - 8));
      tip.style.left = Math.round(left) + 'px';
      tip.style.top = Math.round(top) + 'px';
    }

    function hideTip() {
      clearTimers();
      tipItem = null;
      tip.classList.remove('is-on');
      tip.setAttribute('aria-hidden', 'true');
    }

    function scheduleShow(item) {
      window.clearTimeout(hideTimer);
      hideTimer = 0;
      if (!isCollapsed()) return;
      if (tipItem === item) return;          // уже показан — не пересчитываем
      window.clearTimeout(showTimer);
      showTimer = window.setTimeout(function () {
        if (!isCollapsed()) return;
        tipItem = item;
        tip.textContent = item.getAttribute('data-tip') || '';
        placeTip(item);
      }, 90);
    }

    function scheduleHide(item) {
      window.clearTimeout(showTimer);
      showTimer = 0;
      if (tipItem !== item) return;
      window.clearTimeout(hideTimer);
      hideTimer = window.setTimeout(hideTip, 60);
    }

    dockNav.querySelectorAll('.cab-nav__item[data-tip]').forEach(function (item) {
      item.addEventListener('mouseenter', function () { scheduleShow(item); });
      item.addEventListener('mouseleave', function () { scheduleHide(item); });
      item.addEventListener('focus', function () {
        if (isCollapsed() && item.matches(':focus-visible')) scheduleShow(item);
      });
      item.addEventListener('blur', hideTip);
    });

    // Подсказка не должна «зависать» при скролле, ресайзе и раскрытии рельса.
    window.addEventListener('scroll', hideTip, { passive: true });
    window.addEventListener('resize', hideTip);
    if (toggle) {
      toggle.addEventListener('click', function () {
        var open = !dockNav.classList.contains('is-expanded');
        dockNav.classList.toggle('is-expanded', open);
        toggle.setAttribute('aria-expanded', String(open));
        hideTip();
      });
    }
  })();

  // Поле поиска в шапке: ⌘K/Ctrl+K — фокус; Enter — пока честный ответ.
  var search = document.getElementById('headSearch');
  if (search) {
    document.addEventListener('keydown', function (event) {
      if ((event.metaKey || event.ctrlKey) && (event.key === 'k' || event.key === 'K')) {
        event.preventDefault();
        search.focus();
      }
    });
    search.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        var toast = document.getElementById('toast');
        if (toast) {
          toast.textContent = 'Поиск в разработке — скоро появится.';
          toast.classList.add('is-on');
          window.setTimeout(function () { toast.classList.remove('is-on'); }, 2600);
        }
      }
    });
  }

  // Модальные окна загрузки файлов: расписание и учебный план.
  function initImportDialog(dialogId, openId) {
    var dialog = document.getElementById(dialogId);
    if (!dialog) return;
    var open = document.getElementById(openId);
    var file = dialog.querySelector('[data-import-file]');
    var name = dialog.querySelector('[data-import-name]');
    var nameDefault = name ? name.textContent : '';
    var confirmText = dialog.getAttribute('data-import-confirm') || 'Удалить файл?';

    if (open && typeof dialog.showModal === 'function') {
      open.addEventListener('click', function () { dialog.showModal(); });
    }
    dialog.querySelectorAll('[data-import-close]').forEach(function (button) {
      button.addEventListener('click', function () { dialog.close(); });
    });
    // Клик по подложке за пределами окна — закрыть.
    dialog.addEventListener('click', function (event) {
      if (event.target === dialog) dialog.close();
    });
    if (file && name) {
      var submit = dialog.querySelector('[data-import-submit]');
      var syncFiles = function () {
        var files = file.files ? Array.prototype.slice.call(file.files) : [];
        if (!files.length) {
          name.textContent = nameDefault;
        } else if (files.length === 1) {
          name.textContent = files[0].name;
        } else {
          name.textContent = 'выбрано ' + files.length + ': ' + files.map(function (item) {
            return item.name;
          }).join(', ');
        }
        dialog.classList.toggle('has-file', files.length > 0);
        if (submit) submit.disabled = files.length === 0;
      };
      file.addEventListener('change', syncFiles);
      syncFiles();
    }
    // Удаление загруженного файла: кнопка в списке шлёт форму на отдельный URL.
    dialog.querySelectorAll('[data-import-delete]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        if (!window.confirm(confirmText)) {
          event.preventDefault();
          return;
        }
        // Не отправляем выбранные, но ещё не загруженные файлы в DELETE-запрос.
        if (file) file.value = '';
      });
    });
  }
  initImportDialog('scheduleImport', 'scheduleImportOpen');
  initImportDialog('planImport', 'planImportOpen');

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

  // Чётные/нечётные недели: горизонтальный свайп по сетке дней — другая неделя.
  // Сервер отдаёт расписание для чётности выбранной недели (?week=N: N с чётным
  // ISO-номером → чётная), поэтому смена чётности = ссылка на соседнюю неделю.
  (function () {
    var panel = document.querySelector('[data-week-panel]');
    if (!panel) return;
    var links = {
      odd: panel.getAttribute('data-parity-odd') || '',
      even: panel.getAttribute('data-parity-even') || ''
    };
    var current = panel.getAttribute('data-parity');
    if (!links.odd || !links.even) return;

    var dashboard = document.querySelector('.sched-panel');
    if (!dashboard) return;
    var grid = dashboard.querySelector('.sched-table');
    var dragOffset = 0;
    var pending = null;
    var busy = false;
    var wheelLock = false;
    var wheelTimer = 0;
    var THRESHOLD = 72; // px: короче — это обычный клик по ячейке

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    function applyOffset() {
      if (!grid) return;
      grid.style.transform = dragOffset ? 'translateX(' + dragOffset + 'px)' : '';
      grid.style.opacity = dragOffset ? String(Math.max(0.55, 1 - Math.abs(dragOffset) / 320)) : '';
    }

    function release(pointerId) {
      if (pointerId !== undefined && dashboard.hasPointerCapture && dashboard.hasPointerCapture(pointerId)) {
        dashboard.releasePointerCapture(pointerId);
      }
      pending = null;
      dashboard.classList.remove('is-week-dragging');
    }

    function setBusy() {
      busy = true;
      dashboard.classList.add('sched-is-loading');
    }

    // Смена недели без перезагрузки: тянем HTML и меняем только сетку и шапку недели.
    function goTo(parity) {
      if (busy || parity === current || !links[parity]) return;
      setBusy();
      window.fetch(links[parity], { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
        .then(function (response) {
          if (!response.ok) throw new Error('HTTP ' + response.status);
          return response.text();
        })
        .then(function (html) {
          var doc = new window.DOMParser().parseFromString(html, 'text/html');
          var fresh = doc.querySelector('.sched-panel');
          var freshPanel = doc.querySelector('[data-week-panel]');
          if (!fresh) {
            // У новой недели занятий нет — отдаём решение серверу.
            window.location.href = links[parity];
            return;
          }
          dashboard.innerHTML = fresh.innerHTML;
          dashboard.className = fresh.className + ' sched-in-' + (parity === 'even' ? 'next' : 'prev');
          if (freshPanel) panel.replaceWith(freshPanel);
          current = parity;
          dragOffset = 0;
          if (window.history && window.history.replaceState) {
            window.history.replaceState(null, '', links[parity]);
          }
          document.dispatchEvent(new window.CustomEvent('schedule:week', { detail: { parity: parity } }));
        })
        .catch(function () {
          window.location.href = links[parity];
        })
        .finally(function () {
          busy = false;
          dashboard.classList.remove('sched-is-loading');
        });
    }

    // Свайп и «перенос» сетки мышью: pointer events покрывают и тач, и трекпад.
    dashboard.addEventListener('pointerdown', function (event) {
      if (busy || event.button !== 0 || pending) return;
      // Внутри таблицы не мешаем выделять текст: свайп включается только
      // по пустым местам/шапке (текст там не нужен).
      pending = { x: event.clientX, y: event.clientY, id: event.pointerId, armed: false };
    });

    dashboard.addEventListener('pointermove', function (event) {
      if (!pending || event.pointerId !== pending.id) return;
      var dx = event.clientX - pending.x;
      var dy = event.clientY - pending.y;
      if (!pending.armed && Math.abs(dx) > 14 && Math.abs(dx) > Math.abs(dy) * 1.6) {
        pending.armed = true;
        dashboard.classList.add('is-week-dragging');
        if (dashboard.setPointerCapture) dashboard.setPointerCapture(event.pointerId);
      }
      if (!pending.armed) return;
      dragOffset = clamp(dx, -160, 160);
      applyOffset();
    });

    function finish(event) {
      if (!pending) return;
      var armed = pending.armed;
      var dx = event.clientX - pending.x;
      release(pending.id);
      if (!armed) return;
      var moved = Math.abs(dx) >= THRESHOLD;
      var next = moved ? (dx < 0 ? 'odd' : 'even') : '';
      dragOffset = 0;
      applyOffset();
      if (next && next !== current) {
        goTo(next);
      }
    }

    dashboard.addEventListener('pointerup', finish);
    dashboard.addEventListener('pointercancel', function (event) {
      release(pending ? pending.id : event.pointerId);
      dragOffset = 0;
      applyOffset();
    });

    // Горизонтальный трекпад по сетке: показать следующую неделю той же чётности
    // нельзя (week=odd|even — фиксированная чётность), поэтому скролл недель
    // переключает чётность; повторно на той же — переходит на неделю +2/+1.
    dashboard.addEventListener('wheel', function (event) {
      if (busy || !event.target.closest('[data-week-grid]')) return;
      var dominant = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : 0;
      if (Math.abs(dominant) < 30 || wheelLock) return;
      wheelLock = true;
      window.clearTimeout(wheelTimer);
      wheelTimer = window.setTimeout(function () { wheelLock = false; }, 550);
      var next = dominant > 0 ? 'odd' : 'even';
      if (next === current) {
        var arrow = panel.querySelector('[data-week-arrow="' + (dominant > 0 ? 'prev' : 'next') + '"]');
        if (arrow) window.location.href = arrow.getAttribute('href');
        return;
      }
      goTo(next);
    }, { passive: true });
  })();
})();
