from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_staffregistrationrequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentprofile',
            name='hod_rejection_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='hod_resubmitted',
            field=models.BooleanField(default=False),
        ),
    ]
