"""Разбор «Плана учебного процесса» БГИТУ (PDF) в дисциплины по семестрам.

Файл: «УП 09.03.02 ИСТ 2026.pdf» — Excel 2007 -> PDF, A4 landscape 841.8 x 595.2.
Скачан с bgitu.ru/sveden/education/uchebnye-plany/2026-27/up-ochnoe/bac/

Сетка таблицы (x — левый край фрагмента, пункты; проверено на этом файле):

  32–36    код дисциплины (Б1.О.1, Б1.В.12, Б1.ДВ.1, ФТД.1 …)
  71.7     названия: ячейка занимает 2–3 строки, поэтому части названия стоят
           выше и/или ниже строки с кодом (шаг 5.7 … 12.4 пт), а в строке с
           кодом названия может не быть вовсе
  242.1    «N семестр» — строка-раскладка многосеместровой дисциплины
  292.4    полоса контроля (номер семестра) у односеместровых строк
  307–312  номер семестра экзамена: 312.1 число, 307.4 «1,2», 308.2 «4-6»
  326/329  курсовая: «2ргр» / «ргр»
  352–358  зачётные единицы семестра
  375–410  часы;  599.3+22.1·(n−1) — колонки недельной нагрузки, семестры 1..8
  764      кафедра

Правила:
  * подзаголовки блоков («Б1.В. Часть, формируемая…», «Б1.ДВ. Дисциплины по
    выбору») и их переносы — не дисциплины, в названия не попадают;
  * каждая строка названия приписывается БЛИЖАЙШЕЙ по y строке с кодом (±14 пт);
  * односеместровая дисциплина — строка с кодом и з.е., где есть полоса
    контроля или колонка недельной нагрузки;
  * многосеместровая — шапка с кодом и итогом, семестры из строк «N семестр …»;
  * экзамен = номер семестра в полосе контроля, иначе зачёт;
  * «ргр»/«2ргр» = курсовая работа.
"""
import re

import pypdf

HEADER_Y = 420.0
NAME_RADIUS = 14.0
X_CODE = (30.0, 71.0)
X_NAME = (71.0, 72.6)
X_SEM_LABEL = (235.0, 260.0)
X_MARK = (285.0, 303.0)
X_EXAM = (303.0, 325.0)
X_COURSEWORK = (325.0, 331.0)
X_ZE = (345.0, 372.0)
X_HOURS = (372.0, 420.0)
X_SEM_COL_BASE = 599.3
X_SEM_COL_STEP = 22.1
X_DEPT = (760.0, 805.0)

CODE_RE = re.compile(r'^(Б1\.(?:О|В|ДВ)\.\d+|Б1\.О\.ДВ\.\d+|Б2\.(?:О|В)(?:\.\d+)?|Б3(?:\.\d+)?|ФТД\.\d+)$')
HEADING_RE = re.compile(r'^(Б1\.\s*(?:О|В|ДВ)\b|Б2\.|Б3\b|Блок\b|Факультативы|Всего|ВСЕГО|Итого)')
SUBTITLE_RE = re.compile(r'Часть, формируемая|Дисциплины по выбору|образовательных отношений')
NONDISCIPLINE_RE = re.compile(
    r'^(Б2\b|Б3\b|Учебная практика|Ознакомительная|Производственная|Технологическая|'
    r'Преддипломная|Подготовка к сдаче|Выполнение и защита|Всего|ВСЕГО|Итого|деятельности$)')


def _num(t):
    t = t.replace(',', '.')
    return float(t) if re.fullmatch(r'\d+(?:\.\d+)?', t) else None


def _in(x, span):
    return span[0] <= x < span[1]


def extract_runs(pdf_path):
    reader = pypdf.PdfReader(pdf_path)
    out = []
    for page_no, page in enumerate(reader.pages):
        buf = []

        def visit(text, cm, tm, fd, fs, buf=buf):
            t = text.strip()
            if t:
                buf.append((round(float(tm[5]), 1), round(float(tm[4]), 1), t))

        page.extract_text(visitor_text=visit)
        out.extend((page_no, y, x, t) for y, x, t in buf)
    return out


