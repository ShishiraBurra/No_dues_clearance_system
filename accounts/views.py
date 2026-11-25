from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.db import OperationalError, transaction
from django.db.models import Q
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from .models import (
    DEPARTMENTS,
    CollegeMasterList,
    CollegeRegistrationCode,
    DuesRecord,
    HODProfile,
    InChargeProfile,
    StaffRegistrationRequest,
    StudentProfile,
    UserRoleMapping,
)
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.contrib.admin.views.decorators import staff_member_required
import json
import pandas as pd
import re
from django.contrib.auth.decorators import user_passes_test


def _is_valid_mobile_number(value):
    return len(value) <= 15 and re.fullmatch(r'\+?[0-9]{7,15}', value) is not None


def _is_valid_academic_year(value):
    return re.fullmatch(r'[0-9]{4}-[0-9]{4}', value) is not None


def _validate_spreadsheet_columns(dataframe, required_columns, sheet_name):
    missing_columns = required_columns.difference(dataframe.columns)
    if missing_columns:
        missing = ', '.join(sorted(missing_columns))
        raise ValueError(f'{sheet_name} spreadsheet is missing columns: {missing}.')


def _spreadsheet_value(row, column, row_number, max_length):
    value = row[column]
    if pd.isna(value):
        raise ValueError(f'Row {row_number}: {column} is required.')

    value = str(value).strip()
    if not value:
        raise ValueError(f'Row {row_number}: {column} is required.')
    if len(value) > max_length:
        raise ValueError(
            f'Row {row_number}: {column} must be no more than {max_length} characters.'
        )
    return value


def _password_input_error(password, confirmation):
    if not password:
        return 'Password is required.'
    if password != confirmation:
        return 'Passwords do not match.'
    return ''


def _active_student_roster(pin):
    return CollegeMasterList.objects.filter(
        pin__iexact=pin,
        allowed_role='student',
        is_active_batch=True,
    ).order_by('-academic_year').first()


def _provision_student_year(user, roster_entry):
    StudentProfile.objects.filter(user=user, is_active=True).exclude(
        academic_year=roster_entry.academic_year
    ).update(is_active=False)

    profile, created = StudentProfile.objects.get_or_create(
        pin__iexact=roster_entry.pin,
        academic_year=roster_entry.academic_year,
        defaults={
            'pin': roster_entry.pin,
            'user': user,
            'mobile_number': roster_entry.mobile_number,
            'branch': roster_entry.student_branch,
            'is_active': True,
        },
    )
    if profile.user_id != user.pk:
        raise ValueError('This student PIN is already linked to another account.')

    profile.mobile_number = roster_entry.mobile_number
    profile.branch = roster_entry.student_branch
    profile.pin = roster_entry.pin
    profile.is_active = True
    profile.save(update_fields=('pin', 'mobile_number', 'branch', 'is_active'))

    for department, _ in DEPARTMENTS:
        DuesRecord.objects.get_or_create(student=profile, department=department)

    return profile


def _select_year(requested_year, available_years, active_years=()):
    if requested_year in available_years:
        return requested_year
    return next((year for year in available_years if year in active_years),
                available_years[0] if available_years else '')


def _branch_token(managing_branch):
    branch = ''.join(character for character in managing_branch.lower() if character.isalnum())
    if branch in ('cs', 'cse') or 'computer' in branch:
        return 'cs'
    if branch in ('ec', 'ece') or 'electronic' in branch:
        return 'ec'
    if branch in ('me', 'mech') or 'mechanical' in branch:
        return 'me'
    if branch in ('ee', 'eee') or 'electrical' in branch:
        return 'ee'
    if branch in ('au', 'automobile', 'automotive') or 'automobile' in branch:
        return 'au'
    return 'ce'


def _branch_profile_filter(branch_token):
    branch_aliases = {
        'cs': ('cs', 'cse', 'computer', 'computer science', 'computer science and engineering'),
        'ec': ('ec', 'ece', 'electronic', 'electronics', 'electronics and communication'),
        'me': ('me', 'mech', 'mechanical'),
        'ee': ('ee', 'eee', 'electrical', 'electrical and electronics'),
        'au': ('au', 'automobile', 'automotive'),
        'ce': ('ce', 'civil', 'civil engineering'),
    }[branch_token]
    branch_filter = Q(pin__icontains=f"-{branch_token}-")
    for branch_alias in branch_aliases:
        branch_filter |= Q(branch__iexact=branch_alias)
    return branch_filter


def _pdf_literal(value):
    encoded = str(value).encode('cp1252', errors='replace')
    escaped = bytearray()
    for character in encoded:
        if character in (ord('\\'), ord('('), ord(')')):
            escaped.append(ord('\\'))
        if character < 32 or character > 126:
            escaped.extend(f'\\{character:03o}'.encode('ascii'))
        else:
            escaped.append(character)
    return bytes(escaped)


