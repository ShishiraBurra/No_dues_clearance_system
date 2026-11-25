import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_studentprofile_hod_approval_details'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CollegeRegistrationCode',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('pin', models.CharField(max_length=50)),
                ('academic_year', models.CharField(max_length=9)),
                (
                    'code_hash',
                    models.CharField(
                        max_length=128,
                    ),
                ),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                (
                    'used_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='used_college_registration_codes',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('pin', 'academic_year'),
                        name='unique_registration_code_per_student_year',
                    ),
                ],
            },
        ),
    ]
