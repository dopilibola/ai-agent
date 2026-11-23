from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="adminprofile",
            name="sections",
            field=models.CharField(
                blank=True, default="dashboard,users,reply,export,outreach,learning,analytics,health",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="adminprofile",
            name="readonly",
            field=models.BooleanField(default=False),
        ),
    ]
