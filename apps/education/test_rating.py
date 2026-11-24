"""Тесты рейтинга аттестаций — перенос схемы ведомости ЭОС.

КТ1…КТ3 за семестр, веса видов занятий внутри точки, итог точки,
рейтинг дисциплины, прогноз «будет ли аттестация».
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from django.contrib.auth.models import User

from apps.accounts.models import Profile, Role
from .models import ControlPoint, Course, Enrollment, Group, PointMark, PointWeight
from .rating import (STATUS_ATTESTED, STATUS_FAILED, STATUS_PENDING, VERDICT_NO, VERDICT_WAIT,
                     VERDICT_YES, course_rating, ensure_points, point_result)


class RatingBase(TestCase):
    def setUp(self):
        student_role, _ = Role.objects.get_or_create(code='student', defaults={'name': 'Студент'})
        teacher_role, _ = Role.objects.get_or_create(code='teacher', defaults={'name': 'Преподаватель'})
        teacher_role.can_grade = True
        teacher_role.can_view_all = True
        teacher_role.can_create_courses = True
        teacher_role.save()
        self.group = Group.objects.get_or_create(code='РИТ-301', defaults={'course': 3})[0]
        self.student = User.objects.create_user('rstudent', password='x')
        Profile.objects.update_or_create(user=self.student, defaults={'role': student_role, 'group': self.group})
        self.teacher = User.objects.create_user('rteacher', password='x')
        Profile.objects.update_or_create(user=self.teacher, defaults={'role': teacher_role})
        self.course = Course.objects.create(
            title='Архитектура ЭВМ', code='АРХ-301', group=self.group, teacher=self.teacher,
            semester=3, control_form=Course.FORM_EXAM,
        )
        Enrollment.objects.create(course=self.course, student=self.student)


class PointResultTests(RatingBase):
    def setUp(self):
        super().setUp()
        self.point = ControlPoint.objects.create(course=self.course, number=1, weight=30, pass_score=50)
        PointWeight.objects.create(point=self.point, kind='lecture', weight=50)
        PointWeight.objects.create(point=self.point, kind='practice', weight=50)

    def test_total_is_weighted_sum(self):
        result = point_result(self.point, {'lecture': 80, 'practice': 60})
        self.assertEqual(result['total'], 70)
        self.assertEqual(result['possible'], 70)
        self.assertEqual(result['status'], STATUS_ATTESTED)

    def test_partial_marks_count_zero_and_stay_pending(self):
        result = point_result(self.point, {'lecture': 80})
        self.assertEqual(result['total'], 40)
        self.assertEqual(result['possible'], 90)
        self.assertEqual(result['status'], STATUS_PENDING)
        self.assertEqual(result['missing'], [('practice', 50)])

    def test_below_pass_is_failed(self):
        result = point_result(self.point, {'lecture': 40, 'practice': 30})
        self.assertEqual(result['total'], 35)
        self.assertEqual(result['status'], STATUS_FAILED)

    def test_point_without_weights_is_hundred_percent_other(self):
        point = ControlPoint.objects.create(course=self.course, number=2, weight=20)
        result = point_result(point, {'other': 90})
        self.assertEqual(result['total'], 90)


class CourseRatingTests(RatingBase):
    def setUp(self):
        super().setUp()
        self.course.admission_score = 50
        self.course.save()
        self.points = ensure_points(self.course)
        self.point = self.points[0]

    def test_ensure_points_creates_three_points_with_hundred_weight(self):
        self.assertEqual(len(self.points), 3)
        self.assertEqual(sum(p.weight for p in self.points), 100)  # 34+33+33
        kinds = list(self.point.weights.values_list('kind', flat=True))
        self.assertEqual(kinds, ['lab', 'lecture', 'practice'])
        self.assertEqual(sum(self.point.weights.values_list('weight', flat=True)), 100)

    def test_rating_sums_weighted_points(self):
        for point in self.points:
            for kind in ('lecture', 'practice', 'lab'):
                PointMark.objects.create(point=point, student=self.student, kind=kind, score=80)
        rating = course_rating(self.course, self.student)
        self.assertEqual(rating['rating'], 80)  # 80 * 100 / 100
        self.assertEqual(rating['verdict'], VERDICT_YES)

    def test_pending_points_give_wait_verdict(self):
        for kind in ('lecture', 'practice', 'lab'):
            PointMark.objects.create(point=self.point, student=self.student, kind=kind, score=80)
        rating = course_rating(self.course, self.student)
        self.assertEqual(rating['verdict'], VERDICT_WAIT)
        self.assertTrue(any('не выставлено' in reason for reason in rating['reasons']))

    def test_low_marks_mean_no_attestation(self):
        for point in self.points:
            for kind in ('lecture', 'practice', 'lab'):
                PointMark.objects.create(point=point, student=self.student, kind=kind, score=20)
        rating = course_rating(self.course, self.student)
        self.assertEqual(rating['rating'], 20)
        self.assertEqual(rating['verdict'], VERDICT_NO)

    def test_unreachable_threshold(self):
        self.course.admission_score = 95
        self.course.save()
        for point in self.points:
            for kind in ('lecture', 'practice', 'lab'):
                PointMark.objects.create(point=point, student=self.student, kind=kind, score=50)
        rating = course_rating(self.course, self.student)
        self.assertEqual(rating['verdict'], VERDICT_NO)
        self.assertTrue(any('ниже порога' in reason for reason in rating['reasons']))


class PointsViewsTests(RatingBase):
    def setUp(self):
        super().setUp()

    def test_teacher_sees_points_grid(self):
        self.client.force_login(self.teacher)
        ensure_points(self.course)
        response = self.client.get(reverse('education:journal', args=[self.course.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Аттестации по рейтингу')
        self.assertContains(response, 'КТ1')

    def test_bulk_marks_are_saved(self):
        self.client.force_login(self.teacher)
        points = ensure_points(self.course)
        point = points[0]
        response = self.client.post(reverse('education:set_point_marks', args=[self.course.id]), {
            'student': self.student.profile.id,
            f'p{point.id}_lecture': '90',
            f'p{point.id}_practice': '70',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PointMark.objects.filter(point=point, student=self.student).count(), 2)
        self.assertEqual(PointMark.objects.get(point=point, student=self.student, kind='lecture').score, 90)

    def test_bulk_marks_clamped_and_cleared(self):
        self.client.force_login(self.teacher)
        point = ensure_points(self.course)[0]
        PointMark.objects.create(point=point, student=self.student, kind='lecture', score=50)
        self.client.post(reverse('education:set_point_marks', args=[self.course.id]), {
            'student': self.student.profile.id,
            f'p{point.id}_lecture': '150',
            f'p{point.id}_practice': '',
        })
        self.assertEqual(PointMark.objects.get(point=point, student=self.student, kind='lecture').score, 100)
        self.assertFalse(PointMark.objects.filter(point=point, student=self.student, kind='practice').exists())

    def test_student_cannot_open_points_page(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('education:course_points', args=[self.course.id]))
        self.assertEqual(response.status_code, 403)

    def test_points_page_saves_weights(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('education:course_points', args=[self.course.id]), {
            'count': '3', 'admission_score': '55',
            'weight_1': '40', 'pass_1': '50', 'title_1': 'Аттестация 1',
            'kw_1_lecture': '50', 'kw_1_practice': '30', 'kw_1_lab': '20',
            'weight_2': '30', 'weight_3': '30',
        })
        self.assertEqual(response.status_code, 302)
        self.course.refresh_from_db()
        self.assertEqual(self.course.admission_score, 55)
        self.assertEqual(self.course.control_points.count(), 3)
        point = self.course.control_points.get(number=1)
        self.assertEqual(point.weight, 40)
        self.assertEqual(sum(point.weights.values_list('weight', flat=True)), 100)

    def test_student_sees_own_rating(self):
        for point in ensure_points(self.course):
            for kind in ('lecture', 'practice', 'lab'):
                PointMark.objects.create(point=point, student=self.student, kind=kind, score=90)
        self.client.force_login(self.student)
        response = self.client.get(reverse('education:course_detail', args=[self.course.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Мои аттестации')
        self.assertContains(response, 'будет аттестован')

    def test_close_point_toggles(self):
        self.client.force_login(self.teacher)
        point = ensure_points(self.course)[0]
        self.client.post(reverse('education:close_point', args=[self.course.id]), {'point': point.id})
        point.refresh_from_db()
        self.assertTrue(point.closed)