def group_lines(items):
    lines = []
    for y, x, t in sorted(items, key=lambda z: (-z[0], z[1])):
        if lines and abs(lines[-1][0] - y) <= 1.6:
            lines[-1][1].append((x, t))
        else:
            lines.append([y, [(x, t)]])
    return [(y, sorted(f)) for y, f in lines]


def _semester_at(x):
    if not (585.0 <= x < 765.0):
        return None
    n = round((x - X_SEM_COL_BASE) / X_SEM_COL_STEP) + 1
    return n if 1 <= n <= 8 else None


def _row(frags):
    row = {'code': None, 'name': None, 'sem_label': None, 'exam_sems': [],
           'coursework': False, 'zets': None, 'sem_cols': [], 'dept': None,
           'has_hours': False}
    for x, t in frags:
        if _in(x, X_CODE):
            row['code'] = t
        elif _in(x, X_NAME):
            row['name'] = t
        elif _in(x, X_SEM_LABEL):
            m = re.match(r'^([1-8])\s*семестр$', t)
            if m:
                row['sem_label'] = int(m.group(1))
        elif _in(x, X_EXAM):
            row['exam_sems'] += [int(n) for n in re.findall(r'\d+', t) if 1 <= int(n) <= 8]
        elif _in(x, X_MARK):
            v = _num(t)
            if v is not None and 1 <= int(v) <= 8:
                row['exam_sems'].append(int(v))
        elif _in(x, X_COURSEWORK):
            if 'гр' in t.lower():
                row['coursework'] = True
            else:
                v = _num(t)
                if v is not None and 1 <= int(v) <= 8:
                    row['exam_sems'].append(int(v))
        elif _in(x, X_ZE):
            v = _num(t)
            if v is not None:
                row['zets'] = v
        elif _in(x, X_HOURS):
            if _num(t) is not None:
                row['has_hours'] = True
        else:
            sem = _semester_at(x)
            if sem is not None and _num(t) is not None and (row['zets'] is not None or row['has_hours']):
                row['sem_cols'].append(sem)
        if _in(x, X_DEPT):
            row['dept'] = t
    return row


def _norm(name):
    """Нормализация названия: убрать перенос «сло- во», лишние пробелы."""
    name = re.sub(r'(\w)- (\w)', r'\1-\2', name)   # «оперативно- плановом»
    return re.sub(r'\s+', ' ', name).strip()


