"""Рейтинг и аттестации по дисциплине — перенос схемы ЭОС БГИТУ.

Дисциплина делится на контрольные точки (КТ1…КТ3 за семестр). Внутри точки
баллы распределены по видам занятий с весами (сумма весов точки = 100 %).
Балл по виду занятий — 0…100. Итог точки = сумма баллов с весами.
Итоговый рейтинг дисциплины = сумма итогов точек с весами точек.

Аттестация по точке выставляется автоматически по проходному баллу, а прогноз
«будет ли аттестация» считается по рейтингу и остатку невыставленных баллов.
Итоговая оценка (зачёт/экзамен) остаётся отдельной и может быть выставлена
независимо от аттестаций — как в ЭОС.
"""
from .models import ControlPoint, PointMark, PointWeight

VERDICT_YES = 'yes'
VERDICT_WAIT = 'wait'
VERDICT_NO = 'no'

STATUS_ATTESTED = 'attested'
STATUS_FAILED = 'failed'
STATUS_PENDING = 'pending'

STATUS_LABELS = {
    STATUS_ATTESTED: 'аттестован',
    STATUS_FAILED: 'не аттестован',
    STATUS_PENDING: 'не выставлено',
}
VERDICT_LABELS = {
    VERDICT_YES: 'будет аттестован',
    VERDICT_WAIT: 'под вопросом',
    VERDICT_NO: 'не будет аттестован',
}


def point_weights(point):
    """Веса видов занятий точки; если веса не заданы — одна точка «др.» на 100 %."""
    weights = {w.kind: w.weight for w in point.weights.all() if w.weight}
    return weights or {'other': 100}


def point_result(point, scores):
    """Итог точки по баллам студента.

    scores — {вид занятий: балл или None}. Возвращает словарь с итогом,
    максимально возможным итогом, статусом аттестации и списком незаполненных видов.
    """
    weights = point_weights(point)
    total = 0
    missing = []
    for kind, weight in weights.items():
        score = scores.get(kind)
        if score is None:
            missing.append((kind, weight))
        else:
            total += score * weight / 100
    possible = total + sum(w for _, w in missing)
    if not missing:
        status = STATUS_ATTESTED if total >= point.pass_score else STATUS_FAILED
    elif total >= point.pass_score and possible >= point.pass_score:
        status = STATUS_ATTESTED
    else:
        status = STATUS_PENDING
    return {
        'point': point,
        'scores': dict(scores),
        'total': round(total),
        'possible': round(possible),
        'missing': missing,
        'status': status,
        'status_label': STATUS_LABELS[status],
        'weights': weights,
    }


def course_rating(course, student=None, points=None, marks=None):
    """Рейтинг студента по дисциплине и прогноз аттестации.

    marks — список PointMark, словарь {point_id: {kind: score}} или None
    (тогда берутся баллы студента из БД).
    """
    if points is None:
        points = list(course.control_points.prefetch_related('weights'))
    if marks is None:
        marks = PointMark.objects.filter(point__course=course, student=student)
    if isinstance(marks, dict):
        by_point = marks
    else:
        by_point = {}
        for mark in marks:
            by_point.setdefault(mark.point_id, {})[mark.kind] = mark.score

    results = []
    rating = 0.0
    possible = 0.0
    weight_sum = 0
    for point in points:
        result = point_result(point, by_point.get(point.id, {}))
        result['weight'] = point.weight
        result['weighted'] = round(result['total'] * point.weight / 100)
        results.append(result)
        rating += result['total'] * point.weight / 100
        possible += result['possible'] * point.weight / 100
        weight_sum += point.weight

    rating = round(rating)
    possible = round(possible)
    threshold = course.admission_score

    reasons = []
    if not points:
        verdict = VERDICT_NO
        reasons.append('контрольные точки не заданы')
    else:
        failed = [r for r in results if r['status'] == STATUS_FAILED]
        pending = [r for r in results if r['status'] == STATUS_PENDING]
        if failed:
            verdict = VERDICT_NO
            for r in failed:
                reasons.append(f'КТ{r["point"].number}: {r["total"]} % ниже проходного {r["point"].pass_score} %')
        elif possible < threshold:
            verdict = VERDICT_NO
            reasons.append(f'даже максимум {possible} % ниже порога {threshold} %')
        elif pending:
            verdict = VERDICT_WAIT
            for r in pending:
                gaps = ', '.join(f'{dict(PointMark._meta.get_field("kind").choices).get(k, k)}' for k, _ in r['missing'])
                reasons.append(f'КТ{r["point"].number}: не выставлено ({gaps})')
            if rating < threshold:
                reasons.append(f'рейтинг {rating} % ниже порога {threshold} %')
        elif rating < threshold:
            verdict = VERDICT_WAIT
            reasons.append(f'рейтинг {rating} % ниже порога {threshold} %')
        else:
            verdict = VERDICT_YES

    return {
        'points': results,
        'rating': rating,
        'possible': possible,
        'threshold': threshold,
        'weight_sum': weight_sum,
        'verdict': verdict,
        'verdict_label': VERDICT_LABELS[verdict],
        'reasons': reasons,
    }


def ensure_points(course, count=None):
    """Создаёт недостающие контрольные точки с равными весами (сумма 100 %).

    Внутри точки по умолчанию: лекции 40 %, практика 40 %, лабораторные 20 %.
    """
    count = count or course.attestation_count
    existing = {p.number for p in course.control_points.all()}
    base, remainder = divmod(100, count) if count else (0, 0)
    created = []
    for number in range(1, count + 1):
        if number in existing:
            continue
        weight = base + (1 if number <= remainder else 0)
        point = ControlPoint.objects.create(
            course=course, number=number, weight=weight,
            title=f'Аттестация {number}', pass_score=course.admission_score,
        )
        for kind, kind_weight in (('lecture', 40), ('practice', 40), ('lab', 20)):
            PointWeight.objects.create(point=point, kind=kind, weight=kind_weight)
        created.append(point)
    return created
