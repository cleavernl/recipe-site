from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0002_preserve_recipes_when_owner_deleted"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipe",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