def parse(pdf_path):
    runs = extract_runs(pdf_path)
    lines = []
    for page_no in sorted({r[0] for r in runs}):
        items = [(y, x, t) for p, y, x, t in runs if p == page_no]
        lines.extend((page_no, y, frags) for y, frags in group_lines(items))

    rows = []
    for page_no, y, frags in lines:
        if y > HEADER_Y:
            continue
        row = _row(frags)
        row['page'] = page_no + 1
        row['y'] = y
        rows.append(row)

    code_idx = [i for i, r in enumerate(rows) if r['code'] and CODE_RE.match(r['code'])]

    # 1. названия -> ближайший код
    names = {i: [] for i in code_idx}
    for j, r in enumerate(rows):
        if not r['name'] or r['code'] or r['sem_label'] is not None or r['zets'] is not None:
            continue
        if HEADING_RE.match(r['name']) or SUBTITLE_RE.search(r['name']):
            continue
        best, best_dy = None, NAME_RADIUS
        for i in code_idx:
            rc = rows[i]
            if rc['page'] != r['page']:
                continue
            dy = abs(rc['y'] - r['y'])
            if dy <= best_dy:
                best, best_dy = i, dy
        if best is not None:
            names[best].append((r['y'], r['name']))

    # 1b. склеиваем переносы: строка, начинающаяся со строчной буквы, — продолжение
    #     предыдущего фрагмента названия (убывание y = сверху вниз)
    for i in code_idx:
        merged = []
        for y_, t_ in sorted(names[i], key=lambda z: -z[0]):
            if merged and re.match(r'^[а-яё]', t_):
                merged[-1] = (merged[-1][0], f'{merged[-1][1]} {t_}')
            else:
                merged.append((y_, t_))
        names[i] = merged

    disciplines = []
    for k, i in enumerate(code_idx):
        row = rows[i]
        parts = list(names[i])
        if row['name']:
            parts.append((row['y'], row['name']))
        parts.sort(key=lambda z: -z[0])
        if row['code'].startswith(('Б1.ДВ', 'Б1.В.ДВ')):
            # «Дисциплины по выбору»: под кодом перечислены АЛЬТЕРНАТИВЫ
            # («Вычислительные машины и операционные системы» /
            #  «Архитектура информационных систем» / «Адаптация…»).
            # Первая альтернатива часто начата на строке ВЫШЕ кода, а закончена
            # в самой строке кода (её собственное название) — склеиваем.
            alts = [t for y_, t in sorted(names[i], key=lambda z: -z[0])
                    if not SUBTITLE_RE.search(t) and not t.startswith('Б1.')]
            if row['name']:
                if alts and len(alts) >= 1 and re.match(r'^[а-яё]', row['name']) is None \
                        and abs(names[i][-1][0] - row['y']) < NAME_RADIUS + 0.5 \
                        and len(alts[-1].split()) and not re.search(r'[.]$', alts[-1]):
                    alts[-1] = f'{alts[-1]} {row["name"]}'
                elif not alts:
                    alts.append(row['name'])
            name = ' / '.join(a.strip() for a in alts if a.strip())
            disc = {'code': row['code'], 'name': name, 'department': row['dept'],
                    'semesters': [], 'page': row['page'], 'elective_options': True}
            disciplines.append(disc)
            _fill_semesters(disc, row, rows, i, code_idx, k)
            continue
        name = re.sub(r'\s+', ' ', ' '.join(t for _, t in parts)).strip()

        disc = {'code': row['code'], 'name': name, 'department': row['dept'],
                'semesters': [], 'page': row['page']}
        disciplines.append(disc)
        _fill_semesters(disc, row, rows, i, code_idx, k)

    return [dict(d, name=_norm(d['name'])) for d in disciplines
            if d['semesters'] and not NONDISCIPLINE_RE.match(d['name'])]


def _fill_semesters(disc, row, rows, i, code_idx, k):
    """Проставляет семестры дисциплины из её строки и строк «N семестр …»."""
    nxt = code_idx[k + 1] if k + 1 < len(code_idx) else len(rows)
    if row['zets'] is not None:
        if row['exam_sems']:
            disc['semesters'].append({'semester': row['exam_sems'][0], 'zets': row['zets'],
                                      'exam': True, 'coursework': row['coursework']})
        elif row['sem_cols']:
            disc['semesters'].append({'semester': row['sem_cols'][0], 'zets': row['zets'],
                                      'exam': False, 'coursework': row['coursework']})
    else:
        sems = set(row['exam_sems']) or set(row['sem_cols'])
        if row['has_hours'] and sems:
            # элективная физкультура (Б1.ДВ.3): часы есть, з.е. в плане нет
            for n in sorted(sems):
                disc['semesters'].append({'semester': n, 'zets': None,
                                          'exam': False, 'coursework': False})
    for j in range(i + 1, nxt):
        r2 = rows[j]
        if r2['sem_label'] is not None:
            disc['semesters'].append({'semester': r2['sem_label'], 'zets': r2['zets'],
                                      'exam': bool(r2['exam_sems']),
                                      'coursework': r2['coursework']})


if __name__ == '__main__':
    import sys
    for d in parse(sys.argv[1]):
        print(d['code'].ljust(12), (d['name'] or '—')[:52].ljust(52),
              [(s['semester'], s['zets'], 'Э' if s['exam'] else 'З', 'КР' if s['coursework'] else '')
               for s in d['semesters']])
