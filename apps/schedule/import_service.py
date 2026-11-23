"""Разбор файла расписания БГИТУ (.xls/.xlsx) в занятия."""
import re

WEEKDAYS = {
    'понедельник': 1, 'вторник': 2, 'среда': 3, 'четверг': 4, 'пятница': 5, 'суббота': 6,
}
TIME_RE = re.compile(r'^\s*\d{1,2}[.:]\d{2}\s*[-–—]\s*\d{1,2}[.:]\d{2}\s*$')


def normalize_time(value):
    value = value.strip().replace('.', ':')
    found = re.match(r'^(\d{1,2}):(\d{2})$', value)
    return f'{int(found.group(1))}:{found.group(2)}' if found else value
GROUP_RE = re.compile(r'^[А-ЯЁA-Z]{2,6}\s*-\s*\d{2,3}$')


def cell_text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return str(value).replace('\xa0', ' ').replace('\r', '\n').strip()


def _xls_colours(book, sheet):
    """{(row, col): 'red'|'black'} по цвету шрифта. Красный в БГИТУ — 2-й корпус."""
    colours = {}
    for r in range(sheet.nrows):
        for c in range(sheet.ncols):
            cell = sheet.cell(r, c)
            if not str(cell.value).strip():
                continue
            font = book.font_list[book.xf_list[cell.xf_index].font_index]
            rgb = book.colour_map.get(font.colour_index)
            red = font.colour_index == 10 or (
                bool(rgb) and rgb[0] >= 180 and rgb[1] <= 90 and rgb[2] <= 90
            )
            colours[(r, c)] = 'red' if red else 'black'
    return colours


def _xlsx_colours(sheet):
    """То же для .xlsx: цвет шрифта из openpyxl (красный = 2-й корпус)."""
    colours = {}
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value is None or not str(cell.value).strip():
                continue
            colour = getattr(cell.font, 'color', None)
            rgb = (getattr(colour, 'rgb', '') or '') if colour else ''
            red = False
            if isinstance(rgb, str) and len(rgb) == 8:
                red = int(rgb[2:4], 16) >= 180 and int(rgb[4:6], 16) <= 90 and int(rgb[6:8], 16) <= 90
            colours[(cell.row - 1, cell.column - 1)] = 'red' if red else 'black'
    return colours