def _certificate_pdf(student_profile, departments, incharge_names):
    commands = []
    brown = (0.42, 0.27, 0.21)
    dark_brown = (0.21, 0.16, 0.14)
    green = (0.02, 0.59, 0.41)
    cream = (0.98, 0.96, 0.93)
    page_width = 595

    def text(x, y, value, size=10, color=(0.16, 0.13, 0.11)):
        commands.append(
            f'{color[0]} {color[1]} {color[2]} rg BT /F1 {size} Tf '
            f'{x} {y} Td ('.encode('ascii')
            + _pdf_literal(value)
            + b') Tj ET'
        )

    def center_text(center_x, y, value, size=10, color=dark_brown):
        widths = {
            ' ': 278, '!': 278, '"': 355, '#': 556, '$': 556, '%': 889,
            '&': 667, "'": 191, '(': 333, ')': 333, '*': 389, '+': 584,
            ',': 278, '-': 333, '.': 278, '/': 278,
            '0': 556, '1': 556, '2': 556, '3': 556, '4': 556,
            '5': 556, '6': 556, '7': 556, '8': 556, '9': 556,
            ':': 278, ';': 278, '<': 584, '=': 584, '>': 584, '?': 556,
            '@': 1015, 'A': 667, 'B': 667, 'C': 722, 'D': 722,
            'E': 667, 'F': 611, 'G': 778, 'H': 722, 'I': 278,
            'J': 500, 'K': 667, 'L': 556, 'M': 833, 'N': 722,
            'O': 778, 'P': 667, 'Q': 778, 'R': 722, 'S': 667,
            'T': 611, 'U': 722, 'V': 667, 'W': 944, 'X': 667,
            'Y': 667, 'Z': 611, '[': 278, '\\': 278, ']': 278,
            '^': 469, '_': 556, '`': 333, 'a': 556, 'b': 556,
            'c': 500, 'd': 556, 'e': 556, 'f': 278, 'g': 556,
            'h': 556, 'i': 222, 'j': 222, 'k': 500, 'l': 222,
            'm': 833, 'n': 556, 'o': 556, 'p': 556, 'q': 556,
            'r': 333, 's': 500, 't': 278, 'u': 556, 'v': 500,
            'w': 722, 'x': 500, 'y': 500, 'z': 500, '{': 334,
            '|': 260, '}': 334, '~': 584,
        }
        rendered = str(value).encode('cp1252', errors='replace').decode('cp1252')
        width = sum(widths.get(character, 556) for character in rendered) * size / 1000
        text(center_x - width / 2, y, value, size, color)

    def line(x1, y1, x2, y2, color=(0.75, 0.72, 0.68)):
        commands.append(
            f'{color[0]} {color[1]} {color[2]} RG {x1} {y1} m {x2} {y2} l S'.encode('ascii')
        )

    def stamp(x, y, label, width=88):
        commands.append(
            f'{green[0]} {green[1]} {green[2]} RG 1 w {x} {y} {width} 17 re S'.encode('ascii')
        )
        center_text(x + width / 2, y + 5, label, 8, green)

    def wrap_name(value, width=44):
        lines = []
        current = ''
        for word in value.split():
            while len(word) > width:
                if current:
                    lines.append(current)
                    current = ''
                lines.append(word[:width])
                word = word[width:]
            candidate = f'{current} {word}'.strip()
            if len(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or ['Not assigned']

    commands.append(
        f'{cream[0]} {cream[1]} {cream[2]} rg 0 0 {page_width} 842 re f'.encode('ascii')
    )
    center_text(page_width / 2, 790, 'NO DUES CLEARANCE CERTIFICATE', 18, brown)
    center_text(page_width / 2, 771, 'Final clearance approved by the Head of Department', 10)

    commands.extend([
        b'0.42 0.27 0.21 RG 1 w 40 692 515 62 re S',
        b'0.42 0.27 0.21 RG 40 692 m 555 692 l S',
    ])
    center_text(page_width / 2, 735, f"Student name: {student_profile.user.get_full_name() or student_profile.user.username}", 11)
    center_text(page_width / 2, 715, f'PIN: {student_profile.pin}     Branch: {student_profile.branch.upper()}', 10)
    center_text(page_width / 2, 699, f'Academic year: {student_profile.academic_year}', 10)

    commands.append(b'0.42 0.27 0.21 rg 40 651 515 22 re f')
    text(50, 659, 'CLEARANCE DEPARTMENT', 9, (1, 1, 1))
    text(250, 659, 'IN-CHARGE NAME', 9, (1, 1, 1))
    center_text(500, 659, 'APPROVAL', 9, (1, 1, 1))

    row_top = 651
    for index, (department_code, department_name) in enumerate(departments):
        names = ', '.join(incharge_names.get(department_code, ())) or 'Not assigned'
        name_lines = wrap_name(names)
        row_height = max(24, len(name_lines) * 10 + 12)
        text(50, row_top - 16, f'{index + 1}. {department_name}', 9)
        for line_index, name_line in enumerate(name_lines):
            text(250, row_top - 14 - line_index * 10, name_line, 8)
        stamp(455, row_top - (row_height + 17) / 2, 'APPROVED')
        row_top -= row_height
        line(40, row_top, 555, row_top, (0.88, 0.86, 0.83))

    approver = student_profile.approved_by
    approver_name = (
        approver.get_full_name() or approver.username
        if approver else 'Head of Department'
    )
    approval_date = (
        timezone.localtime(student_profile.approved_at).strftime('%Y-%m-%d')
        if student_profile.approved_at else 'Date unavailable'
    )
    commands.append(b'0.02 0.59 0.41 RG 1.5 w 193.5 68 208 66 re S')
    center_text(page_width / 2, 116, 'FINAL HOD APPROVED', 11, green)
    center_text(page_width / 2, 96, f'Approved by: {approver_name}', 8, green)
    center_text(page_width / 2, 80, f'Date: {approval_date}', 8, green)

    stream = b'\n'.join(commands)
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
        b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica '
        b'/Encoding /WinAnsiEncoding >>',
        b'<< /Length ' + str(len(stream)).encode('ascii') + b' >>\nstream\n'
        + stream + b'\nendstream',
    ]
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f'{object_number} 0 obj\n'.encode('ascii'))
        pdf.extend(body)
        pdf.extend(b'\nendobj\n')

    xref_offset = len(pdf)
    pdf.extend(f'xref\n0 {len(objects) + 1}\n'.encode('ascii'))
    pdf.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        pdf.extend(f'{offset:010} 00000 n \n'.encode('ascii'))
    pdf.extend(
        f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n'
        f'startxref\n{xref_offset}\n%%EOF\n'.encode('ascii')
    )
    return bytes(pdf)


