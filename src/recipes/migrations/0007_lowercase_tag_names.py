from django.db import migrations


def lowercase_tag_names(apps, schema_editor):
    Tag = apps.get_model("recipes", "Tag")
    for tag in Tag.objects.all().iterator():
        lowered = tag.name.lower()
        if tag.name != lowered:
            tag.name = lowered
            tag.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0006_recipe_made"),
    ]

    operations = [
        migrations.RunPython(lowercase_tag_names, migrations.RunPython.noop),
    ]
