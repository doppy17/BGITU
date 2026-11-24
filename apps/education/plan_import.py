"""Разбор учебных планов из файлов .xls/.xlsx/.docx/.pdf.

План читается как таблица: строки — дисциплины, колонки — семестры 1–8.
Поддерживаются три формы таблицы:

* широкая — колонки «1 семестр», «2 семестр», …; отметка в колонке = дисциплина
  читается в этом семестре;
* длинная — колонки «Дисциплина» + «Семестр» (одна строка = один семестр);
* диапазон — «Дисциплина» + «Семестр с» / «Семестр по».

PDF разбирается по координатам слов (`pdftotext -bbox-layout`): шапка даёт
x-координаты семестровых колонок, а дисциплина определяется по коду Б1/Б2/Б3/ФТД
и числам в этих колонках.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import zipfile
from xml.etree import ElementTree

PLAN_EXTENSIONS = ('.xls', '.xlsx', '.docx', '.pdf')
MAX_SEMESTER = 8

DIRECTION_CODES = {
    '09.03.02': 'ist',
}
ABBREVIATION_CODES = {
    'ИСТ': 'ist',
    'ИВТ': 'ivt',
    'ПИ': 'pi',
    'ПРИ': 'pri',
    'ИЭИ': 'iei',
    'IST': 'ist',
    'IVT': 'ivt',
    'PRI': 'pri',
}

_CODE_RE = re.compile(
    r'^(?:Б\d+(?:\.[А-ЯЁA-Z]{1,3})?(?:\.\d+)?|ФТД(?:\.\d+)?)[\s.:]*',
    re.IGNORECASE,
)
_DIRECTION_RE = re.compile(r'\b(\d{2}\s*\.\s*\d{2}\s*\.\s*\d{2})\b')
_CODE_ONLY_RE = re.compile(
    r'^(?:Б\d+(?:\.[А-ЯЁA-Z]{1,3})?(?:\.\d+)?|ФТД(?:\.\d+)?)$',
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(
    r'^(?:итого|всего|блок|факультатив|б\d\s*\.|фтд\s*\.|в\s+том\s+числе|'
    r'обязательная\s+часть|часть,?\s+формируемая|дисциплины\s+по\s+выбору)',
    re.IGNORECASE,
)
_SEM_LABEL_RE = re.compile(r'(\d{1,2})\s*(?:-?\s*семестр|сем\.?)', re.IGNORECASE)
_SEM_LABEL_REV_RE = re.compile(r'семестр\s*(\d{1,2})', re.IGNORECASE)


def clean_text(value):
    if value is None:
        return ''
    text = str(value).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def strip_code(name):
    """Убрать шифр дисциплины: «Б1.О.1 Иностранный язык» → «Иностранный язык»."""
    text = clean_text(name)
    stripped = _CODE_RE.sub('', text, count=1).strip(' .:;-')
    if stripped and len(stripped) >= 3:
        return stripped
    return text


def is_total_row(name):
    return bool(_TOTAL_RE.match(clean_text(name)))


def semester_of_label(value):
    """«1 семестр», «Семестр 2», «3 сем.» → номер семестра."""
    text = clean_text(value).lower().replace('ё', 'е')
    if 'семестр' not in text and 'сем.' not in text:
        return None
    match = _SEM_LABEL_RE.search(text) or _SEM_LABEL_REV_RE.search(text)
    if not match:
        return None
    number = int(match.group(1))
    return number if 1 <= number <= MAX_SEMESTER else None


def cell_has_mark(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = clean_text(value)
    return bool(text) and text not in ('-', '—', '–', '0', 'нет', 'nan')


def segment_semesters(semesters):
    """[1, 2, 4] → [(1, 2), (4, 4)]."""
    values = sorted({int(item) for item in semesters if 1 <= int(item) <= MAX_SEMESTER})
    segments = []
    for number in values:
        if segments and number == segments[-1][1] + 1:
            segments[-1] = (segments[-1][0], number)
        else:
            segments.append((number, number))
    return segments


# --------------------------------------------------------------------------- #
# Таблицы (xls/xlsx/docx)
# --------------------------------------------------------------------------- #

def _rows_from_xlsx(path):
    from openpyxl import load_workbook

    book = load_workbook(path, data_only=True, read_only=True)
    try:
        for sheet in book.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            if rows:
                yield rows
    finally:
        book.close()


def _rows_from_xls(path):
    import xlrd

    book = xlrd.open_workbook(str(path))
    for sheet in book.sheets():
        rows = []
        for index in range(sheet.nrows):
            rows.append([sheet.cell_value(index, col) for col in range(sheet.ncols)])
        if rows:
            yield rows


def _rows_from_docx(path):
    """Таблицы docx читаем стандартной библиотекой: docx — это zip + XML."""
    namespace = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        xml = archive.read('word/document.xml')
    root = ElementTree.fromstring(xml)
    for table in root.iter(f'{{{namespace["w"]}}}tbl'):
        rows = []
        for tr in table.findall(f'{{{namespace["w"]}}}tr'):
            cells = []
            for tc in tr.findall(f'{{{namespace["w"]}}}tc'):
                cells.append(''.join(node.text or '' for node in tc.iter(f'{{{namespace["w"]}}}t')))
            if cells:
                rows.append(cells)
        if rows:
            yield rows


def _header_map(row):
    """Колонки семестров в шапке: {index: semester}."""
    mapping = {}
    for index, value in enumerate(row):
        semester = semester_of_label(value)
        if semester and semester not in mapping.values():
            mapping[index] = semester
    return mapping


def _name_column(row, semester_cols):
    """Колонка с названием дисциплины: по заголовку, иначе слева от семестров."""
    priorities = ('наименован', 'дисциплин', 'предмет', 'модул')
    for word in priorities:
        for index, value in enumerate(row):
            if word in clean_text(value).lower():
                return index
    first_semester = min(semester_cols) if semester_cols else len(row)
    for index in range(first_semester):
        if clean_text(row[index]):
            return index
    return 0


def _row_name(row, name_col, semester_cols):
    """Название дисциплины; если колонка имени содержит только шифр — соседняя."""
    raw = clean_text(row[name_col]) if name_col < len(row) else ''
    name = strip_code(raw)
    if name and not _CODE_ONLY_RE.match(name):
        return name
    limit = min(semester_cols) if semester_cols else len(row)
    for index in range(name_col + 1, limit):
        candidate = strip_code(row[index] if index < len(row) else '')
        if candidate and len(candidate) > 2 and not is_total_row(candidate):
            return candidate
    return name


def _long_header(row):
    """Длинная форма: (колонка дисциплины, колонка семестра/с/по)."""
    name_col = semester_col = from_col = to_col = None
    for index, value in enumerate(row):
        text = clean_text(value).lower()
        if any(word in text for word in ('дисциплин', 'наименован', 'предмет')):
            name_col = index
        elif 'семестр' in text or text == 'сем.':
            if re.search(r'семестр\s*(?:с|от)\b', text):
                from_col = index
            elif re.search(r'семестр\s*(?:по|до)\b', text):
                to_col = index
            elif not re.search(r'\d', text):
                semester_col = index
    if name_col is None:
        return None
    if from_col is not None and to_col is not None:
        return {'name': name_col, 'from': from_col, 'to': to_col}
    if semester_col is not None:
        return {'name': name_col, 'semester': semester_col}
    return None


def _parse_long_row(row, header):
    name = strip_code(row[header['name']] if header['name'] < len(row) else '')
    if not name or is_total_row(name):
        return None
    if 'semester' in header:
        raw = clean_text(row[header['semester']]) if header['semester'] < len(row) else ''
        numbers = [int(item) for item in re.findall(r'\d{1,2}', raw)]
        numbers = [item for item in numbers if 1 <= item <= MAX_SEMESTER]
        return (name, numbers) if numbers else None
    start = clean_text(row[header['from']]) if header['from'] < len(row) else ''
    end = clean_text(row[header['to']]) if header['to'] < len(row) else ''
    try:
        start_number = int(re.search(r'\d{1,2}', start).group())
        end_number = int(re.search(r'\d{1,2}', end).group()) if end else start_number
    except (AttributeError, ValueError):
        return None
    if not (1 <= start_number <= MAX_SEMESTER):
        return None
    end_number = min(MAX_SEMESTER, max(start_number, end_number))
    return (name, list(range(start_number, end_number + 1)))


def entries_from_rows(rows):
    """Разобрать таблицу в список (дисциплина, [семестры])."""
    rows = [list(row) for row in rows if any(clean_text(cell) for cell in row)]
    for header_index, row in enumerate(rows):
        semester_cols = _header_map(row)
        if len(semester_cols) >= 3:
            name_col = _name_column(row, semester_cols)
            found = []
            current_name = ''
            for data_row in rows[header_index + 1:]:
                name = _row_name(data_row, name_col, semester_cols)
                marks = [
                    semester for index, semester in semester_cols.items()
                    if index < len(data_row) and cell_has_mark(data_row[index])
                ]
                if name and not is_total_row(name):
                    current_name = name
                elif not name and marks:
                    name = current_name
                if not name or is_total_row(name) or not marks:
                    continue
                found.append((name, marks))
            if found:
                return found

    for header_index, row in enumerate(rows):
        header = _long_header(row)
        if not header:
            continue
        found = []
        for data_row in rows[header_index + 1:]:
            parsed = _parse_long_row(data_row, header)
            if parsed and parsed[1]:
                found.append(parsed)
        if found:
            return found
    return []


def entries_from_table_sources(path):
    extension = os.path.splitext(str(path))[1].lower()
    if extension == '.xlsx':
        sources = _rows_from_xlsx(path)
    elif extension == '.xls':
        sources = _rows_from_xls(path)
    elif extension == '.docx':
        sources = _rows_from_docx(path)
    else:
        raise ValueError(f'формат {extension} не является таблицей')
    for rows in sources:
        entries = entries_from_rows(rows)
        if entries:
            return entries
    return []


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

_PDF_CODE_RE = re.compile(r'^(?:Б\d+(?:\.[А-ЯЁA-Z]{1,3})?(?:\.\d+)?|ФТД(?:\.\d+)?)$')


def _pdftotext_candidates():
    """Кандидаты на pdftotext: настроенный путь, локальный poppler, PATH.

    Xpdf из MSYS (`/mingw64/bin/pdftotext`) не умеет `-bbox-layout`, поэтому
    явный путь poppler важнее того, что первым нашлось в PATH.
    """
    candidates = []
    configured = os.environ.get('PDFTOTEXT')
    if configured:
        candidates.append(configured)
    for pattern in (
        'D:/tools/poppler/*/Library/bin/pdftotext.exe',
        'D:/tools/poppler/*/bin/pdftotext.exe',
        '/usr/bin/pdftotext',
    ):
        candidates.extend(sorted(glob.glob(pattern), reverse=True))
    on_path = shutil.which('pdftotext')
    if on_path:
        candidates.append(on_path)
    return list(dict.fromkeys(candidates))


def _pdf_pages(path):
    """Страницы PDF → строки слов с координатами.

    `pdftotext -bbox-layout` группирует слова в свои «line»-блоки не по
    горизонтали (в Excel-выгрузке шапка и цифры семестров попадают в один
    блок), поэтому строки собираем сами по y-координате.
    """
    candidates = _pdftotext_candidates()
    if not candidates:
        raise ValueError('для разбора PDF нужен pdftotext (poppler)')
    result = None
    for executable in candidates:
        try:
            attempt = subprocess.run(
                [executable, '-bbox-layout', str(path), '-'],
                capture_output=True, check=False, timeout=120,
            )
        except (FileNotFoundError, OSError):
            continue
        if attempt.returncode == 0 and attempt.stdout.strip():
            result = attempt
            break
    if result is None:
        raise ValueError('не удалось прочитать PDF')
    root = ElementTree.fromstring(result.stdout.decode('utf-8', errors='replace'))
    pages = []
    for page in root.iter():
        if page.tag.split('}')[-1] != 'page':
            continue
        words = []
        for word in page.iter():
            if word.tag.split('}')[-1] != 'word':
                continue
            text = (word.text or '').strip()
            if not text:
                continue
            try:
                words.append({
                    'x0': float(word.attrib['xMin']),
                    'x1': float(word.attrib['xMax']),
                    'y0': float(word.attrib['yMin']),
                    'text': text,
                })
            except (KeyError, ValueError, TypeError):
                continue
        rows = []
        for word in sorted(words, key=lambda item: (item['y0'], item['x0'])):
            for row in rows[-4:]:
                if abs(row['y'] - word['y0']) <= 3.5:
                    row['words'].append(word)
                    break
            else:
                rows.append({'y': word['y0'], 'words': [word]})
        rows.sort(key=lambda item: item['y'])
        for row in rows:
            row['words'].sort(key=lambda item: item['x0'])
        if rows:
            pages.append(rows)
    return pages


def _semester_columns(rows):
    """x-координаты семестровых колонок по строке цифр 1..8 в шапке."""
    for row in rows:
        numbers = []
        for word in row['words']:
            text = word['text'].strip()
            if text.isdigit() and 1 <= int(text) <= MAX_SEMESTER:
                numbers.append(word)
        if len(numbers) != MAX_SEMESTER:
            continue
        if [int(item['text']) for item in numbers] != list(range(1, MAX_SEMESTER + 1)):
            continue
        span = numbers[-1]['x0'] - numbers[0]['x0']
        if span < 80:
            continue
        return {index + 1: word['x0'] for index, word in enumerate(numbers)}
    return {}


def _pdf_line_name(words, name_limit):
    texts = [word['text'] for word in words if word['x1'] <= name_limit]
    return clean_text(' '.join(texts))


def entries_from_pdf(path):
    pages = _pdf_pages(path)
    if not pages:
        return []
    found = []
    for rows in pages:
        columns = _semester_columns(rows)
        if not columns:
            continue
        first_x = min(columns.values())
        name_limit = first_x - 295
        bounds = [
            (semester, start - 6, start + 18)
            for semester, start in columns.items()
        ]
        current = None
        pending = ''
        for index, row in enumerate(rows):
            words = row['words']
            first = words[0]
            text = clean_text(' '.join(word['text'] for word in words))
            lowered = text.lower().replace('ё', 'е')
            starts_with_code = bool(_PDF_CODE_RE.match(first['text'])) and first['x0'] < 70
            next_starts_with_code = False
            next_name_count = 0
            if index + 1 < len(rows):
                next_words = rows[index + 1]['words']
                next_first = next_words[0]
                next_starts_with_code = (
                    bool(_PDF_CODE_RE.match(next_first['text'])) and next_first['x0'] < 70
                )
                if next_starts_with_code:
                    next_name_count = len([
                        word for word in next_words[1:]
                        if word['x1'] <= name_limit and not word['text'].isdigit()
                    ])

            is_semester_line = (
                first['x0'] < first_x - 40
                and first['text'].isdigit()
                and any(word['text'].lower().startswith('семестр') for word in words)
            )
            if is_semester_line:
                if current is not None:
                    number = int(first['text'])
                    if 1 <= number <= MAX_SEMESTER:
                        current['semesters'].add(number)
                continue

            if starts_with_code:
                name = _pdf_line_name(words[1:], name_limit)
                if pending:
                    name = f'{pending} {name}'.strip()
                    pending = ''
                current = {'name': name, 'semesters': set(), 'extra': 0}
                found.append(current)
                # Дисциплина в одну строку: семестр стоит числом в колонке.
                for word in words[1:]:
                    if not word['text'].isdigit():
                        continue
                    number = int(word['text'])
                    if not 1 <= number <= MAX_SEMESTER:
                        continue
                    matched = next(
                        (sem for sem, left, right in bounds if left <= word['x0'] <= right),
                        None,
                    )
                    if matched:
                        current['semesters'].add(matched)
                continue

            if any(marker in lowered for marker in (
                'название дисциплин', 'формы', 'контроля', 'общая трудоемкость',
                'распределение часов', 'закрепление дисциплины', 'всего', 'блок ',
                'обязательная часть', 'часть, формируемая', 'факультатив',
                'план учебного процесса', 'курс.работа', 'самостоятельная',
                'зачетных', 'аудиторных', 'контактной', 'практические',
                'лабораторные', 'лекции', 'знаний', 'семестр', 'дисциплин',
                'образовательных отношений', 'формируемая участниками',
            )):
                continue
            if current is None:
                continue

            # Строка-продолжение названия: берём только слова из колонки
            # дисциплин (числа и часы справа не трогаем).
            name_words = [
                word['text'] for word in words
                if word['x1'] <= name_limit and not word['text'].isdigit()
            ]
            name_part = clean_text(' '.join(name_words))
            if not name_part or is_total_row(name_part):
                continue
            if next_starts_with_code and next_name_count < 2 and not current.get('name'):
                current['name'] = name_part
            elif next_starts_with_code and next_name_count < 2:
                # Имя стоит ПЕРЕД шифром дисциплины (Excel режет ячейку).
                pending = f'{pending} {name_part}'.strip()
            elif current.get('name') and current.get('extra', 0) < 1:
                if name_part.lower() not in current['name'].lower():
                    current['name'] = f"{current['name']} {name_part}".strip()
                    current['extra'] = current.get('extra', 0) + 1
            elif pending and not current.get('name'):
                current['name'] = pending
                pending = ''

    result = []
    for item in found:
        name = strip_code(item['name'])
        if not name or is_total_row(name) or not item['semesters']:
            continue
        result.append((name, sorted(item['semesters'])))
    return result


# --------------------------------------------------------------------------- #
# Публичный API
# --------------------------------------------------------------------------- #

def parse_plan(path):
    """Прочитать файл плана → [(дисциплина, [семестры])]."""
    extension = os.path.splitext(str(path))[1].lower()
    if extension == '.pdf':
        return entries_from_pdf(path)
    if extension in ('.xls', '.xlsx', '.docx'):
        return entries_from_table_sources(path)
    raise ValueError(f'неподдерживаемый формат файла: {extension or "без расширения"}')


def entries_to_segments(entries):
    """[(дисциплина, [семестры])] → [(дисциплина, с, по)] без дублей.

    Одинаковые дисциплины внутри одного файла склеиваются, непрерывные
    семестры становятся одним отрезком.
    """
    merged = {}
    names = {}
    for name, semesters in entries:
        key = subject_key(name)
        if not key:
            continue
        display = clean_text(name)
        merged.setdefault(key, set()).update(semesters)
        if key not in names or len(display) > len(names[key]):
            names[key] = display
    segments = []
    for key, semesters in merged.items():
        for start, end in segment_semesters(semesters):
            segments.append((names[key], start, end))
    segments.sort(key=lambda item: (item[1], item[0].lower()))
    return segments


def subject_key(name):
    """Ключ дисциплины для поиска конфликтов между файлами."""
    text = clean_text(name).lower().replace('ё', 'е')
    text = re.sub(r'[«»"“”\'`]', '', text)
    text = re.sub(r'\s*[–—-]\s*', '-', text)
    return re.sub(r'\s+', ' ', text).strip(' .')


def direction_code_for_program(program):
    """Шифр направления → короткий код плана: 09.03.02 → ist."""
    text = clean_text(program)
    match = _DIRECTION_RE.search(text)
    if match:
        number = re.sub(r'\s+', '', match.group(1))
        return DIRECTION_CODES.get(number, number.replace('.', '-'))
    for token in re.findall(r'[А-ЯЁA-Z]{2,4}', text.upper()):
        if token in ABBREVIATION_CODES:
            return ABBREVIATION_CODES[token]
    return ''


def detect_direction(filename, sample=''):
    """Направление файла: шифр 09.03.02 и/или аббревиатура ИСТ в имени/тексте."""
    stem = os.path.splitext(os.path.basename(filename or ''))[0]
    blob = f'{stem}\n{sample or ""}'
    number_match = _DIRECTION_RE.search(blob)
    number = re.sub(r'\s+', '', number_match.group(1)) if number_match else ''

    def first_abbr(text):
        for token in re.findall(r'[A-Za-zА-Яа-яЁё]{2,6}', text):
            if token.upper() in ABBREVIATION_CODES:
                return token.upper()
        return ''

    stem_abbreviation = first_abbr(stem)
    abbreviation = stem_abbreviation or ('' if number else first_abbr(sample or ''))

    code = ''
    if number:
        code = DIRECTION_CODES.get(number, number.replace('.', '-'))
    elif abbreviation:
        code = ABBREVIATION_CODES[abbreviation]
    if not code and not number and not abbreviation:
        raise ValueError(
            'не удалось определить направление: нужен шифр вида 09.03.02 '
            'в имени файла или в самом плане'
        )

    parts = []
    if number:
        parts.append(number)
    if abbreviation:
        parts.append(abbreviation)
    direction = ' · '.join(parts) if parts else stem[:200]
    return {'direction': direction[:200], 'direction_code': code[:32]}


def direction_sample(path):
    """Первые строки файла — чтобы найти шифр направления, если его нет в имени."""
    extension = os.path.splitext(str(path))[1].lower()
    try:
        if extension in ('.xlsx', '.xls', '.docx'):
            for rows in (
                _rows_from_xlsx(path) if extension == '.xlsx'
                else _rows_from_xls(path) if extension == '.xls'
                else _rows_from_docx(path)
            ):
                values = [
                    clean_text(cell) for row in rows[:15] for cell in row if clean_text(cell)
                ]
                return ' '.join(values)[:3000]
        if extension == '.pdf':
            result = subprocess.run(
                ['pdftotext', '-f', '1', '-l', '2', '-layout', str(path), '-'],
                capture_output=True, check=False, timeout=60,
            )
            if result.returncode == 0:
                return result.stdout.decode('utf-8', errors='replace')[:3000]
    except Exception:
        return ''
    return ''