@user_passes_test(lambda user: user.is_superuser)
@transaction.atomic
def upload_master_list_view(request):
    if request.method == 'POST':
        student_file = request.FILES.get('student_excel')
        in_charge_file = request.FILES.get('in_charge_excel')
        hod_file = request.FILES.get('hod_excel')
        registration_codes_file = request.FILES.get('registration_codes_excel')

        try:
            uploaded_files = (
                ('Student', student_file),
                ('In-charge', in_charge_file),
                ('HOD', hod_file),
                ('Registration-code', registration_codes_file),
            )
            if not any(uploaded_file for _, uploaded_file in uploaded_files):
                raise ValueError('Select at least one spreadsheet to upload.')
            for sheet_name, uploaded_file in uploaded_files:
                if uploaded_file and not uploaded_file.name.lower().endswith('.xlsx'):
                    raise ValueError(f'{sheet_name} spreadsheet must be an .xlsx file.')

            if student_file:
                df_student = pd.read_excel(student_file)
                _validate_spreadsheet_columns(
                    df_student,
                    {'pin', 'academic_year', 'mobile_number', 'full_name', 'branch'},
                    'Student',
                )
                seen_students = set()
                row_count = 0
                for row_count, (_, row) in enumerate(df_student.iterrows(), start=1):
                    row_number = row_count + 1
                    student_pin = _spreadsheet_value(row, 'pin', row_number, 50)
                    academic_year = _spreadsheet_value(row, 'academic_year', row_number, 9)
                    mobile_number = _spreadsheet_value(row, 'mobile_number', row_number, 15)
                    full_name = _spreadsheet_value(row, 'full_name', row_number, 100)
                    branch = _spreadsheet_value(row, 'branch', row_number, 20).lower()
                    if not _is_valid_academic_year(academic_year):
                        raise ValueError(
                            f'Row {row_number}: academic_year must use the YYYY-YYYY format.'
                        )
                    if not _is_valid_mobile_number(mobile_number):
                        raise ValueError(
                            f'Row {row_number}: mobile_number must contain 7-15 digits '
                            'with an optional leading +.'
                        )
                    student_key = (student_pin.casefold(), academic_year)
                    if student_key in seen_students:
                        raise ValueError(
                            f'Row {row_number}: duplicate student PIN and academic year.'
                        )
                    seen_students.add(student_key)

                    CollegeMasterList.objects.filter(
                        pin__iexact=student_pin,
                        allowed_role='student',
                    ).exclude(academic_year=academic_year).update(is_active_batch=False)
                    StudentProfile.objects.filter(
                        pin__iexact=student_pin,
                    ).exclude(academic_year=academic_year).update(is_active=False)

                    CollegeMasterList.objects.update_or_create(
                        pin__iexact=student_pin,
                        academic_year=academic_year,
                        defaults={
                            'pin': student_pin,
                            'mobile_number': mobile_number,
                            'full_name': full_name,
                            'allowed_role': 'student',
                            'student_branch': branch,
                            'assigned_department': 'none',
                            'is_active_batch': True
                        }
                    )

                    existing_user = User.objects.filter(username__iexact=student_pin).first()
                    if (existing_user and
                            getattr(getattr(existing_user, 'role_mapping', None), 'role', None) == 'student'):
                        roster_entry = _active_student_roster(student_pin)
                        if roster_entry:
                            _provision_student_year(existing_user, roster_entry)
                if row_count == 0:
                    raise ValueError('Student spreadsheet must contain at least one data row.')

            if in_charge_file:
                df_charge = pd.read_excel(in_charge_file)
                _validate_spreadsheet_columns(
                    df_charge,
                    {'identity_code', 'mobile_number', 'full_name', 'assigned_department'},
                    'In-charge',
                )
                seen_staff_ids = set()
                row_count = 0
                for row_count, (_, row) in enumerate(df_charge.iterrows(), start=1):
                    row_number = row_count + 1
                    identity_code = _spreadsheet_value(row, 'identity_code', row_number, 50)
                    mobile_number = _spreadsheet_value(row, 'mobile_number', row_number, 15)
                    full_name = _spreadsheet_value(row, 'full_name', row_number, 100)
                    department = _spreadsheet_value(
                        row, 'assigned_department', row_number, 20
                    ).lower()
                    if not _is_valid_mobile_number(mobile_number):
                        raise ValueError(
                            f'Row {row_number}: mobile_number must contain 7-15 digits '
                            'with an optional leading +.'
                        )
                    if department not in {code for code, _ in DEPARTMENTS}:
                        raise ValueError(
                            f'Row {row_number}: assigned_department is not a valid department code.'
                        )
                    staff_key = identity_code.casefold()
                    if staff_key in seen_staff_ids:
                        raise ValueError(f'Row {row_number}: duplicate staff ID.')
                    seen_staff_ids.add(staff_key)

                    CollegeMasterList.objects.update_or_create(
                        pin=identity_code,
                        defaults={
                            'mobile_number': mobile_number,
                            'full_name': full_name,
                            'allowed_role': 'in_charge',
                            'assigned_department': department,
                            'is_active_batch': True
                        }
                    )
                if row_count == 0:
                    raise ValueError('In-charge spreadsheet must contain at least one data row.')

            if hod_file:
                df_hod = pd.read_excel(hod_file)
                _validate_spreadsheet_columns(
                    df_hod,
                    {'identity_code', 'mobile_number', 'full_name', 'assigned_department'},
                    'HOD',
                )
                seen_hod_ids = set()
                row_count = 0
                for row_count, (_, row) in enumerate(df_hod.iterrows(), start=1):
                    row_number = row_count + 1
                    identity_code = _spreadsheet_value(row, 'identity_code', row_number, 50)
                    mobile_number = _spreadsheet_value(row, 'mobile_number', row_number, 15)
                    full_name = _spreadsheet_value(row, 'full_name', row_number, 100)
                    branch = _spreadsheet_value(
                        row, 'assigned_department', row_number, 20
                    ).lower()
                    if not _is_valid_mobile_number(mobile_number):
                        raise ValueError(
                            f'Row {row_number}: mobile_number must contain 7-15 digits '
                            'with an optional leading +.'
                        )
                    staff_key = identity_code.casefold()
                    if staff_key in seen_hod_ids:
                        raise ValueError(f'Row {row_number}: duplicate HOD ID.')
                    seen_hod_ids.add(staff_key)

                    CollegeMasterList.objects.update_or_create(
                        pin=identity_code,
                        defaults={
                            'mobile_number': mobile_number,
                            'full_name': full_name,
                            'allowed_role': 'hod',
                            'assigned_department': 'none',
                            'student_branch': branch,
                            'is_active_batch': True
                        }
                    )
                if row_count == 0:
                    raise ValueError('HOD spreadsheet must contain at least one data row.')

            if registration_codes_file:
                code_rows = pd.read_excel(registration_codes_file)
                required_columns = {'pin', 'academic_year', 'registration_code'}
                _validate_spreadsheet_columns(
                    code_rows,
                    required_columns,
                    'Registration-code',
                )

                seen_students = set()
                seen_codes = set()
                prepared_codes = []
                row_count = 0
                for row_count, (_, row) in enumerate(code_rows.iterrows(), start=1):
                    row_number = row_count + 1
                    student_pin = _spreadsheet_value(row, 'pin', row_number, 50)
                    academic_year = _spreadsheet_value(row, 'academic_year', row_number, 9)
                    registration_code = _spreadsheet_value(
                        row, 'registration_code', row_number, 128
                    )
                    if not _is_valid_academic_year(academic_year):
                        raise ValueError(
                            f'Row {row_number}: academic_year must use the YYYY-YYYY format.'
                        )
                    student_key = (student_pin.casefold(), academic_year)

                    if student_key in seen_students:
                        raise ValueError(
                            f'Multiple registration codes were supplied for PIN {student_pin}.'
                        )
                    if registration_code in seen_codes:
                        raise ValueError(
                            'Each student must have a different registration code.'
                        )
                    seen_students.add(student_key)
                    seen_codes.add(registration_code)

                    roster_entry = CollegeMasterList.objects.filter(
                        pin__iexact=student_pin,
                        academic_year=academic_year,
                        allowed_role='student',
                        is_active_batch=True,
                    ).first()
                    if roster_entry is None:
                        raise ValueError(
                            f'No active student roster entry found for PIN {student_pin} '
                            f'in academic year {academic_year}.'
                        )

                    prepared_code = CollegeRegistrationCode(
                        pin=roster_entry.pin,
                        academic_year=academic_year,
                    )
                    prepared_code.set_code(registration_code)
                    prepared_codes.append(prepared_code)
                if row_count == 0:
                    raise ValueError(
                        'Registration-code spreadsheet must contain at least one data row.'
                    )

                with transaction.atomic():
                    for prepared_code in prepared_codes:
                        existing_code = CollegeRegistrationCode.objects.select_for_update().filter(
                            pin=prepared_code.pin,
                            academic_year=prepared_code.academic_year,
                        ).first()
                        if existing_code and existing_code.used_at:
                            raise ValueError(
                                f'The registration code for PIN {prepared_code.pin} has already been used.'
                            )
                        if existing_code:
                            existing_code.code_hash = prepared_code.code_hash
                            existing_code.save(update_fields=('code_hash',))
                        else:
                            prepared_code.save()

            messages.success(request, "All files uploaded successfully!")
            return redirect('upload_master_list_view')

        except OperationalError as e:
            transaction.set_rollback(True)
            if 'locked' in str(e).casefold():
                messages.error(
                    request,
                    'The database is busy saving another change. Wait a few seconds, '
                    'then upload the file again.',
                )
            else:
                messages.error(request, f"Database error while saving files: {str(e)}")
            return redirect('upload_master_list_view')
        except Exception as e:
            transaction.set_rollback(True)
            messages.error(request, f"Upload failed: {str(e)}")
            return redirect('upload_master_list_view')

    return render(request, 'accounts/upload_master.html')


