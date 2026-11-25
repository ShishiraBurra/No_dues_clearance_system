from django.db import models
from django.db.models.functions import Lower
from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password, make_password

DEPARTMENTS = [
    ('civil', 'Civil Section'),
    ('electric', 'Electric Lab'),
    ('mechanical', 'Mechanical Lab'),
    ('electronic', 'Electronic Lab'),
    ('automobile', 'Automobile Lab'),
    ('computer', 'Computer Lab'),
    ('phy_lab', 'Physics Lab'),
    ('chem_lab', 'Chemistry Lab'),
    ('pharmacy', 'Pharmacy'),
    ('library', 'College Library'),
    ('office', 'Office'),
    ('work_shop', 'Work Shop'),
    ('hostel', 'Hostel'),
    ('ncc', 'N.C.C'),
    ('games', 'Games and Sports'),
]

class UserRoleMapping(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='role_mapping')
    ROLE_CHOICES = [
        ('student', 'Student'),
        ('in_charge', 'Department In-Charge'),
        ('hod', 'Head of Department'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    def __str__(self):
        return f"{self.user.username} - {self.role}"

class StudentProfile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='student_profiles')
    pin = models.CharField(max_length=50)
    mobile_number = models.CharField(max_length=15)
    branch = models.CharField(max_length=20)
    academic_year = models.CharField(max_length=9, default='2026-2027')
    is_active = models.BooleanField(default=True)
    is_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='hod_approved_student_profiles',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    hod_rejection_reason = models.TextField(blank=True)
    hod_resubmitted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('pin', 'academic_year'),
                name='unique_student_pin_per_academic_year',
            ),
            models.UniqueConstraint(
                Lower('pin'),
                'academic_year',
                name='uniq_student_pin_lower_year',
            ),
        ]
    def __str__(self):
        return f"Student: {self.pin} ({self.academic_year})"

class InChargeProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='incharge_profile')
    employee_id = models.CharField(max_length=50, unique=True)
    mobile_number = models.CharField(max_length=15)
    assigned_department = models.CharField(max_length=20, choices=DEPARTMENTS)

    def __str__(self):
        return f"InCharge: {self.employee_id} - {self.get_assigned_department_display()}"

class HODProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='hod_profile')
    employee_id = models.CharField(max_length=50, unique=True)
    mobile_number = models.CharField(max_length=15)
    managing_branch = models.CharField(max_length=20)

    def __str__(self):
        return f"HOD: {self.managing_branch.upper()} Department"

class DuesRecord(models.Model):
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='dues')
    department = models.CharField(max_length=20, choices=DEPARTMENTS)
    fine_amount_pending = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    staff_remarks = models.TextField(blank=True)
    STATUS_CHOICES = [
        ('not_initiated', 'Not Initiated'),
        ('pending', 'Pending Dues'),
        ('cleared', 'Fully Cleared'),
        ('rejected', 'Rejected'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='not_initiated')
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('student' , 'department')

    def __str__(self):
        return f"{self.student.pin} - {self.department} - {self.status}"

class CollegeMasterList(models.Model):
    pin = models.CharField(max_length=50, verbose_name="Roll No")
    mobile_number = models.CharField(max_length=15)
    full_name = models.CharField(max_length=100)
    allowed_role = models.CharField(max_length=20, choices=UserRoleMapping.ROLE_CHOICES)
    student_branch = models.CharField(max_length=20, blank=True, default='none')
    academic_year = models.CharField(max_length=9, default='2024-2027')
    assigned_department = models.CharField(max_length=50, blank=True, default='none')
    is_active_batch = models.BooleanField(default=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['pin', 'academic_year'],
                name='unique_roster_pin_per_academic_year',
            ),
        ]

    def __str__(self):
        return f"{self.pin} ({self.allowed_role})"


class CollegeRegistrationCode(models.Model):
    pin = models.CharField(max_length=50)
    academic_year = models.CharField(max_length=9)
    code_hash = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='used_college_registration_codes',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('pin', 'academic_year'),
                name='unique_registration_code_per_student_year',
            ),
        ]

    def set_code(self, code):
        self.code_hash = make_password(code)

    def check_code(self, code):
        return check_password(code, self.code_hash)

    def __str__(self):
        return f'{self.pin} ({self.academic_year})'


class StaffRegistrationRequest(models.Model):
    roster_entry = models.OneToOneField(
        CollegeMasterList,
        on_delete=models.CASCADE,
        related_name='staff_registration_request',
    )
    STATUS_CHOICES = [
        ('pending', 'Pending admin review'),
        ('approved', 'Approved; awaiting password setup'),
        ('rejected', 'Rejected'),
        ('completed', 'Registration completed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_staff_registration_requests',
    )

    def __str__(self):
        return f'{self.roster_entry.pin} - {self.get_status_display()}'
