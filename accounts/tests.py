import json
from unittest.mock import patch

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from .models import (
	CollegeRegistrationCode,
	CollegeMasterList,
	DEPARTMENTS,
	DuesRecord,
	HODProfile,
	InChargeProfile,
	StaffRegistrationRequest,
	StudentProfile,
	UserRoleMapping,
)


class AcademicYearWorkflowTests(TestCase):
	def setUp(self):
		self.pin = '24001-cs-300'
		self.user = User.objects.create_user(username=self.pin, password='TestPass123!')
		UserRoleMapping.objects.create(user=self.user, role='student')
		self.previous_profile = StudentProfile.objects.create(
			user=self.user,
			pin=self.pin,
			mobile_number='9876543210',
			branch='cs',
			academic_year='2024-2025',
			is_active=False,
		)
		self.previous_due = DuesRecord.objects.create(
			student=self.previous_profile,
			department=DEPARTMENTS[0][0],
			status='cleared',
		)
		self.previous_pending_due = DuesRecord.objects.create(
			student=self.previous_profile,
			department=DEPARTMENTS[1][0],
			status='pending',
		)
		CollegeMasterList.objects.create(
			pin=self.pin,
			mobile_number='9876543210',
			full_name='Test Student',
			allowed_role='student',
			student_branch='cs',
			academic_year='2025-2026',
			is_active_batch=True,
		)

	def test_login_displays_error_for_unregistered_or_invalid_credentials(self):
		response = self.client.post(reverse('login'), {
			'username': '',
			'password': '',
		})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Enter both your user ID and password.')

		response = self.client.post(reverse('login'), {
			'username': 'not-registered',
			'password': 'WrongPass123!',
		})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Invalid PIN or account password.')
		self.assertContains(response, 'role="alert"')

		response = self.client.post(reverse('login'), {
			'username': self.pin,
			'password': 'WrongPass123!',
		})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Invalid PIN or account password.')

	def test_registration_displays_error_for_unrecognized_student_data(self):
		response = self.client.post(reverse('register'), {
			'user_pin': 'unrecognized-student',
			'mobile_number': '9876543210',
			'registration_code': 'invalid-code',
			'password': 'StrongPass123!',
			'confirm_password': 'StrongPass123!',
		})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "is not authorized")

	def test_registration_allows_common_password_but_requires_nonempty_password(self):
		pin = '24007-cs-306'
		CollegeMasterList.objects.create(
			pin=pin,
			mobile_number='9876543218',
			full_name='Password Validation Student',
			allowed_role='student',
			student_branch='cs',
			academic_year='2025-2026',
			is_active_batch=True,
		)
		registration_code = CollegeRegistrationCode(
			pin=pin,
			academic_year='2025-2026',
		)
		registration_code.set_code('student-code-306')
		registration_code.save()

		registration_data = {
			'user_pin': pin,
			'mobile_number': 'invalid-number',
			'registration_code': 'student-code-306',
			'password': 'StrongPass123!',
			'confirm_password': 'StrongPass123!',
		}
		response = self.client.post(reverse('register'), registration_data)
		self.assertContains(response, 'Enter a mobile number')

		registration_data['mobile_number'] = '9876543218'
		registration_data['password'] = ''
		registration_data['confirm_password'] = ''
		response = self.client.post(reverse('register'), registration_data)
		self.assertContains(response, 'Password is required.')

		registration_data['password'] = 'password'
		registration_data['confirm_password'] = 'password'
		response = self.client.post(reverse('register'), registration_data)
		self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
		student_user = User.objects.get(username=pin)
		self.assertTrue(student_user.check_password('password'))
		registration_code.refresh_from_db()
		self.assertEqual(registration_code.used_by, student_user)

	def test_staff_registration_accepts_short_or_common_passwords_for_both_roles(self):
		staff_entries = (
			(
				'incharge-short-password',
				'in_charge',
				'civil',
				'',
				'1',
			),
			(
				'hod-common-password',
				'hod',
				'',
				'cs',
				'password',
			),
		)
		for pin, role, department, branch, password in staff_entries:
			roster_entry = CollegeMasterList.objects.create(
				pin=pin,
				mobile_number='9876543220',
				full_name='Staff Password Demo',
				allowed_role=role,
				assigned_department=department or 'none',
				student_branch=branch or 'none',
				academic_year='2025-2026',
				is_active_batch=True,
			)
			StaffRegistrationRequest.objects.create(
				roster_entry=roster_entry,
				status='approved',
			)

			response = self.client.post(reverse('register'), {
				'user_pin': pin,
				'mobile_number': '9876543220',
				'registration_flow': 'staff_complete',
				'password': password,
				'confirm_password': password,
			})

			self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
			staff_user = User.objects.get(username=pin)
			self.assertTrue(staff_user.check_password(password))

	def test_student_spreadsheet_rejects_malformed_rows_before_import(self):
		class StudentRows:
			columns = {'pin', 'academic_year', 'mobile_number', 'full_name', 'branch'}

			def iterrows(self):
				return iter([
					(
						0,
						{
							'pin': '24008-cs-307',
							'academic_year': '2025-2026',
							'mobile_number': '9876543219',
							'full_name': 'Valid First Row',
							'branch': 'cs',
						},
					),
					(
						1,
						{
							'pin': '24009-cs-308',
							'academic_year': '2025-2026',
							'mobile_number': 'abc-def-g',
							'full_name': 'Invalid Mobile Student',
							'branch': 'cs',
						},
					),
				])

		admin_user = User.objects.create_superuser(
			username='spreadsheet-validation-admin',
			email='spreadsheet-admin@example.com',
			password='StrongPass123!',
		)
		self.client.force_login(admin_user)
		with patch('accounts.views.pd.read_excel', return_value=StudentRows()):
			response = self.client.post(
				reverse('upload_master_list_view'),
				{
					'student_excel': SimpleUploadedFile(
						'students.xlsx',
						b'test spreadsheet upload',
						content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
					),
				},
				follow=True,
			)

		self.assertContains(response, 'Row 3: mobile_number must contain 7-15 digits')
		self.assertFalse(CollegeMasterList.objects.filter(
			pin__in=('24008-cs-307', '24009-cs-308'),
		).exists())

		class MissingColumns:
			columns = {'pin'}

			def iterrows(self):
				return iter([])

		with patch('accounts.views.pd.read_excel', return_value=MissingColumns()):
			response = self.client.post(
				reverse('upload_master_list_view'),
				{
					'student_excel': SimpleUploadedFile(
						'incomplete-students.xlsx',
						b'test spreadsheet upload',
						content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
					),
				},
				follow=True,
			)

		self.assertContains(response, 'Student spreadsheet is missing columns:')

	def test_login_creates_new_year_ledger_without_replacing_history(self):
		self.previous_profile.is_active = True
		self.previous_profile.save(update_fields=('is_active',))

		response = self.client.post(reverse('login'), {
			'username': self.pin,
			'password': 'TestPass123!',
		})

		self.assertRedirects(response, reverse('student_view'), fetch_redirect_response=False)
		self.previous_profile.refresh_from_db()
		self.previous_due.refresh_from_db()
		self.assertFalse(self.previous_profile.is_active)
		self.assertEqual(self.previous_due.status, 'cleared')

		current_profile = StudentProfile.objects.get(
			user=self.user,
			pin=self.pin,
			academic_year='2025-2026',
		)
		self.assertTrue(current_profile.is_active)
		self.assertEqual(current_profile.dues.count(), len(DEPARTMENTS))
		self.assertEqual(self.user.student_profiles.count(), 2)

	def test_login_reuses_profile_when_roster_pin_case_changes(self):
		existing_profile = StudentProfile.objects.create(
			user=self.user,
			pin=self.pin.upper(),
			mobile_number='9876543210',
			branch='cs',
			academic_year='2025-2026',
			is_active=False,
		)
		self.client.post(reverse('login'), {
			'username': self.pin,
			'password': 'TestPass123!',
		})

		existing_profile.refresh_from_db()
		self.assertEqual(existing_profile.pin, self.pin)
		self.assertTrue(existing_profile.is_active)
		self.assertEqual(
			self.user.student_profiles.filter(academic_year='2025-2026').count(),
			1,
		)
		self.assertEqual(existing_profile.dues.count(), len(DEPARTMENTS))

	def test_previous_year_dashboard_is_read_only(self):
		self.client.force_login(self.user)

		response = self.client.get(reverse('student_view'), {
			'academic_year': '2024-2025',
		})
		self.assertEqual(response.context['student_profile'], self.previous_profile)
		self.assertFalse(response.context['can_apply'])

		response = self.client.post(reverse('student_view'), {
			'academic_year': '2024-2025',
		})
		self.assertEqual(response.status_code, 302)
		self.previous_due.refresh_from_db()
		self.previous_pending_due.refresh_from_db()
		self.assertEqual(self.previous_due.status, 'cleared')
		self.assertEqual(self.previous_pending_due.status, 'pending')

	def test_registration_code_is_bound_to_student_and_can_only_be_used_once(self):
		pin = '24002-cs-301'
		CollegeMasterList.objects.create(
			pin=pin,
			mobile_number='9876543213',
			full_name='New Student',
			allowed_role='student',
			student_branch='cs',
			academic_year='2025-2026',
			is_active_batch=True,
		)
		registration_code = CollegeRegistrationCode(
			pin=pin,
			academic_year='2025-2026',
		)
		registration_code.set_code('student-code-301')
		registration_code.save()

		registration_data = {
			'user_pin': pin,
			'mobile_number': '9876543213',
			'registration_code': 'wrong-code',
			'password': 'StrongPass123!',
			'confirm_password': 'StrongPass123!',
		}
		response = self.client.post(reverse('register'), registration_data)
		self.assertContains(response, 'Invalid college-issued registration code.')
		registration_code.refresh_from_db()
		self.assertIsNone(registration_code.used_at)
		self.assertFalse(User.objects.filter(username=pin).exists())

		response = self.client.post(reverse('register'), {
			**registration_data,
			'registration_code': 'student-code-301',
		})
		self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
		response = self.client.get(reverse('login'))
		self.assertContains(response, 'Registration successful! You can now log in.')
		student = User.objects.get(username=pin)
		registration_code.refresh_from_db()
		self.assertEqual(registration_code.used_by, student)
		self.assertIsNotNone(registration_code.used_at)
		self.assertNotEqual(registration_code.code_hash, 'student-code-301')

		student.delete()
		response = self.client.post(reverse('register'), {
			**registration_data,
			'registration_code': 'student-code-301',
		})
		self.assertContains(response, 'This registration code has already been used.')
		self.assertFalse(User.objects.filter(username=pin).exists())

	def test_registration_code_cannot_be_used_for_another_student_pin(self):
		CollegeMasterList.objects.create(
			pin='24003-cs-302',
			mobile_number='9876543214',
			full_name='Another Student',
			allowed_role='student',
			student_branch='cs',
			academic_year='2025-2026',
			is_active_batch=True,
		)
		registration_code = CollegeRegistrationCode(
			pin='24003-cs-302',
			academic_year='2025-2026',
		)
		registration_code.set_code('student-code-302')
		registration_code.save()

		response = self.client.post(reverse('register'), {
			'user_pin': '24003-cs-302',
			'mobile_number': '9876543214',
			'registration_code': 'student-code-301',
			'password': 'StrongPass123!',
			'confirm_password': 'StrongPass123!',
		})
		self.assertContains(response, 'Invalid college-issued registration code.')
		self.assertFalse(User.objects.filter(username='24003-cs-302').exists())

	def test_superuser_can_upload_student_registration_code_list(self):
		pin = '24004-cs-303'
		CollegeMasterList.objects.create(
			pin=pin,
			mobile_number='9876543215',
			full_name='Registration Code Student',
			allowed_role='student',
			student_branch='cs',
			academic_year='2025-2026',
			is_active_batch=True,
		)

		class CodeRows:
			columns = {'pin', 'academic_year', 'registration_code'}
			pin_value = '24004-cs-303'

			def iterrows(self):
				return iter([(
					0,
					{
						'pin': self.pin_value,
						'academic_year': '2025-2026',
						'registration_code': 'student-code-303',
					},
				)])

		admin_user = User.objects.create_superuser(
			username='code-admin',
			email='code-admin@example.com',
			password='StrongPass123!',
		)
		self.client.force_login(admin_user)
		with patch('accounts.views.pd.read_excel', return_value=CodeRows()):
			response = self.client.post(reverse('upload_master_list_view'), {
				'registration_codes_excel': SimpleUploadedFile(
					'registration-codes.xlsx',
					b'test spreadsheet upload',
					content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
				),
			})

		self.assertRedirects(
			response,
			reverse('upload_master_list_view'),
			fetch_redirect_response=False,
		)
		code_record = CollegeRegistrationCode.objects.get(
			pin=pin,
			academic_year='2025-2026',
		)
		self.assertTrue(code_record.check_code('student-code-303'))
		self.assertNotEqual(code_record.code_hash, 'student-code-303')

	def test_registration_code_upload_shows_database_busy_message(self):
		pin = '24005-cs-304'
		CollegeMasterList.objects.create(
			pin=pin,
			mobile_number='9876543217',
			full_name='Busy Database Student',
			allowed_role='student',
			student_branch='cs',
			academic_year='2025-2026',
			is_active_batch=True,
		)

		class CodeRows:
			columns = {'pin', 'academic_year', 'registration_code'}
			pin_value = pin

			def iterrows(self):
				return iter([(
					0,
					{
						'pin': self.pin_value,
						'academic_year': '2025-2026',
						'registration_code': 'student-code-304',
					},
				)])

		admin_user = User.objects.create_superuser(
			username='busy-db-admin',
			email='busy-db-admin@example.com',
			password='StrongPass123!',
		)
		self.client.force_login(admin_user)
		with (
			patch('accounts.views.pd.read_excel', return_value=CodeRows()),
			patch(
				'accounts.models.CollegeRegistrationCode.save',
				side_effect=OperationalError('database is locked'),
			),
		):
			response = self.client.post(
				reverse('upload_master_list_view'),
				{
					'registration_codes_excel': SimpleUploadedFile(
						'registration-codes.xlsx',
						b'test spreadsheet upload',
						content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
					),
				},
				follow=True,
			)

		self.assertContains(
			response,
			'The database is busy saving another change. Wait a few seconds, '
			'then upload the file again.',
		)

	def test_staff_must_receive_admin_approval_before_setting_password(self):
		staff_pin = 'staff-hod-cs'
		roster_entry = CollegeMasterList.objects.create(
			pin=staff_pin,
			mobile_number='9876543216',
			full_name='Staff HOD',
			allowed_role='hod',
			student_branch='cs',
			academic_year='2025-2026',
			is_active_batch=True,
		)
		staff_data = {
			'user_pin': staff_pin,
			'mobile_number': '9876543216',
			'registration_flow': 'staff_request',
		}

		response = self.client.post(reverse('register'), staff_data)
		self.assertContains(response, 'registration request has been sent')
		staff_request = StaffRegistrationRequest.objects.get(roster_entry=roster_entry)
		self.assertEqual(staff_request.status, 'pending')
		self.assertFalse(User.objects.filter(username=staff_pin).exists())

		complete_data = {
			'user_pin': staff_pin,
			'mobile_number': '9876543216',
			'registration_flow': 'staff_complete',
			'password': 'StaffPass123!',
			'confirm_password': 'StaffPass123!',
		}
		response = self.client.post(reverse('register'), complete_data)
		self.assertContains(response, 'must be approved by the college admin')
		self.assertFalse(User.objects.filter(username=staff_pin).exists())

		non_admin = User.objects.create_user(username='not-admin', password='TestPass123!')
		self.client.force_login(non_admin)
		response = self.client.post(reverse('staff_registration_admin_view'), {
			'request_id': staff_request.id,
			'action': 'approve',
		})
		self.assertEqual(response.status_code, 302)
		staff_request.refresh_from_db()
		self.assertEqual(staff_request.status, 'pending')

		admin_user = User.objects.create_superuser(
			username='staff-approval-admin',
			email='staff-admin@example.com',
			password='AdminPass123!',
		)
		self.client.force_login(admin_user)
		response = self.client.post(reverse('staff_registration_admin_view'), {
			'request_id': staff_request.id,
			'action': 'approve',
		})
		self.assertRedirects(
			response,
			reverse('staff_registration_admin_view'),
			fetch_redirect_response=False,
		)
		staff_request.refresh_from_db()
		self.assertEqual(staff_request.status, 'approved')
		self.assertEqual(staff_request.reviewed_by, admin_user)

		self.client.logout()
		response = self.client.post(reverse('register'), complete_data)
		self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
		staff_user = User.objects.get(username=staff_pin)
		self.assertEqual(staff_user.role_mapping.role, 'hod')
		self.assertEqual(staff_user.hod_profile.managing_branch, 'cs')
		staff_request.refresh_from_db()
		self.assertEqual(staff_request.status, 'completed')

	def test_rejected_staff_registration_can_be_resubmitted(self):
		staff_pin = 'staff-incharge-civil'
		roster_entry = CollegeMasterList.objects.create(
			pin=staff_pin,
			mobile_number='9876543217',
			full_name='Civil In-Charge',
			allowed_role='in_charge',
			assigned_department='civil',
			academic_year='2025-2026',
			is_active_batch=True,
		)
		staff_request = StaffRegistrationRequest.objects.create(
			roster_entry=roster_entry,
			status='rejected',
		)

		response = self.client.post(reverse('register'), {
			'user_pin': staff_pin,
			'mobile_number': '9876543217',
			'registration_flow': 'staff_request',
		})
		self.assertContains(response, 'registration request has been sent')
		staff_request.refresh_from_db()
		self.assertEqual(staff_request.status, 'pending')
		self.assertFalse(User.objects.filter(username=staff_pin).exists())

	def test_incharge_can_view_previous_year_without_edit_controls(self):
		staff_user = User.objects.create_user(username='faculty-1', password='TestPass123!')
		UserRoleMapping.objects.create(user=staff_user, role='in_charge')
		InChargeProfile.objects.create(
			user=staff_user,
			employee_id='faculty-1',
			mobile_number='9876543211',
			assigned_department=DEPARTMENTS[0][0],
		)
		self.client.force_login(staff_user)

		response = self.client.get(reverse('in_charge_view'), {
			'branch': 'cs',
			'academic_year': '2024-2025',
		})

		self.assertEqual(response.context['academic_year'], '2024-2025')
		self.assertFalse(response.context['can_update_year'])
		self.assertEqual(list(response.context['dues_records']), [self.previous_due])

		response = self.client.post(
			reverse('update_department_due_api'),
			data=json.dumps({'record_id': self.previous_due.id, 'action': 'clear'}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 404)
		self.previous_due.refresh_from_db()
		self.assertEqual(self.previous_due.status, 'cleared')

	def test_incharge_rejection_requires_a_nonempty_reason(self):
		profile = StudentProfile.objects.create(
			user=self.user,
			pin=self.pin,
			mobile_number='9876543210',
			branch='cs',
			academic_year='2025-2026',
			is_active=True,
		)
		pending_due = DuesRecord.objects.create(
			student=profile,
			department=DEPARTMENTS[0][0],
			status='pending',
		)
		staff_user = User.objects.create_user(username='reason-required-incharge')
		UserRoleMapping.objects.create(user=staff_user, role='in_charge')
		InChargeProfile.objects.create(
			user=staff_user,
			employee_id='reason-required-incharge',
			mobile_number='9876543211',
			assigned_department=DEPARTMENTS[0][0],
		)
		self.client.force_login(staff_user)

		response = self.client.post(
			reverse('update_department_due_api'),
			data=json.dumps({'record_id': pending_due.id, 'action': 'reject'}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 400)
		pending_due.refresh_from_db()
		self.assertEqual(pending_due.status, 'pending')

	def test_hod_can_view_previous_year_without_signing_it_off(self):
		hod_user = User.objects.create_user(username='hod-1', password='TestPass123!')
		UserRoleMapping.objects.create(user=hod_user, role='hod')
		HODProfile.objects.create(
			user=hod_user,
			employee_id='hod-1',
			mobile_number='9876543212',
			managing_branch='cse',
		)
		self.client.force_login(hod_user)

		response = self.client.get(reverse('hod_view'), {
			'academic_year': '2024-2025',
		})

		self.assertEqual(response.context['academic_year'], '2024-2025')
		self.assertIn(self.previous_profile, response.context['pending_students'])
		self.assertFalse(self.previous_profile.is_active)

		response = self.client.post(
			reverse('update_hod_signoff_api'),
			data=json.dumps({'profile_id': self.previous_profile.id, 'action': 'clear'}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 404)
		self.previous_profile.refresh_from_db()
		self.assertFalse(self.previous_profile.is_approved)

	def test_hod_dashboard_lists_computer_science_students_by_branch(self):
		hod_user = User.objects.create_user(username='hod-cs', password='TestPass123!')
		UserRoleMapping.objects.create(user=hod_user, role='hod')
		HODProfile.objects.create(
			user=hod_user,
			employee_id='hod-cs',
			mobile_number='9876543212',
			managing_branch='cs',
		)

		registered_user = User.objects.create_user(
			username='registered-cs',
			first_name='Registered Student',
		)
		registered_profile = StudentProfile.objects.create(
			user=registered_user,
			pin='registered-cs-300',
			mobile_number='9876543213',
			branch='Computer Science',
			academic_year='2025-2026',
		)
		cleared_user = User.objects.create_user(username='cleared-cs', first_name='Cleared Student')
		cleared_profile = StudentProfile.objects.create(
			user=cleared_user,
			pin='cleared-student-301',
			mobile_number='9876543214',
			branch='cse',
			academic_year='2025-2026',
		)
		other_user = User.objects.create_user(username='registered-ec', first_name='Other Branch Student')
		StudentProfile.objects.create(
			user=other_user,
			pin='registered-ec-302',
			mobile_number='9876543215',
			branch='ec',
			academic_year='2025-2026',
		)

		for department, _ in DEPARTMENTS:
			DuesRecord.objects.create(student=registered_profile, department=department)
			DuesRecord.objects.create(
				student=cleared_profile,
				department=department,
				status='cleared',
			)
		duplicate_profile = StudentProfile.objects.create(
			user=cleared_user,
			pin='cleared-student-301-duplicate',
			mobile_number='9876543214',
			branch='cse',
			academic_year='2025-2026',
		)
		for department, _ in DEPARTMENTS:
			DuesRecord.objects.create(student=duplicate_profile, department=department)

		self.client.force_login(hod_user)
		response = self.client.get(reverse('hod_view'), {'academic_year': '2025-2026'})

		self.assertEqual(response.status_code, 200)
		self.assertIn(registered_profile, response.context['inactive_students'])
		self.assertIn(cleared_profile, response.context['cleared_students'])
		self.assertIn(cleared_profile, response.context['ready_to_clear_students'])
		self.assertNotIn(duplicate_profile, response.context['inactive_students'])
		self.assertNotIn(other_user.student_profiles.first(), response.context['inactive_students'])

		response = self.client.post(
			reverse('update_hod_signoff_api'),
			data=json.dumps({'profile_id': cleared_profile.id, 'action': 'clear'}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 200)
		cleared_profile.refresh_from_db()
		self.assertTrue(cleared_profile.is_approved)
		self.assertEqual(cleared_profile.approved_by, hod_user)
		self.assertIsNotNone(cleared_profile.approved_at)

	def test_student_resubmits_rejected_department_clearance_to_incharge_queue(self):
		profile = StudentProfile.objects.create(
			user=self.user,
			pin=self.pin,
			mobile_number='9876543210',
			branch='cs',
			academic_year='2025-2026',
			is_active=True,
		)
		rejected_due = None
		for department, _ in DEPARTMENTS:
			record = DuesRecord.objects.create(
				student=profile,
				department=department,
				status='pending',
			)
			if rejected_due is None:
				rejected_due = record

		incharge_user = User.objects.create_user(username='library-incharge')
		UserRoleMapping.objects.create(user=incharge_user, role='in_charge')
		InChargeProfile.objects.create(
			user=incharge_user,
			employee_id='library-incharge',
			mobile_number='9876543211',
			assigned_department=rejected_due.department,
		)
		self.client.force_login(incharge_user)
		reject_response = self.client.post(
			reverse('update_department_due_api'),
			data=json.dumps({
				'record_id': rejected_due.id,
				'action': 'reject',
				'remark': 'Please resolve the outstanding library item.',
			}),
			content_type='application/json',
		)
		self.assertEqual(reject_response.status_code, 200)
		rejected_due.refresh_from_db()
		self.assertEqual(rejected_due.status, 'rejected')
		self.assertEqual(
			rejected_due.staff_remarks,
			'Please resolve the outstanding library item.',
		)

		queue_response = self.client.get(reverse('in_charge_view'), {
			'branch': 'cs',
			'academic_year': profile.academic_year,
		})
		self.assertNotIn(rejected_due, queue_response.context['dues_records'])

		self.client.force_login(self.user)
		response = self.client.post(reverse('student_view'), {
			'academic_year': profile.academic_year,
			'action': 'resubmit_departments',
		})
		self.assertRedirects(
			response,
			f"{reverse('student_view')}?academic_year={profile.academic_year}",
			fetch_redirect_response=False,
		)
		rejected_due.refresh_from_db()
		self.assertEqual(rejected_due.status, 'pending')
		self.assertEqual(
			rejected_due.staff_remarks,
			'Please resolve the outstanding library item.',
		)

		self.client.force_login(incharge_user)
		queue_response = self.client.get(reverse('in_charge_view'), {
			'branch': 'cs',
			'academic_year': profile.academic_year,
		})
		self.assertIn(rejected_due, queue_response.context['dues_records'])
		self.assertContains(queue_response, 'Previous rejection reason')

	def test_hod_rejection_reason_requires_student_resubmission_before_reconsideration(self):
		profile = StudentProfile.objects.create(
			user=self.user,
			pin=self.pin,
			mobile_number='9876543210',
			branch='cs',
			academic_year='2025-2026',
			is_active=True,
		)
		for department, _ in DEPARTMENTS:
			DuesRecord.objects.create(
				student=profile,
				department=department,
				status='cleared',
			)

		hod_user = User.objects.create_user(username='hod-reviewer')
		UserRoleMapping.objects.create(user=hod_user, role='hod')
		HODProfile.objects.create(
			user=hod_user,
			employee_id='hod-reviewer',
			mobile_number='9876543212',
			managing_branch='cs',
		)
		self.client.force_login(hod_user)
		reject_response = self.client.post(
			reverse('update_hod_signoff_api'),
			data=json.dumps({
				'profile_id': profile.id,
				'action': 'reject',
				'remark': 'Submit the missing physical library clearance.',
			}),
			content_type='application/json',
		)
		self.assertEqual(reject_response.status_code, 200)
		profile.refresh_from_db()
		self.assertEqual(
			profile.hod_rejection_reason,
			'Submit the missing physical library clearance.',
		)
		self.assertFalse(profile.hod_resubmitted)

		hod_dashboard = self.client.get(reverse('hod_view'), {
			'academic_year': profile.academic_year,
		})
		self.assertNotIn(profile, hod_dashboard.context['ready_to_clear_students'])
		blocked_approval = self.client.post(
			reverse('update_hod_signoff_api'),
			data=json.dumps({'profile_id': profile.id, 'action': 'clear'}),
			content_type='application/json',
		)
		self.assertEqual(blocked_approval.status_code, 400)

		self.client.force_login(self.user)
		student_dashboard = self.client.get(reverse('student_view'), {
			'academic_year': profile.academic_year,
		})
		self.assertTrue(student_dashboard.context['can_resubmit_hod'])
		self.assertContains(student_dashboard, 'Submit the missing physical library clearance.')
		resubmit_response = self.client.post(reverse('student_view'), {
			'academic_year': profile.academic_year,
			'action': 'resubmit_hod',
		})
		self.assertEqual(resubmit_response.status_code, 302)
		profile.refresh_from_db()
		self.assertTrue(profile.hod_resubmitted)
		self.assertEqual(
			profile.hod_rejection_reason,
			'Submit the missing physical library clearance.',
		)

		self.client.force_login(hod_user)
		hod_dashboard = self.client.get(reverse('hod_view'), {
			'academic_year': profile.academic_year,
		})
		self.assertIn(profile, hod_dashboard.context['ready_to_clear_students'])
		self.assertContains(hod_dashboard, 'Student resubmitted')
		approve_response = self.client.post(
			reverse('update_hod_signoff_api'),
			data=json.dumps({'profile_id': profile.id, 'action': 'clear'}),
			content_type='application/json',
		)
		self.assertEqual(approve_response.status_code, 200)
		profile.refresh_from_db()
		self.assertTrue(profile.is_approved)
		self.assertFalse(profile.hod_rejection_reason)
		self.assertFalse(profile.hod_resubmitted)

	def test_admin_student_list_shows_fully_cleared_dues(self):
		admin_user = User.objects.create_superuser(
			username='dues-admin',
			email='dues-admin@example.com',
			password='StrongPass123!',
		)
		profile = StudentProfile.objects.create(
			user=self.user,
			pin='24003-cs-302',
			mobile_number='9876543210',
			branch='cs',
			academic_year='2025-2026',
		)
		for department, _ in DEPARTMENTS:
			DuesRecord.objects.create(
				student=profile,
				department=department,
				status='cleared',
			)

		self.client.force_login(admin_user)
		response = self.client.get(reverse('admin:accounts_studentprofile_changelist'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Fully Cleared')
		admin_profile = next(
			result for result in response.context['cl'].result_list
			if result.pk == profile.pk
		)
		self.assertEqual(admin_profile.admin_cleared_count, len(DEPARTMENTS))

	def test_registration_code_admin_list_renders_code_status(self):
		admin_user = User.objects.create_superuser(
			username='registration-code-admin',
			email='registration-code-admin@example.com',
			password='StrongPass123!',
		)
		available_code = CollegeRegistrationCode(
			pin='available-pin',
			academic_year='2025-2026',
		)
		available_code.set_code('available-registration-code')
		available_code.save()

		used_code = CollegeRegistrationCode(
			pin='used-pin',
			academic_year='2025-2026',
			used_at=timezone.now(),
			used_by=admin_user,
		)
		used_code.set_code('used-registration-code')
		used_code.save()

		self.client.force_login(admin_user)
		response = self.client.get(
			reverse('admin:accounts_collegeregistrationcode_changelist')
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Available')
		self.assertContains(response, 'Used')

	def test_student_downloads_certificate_only_after_hod_approval(self):
		self.user.first_name = 'Test Student'
		self.user.save(update_fields=('first_name',))
		profile = StudentProfile.objects.create(
			user=self.user,
			pin='24002-cs-301',
			mobile_number='9876543210',
			branch='cs',
			academic_year='2025-2026',
		)
		for department, _ in DEPARTMENTS:
			DuesRecord.objects.create(
				student=profile,
				department=department,
				status='cleared',
			)

			incharge_user = User.objects.create_user(
				username=f'incharge-{department}',
				first_name=f'{department.title()} Incharge',
				last_name='Name',
			)
			InChargeProfile.objects.create(
				user=incharge_user,
				employee_id=f'incharge-{department}',
				mobile_number='9876543211',
				assigned_department=department,
			)

		self.client.force_login(self.user)
		student_dashboard = self.client.get(reverse('student_view'), {
			'academic_year': profile.academic_year,
		})
		self.assertFalse(student_dashboard.context['can_download_certificate'])
		self.assertTrue(student_dashboard.context['awaiting_hod_approval'])
		self.assertContains(student_dashboard, 'awaiting HOD approval')
		response = self.client.get(reverse(
			'download_no_dues_certificate',
			args=(profile.id,),
		))
		self.assertEqual(response.status_code, 403)

		hod_user = User.objects.create_user(
			username='approving-hod',
			first_name='HOD First',
			last_name='HOD Last',
		)
		UserRoleMapping.objects.create(user=hod_user, role='hod')
		HODProfile.objects.create(
			user=hod_user,
			employee_id='approving-hod',
			mobile_number='9876543212',
			managing_branch='cs',
		)
		self.client.force_login(hod_user)
		approval_response = self.client.post(
			reverse('update_hod_signoff_api'),
			data=json.dumps({'profile_id': profile.id, 'action': 'clear'}),
			content_type='application/json',
		)
		self.assertEqual(approval_response.status_code, 200)
		profile.refresh_from_db()
		self.assertEqual(profile.approved_by, hod_user)
		self.assertIsNotNone(profile.approved_at)

		self.client.force_login(self.user)
		student_dashboard = self.client.get(reverse('student_view'), {
			'academic_year': profile.academic_year,
		})
		self.assertTrue(student_dashboard.context['can_download_certificate'])
		self.assertContains(student_dashboard, 'Download Approved No Dues PDF')

		response = self.client.get(reverse(
			'download_no_dues_certificate',
			args=(profile.id,),
		))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertIn('attachment;', response['Content-Disposition'])
		self.assertTrue(response.content.startswith(b'%PDF-1.4'))
		for department, department_name in DEPARTMENTS:
			self.assertIn(department_name.encode('cp1252'), response.content)
			self.assertIn(f'{department.title()} Incharge Name'.encode('ascii'), response.content)
		self.assertIn(b'APPROVED', response.content)
		self.assertIn(b'FINAL HOD APPROVED', response.content)
		self.assertIn(b'0.02 0.59 0.41 RG', response.content)
		self.assertIn(b'0.42 0.27 0.21 rg 40 651 515 22 re f', response.content)
		self.assertIn(b'50 659 Td (CLEARANCE DEPARTMENT)', response.content)
		self.assertIn(b'250 659 Td (IN-CHARGE NAME)', response.content)
		self.assertIn(b'50 635 Td (1. Civil Section)', response.content)
		self.assertIn(b'250 637 Td (Civil Incharge Name)', response.content)
		self.assertIn(b'Test Student', response.content)
		self.assertIn(b'24002-cs-301', response.content)
		self.assertIn(b'2025-2026', response.content)
		self.assertIn(b'HOD First HOD Last', response.content)