@user_passes_test(lambda user: user.is_superuser)
def staff_registration_admin_view(request):
    if request.method == 'POST':
        request_id = request.POST.get('request_id')
        action = request.POST.get('action')
        try:
            staff_request = StaffRegistrationRequest.objects.select_related(
                'roster_entry'
            ).get(id=request_id, status='pending')
        except (StaffRegistrationRequest.DoesNotExist, ValueError, TypeError):
            messages.error(request, 'The pending staff registration request was not found.')
            return redirect('staff_registration_admin_view')

        if action not in ('approve', 'reject'):
            messages.error(request, 'Invalid staff registration action.')
            return redirect('staff_registration_admin_view')

        roster_entry = staff_request.roster_entry
        if (
            roster_entry.allowed_role not in ('in_charge', 'hod')
            or not roster_entry.is_active_batch
        ):
            messages.error(request, 'This staff roster entry is no longer active.')
            return redirect('staff_registration_admin_view')
        if User.objects.filter(username__iexact=roster_entry.pin).exists():
            messages.error(request, 'An account already exists for this staff PIN.')
            return redirect('staff_registration_admin_view')

        staff_request.status = 'approved' if action == 'approve' else 'rejected'
        staff_request.reviewed_by = request.user
        staff_request.reviewed_at = timezone.now()
        staff_request.save(update_fields=('status', 'reviewed_by', 'reviewed_at'))
        messages.success(
            request,
            'Staff registration approved. The staff member can now set a password.'
            if action == 'approve'
            else 'Staff registration request rejected.',
        )
        return redirect('staff_registration_admin_view')

    requests = StaffRegistrationRequest.objects.select_related(
        'roster_entry',
        'reviewed_by',
    ).filter(status__in=('pending', 'approved')).order_by('submitted_at')
    return render(request, 'accounts/staff_registration_admin.html', {
        'staff_requests': requests,
    })


