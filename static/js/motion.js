/* Появление панелей при загрузке: класс js-motion ставится синхронно в <head>,
   чтобы до первого кадра панели уже стояли на паузе в стартовом состоянии
   (opacity 0) и контент не мигнул. Без JS класса нет — всё видно сразу.

   1) раздаёт панелям --mi для stagger 40 мс и запускает анимацию (motion-go);
   2) когда появление закончилось, снимает классы: CSS-анимации не остаются
      висеть (document.getAnimations() возвращается к нулю, а transform не
      держит containing block для position: fixed внутри панели);
   3) prefers-reduced-motion и режим «скрыть изображения и анимации»
      (localStorage bgitu-a11y.im) — класс не ставится вообще. */
(() => {
  const root = document.documentElement;
  const STEP = 40;      /* мс между панелями */
  const DUR = 320;      /* --dur-3 */
  const MAX = 12;       /* дальше 480 мс очередь не растягиваем */
  const SAFETY = 2600;  /* страховка: контент покажется, даже если что-то упало */

  let reduced = false;
  try { reduced = matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}
  let imagesOff = false;
  try { imagesOff = JSON.parse(localStorage.getItem('bgitu-a11y') || '{}').im === '1'; } catch (e) {}
  if (reduced || imagesOff) return;

  root.classList.add('js-motion');

  let timer = setTimeout(() => root.classList.remove('js-motion'), SAFETY);

  const start = () => {
    const panels = Array.prototype.slice.call(document.querySelectorAll('.panel'));
    panels.forEach((el, i) => el.style.setProperty('--mi', String(Math.min(i, MAX))));
    root.classList.add('motion-go');

    const last = panels.length ? Math.min(panels.length - 1, MAX) : 0;
    clearTimeout(timer);
    timer = setTimeout(() => {
      root.classList.remove('motion-go');
      root.classList.remove('js-motion');
    }, last * STEP + DUR + 160);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
