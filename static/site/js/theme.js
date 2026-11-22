/* тема до первой отрисовки: ?theme= (для макетов) → сохранённый выбор → «Бумага» по умолчанию */
(() => { const r = document.documentElement, q = new URLSearchParams(location.search);
  let t = q.get('theme'); try { t = t || localStorage.getItem('bgitu-theme'); } catch (e) {}
  if (['paper', 'surik', 'latun'].includes(t)) r.dataset.theme = t;
  if (q.has('shot')) r.classList.add('shot'); })();