@transaction.atomic
def register_view(request):
    if request.method == 'POST':
        try:
            input_pin = request.POST.get('user_pin', '').strip()
            input_mobile = request.POST.get('mobile_number', '').strip()
            registration_code = request.POST.get('registration_code', '').strip()
            password_1 = request.POST.get('password', '')
            password_2 = request.POST.get('confirm_password', '')

            if not input_pin:
                return render(request, 'accounts/register.html', {
                    'error': 'PIN or staff ID is required.'
                })
            if len(input_pin) > 50:
                return render(request, 'accounts/register.html', {
                    'error': 'PIN or staff ID must be no more than 50 characters.'
                })
            if not _is_valid_mobile_number(input_mobile):
                return render(request, 'accounts/register.html', {
                    'error': (
                        'Enter a mobile number with 7-15 digits and an optional leading +.'
                    )
                })
            
            user_match = CollegeMasterList.objects.filter(
                pin__iexact=input_pin,
                is_active_batch=True,
            ).order_by('-academic_year').first()
            
            if not user_match and '.' not in input_pin:
                user_match = CollegeMasterList.objects.filter(
                    pin__iexact=f"{input_pin}.0",
                    is_active_batch=True,
                ).order_by('-academic_year').first()

            if not user_match:
                return render(request, 'accounts/register.html', {
                    'error': f"ID code '{input_pin}' is not authorized. Check your spelling or see your administrator."
                })
                
            if input_mobile != user_match.mobile_number:
                return render(request, 'accounts/register.html', {
                    'error': f"Mobile number does not match our master records for ID {input_pin}."
                })

            if user_match.allowed_role in ('in_charge', 'hod'):
                if User.objects.filter(username__iexact=input_pin).exists():
                    return render(request, 'accounts/register.html', {
                        'error': 'This ID already has an account. Log in to continue.'
                    })

                registration_flow = request.POST.get('registration_flow')

                if registration_flow == 'staff_request':
                    staff_request, created = StaffRegistrationRequest.objects.get_or_create(
                        roster_entry=user_match,
                    )
                    if created:
                        return render(request, 'accounts/register.html', {
                            'staff_form_pin': input_pin,
                            'staff_message': 'Your registration request has been sent to the college admin.',
                        })
                    if staff_request.status == 'completed':
                        return render(request, 'accounts/register.html', {
                            'error': 'This staff account is already registered.'
                        })
                    if staff_request.status == 'approved':
                        return render(request, 'accounts/register.html', {
                            'staff_form_pin': input_pin,
                            'staff_message': (
                                'Your registration is approved. Use the staff password form '
                                'below to finish setting up your account.'
                            ),
                        })
                    if staff_request.status == 'pending':
                        return render(request, 'accounts/register.html', {
                            'staff_form_pin': input_pin,
                            'staff_message': 'Your registration request is awaiting admin approval.',
                        })

                    staff_request.status = 'pending'
                    staff_request.submitted_at = timezone.now()
                    staff_request.reviewed_at = None
                    staff_request.reviewed_by = None
                    staff_request.save(update_fields=(
                        'status',
                        'submitted_at',
                        'reviewed_at',
                        'reviewed_by',
                    ))
                    return render(request, 'accounts/register.html', {
                        'staff_form_pin': input_pin,
                        'staff_message': 'Your registration request has been sent to the college admin.',
                    })

                if registration_flow != 'staff_complete':
                    return render(request, 'accounts/register.html', {
                        'error': 'Submit a staff registration request before setting a password.'
                    })
                try:
                    staff_request = StaffRegistrationRequest.objects.select_for_update().get(
                        roster_entry=user_match,
                    )
                except StaffRegistrationRequest.DoesNotExist:
                    return render(request, 'accounts/register.html', {
                        'error': 'Submit a staff registration request before setting a password.'
                    })
                if staff_request.status != 'approved':
                    return render(request, 'accounts/register.html', {
                        'error': 'Your registration must be approved by the college admin before setting a password.'
                    })
                password_1 = request.POST.get('password', '')
                password_2 = request.POST.get('confirm_password', '')
                password_error = _password_input_error(password_1, password_2)
                if password_error:
                    return render(request, 'accounts/register.html', {
                        'error': password_error
                    })

                new_user = User.objects.create_user(username=input_pin, password=password_1)
                new_user.first_name = user_match.full_name
                new_user.save()
                UserRoleMapping.objects.create(user=new_user, role=user_match.allowed_role)

                if user_match.allowed_role == 'in_charge':
                    InChargeProfile.objects.create(
                        user=new_user,
                        employee_id=input_pin,
                        mobile_number=input_mobile,
                        assigned_department=user_match.assigned_department,
                    )
                else:
                    HODProfile.objects.create(
                        user=new_user,
                        employee_id=input_pin,
                        mobile_number=input_mobile,
                        managing_branch=user_match.student_branch,
                    )

                staff_request.status = 'completed'
                staff_request.save(update_fields=('status',))
                messages.success(request, 'Registration complete. You can now log in.')
                return redirect('login')

            if user_match.allowed_role != 'student':
                return render(request, 'accounts/register.html', {
                    'error': 'This account type cannot register through this page.'
                })
                
            password_error = _password_input_error(password_1, password_2)
            if password_error:
                return render(request, 'accounts/register.html', {'error': password_error})
            if User.objects.filter(username__iexact=input_pin).exists():
                return render(request, 'accounts/register.html', {
                    'error': 'This ID already has an account. Log in to access the current academic year.'
                })
            if not registration_code:
                return render(request, 'accounts/register.html', {
                    'error': 'College-issued registration code is required.'
                })

            try:
                code_record = CollegeRegistrationCode.objects.select_for_update().get(
                    pin=user_match.pin,
                    academic_year=user_match.academic_year,
                )
            except CollegeRegistrationCode.DoesNotExist:
                return render(request, 'accounts/register.html', {
                    'error': 'No registration code has been issued for this ID. Contact the college.'
                })

            if code_record.used_at:
                return render(request, 'accounts/register.html', {
                    'error': 'This registration code has already been used.'
                })
            if not code_record.check_code(registration_code):
                return render(request, 'accounts/register.html', {
                    'error': 'Invalid college-issued registration code.'
                })

            new_user = User.objects.create_user(username=input_pin, password=password_1)
            new_user.first_name = user_match.full_name
            new_user.save()

            UserRoleMapping.objects.create(user=new_user, role=user_match.allowed_role)

            if user_match.allowed_role == 'student':
                _provision_student_year(new_user, user_match)

            elif user_match.allowed_role == 'in_charge':
                InChargeProfile.objects.create(
                    user=new_user, employee_id=input_pin, mobile_number=input_mobile,
                    assigned_department=user_match.assigned_department
                )

            elif user_match.allowed_role == 'hod':
                HODProfile.objects.create(
                    user=new_user, employee_id=input_pin, mobile_number=input_mobile,
                    managing_branch=user_match.student_branch
                )

            code_record.used_at = timezone.now()
            code_record.used_by = new_user
            code_record.save(update_fields=('used_at', 'used_by'))

            messages.success(request, "Registration successful! You can now log in.")
            return redirect('login')
            
        except Exception as database_error:
            transaction.set_rollback(True)
            return render(request, 'accounts/register.html', {'error': f"Processing Fault: {str(database_error)}"})
        
    return render(request, 'accounts/register.html')



