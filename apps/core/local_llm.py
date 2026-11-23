"""Локальная LLM для подсказок поиска — llama.cpp, без сети и внешних демонов.

Модель лежит в ``<BASE_DIR>/models/*.gguf`` и загружается лениво при первом
обращении к :func:`ask` (импорт модуля модель НЕ грузит). Любая ошибка —
отсутствие файла, сбой загрузки, таймаут генерации — возвращает ``None``:
вызывающий код обязан уметь работать без модели.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL_FILE = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
N_CTX = 2048
N_THREADS = 8
MAX_TOKENS = 120

# Одна модель на процесс: генерация не потокобезопасна.
_lock = threading.Lock()
_llm = None
_load_error: str | None = None
_last_latency_ms: int | None = None

_SYSTEM = 'Ты — краткий помощник поиска по сайту университета. Отвечай по-русски, без вступлений.'

# Аварийный выключатель: BGITU_LLM=0 — поиск работает без модели (быстрый fallback).
_DISABLED = os.environ.get('BGITU_LLM', '1').lower() in {'0', 'off', 'false', 'no'}


def model_path() -> Path:
    """Путь к GGUF-файлу внутри проекта (не абсолютный, чтобы работало на любом хосте)."""
    return Path(settings.BASE_DIR) / 'models' / MODEL_FILE


def _load_locked():
    """Загружает модель. Вызывать только под ``_lock``."""
    global _llm, _load_error
    if _llm is not None:
        return _llm
    path = model_path()
    if not path.is_file():
        _load_error = f'файл модели не найден: {path}'
        logger.warning('LLM: %s', _load_error)
        return None
    try:
        from llama_cpp import Llama
    except Exception as exc:  # noqa: BLE001 — библиотека необязательна
        _load_error = f'llama-cpp-python не установлен: {exc}'
        logger.warning('LLM: %s', _load_error)
        return None
    try:
        started = time.perf_counter()
        _llm = Llama(
            model_path=str(path),
            n_ctx=N_CTX,
            n_threads=N_THREADS,
            n_gpu_layers=-1,  # все слои на Metal (M4); на CPU-хостах llama.cpp сам откатится
            verbose=False,
        )
        _load_error = None
        logger.info('LLM: модель загружена за %.2f с (%s)', time.perf_counter() - started, path.name)
        return _llm
    except Exception as exc:  # noqa: BLE001 — битый файл, нехватка памяти и т.п.
        _load_error = f'ошибка загрузки модели: {exc}'
        logger.warning('LLM: %s', _load_error)
        return None


def ask(prompt: str, max_tokens: int = MAX_TOKENS, timeout: float | None = None) -> str | None:
    """Возвращает ответ модели или ``None``, если модель недоступна/не успела.

    ``timeout`` — бюджет ожидания в секундах; генерация идёт в фоновом потоке,
    поэтому по истечении бюджета сразу отдаём ``None`` (поиск переходит на
    локальный fallback), а модель догенерирует в фоне.
    """
    global _last_latency_ms
    if _DISABLED or not prompt or not prompt.strip():
        return None
    if not _lock.acquire(blocking=False):
        return None  # модель занята — не копим очередь

    box: dict[str, object] = {}

    def run() -> None:
        try:
            started = time.perf_counter()
            llm = _load_locked()
            if llm is None:
                return
            if timeout is not None and time.perf_counter() - started > timeout:
                return
            result = llm.create_chat_completion(
                messages=[
                    {'role': 'system', 'content': _SYSTEM},
                    {'role': 'user', 'content': prompt},
                ],
                max_tokens=max(1, min(int(max_tokens), MAX_TOKENS)),
                temperature=0.2,
                top_p=0.9,
                repeat_penalty=1.05,
            )
            text = (result['choices'][0]['message']['content'] or '').strip()
            elapsed = time.perf_counter() - started
            _last_latency_ms = int(elapsed * 1000)
            logger.info('LLM: ответ за %.2f с, %d симв.', elapsed, len(text))
            box['text'] = text or None
        except Exception as exc:  # noqa: BLE001 — страница не должна падать
            logger.warning('LLM: ошибка генерации: %s', exc)
            box['error'] = str(exc)
        finally:
            _lock.release()

    worker = threading.Thread(target=run, name='bgitu-llm', daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.warning('LLM: таймаут %.1f с — отдаю fallback', timeout or 0)
        return None
    text = box.get('text')
    return text if isinstance(text, str) and text else None


def warm(timeout: float = 20.0) -> bool:
    """Загружает модель заранее (диагностика/прогрев), без пользовательского запроса."""
    return ask('Ответь одним словом: готов?', max_tokens=4, timeout=timeout) is not None


def _shutdown() -> None:
    """Закрывает модель до выгрузки Metal-контекста — иначе llama.cpp падает в assert."""
    global _llm
    if not _lock.acquire(blocking=False):
        return
    try:
        llm, _llm = _llm, None
        if llm is not None:
            try:
                llm.close()
            except Exception:  # noqa: BLE001 — на выходе уже неважно
                pass
    finally:
        _lock.release()


atexit.register(_shutdown)


def status(probe: bool = True, timeout: float = 20.0) -> dict:
    """Состояние рантайма для /api/ai/status/ (только staff)."""
    path = model_path()
    info: dict[str, object] = {
        'runtime': 'llama-cpp-python',
        'model': MODEL_FILE,
        'model_path': str(path),
        'model_exists': path.is_file(),
        'model_ok': _llm is not None or (path.is_file() and _load_error is None and not _DISABLED),
        'loaded': _llm is not None,
        'latency_ms': _last_latency_ms,
    }
    if not path.is_file():
        info['error'] = _load_error or 'файл модели не найден'
        return info
    if probe:
        started = time.perf_counter()
        answer = ask('Ответь одним словом: работает?', max_tokens=8, timeout=timeout)
        info['latency_ms'] = int((time.perf_counter() - started) * 1000)
        info['model_ok'] = answer is not None
        info['loaded'] = _llm is not None
        info['answer'] = answer
    if _load_error:
        info['error'] = _load_error
    return info