def read_sheet(path):
    """(строки, объединённые диапазоны, цвета шрифта) в 0-индексах, rhi/chi исключающие.

    Цвет шрифта нужен для корпуса: красный текст в файлах БГИТУ — 2-й корпус,
    чёрный (включая «авто») — 1-й.
    """
    if path.lower().endswith('.xls'):
        import xlrd

        book = xlrd.open_workbook(path, formatting_info=True)
        sheet = book.sheet_by_index(0)
        rows = [[cell_text(sheet.cell_value(r, c)) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
        return rows, list(sheet.merged_cells), _xls_colours(book, sheet)

    from openpyxl import load_workbook

    book = load_workbook(path, data_only=True, read_only=False)
    sheet = book.worksheets[0]
    rows = [
        [cell_text(sheet.cell(row=r, column=c).value) for c in range(1, sheet.max_column + 1)]
        for r in range(1, sheet.max_row + 1)
    ]
    merged = [(m.min_row - 1, m.max_row, m.min_col - 1, m.max_col) for m in sheet.merged_cells.ranges]
    return rows, merged, _xlsx_colours(sheet)


def read_rows(path):
    """Совместимость: только строки и объединённые диапазоны."""
    rows, merged, _colours = read_sheet(path)
    return rows, merged


def _header_subgroup(value):
    """«а»/«б» из шапки; числовые «1»/«2» приводятся к тем же буквам."""
    return {'а': 'а', 'б': 'б', '1': 'а', '2': 'б'}.get((value or '').strip().lower(), '')


def group_columns(rows, merged):
    """Группы из шапки: [{code, columns: [(col, 'а'|'б'|'')], divided}].

    Деление на подгруппы есть ТОЛЬКО у групп, под именем которых в шапке
    стоят подстолбцы «а» и «б». Подстолбцы без подписей — одна группа,
    а не две подгруппы.
    """
    groups = []
    header = 1
    if len(rows) <= header:
        return groups
    seen = set()

    def add(code, clo, chi):
        labels = []
        for col in range(clo, chi):
            value = rows[2][col] if len(rows) > 2 and col < len(rows[2]) else ''
            labels.append(_header_subgroup(value))
        groups.append({
            'code': ' '.join(str(code).split()).upper(),
            'columns': [(clo + index, labels[index]) for index in range(chi - clo)],
            'divided': len({label for label in labels if label}) >= 2,
        })
        seen.update(range(clo, chi))

    for rlo, rhi, clo, chi in merged:
        if rlo <= header < rhi and clo >= 3:
            text = rows[header][clo] if clo < len(rows[header]) else ''
            if GROUP_RE.match(' '.join(str(text).split())):
                add(text, clo, chi)
    for col, text in enumerate(rows[header]):
        value = ' '.join(str(text).split())
        if col < 3 or col in seen or not GROUP_RE.match(value):
            continue
        add(value, col, col + 1)
    groups.sort(key=lambda item: item['columns'][0][0])
    return groups


def cell_block(merged, row, col):
    """Объединённый блок ячейки (rlo, rhi, clo, chi) или одиночная ячейка."""
    for rlo, rhi, clo, chi in merged:
        if rlo <= row < rhi and clo <= col < chi:
            return (rlo, rhi, clo, chi)
    return (row, row + 1, col, col + 1)


def block_week(block, start, end):
    """Верхняя половина пары — 1-я неделя, нижняя — 2-я, общая ячейка — каждую.

    Пара занимает две строки: объединённая на обе строки ячейка читается как
    занятие каждую неделю, ячейка только в верхней строке — 1-я неделя,
    только в нижней — 2-я. Это НЕ подгруппы.
    """
    rlo, rhi, _clo, _chi = block
    mid = start + (end - start) // 2
    if rlo <= start and rhi >= end:
        return 'all'
    if rhi <= mid:
        return 'odd'
    if rlo >= mid:
        return 'even'
    return 'all'


def pair_blocks(rows, merged, group, start, end):
    """Блоки ячеек группы внутри пары (без повторов одного объединения)."""
    blocks = set()
    for col, _label in group['columns']:
        for row in range(start, end):
            if col >= len(rows[row]) or not rows[row][col].strip():
                continue
            block = cell_block(merged, row, col)
            # Объединение, начавшееся в прошлой паре, целиком принадлежит ей.
            if block[0] < start or block[0] >= end:
                continue
            blocks.add(block)
    return sorted(blocks)


def block_text(rows, group, block):
    """Текст блока: значение хранится в его левой верхней ячейке."""
    rlo, _rhi, clo, chi = block
    if clo < len(rows[rlo]) and rows[rlo][clo].strip():
        return rows[rlo][clo]
    for col, _label in group['columns']:
        if clo <= col < chi and col < len(rows[rlo]) and rows[rlo][col].strip():
            return rows[rlo][col]
    return ''


def block_subgroup(group, block):
    """Подгруппа блока: объединённая на обе подколонки ячейка — общая пара."""
    _rlo, _rhi, clo, chi = block
    covered = [(col, label) for col, label in group['columns'] if clo <= col < chi]
    if not group['divided'] or len(covered) > 1:
        return ''
    return covered[0][1] if covered else ''


def split_cell_entries(text):
    """Ячейка БГИТУ: 'Информатика|№228 (Юркова О.В.)' и/или несколько занятий через пустую строку."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    blocks, current = [], []
    for raw in text.split('\n'):
        line = raw.strip()
        if line:
            current.append(line)
        elif current:
            blocks.append('\n'.join(current))
            current = []
    if current:
        blocks.append('\n'.join(current))
    return blocks


ODD_WORDS = re.compile(r'(?<![а-яё])(?:неч[её]тн|непарн)\w*|верхн\w*\s+(?:нед|полов)|(?<![а-яё])(?:в\s+)?числител\w*')
EVEN_WORDS = re.compile(r'(?<![а-яё])ч[её]тн\w*|(?<![а-яё])парн\w*\s+нед|нижн\w*\s+(?:нед|полов)|(?<![а-яё])(?:в\s+)?знаменател\w*')
ROMAN_WEEK = re.compile(r'\b(i|ii)\s*(?:нед|полуг)', re.I)


def detect_week(text):
    """Чётность недели по обозначениям файла БГИТУ.

    Понимает: «нечётная/чётная», «нечёт/чёт», «I/II неделя», «I/II полугодие»,
    «верхняя/нижняя неделя», «числитель/знаменатель». Дробь вида «7/14»
    (номера недель через слэш) тоже читается: нечётный числитель → odd и т.д.
    """
    flat = ' '.join(text.split()).lower()
    if ROMAN_WEEK.search(flat):
        return 'odd' if ROMAN_WEEK.search(flat).group(1).lower() == 'i' else 'even'
    fraction = re.search(r'\b(\d{1,2})\s*/\s*(\d{1,2})\b', flat)
    if fraction:
        left, right = (int(value) for value in fraction.groups())
        if (left % 2) == (right % 2):
            return 'odd' if left % 2 else 'even'
    if ODD_WORDS.search(flat):
        return 'odd'
    if EVEN_WORDS.search(flat):
        return 'even'
    # Короткие пометки «(нечёт)»/«(чёт)» — только отдельным словом.
    if re.search(r'(?:^|[\s(])неч[её]т(?=$|[\s)])', flat):
        return 'odd'
    if re.search(r'(?:^|[\s(])ч[её]т(?=$|[\s)])', flat):
        return 'even'
    return 'all'


def cell_week_parity(text):
    """«числитель/знаменатель»: одна ячейка на две недели → 'pair' (или 'words').

    Деление считается явным, если: пишутся оба слова «числитель» и «знаменатель»;
    есть слэш между разными числами; или в тексте ровно один слэш, делящий
    «Физика … / Информатика …» на два занятия.
    """
    flat = ' '.join(text.split()).lower()
    if re.search(r'(?<![а-яё])числител', flat) and re.search(r'(?<![а-яё])знаменател', flat):
        return 'words'
    fraction = re.search(r'(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])\s*(нед|полуг)?', text, re.I)
    if fraction:
        if int(fraction.group(1)) == int(fraction.group(2)):
            return 'all'
        # «7/14 неделя» — суммарные номера недель в метке, а не два занятия.
        return 'all' if fraction.group(3) else 'pair'
    sides = [part.strip() for part in text.split('/')]
    if len(sides) == 2 and all(len(part) >= 2 for part in sides):
        return 'pair'
    return 'all'


def parse_cell(text, subgroup=''):
    """Одно занятие: 'Физика \\n314 (Симохин С.П.)' → subject/room/teacher/week.

    Метка подгруппы в начале первой строки («а Физика», «б Информатика») снимается
    и возвращается отдельным полем — из неё же берётся подгруппа занятия.
    """
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if not lines:
        return None
    entry = {'week': detect_week(text)}
    lead = re.match(r'^([аб12])\s*[.)]?\s+(\S.*)$', lines[0], re.S)
    if lead:
        entry['subgroup'] = lead.group(1)
        lines[0] = lead.group(2).strip()
    flat = ' '.join(lines)
    # Пометка недели в скобках или квадратных скобках — «(I неделя)», «[чётная]»:
    # это чётность, а не преподаватель. Вырезаем до поиска ФИО в скобках.
    flat = re.sub(r'[\(\[]\s*(?:i{1,2}|\d{1,2}\s*/\s*\d{1,2})\s*(?:нед\w*|полугод\w*)\s*[\)\]]', '', flat, flags=re.I)
    flat = re.sub(r'[\(\[]\s*(?:неч[её]тн\w*|непарн\w*|ч[её]тн\w*|верхн\w*\s+нед\w*|нижн\w*\s+нед\w*|(?:в\s+)?числител\w*|(?:в\s+)?знаменател\w*)\s*[\)\]]', '', flat, flags=re.I).strip()
    # «[нечётная неделя]» — метка от split_fraction_entries (числитель/знаменатель).
    flat = re.sub(r'\[[^\]]*нед\w*\]', '', flat, flags=re.I).strip()
    teacher = ''
    found = re.search(r'\(([^()]{2,90})\)\s*$', flat)
    if found:
        teacher = found.group(1).strip()
        flat = flat[: found.start()].strip()

    room = ''
    found = re.search(r'№\s*([0-9]{1,4}[а-яА-Я]?)', flat)
    if found:
        room = found.group(1).strip()
        flat = flat[: found.start()].strip()
    elif re.search(r'\bДОТ\b', flat):
        room = 'ДОТ'
        flat = re.sub(r'\bДОТ\b', '', flat).strip()
    elif re.search(r'\bДО\b', flat):
        room = 'ДО'
        flat = re.sub(r'\bДО\b', '', flat).strip()
    else:
        found = re.search(r'(?:^|\s)([0-9]{2,4}[а-яА-Я]?)\s*$', flat)
        if found:
            room = found.group(1)
            flat = flat[: found.start()].strip()

    marker = re.search(r'(?:^|\s)(нечётн\w*|нечетн\w*|чётн\w*|четн\w*|непарн\w*|верхн\w*\s+нед\w*|нижн\w*\s+нед\w*|неч[её]т|ч[её]т)\.?\s*$', flat, re.I)
    if marker:
        entry['week'] = 'odd' if ODD_WORDS.search(marker.group(1).lower()) or marker.group(1).lower().startswith('неч') else 'even'
        flat = flat[: marker.start()].strip()
    # Слэш-метка «7/14 неделя» / «неделя 7/14» в конце — только чётность.
    # Если других слэшей в тексте нет (иначе это деление ячейки, см. split_fraction_entries).
    slash = re.search(r'(?:(?:^|\s)нед\w*\s*)?(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])\s*(?:\s*нед\w*)?$', flat, re.I)
    if slash:
        head = flat[: slash.start()]
        same = re.search(
            rf'(?<![\d/]){re.escape(slash.group(1))}\s*/\s*{re.escape(slash.group(2))}(?![\d/])', head
        )
        left, right = int(slash.group(1)), int(slash.group(2))
        if not same and left % 2 == right % 2:
            entry['week'] = 'odd' if left % 2 else 'even'
            flat = head.strip()
    # Слова-метки «числитель»/«знаменатель» в начале части — это не дисциплина.
    flat = re.sub(r'^[\[\(]?\s*(?:числитель|знаменатель|верхняя неделя|нижняя неделя|нечётная неделя|чётная неделя)\s*[\]\):]?\s*', '', flat, flags=re.I).strip()

    subject = re.sub(r'\s+', ' ', flat).strip(' -–—.,')
    if len(subject) < 2:
        return None
    low = subject.lower()
    kind = ''
    if 'лекц' in low:
        kind = 'лекция'
    elif 'лаборатор' in low or re.search(r'\bлаб\.', low):
        kind = 'лабораторная'
    elif 'практи' in low or 'семинар' in low:
        kind = 'практика'
    elif 'консультац' in low:
        kind = 'консультация'
    if 'физкультур' in low or 'физическая культура' in low:
        kind = kind or 'физкультура'
    entry.update(subject=subject, room=room, teacher_name=teacher, kind=kind)
    return entry


def cell_entries(text, subgroup=''):
    """Все занятия ячейки: 'Физика а (…) \\n \\n Физика б (…)' → два занятия.

    Сначала дробь «числитель/знаменатель» режется на две части с метками
    «нечётная/чётная неделя», затем каждая часть разбирается как обычно.
    """
    entries = []
    for block in split_cell_entries(text):
        for part in split_fraction_entries(block):
            item = parse_cell(part, subgroup)
            if item and item not in entries:
                entries.append(item)
    return entries


def split_fraction_entries(text):
    """«Физика №307 (Петров) / Информатика №228 (Юркова)» → две части с метками недели.

    Дробь «числитель/знаменатель» — два занятия в одной ячейке: верхняя (левая)
    часть относится к нечётной неделе, нижняя (правая) — к чётной. Каждая часть
    получает метку «нечётная неделя» / «чётная неделя», чтобы парсер сохранил
    признак и не склеил одинаковые предметы. Слэш между числами (номера недель
    «7/14») занятие не делит — это метка, её читает detect_week.
    """
    parity = cell_week_parity(text)
    if parity == 'all':
        return [text]
    parts = re.split(r'\s*/\s*', text, maxsplit=1)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        return [text]
    marked = []
    for part, label in zip(parts, ('нечётная неделя', 'чётная неделя')):
        if part.strip():
            marked.append(f'{label}: {part.strip()}')
    return marked or [text]


def subgroup_label(value):
    """'а'→'а', 'б'→'б', '1'→'1', прочее → ''."""
    value = (value or '').strip().lower()
    return value if value in ('а', 'б', '1', '2', '3') else ''


def entries_from_block(text, week, subgroup, building):
    """Занятия одной визуальной ячейки.

    Если в ячейке несколько абзацев (пустая строка между ними), верхний
    считается 1-й неделей, нижний — 2-й. В реальном файле БГИТУ так не
    бывает (верх/низ — отдельные строки пары), но правило действует и здесь.
    """
    entries = []
    paragraphs = split_cell_entries(text)
    for index, paragraph in enumerate(paragraphs):
        paragraph_week = week
        if week == 'all' and len(paragraphs) > 1:
            paragraph_week = 'odd' if index == 0 else ('even' if index == 1 else 'all')
        for part in split_fraction_entries(paragraph):
            item = parse_cell(part)
            if not item:
                continue
            if item.get('week', 'all') in ('all', ''):
                item['week'] = paragraph_week
            item['subgroup'] = subgroup
            item['building'] = building
            entries.append(item)
    return entries


def parse_schedule(path):
    """→ (занятия, звонки).

    Занятие: group, weekday, pair, subject, room, teacher, subgroup, week,
    building. Верх/низ ячейки пары — 1-я/2-я неделя, а не подгруппы; подгруппы
    берутся только из подстолбцов «а»/«б» шапки.
    """
    rows, merged, colours = read_sheet(path)
    groups = group_columns(rows, merged)
    if not groups:
        raise ValueError('В файле не найдена шапка с группами (ожидались коды вида ИСТ-201).')

    day_rows = []
    for index, row in enumerate(rows):
        if not row:
            continue
        name = row[0].strip().lower()
        if name in WEEKDAYS:
            day_rows.append((index, WEEKDAYS[name]))

    lessons = []
    bells = {}
    for order, (day_index, weekday) in enumerate(day_rows):
        day_end = day_rows[order + 1][0] if order + 1 < len(day_rows) else len(rows)
        pair_starts = [
            r for r in range(day_index, day_end)
            if any(TIME_RE.match(rows[r][c]) for c in (1, 2) if c < len(rows[r]))
        ]
        for pair_number, start in enumerate(pair_starts, 1):
            end = pair_starts[pair_number] if pair_number < len(pair_starts) else day_end
            for bell_building, col in (('1', 1), ('2', 2)):
                if col < len(rows[start]) and TIME_RE.match(rows[start][col]):
                    start_time, end_time = [normalize_time(p) for p in re.split(r'[-–—]', rows[start][col])]
                    bells.setdefault((bell_building, pair_number), (start_time, end_time))
            for group in groups:
                parsed = []
                for block in pair_blocks(rows, merged, group, start, end):
                    text = block_text(rows, group, block)
                    if not text:
                        continue
                    colour = colours.get((block[0], block[2]), 'black')
                    item_building = '2' if colour == 'red' else '1'
                    for item in entries_from_block(
                        text,
                        block_week(block, start, end),
                        block_subgroup(group, block),
                        item_building,
                    ):
                        item.update(group=group['code'], weekday=weekday, pair=pair_number)
                        if item not in parsed:
                            parsed.append(item)
                lessons.extend(parsed)
    if not lessons:
        raise ValueError('В файле не найдено ни одного занятия.')
    lessons.sort(key=lambda item: (item['weekday'], item['pair'], item['subgroup'], item['subject']))
    return lessons, bells