def login_view(request):
    if request.method == 'POST':
        username_data = request.POST.get('username', '').strip()
        password_data = request.POST.get('password', '')
        if not username_data or not password_data:
            return render(request, 'accounts/login.html', {
                'error': 'Enter both your user ID and password.'
            })

        user = authenticate(request, username=username_data, password=password_data)

        if user is not None:
            if hasattr(user, 'role_mapping'):
                user_role = user.role_mapping.role
                if user_role == 'student':
                    roster_entry = _active_student_roster(user.username)
                    if roster_entry:
                        try:
                            _provision_student_year(user, roster_entry)
                        except ValueError as error:
                            return render(request, 'accounts/login.html', {'error': str(error)})
                    elif not user.student_profiles.exists():
                        return render(request, 'accounts/login.html', {
                            'error': 'No active student roster entry was found. Contact the administrator.'
                        })
                    login(request, user)
                    return redirect('student_view')
                elif user_role == 'in_charge':
                    login(request, user)
                    return redirect('in_charge_view')
                elif user_role == 'hod':
                    login(request, user)
                    return redirect('hod_view')
            
            return render(request, 'accounts/login.html', {'error': 'Role profile configuration is missing.'})
        else:
            return render(request, 'accounts/login.html', {'error': 'Invalid PIN or account password.'})

    return render(request, 'accounts/login.html')


def hod_view(request):
    if not request.user.is_authenticated or not hasattr(request.user, 'role_mapping') or request.user.role_mapping.role != 'hod':
        return redirect('login')

    hod_profile = request.user.hod_profile
    branch_token = _branch_token(hod_profile.managing_branch)
    branch_profiles = StudentProfile.objects.filter(_branch_profile_filter(branch_token))
    academic_years = list(branch_profiles.order_by('-academic_year').values_list(
        'academic_year', flat=True
    ).distinct())
    active_years = set(branch_profiles.filter(is_active=True).values_list(
        'academic_year', flat=True
    ).distinct())
    academic_year = _select_year(
        request.GET.get('academic_year'), academic_years, active_years
    )

    branch_students = list(branch_profiles.filter(
        academic_year=academic_year
    ).order_by('pin').prefetch_related('dues'))

    ready_to_clear_students = []
    cleared_students = []
    pending_students = []
    inactive_students = []
    
    total_depts = len(DEPARTMENTS)
    users_with_started_clearance = {
        student.user_id
        for student in branch_students
        if any(record.status != 'not_initiated' for record in student.dues.all())
    }

    for student in branch_students:
        records = student.dues.all()
        
        cleared_count = sum(1 for r in records if r.status == 'cleared')
        not_initiated_count = sum(1 for r in records if r.status == 'not_initiated')

        student.cleared_count = cleared_count

        if cleared_count == total_depts:
            cleared_students.append(student)
            if not student.hod_rejection_reason or student.hod_resubmitted:
                ready_to_clear_students.append(student)
        elif not_initiated_count == total_depts:
            if student.user_id not in users_with_started_clearance:
                inactive_students.append(student)
        else:
            pending_students.append(student)

    context = {
        'ready_to_clear_students': ready_to_clear_students,
        'cleared_students': cleared_students,               
        'pending_students': pending_students,              
        'inactive_students': inactive_students,             
        'academic_years': academic_years,
        'academic_year': academic_year,
    }

    return render(request, 'accounts/hod.html', context)


