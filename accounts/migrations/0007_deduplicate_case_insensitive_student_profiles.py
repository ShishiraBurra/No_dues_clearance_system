from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower


def remove_empty_case_duplicates(apps, schema_editor):
    StudentProfile = apps.get_model('accounts', 'StudentProfile')
    DuesRecord = apps.get_model('accounts', 'DuesRecord')
    database = schema_editor.connection.alias

    duplicate_groups = list(
        StudentProfile.objects.using(database)
        .annotate(pin_key=Lower('pin'))
        .values('pin_key', 'academic_year')
        .annotate(profile_count=Count('pk'))
        .filter(profile_count__gt=1)
    )

    for group in duplicate_groups:
        profiles = list(
            StudentProfile.objects.using(database)
            .filter(
                pin__iexact=group['pin_key'],
                academic_year=group['academic_year'],
            )
            .order_by('pk')
        )
        if (
            len({profile.user_id for profile in profiles}) != 1
            or len({profile.mobile_number for profile in profiles}) != 1
            or len({profile.branch.casefold() for profile in profiles}) != 1
            or len({profile.is_active for profile in profiles}) != 1
        ):
            raise RuntimeError(
                'Conflicting case-insensitive student profiles require manual review '
                f'for academic year {group["academic_year"]}.'
            )

        profiles_with_clearance_data = []
        for profile in profiles:
            dues = list(
                DuesRecord.objects.using(database).filter(student_id=profile.pk)
            )
            has_clearance_data = (
                profile.is_approved
                or profile.approved_by_id is not None
                or profile.approved_at is not None
                or bool(profile.hod_rejection_reason)
                or profile.hod_resubmitted
                or any(
                    due.status != 'not_initiated'
                    or bool(due.staff_remarks)
                    or due.fine_amount_pending != 0
                    for due in dues
                )
            )
            if has_clearance_data:
                profiles_with_clearance_data.append(profile)

        if len(profiles_with_clearance_data) > 1:
            raise RuntimeError(
                'Multiple duplicate student profiles contain clearance data; '
                f'manual review is required for academic year {group["academic_year"]}.'
            )

        survivor = (
            profiles_with_clearance_data[0]
            if profiles_with_clearance_data
            else profiles[0]
        )
        for profile in profiles:
            if profile.pk != survivor.pk:
                profile.delete(using=database)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_studentprofile_hod_rejection_resubmission'),
    ]

    operations = [
        migrations.RunPython(
            remove_empty_case_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='studentprofile',
            constraint=models.UniqueConstraint(
                Lower('pin'),
                'academic_year',
                name='uniq_student_pin_lower_year',
            ),
        ),
    ]
