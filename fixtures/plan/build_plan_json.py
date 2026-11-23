"""Собирает fixtures/plan/plan.json из официального учебного плана БГИТУ.

Запуск: python3 build_plan_json.py "<путь к PDF>"
"""
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import plan_parser as pp

SOURCE_URL = ('https://bgitu.ru/sveden/education/uchebnye-plany/2026-27/'
              'up-ochnoe/bac/УП 09.03.02 ИСТ 2026.pdf')
SOURCE_PAGE = 'https://bgitu.ru/sveden/education/'


def build(pdf_path: Path):
    disciplines = pp.parse(str(pdf_path))

    # семестровые строки: одна дисциплина может быть в нескольких семестрах
    rows = []
    for d in disciplines:
        for s in d['semesters']:
            control = ['экзамен' if s['exam'] else 'зачет']
            if s['coursework']:
                control.append('курсовая работа')
            rows.append({
                'code': d['code'],
                'subject': d['name'],
                'semester': s['semester'],
                'zets': s['zets'],
                'control': ', '.join(control),
                'department': d['department'],
                'source_page': d['page'],
                'elective_options': bool(d.get('elective_options')),
            })
    rows.sort(key=lambda r: (r['semester'] or 99, r['code']))

    per_sem = defaultdict(list)
    for r in rows:
        per_sem[r['semester']].append(r)
    totals = {n: round(sum(x['zets'] for x in per_sem[n] if x['zets']), 1) for n in range(1, 9)}
    counts = {n: len(per_sem[n]) for n in range(1, 9)}

    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return {
        'meta': {
            'direction': '09.03.02 Информационные системы и технологии',
            'level': 'бакалавриат',
            'form': 'очная',
            'institute': 'ИЭИ (инженерно-экономический институт), БГИТУ',
            'enrollment_year': 2026,
            'academic_year': '2026/27',
            'source_url': SOURCE_URL,
            'source_page': SOURCE_PAGE,
            'file_name': pdf_path.name,
            'file_size': pdf_path.stat().st_size,
            'sha256': digest,
            'retrieved': '2026-11-23',
            'assumption': ('Точный файл для 09.03.02 (очная форма, план набора 2026) '
                           'найден на bgitu.ru; скачан без допущений.'),
            'control_note': ('В плане проставлены только экзамен (номер семестра) и курсовая '
                             'работа («ргр»/«2ргр»); остальные сессии — зачёт. '
                             'Элективная физкультура (Б1.ДВ.3) в плане без з.е.'),
            'zets_block1_total': 210,
            'zets_total_with_practices_and_gia': 240,
        },
        'semester_counts': counts,
        'semester_zets': totals,
        'disciplines': disciplines_rows(disciplines),
        'by_semester': {str(n): per_sem[n] for n in range(1, 9)},
        'flat_rows': rows,
    }


def disciplines_rows(disciplines):
    out = []
    for d in disciplines:
        out.append({
            'code': d['code'],
            'subject': d['name'],
            'semesters': [s['semester'] for s in d['semesters']],
            'zets_by_semester': {str(s['semester']): s['zets'] for s in d['semesters']},
            'total_zets': round(sum(s['zets'] for s in d['semesters'] if s['zets']), 1),
            'department': d['department'],
            'elective_options': bool(d.get('elective_options')),
        })
    return out


if __name__ == '__main__':
    pdf = Path(sys.argv[1])
    data = build(pdf)
    out = pdf.parent / 'plan.json'
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('written', out, out.stat().st_size, 'bytes')
    print('дисциплин:', len(data['disciplines']), 'сем-строк:', len(data['flat_rows']))
    for n in range(1, 9):
        print(f'  {n} сем.: {data["semester_counts"][n]:2d} дисциплин, '
              f'{data["semester_zets"][n]:5.1f} з.е.')