def student_view(request):
    if not request.user.is_authenticated or not hasattr(request.user, 'role_mapping') or request.user.role_mapping.role != 'student':
        return redirect('login')

    profiles = request.user.student_profiles.order_by('-academic_year')
    available_years = list(profiles.values_list('academic_year', flat=True))
    requested_year = request.POST.get('academic_year') if request.method == 'POST' else request.GET.get('academic_year')
    academic_year = _select_year(
        requested_year,
        available_years,
        profiles.filter(is_active=True).values_list('academic_year', flat=True),
    )
    student_prof = profiles.filter(academic_year=academic_year).first()

    if student_prof is None:
        return redirect('login')

    if request.method == 'POST':
        if not student_prof.is_active:
            messages.error(request, 'Previous academic-year records are read-only.')
        elif request.POST.get('action') == 'resubmit_departments':
            rejected_records = student_prof.dues.filter(status='rejected')
            if rejected_records.exists():
                rejected_records.update(status='pending')
                messages.success(
                    request,
                    'Your rejected department clearances have been resubmitted for review.',
                )
            else:
                messages.error(request, 'There are no rejected department clearances to resubmit.')
        elif request.POST.get('action') == 'resubmit_hod':
            all_departments_cleared = (
                student_prof.dues.filter(status='cleared').count() == len(DEPARTMENTS)
            )
            if (
                all_departments_cleared
                and student_prof.hod_rejection_reason
                and not student_prof.hod_resubmitted
            ):
                student_prof.hod_resubmitted = True
                student_prof.save(update_fields=('hod_resubmitted',))
                messages.success(
                    request,
                    'Your clearance has been resubmitted to the HOD for reconsideration.',
                )
            else:
                messages.error(request, 'This clearance is not ready for HOD resubmission.')
        else:
            student_prof.dues.filter(status='not_initiated').update(status='pending')
            messages.success(request, 'Your clearance application has been submitted.')
        return redirect(f"{reverse('student_view')}?academic_year={student_prof.academic_year}")

    cleared_count = student_prof.dues.filter(status='cleared').count()
    all_departments_cleared = cleared_count == len(DEPARTMENTS)
    has_rejected_departments = student_prof.dues.filter(status='rejected').exists()

    return render(request, 'accounts/student.html', {
        'student_profile': student_prof,
        'academic_years': available_years,
        'academic_year': academic_year,
        'can_apply': student_prof.is_active and student_prof.dues.filter(status='not_initiated').exists(),
        'can_resubmit_departments': student_prof.is_active and has_rejected_departments,
        'can_resubmit_hod': (
            student_prof.is_active
            and all_departments_cleared
            and bool(student_prof.hod_rejection_reason)
            and not student_prof.hod_resubmitted
        ),
        'can_download_certificate': (
            student_prof.is_approved
            and all_departments_cleared
        ),
        'awaiting_hod_approval': (
            all_departments_cleared
            and not student_prof.is_approved
            and not student_prof.hod_rejection_reason
            and not student_prof.hod_resubmitted
        ),
        'awaiting_hod_reconsideration': (
            student_prof.hod_resubmitted
            and not student_prof.is_approved
        ),
        'cleared_count': cleared_count,
        'total_departments': len(DEPARTMENTS),
    })


def download_no_dues_certificate(request, profile_id):
    if (
        not request.user.is_authenticated
        or not hasattr(request.user, 'role_mapping')
        or request.user.role_mapping.role != 'student'
    ):
        return redirect('login')

    student_profile = StudentProfile.objects.filter(
        id=profile_id,
        user=request.user,
    ).select_related('user', 'approved_by').first()
    if student_profile is None:
        return HttpResponseForbidden('This certificate is not available to this account.')

    if (
        not student_profile.is_approved
        or student_profile.dues.filter(status='cleared').count() != len(DEPARTMENTS)
    ):
        return HttpResponseForbidden('The HOD must approve all department clearances first.')

    incharges = {}
    for incharge in InChargeProfile.objects.filter(
        assigned_department__in=[code for code, _ in DEPARTMENTS]
    ).select_related('user').order_by('assigned_department', 'user__first_name', 'user__last_name'):
        incharges.setdefault(incharge.assigned_department, []).append(
            incharge.user.get_full_name() or incharge.user.username
        )

    response = HttpResponse(
        _certificate_pdf(student_profile, DEPARTMENTS, incharges),
        content_type='application/pdf',
    )
    response['Content-Disposition'] = f'attachment; filename="no-dues-{student_profile.id}.pdf"'
    return response


def in_charge_view(request):
    if not request.user.is_authenticated or not hasattr(request.user, 'role_mapping') or request.user.role_mapping.role != 'in_charge':
        return redirect('login')

    faculty_profile = request.user.incharge_profile
    faculty_dept = faculty_profile.assigned_department 

    current_branch = request.GET.get('branch', 'cs').strip().lower()
    department_profiles = StudentProfile.objects.filter(dues__department=faculty_dept)
    academic_years = list(department_profiles.order_by('-academic_year').values_list(
        'academic_year', flat=True
    ).distinct())
    active_years = set(department_profiles.filter(is_active=True).values_list(
        'academic_year', flat=True
    ).distinct())
    academic_year = _select_year(
        request.GET.get('academic_year'), academic_years, active_years
    )
    can_update_year = academic_year in active_years

    incoming_queue = DuesRecord.objects.filter(
        department=faculty_dept,
        student__academic_year=academic_year,
        student__pin__icontains=f"-{current_branch}-",
    ).select_related('student__user')
    if can_update_year:
        incoming_queue = incoming_queue.filter(student__is_active=True, status='pending')

    dues_records = incoming_queue.order_by('student__pin')

    context = {
        'dues_records': dues_records,
        'current_branch': current_branch,
        'academic_years': academic_years,
        'academic_year': academic_year,
        'can_update_year': can_update_year,
    }
    
    return render(request, 'accounts/incharge.html', context)


