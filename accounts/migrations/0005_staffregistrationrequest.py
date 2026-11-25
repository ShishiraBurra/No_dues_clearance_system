import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_collegeregistrationcode'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffRegistrationRequest',
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
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('pending', 'Pending admin review'),
                            ('approved', 'Approved; awaiting password setup'),
                            ('rejected', 'Rejected'),
                            ('completed', 'Registration completed'),
                        ],
                        default='pending',
                        max_length=20,
                    ),
                ),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                (
                    'reviewed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='reviewed_staff_registration_requests',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'roster_entry',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='staff_registration_request',
                        to='accounts.collegemasterlist',
                    ),
                ),
            ],
        ),
    ]
