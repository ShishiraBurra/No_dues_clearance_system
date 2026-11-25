from django.contrib import admin
from django.contrib.auth.models import Group
from django.db.models import Count, Q
from django.utils.html import format_html
from import_export.admin import ImportExportModelAdmin

from .models import (
    CollegeMasterList,
    CollegeRegistrationCode,
    DuesRecord,
    HODProfile,
    InChargeProfile,
    StaffRegistrationRequest,
    StudentProfile,
    UserRoleMapping,
    DEPARTMENTS,
)


admin.site.site_header = 'No Dues Administration'
admin.site.site_title = 'No Dues Admin'
admin.site.index_title = 'College clearance management'
admin.site.unregister(Group)


@admin.register(CollegeMasterList)
class CollegeMasterListAdmin(ImportExportModelAdmin):
    list_display = (
        'pin',
        'full_name',
        'allowed_role',
        'get_assignment',
        'academic_year',
        'is_active_batch',
    )
    list_filter = ('allowed_role', 'academic_year', 'is_active_batch', 'student_branch')
    search_fields = ('pin', 'full_name', 'mobile_number')
    ordering = ('allowed_role', 'pin')
    @admin.display(description='Branch / department')
    def get_assignment(self, obj):
        if obj.allowed_role == 'in_charge':
            return obj.assigned_department
        if obj.allowed_role == 'hod':
            return obj.student_branch.upper()
        return obj.student_branch.upper()

@admin.register(UserRoleMapping)
class UserRoleMappingAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name')

@admin.register(StudentProfile)
class StudentProfileAdmin(ImportExportModelAdmin):
    list_display = (
        'pin',
        'get_full_name',
        'branch',
        'academic_year',
        'get_clearance_status',
        'is_active',
        'is_approved',
    )
    list_filter = ('branch', 'academic_year', 'is_active', 'is_approved')
    search_fields = ('pin', 'user__first_name', 'user__last_name', 'mobile_number')
    ordering = ('pin',)
    list_select_related = ('user', 'approved_by')

    @admin.display(description='Student name', ordering='user__first_name')
    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            admin_dues_count=Count('dues'),
            admin_cleared_count=Count('dues', filter=Q(dues__status='cleared')),
            admin_pending_count=Count('dues', filter=Q(dues__status='pending')),
            admin_rejected_count=Count('dues', filter=Q(dues__status='rejected')),
            admin_started_count=Count(
                'dues',
                filter=~Q(dues__status='not_initiated'),
            ),
        )

    @admin.display(description='Dues status')
    def get_clearance_status(self, obj):
        if (
            obj.admin_dues_count == len(DEPARTMENTS)
            and obj.admin_cleared_count == len(DEPARTMENTS)
        ):
            return 'Fully Cleared'
        if obj.admin_rejected_count:
            return 'Rejected'
        if obj.admin_pending_count:
            return 'Pending Dues'
        if not obj.admin_started_count:
            return 'Not Initiated'
        return 'In Progress'

@admin.register(InChargeProfile)
class InChargeProfileAdmin(admin.ModelAdmin):
    list_display = ('employee_id', 'get_full_name', 'assigned_department', 'mobile_number')
    list_filter = ('assigned_department',)
    search_fields = ('employee_id', 'user__first_name', 'user__last_name')
    list_select_related = ('user',)

    @admin.display(description='In-charge name', ordering='user__first_name')
    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

@admin.register(HODProfile)
class HODProfileAdmin(admin.ModelAdmin):
    list_display = ('employee_id', 'get_full_name', 'managing_branch', 'mobile_number')
    list_filter = ('managing_branch',)
    search_fields = ('employee_id', 'user__first_name', 'user__last_name')
    list_select_related = ('user',)

    @admin.display(description='HOD name', ordering='user__first_name')
    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

@admin.register(DuesRecord)
class DuesRecordAdmin(ImportExportModelAdmin):
    list_display = (
        'get_student_pin',
        'get_student_name',
        'department',
        'fine_amount_pending',
        'status',
        'last_updated',
    )
    list_filter = ('department', 'status', 'student__academic_year', 'last_updated')
    search_fields = ('student__pin', 'student__user__first_name', 'student__user__last_name', 'staff_remarks')
    list_editable = ('status', 'fine_amount_pending')
    ordering = ('student__pin', 'department')
    list_select_related = ('student', 'student__user')
    date_hierarchy = 'last_updated'

    @admin.display(description='PIN', ordering='student__pin')
    def get_student_pin(self, obj):
        return obj.student.pin

    @admin.display(description='Student name', ordering='student__user__first_name')
    def get_student_name(self, obj):
        return obj.student.user.get_full_name() or obj.student.user.username


@admin.register(CollegeRegistrationCode)
class CollegeRegistrationCodeAdmin(admin.ModelAdmin):
    list_display = ('pin', 'academic_year', 'get_status', 'used_at', 'used_by')
    list_filter = ('academic_year', 'used_at')
    search_fields = ('pin', 'used_by__username', 'used_by__first_name', 'used_by__last_name')
    ordering = ('academic_year', 'pin')
    list_select_related = ('used_by',)
    readonly_fields = ('pin', 'academic_year', 'get_status', 'used_at', 'used_by')
    fields = ('pin', 'academic_year', 'get_status', 'used_at', 'used_by')

    @admin.display(description='Code status')
    def get_status(self, obj):
        if obj.used_at:
            return format_html('<strong style="color:#047857">{}</strong>', 'Used')
        return format_html('<strong style="color:#8B5A2E">{}</strong>', 'Available')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StaffRegistrationRequest)
class StaffRegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ('get_staff_name', 'get_pin', 'get_role', 'status', 'submitted_at', 'reviewed_at')
    list_filter = ('status', 'roster_entry__allowed_role')
    search_fields = ('roster_entry__pin', 'roster_entry__full_name', 'roster_entry__mobile_number')
    ordering = ('-submitted_at',)
    list_select_related = ('roster_entry', 'reviewed_by')
    readonly_fields = (
        'roster_entry',
        'status',
        'submitted_at',
        'reviewed_at',
        'reviewed_by',
    )

    @admin.display(description='Staff name', ordering='roster_entry__full_name')
    def get_staff_name(self, obj):
        return obj.roster_entry.full_name

    @admin.display(description='Staff ID', ordering='roster_entry__pin')
    def get_pin(self, obj):
        return obj.roster_entry.pin

    @admin.display(description='Role', ordering='roster_entry__allowed_role')
    def get_role(self, obj):
        return obj.roster_entry.get_allowed_role_display()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
