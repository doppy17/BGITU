/* Кастомный выпадающий список в стиле сайта.
   Прогрессивное улучшение: любой <select class="input|select"> превращается
   в кнопку + тематическую панель; нативный select остаётся для отправки формы.
   Поиск включается для списков с data-searchable (на самом select или на
   форме-родителе) и автоматически для длинных списков (больше 8 опций):
   первым элементом панели появляется поле «поиск…» с живой фильтрацией. */
(() => {
  const TARGETS = 'select.input, select.select';
  const SEARCH_FROM = 8; /* больше этого числа опций — поиск включается сам */
  let uid = 0;
  /* Закрыватели открытых панелей: один слушатель клика на документ вместо
     своего слушателя на каждый селект. После AJAX-подмены блока селекты
     строятся заново, и старые замыкания не копятся. */
  const closers = new Map();
  const CHEVRON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';

  const wantsSearch = (select) => {
    const own = select.getAttribute('data-searchable');
    if (own !== null) return own !== 'false';
    const form = select.closest('form');
    const inherited = form ? form.getAttribute('data-searchable') : null;
    if (inherited !== null) return inherited !== 'false';
    return select.options.length > SEARCH_FROM;
  };

  function build(select) {
    if (select.dataset.selReady) return;
    select.dataset.selReady = '1';

    const number = ++uid;
    const searchable = wantsSearch(select);
    const panelId = 'sel-panel-' + number;

    const wrap = document.createElement('div');
    wrap.className = 'sel';
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add('sel__native');
    select.tabIndex = -1;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'sel__btn';
    btn.setAttribute('role', 'combobox');
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-controls', panelId);
    if (select.id) btn.id = select.id + '-btn';

    const val = document.createElement('span');
    val.className = 'sel__val';
    const chev = document.createElement('span');
    chev.className = 'sel__chev';
    chev.innerHTML = CHEVRON;
    btn.append(val, chev);

    const panel = document.createElement('div');
    panel.className = 'sel__panel';
    panel.id = panelId;
    panel.hidden = true;

    let search = null;
    let list = panel;

    if (searchable) {
      search = document.createElement('input');
      search.type = 'text';
      search.className = 'sel__search';
      search.placeholder = 'поиск…';
      search.setAttribute('aria-label', 'Поиск по списку');
      search.setAttribute('autocomplete', 'off');
      search.setAttribute('spellcheck', 'false');
      panel.appendChild(search);

      list = document.createElement('div');
      list.className = 'sel__list';
      list.id = 'sel-list-' + number;
      list.setAttribute('role', 'listbox');
      const label = select.getAttribute('aria-label');
      if (label) list.setAttribute('aria-label', label);
      search.setAttribute('aria-controls', list.id);
      panel.appendChild(list);
    } else {
      panel.setAttribute('role', 'listbox');
    }

    const options = () => Array.from(list.querySelectorAll('.sel__opt'));

    /* Прокрутка только внутри панели: scrollIntoView на option может
       дёрнуть всю страницу, если панель не влезает во вьюпорт. */
    const reveal = (item) => {
      const panelBox = panel.getBoundingClientRect();
      const itemBox = item.getBoundingClientRect();
      if (!itemBox.height) return;
      const cover = search ? search.offsetHeight + 8 : 6;
      if (itemBox.top < panelBox.top + cover) {
        panel.scrollTop -= panelBox.top + cover - itemBox.top + 4;
      } else if (itemBox.bottom > panelBox.bottom) {
        panel.scrollTop += itemBox.bottom - panelBox.bottom + 4;
      }
    };

    const mark = (item) => {
      list.querySelectorAll('.sel__opt').forEach((option) => {
        option.classList.toggle('is-marked', option === item);
      });
      if (!search) return;
      if (item) {
        search.setAttribute('aria-activedescendant', item.id);
        reveal(item);
      } else {
        search.removeAttribute('aria-activedescendant');
      }
    };

    /* Перерисовка списка с учётом введённого запроса. */
    const render = () => {
      const query = search ? search.value.trim().toLowerCase() : '';
      const selected = select.selectedIndex;
      list.replaceChildren();
      let first = null;
      let selectedItem = null;
      Array.from(select.options).forEach((option, index) => {
        const text = option.textContent.trim();
        if (query && !text.toLowerCase().includes(query)) return;
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'sel__opt' + (option.selected ? ' is-active' : '');
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', option.selected ? 'true' : 'false');
        item.id = 'sel-opt-' + number + '-' + index;
        item.dataset.index = String(index);
        item.textContent = text;
        item.addEventListener('click', () => choose(index));
        list.appendChild(item);
        if (!first) first = item;
        if (index === selected) selectedItem = item;
      });
      if (!first) {
        const empty = document.createElement('div');
        empty.className = 'sel__empty';
        empty.textContent = 'ничего не найдено';
        list.appendChild(empty);
        mark(null);
      } else if (search) {
        mark(selectedItem || first);
      }
    };

    const sync = () => {
      const current = select.options[select.selectedIndex];
      val.textContent = current ? current.textContent.trim() : '';
      render();
    };

    const open = () => {
      if (select.disabled) return;
      panel.hidden = false;
      wrap.classList.add('is-open');
      btn.setAttribute('aria-expanded', 'true');
      if (search) {
        search.value = '';
        render();
        search.focus({ preventScroll: true });
      } else {
        const active = panel.querySelector('.is-active') || panel.firstElementChild;
        if (active) active.focus({ preventScroll: true });
      }
    };

    const close = (focusBtn) => {
      panel.hidden = true;
      wrap.classList.remove('is-open');
      btn.setAttribute('aria-expanded', 'false');
      if (search) search.value = '';
      if (focusBtn) btn.focus({ preventScroll: true });
    };

    const choose = (index) => {
      if (select.selectedIndex !== index) {
        select.selectedIndex = index;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      sync();
      close(true);
    };

    btn.addEventListener('click', () => (panel.hidden ? open() : close()));
    btn.addEventListener('keydown', (event) => {
      if (['ArrowDown', 'Enter', ' '].includes(event.key)) {
        event.preventDefault();
        open();
      }
    });

    panel.addEventListener('keydown', (event) => {
      const items = options();
      const index = items.indexOf(document.activeElement);
      const marked = list.querySelector('.sel__opt.is-marked');

      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (search) {
          if (!items.length) return;
          mark(items[items.indexOf(marked) + 1] || items[0]);
        } else {
          const next = items[Math.min(items.length - 1, index + 1)] || items[0];
          if (next) next.focus();
        }
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        if (search) {
          if (!items.length) return;
          const at = items.indexOf(marked);
          mark(items[at <= 0 ? items.length - 1 : at - 1]);
        } else {
          const prev = items[Math.max(0, index - 1)] || items[0];
          if (prev) prev.focus();
        }
      } else if (event.key === 'Home' && !search) {
        event.preventDefault();
        if (items[0]) items[0].focus();
      } else if (event.key === 'End' && !search) {
        event.preventDefault();
        if (items.length) items[items.length - 1].focus();
      } else if (event.key === 'Enter') {
        if (!search) return; /* у опции-кнопки Enter обрабатывает браузер */
        event.preventDefault();
        if (marked) choose(Number(marked.dataset.index));
      } else if (event.key === 'Escape') {
        event.preventDefault();
        close(true);
      } else if (event.key === 'Tab') {
        close();
      }
      /* Backspace и обычный ввод в поиске не перехватываем. */
    });

    if (search) search.addEventListener('input', render);

    /* Клик вне панели закрывает её. Слушатель один на документ (closers), а не
       свой на каждый селект: после AJAX-подмены блоков селекты строятся заново
       и старые замыкания не накапливаются. */
    closers.set(wrap, close);
    select.addEventListener('change', sync);

    sync();
    wrap.append(btn, panel);
  }

  document.addEventListener('click', (event) => {
    closers.forEach((close, wrap) => {
      // Отсоединённую панель (блок заменён AJAX-ом) закрывать незачем.
      if (wrap.isConnected && !wrap.contains(event.target)) close();
    });
  });

  const init = () => document.querySelectorAll(TARGETS).forEach(build);
  /* Публичный API: после AJAX-подмены блока новые <select> нужно построить
     заново — schedule-ajax.js вызывает initSelects(). Повторный вызов для уже
     готовых селектов безопасен: build() выходит по data-sel-ready. */
  window.initSelects = init;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