@csrf_protect
def update_department_due_api(request):
    if request.method == 'POST' and request.user.is_authenticated:
        if hasattr(request.user, 'role_mapping') and request.user.role_mapping.role == 'in_charge':
            try:
                data = json.loads(request.body)
                record_id = data.get('record_id')
                action = data.get('action') 
                remark_value = data.get('remark', '')
                if action == 'reject' and (
                    not isinstance(remark_value, str) or not remark_value.strip()
                ):
                    return JsonResponse(
                        {'status': 'error', 'message': 'A rejection reason is required.'},
                        status=400,
                    )
                remark_text = remark_value.strip() if isinstance(remark_value, str) else ''
                
                faculty_dept = request.user.incharge_profile.assigned_department
                record = DuesRecord.objects.get(
                    id=record_id,
                    department=faculty_dept,
                    student__is_active=True,
                )
                if record.status != 'pending':
                    return JsonResponse(
                        {'status': 'error', 'message': 'This clearance is not awaiting review.'},
                        status=400,
                    )
                
                if action == 'clear':
                    record.status = 'cleared'
                    record.staff_remarks = "" 
                elif action == 'reject':
                    record.status = 'rejected'
                    record.staff_remarks = remark_text
                else:
                    return JsonResponse({'status': 'error', 'message': 'Invalid action.'}, status=400)
                    
                record.save()
                return JsonResponse({'status': 'success'})
            except DuesRecord.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Record tracking entry not found.'}, status=404)
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
                
    return JsonResponse({'status': 'error', 'message': 'Unauthorized access or method.'}, status=403)


@csrf_protect
def update_hod_signoff_api(request):
    if request.method == 'POST' and request.user.is_authenticated:
        if hasattr(request.user, 'role_mapping') and request.user.role_mapping.role == 'hod':
            try:
                data = json.loads(request.body)
                student_profile_id = data.get('profile_id')
                action = data.get('action') 
                remark_value = data.get('remark', '')
                if action == 'reject' and (
                    not isinstance(remark_value, str) or not remark_value.strip()
                ):
                    return JsonResponse(
                        {'status': 'error', 'message': 'A rejection reason is required.'},
                        status=400,
                    )
                remark_text = remark_value.strip() if isinstance(remark_value, str) else ''
                
                branch_token = _branch_token(request.user.hod_profile.managing_branch)
                student_profile = StudentProfile.objects.filter(
                    _branch_profile_filter(branch_token),
                ).get(
                    id=student_profile_id,
                    is_active=True,
                )
                
                if action == 'clear':
                    if student_profile.dues.filter(status='cleared').count() == len(DEPARTMENTS):
                        if (
                            student_profile.hod_rejection_reason
                            and not student_profile.hod_resubmitted
                        ):
                            return JsonResponse(
                                {
                                    'status': 'error',
                                    'message': 'The student must resubmit before reconsideration.',
                                },
                                status=400,
                            )
                        student_profile.is_approved = True
                        student_profile.approved_by = request.user
                        student_profile.approved_at = timezone.now()
                        student_profile.hod_rejection_reason = ''
                        student_profile.hod_resubmitted = False
                    else:
                        return JsonResponse({'status': 'error', 'message': 'Cannot sign off. Sub-departments pending.'}, status=400)
                elif action == 'reject':
                    if student_profile.dues.filter(status='cleared').count() != len(DEPARTMENTS):
                        return JsonResponse(
                            {'status': 'error', 'message': 'Cannot reject before all departments are cleared.'},
                            status=400,
                        )
                    if not remark_text:
                        return JsonResponse(
                            {'status': 'error', 'message': 'A rejection reason is required.'},
                            status=400,
                        )
                    student_profile.is_approved = False
                    student_profile.approved_by = None
                    student_profile.approved_at = None
                    student_profile.hod_rejection_reason = remark_text
                    student_profile.hod_resubmitted = False
                else:
                    return JsonResponse({'status': 'error', 'message': 'Invalid action.'}, status=400)
                    
                student_profile.save(update_fields=(
                    'is_approved',
                    'approved_by',
                    'approved_at',
                    'hod_rejection_reason',
                    'hod_resubmitted',
                ))
                return JsonResponse({'status': 'success'})
            except StudentProfile.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Student profile tracking not found.'}, status=404)
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
                
    return JsonResponse({'status': 'error', 'message': 'Unauthorized access.'}, status=403)


@staff_member_required
def archive_current_batch_view(request):
    if request.method == 'POST':
        target_academic_year = request.POST.get('academic_year', '').strip()
        
        if not target_academic_year:
            messages.error(request, "Please enter the precise academic year string to archive.")
            return redirect('admin:index')

        CollegeMasterList.objects.filter(
            allowed_role='student',
            academic_year=target_academic_year,
            is_active_batch=True
        ).update(is_active_batch=False)

        updated_profiles = StudentProfile.objects.filter(
            academic_year=target_academic_year,
            is_active=True
        ).update(is_active=False)

        messages.success(
            request,
            f"Successfully archived {updated_profiles} student profiles from the academic session {target_academic_year}."
        )
        return redirect('admin:index')