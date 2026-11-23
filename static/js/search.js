/* Поиск по сайту и кабинету — диалог в стиле макета (Ctrl/⌘ + K).
   Индекс приходит с сервера (window.BGITU_INDEX) и дополняется элементами
   текущей страницы с data-s — как в исходном макете. */
(() => {
  const dlg = document.getElementById('search');
  const input = document.getElementById('q');
  const list = document.getElementById('results');
  if (!dlg || !input || !list) return;

  const dataEl = document.getElementById('bgitu-index');
  try {
    window.BGITU_INDEX = dataEl ? JSON.parse(dataEl.textContent) : [];
  } catch (error) {
    window.BGITU_INDEX = [];
  }

  const norm = (value) => value.toLowerCase().replace(/ё/g, 'е');
  const SUGGEST_URL = '/api/search/suggest/';
  const AI_MIN = 2;
  let index = null;
  let shown = [];
  let active = 0;
  let aiItems = null;
  let aiSeq = 0;
  let aiTimer = 0;
  let aiController = null;

  const pageItems = () => Array.from(document.querySelectorAll('[data-s]')).map((el) => {
    const title = (el.dataset.t || el.textContent).replace(/\s+/g, ' ').trim();
    const href = el.getAttribute('href');
    return {
      title,
      cat: el.dataset.s,
      soon: el.hasAttribute('data-soon'),
      href: href && href !== '#' ? href : '#',
      hay: norm([title, el.dataset.k].filter(Boolean).join(' ')),
    };
  });

  const build = () => {
    const server = Array.isArray(window.BGITU_INDEX) ? window.BGITU_INDEX : [];
    const items = server.map((item) => ({
      title: item.title,
      cat: item.category,
      soon: false,
      href: item.url,
      hay: norm([item.title, item.keywords].filter(Boolean).join(' ')),
    }));
    const seen = new Set(items.map((item) => item.href + '|' + item.title));
    pageItems().forEach((item) => {
      const key = item.href + '|' + item.title;
      if (!seen.has(key)) {
        seen.add(key);
        items.push(item);
      }
    });
    return items;
  };

  const setActive = (n) => {
    if (!shown.length) return;
    active = (n + shown.length) % shown.length;
    list.querySelectorAll('.search__item').forEach((li, i) => {
      li.setAttribute('aria-selected', String(i === active));
    });
    const current = document.getElementById('r' + active);
    input.setAttribute('aria-activedescendant', 'r' + active);
    if (current) current.scrollIntoView({ block: 'nearest' });
  };

  const render = (override) => {
    const words = norm(input.value).split(/\s+/).filter(Boolean);
    if (Array.isArray(override)) {
      shown = override;
    } else if (words.length) {
      shown = index
        .filter((item) => words.every((word) => item.hay.includes(word)))
        .sort((a, b) => (norm(a.title).indexOf(words[0]) + 1 || 99) - (norm(b.title).indexOf(words[0]) + 1 || 99))
        .slice(0, 10);
    } else {
      shown = index.filter((item) => item.cat === 'Раздел' || item.cat === 'Институт' || item.cat === 'Кабинет').slice(0, 10);
    }

    list.replaceChildren();
    if (!shown.length) {
      const li = document.createElement('li');
      li.className = 'search__empty';
      li.setAttribute('role', 'presentation');
      li.innerHTML = 'Ничего не найдено. Позвоните в приёмную комиссию: <a href="tel:+748****9912">+7 (4832) 64-99-12</a>';
      list.append(li);
      return input.removeAttribute('aria-activedescendant');
    }

    const pattern = words.length
      ? new RegExp('(' + words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi')
      : null;

    shown.forEach((item, n) => {
      const li = document.createElement('li');
      li.className = 'search__item';
      li.id = 'r' + n;
      li.dataset.n = String(n);
      li.setAttribute('role', 'option');
      const title = document.createElement('span');
      (pattern ? item.title.split(pattern) : [item.title]).forEach((part, i) => {
        if (i % 2) {
          const mark = document.createElement('mark');
          mark.textContent = part;
          title.append(mark);
        } else {
          title.append(part);
        }
      });
      const cat = document.createElement('span');
      cat.className = 'search__cat mono';
      cat.textContent = item.cat;
      if (item.why) li.title = item.why;
      li.append(title, cat);
      list.append(li);
    });
    setActive(0);
  };

  const go = (item) => {
    dlg.close();
    if (item.soon) return;
    if (item.href && item.href[0] === '#') {
      const target = document.querySelector(item.href);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (item.href) window.location.href = item.href;
  };

  const aiItemsFrom = (items) => {
    const byKey = new Map(index.map((item) => [item.href + '|' + item.title, item]));
    return items.map((item) => {
      const base = byKey.get(item.url + '|' + item.title);
      return {
        title: item.title,
        cat: (base && base.cat) || 'Раздел',
        soon: !!(base && base.soon),
        href: item.url,
        why: item.why || '',
        hay: base ? base.hay : norm(item.title),
      };
    });
  };

  const cancelSuggest = () => {
    clearTimeout(aiTimer);
    if (aiController) {
      aiController.abort();
      aiController = null;
    }
  };

  const askSuggest = () => {
    const query = input.value.trim();
    if (!dlg.open || query.length < AI_MIN) return;
    const seq = ++aiSeq;
    const controller = new AbortController();
    aiController = controller;
    const timer = setTimeout(() => controller.abort(), 4000);
    fetch(SUGGEST_URL + '?q=' + encodeURIComponent(query), {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data || seq !== aiSeq || !dlg.open || input.value.trim() !== query) return;
        const items = Array.isArray(data.items) ? data.items : [];
        if (!items.length) return;
        aiItems = aiItemsFrom(items);
        render(aiItems);
      })
      .catch(() => {})
      .finally(() => {
        clearTimeout(timer);
        if (aiController === controller) aiController = null;
      });
  };

  const onInput = () => {
    const query = input.value.trim();
    cancelSuggest();
    aiSeq += 1;
    aiItems = null;
    render();
    if (query.length >= AI_MIN) aiTimer = setTimeout(askSuggest, 180);
  };

  const openSearch = () => {
    index = index || build();
    cancelSuggest();
    aiSeq += 1;
    aiItems = null;
    dlg.showModal();
    input.value = '';
    render();
    input.focus();
  };

  document.querySelectorAll('[data-search-open]').forEach((button) => {
    button.addEventListener('click', openSearch);
  });
  const kbd = document.querySelector('[data-kbd]');
  if (kbd) kbd.textContent = /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘ K' : 'Ctrl K';

  addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.code === 'KeyK') {
      event.preventDefault();
      if (dlg.open) dlg.close(); else openSearch();
    }
  });
  dlg.addEventListener('click', (event) => { if (event.target === dlg) dlg.close(); });
  dlg.addEventListener('close', () => {
    cancelSuggest();
    aiSeq += 1;
    aiItems = null;
  });
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', (event) => {
    if (!shown.length) return;
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive(active + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(active - 1); }
    else if (event.key === 'Enter') { event.preventDefault(); go(shown[active]); }
  });
  list.addEventListener('pointermove', (event) => {
    const li = event.target.closest('.search__item');
    if (li && Number(li.dataset.n) !== active) setActive(Number(li.dataset.n));
  });
  list.addEventListener('click', (event) => {
    const li = event.target.closest('.search__item');
    if (li) go(shown[Number(li.dataset.n)]);
  });
})();
