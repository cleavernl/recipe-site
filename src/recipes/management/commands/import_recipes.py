from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from recipes.yaml_import import RecipeImportError, import_recipes_from_directory

User = get_user_model()

DEFAULT_OWNER_USERNAME = "cleavernl"
DEFAULT_IMPORT_DIR = Path(settings.BASE_DIR) / "migration" / "onenote" / "recipes"


class Command(BaseCommand):
    help = (
        "Import recipes from migration/onenote/recipes/*.recipe.yaml "
        "(ingredients, steps, tags, and photos)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--owner",
            default=DEFAULT_OWNER_USERNAME,
            help=f"Username that will own imported recipes (default: {DEFAULT_OWNER_USERNAME}).",
        )
        parser.add_argument(
            "--dir",
            type=Path,
            default=DEFAULT_IMPORT_DIR,
            help="Directory containing *.recipe.yaml files and images/ subdirectory.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate files and report actions without writing to the database.",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            default=True,
            help="Skip recipes whose title already exists (default: enabled).",
        )
        parser.add_argument(
            "--no-skip-existing",
            action="store_false",
            dest="skip_existing",
            help="Import even when a recipe with the same title already exists.",
        )

    def handle(self, *args, **options) -> None:
        username: str = options["owner"]
        directory: Path = options["dir"].resolve()
        dry_run: bool = options["dry_run"]
        skip_existing: bool = options["skip_existing"]

        try:
            owner = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            msg = f"User {username!r} does not exist."
            raise CommandError(msg) from exc

        mode = "DRY RUN" if dry_run else "IMPORT"
        self.stdout.write(f"{mode}: {directory} → owner {owner.username}")

        try:
            results = import_recipes_from_directory(
                owner=owner,
                directory=directory,
                dry_run=dry_run,
                skip_existing=skip_existing,
            )
        except RecipeImportError as exc:
            raise CommandError(str(exc)) from exc

        counts: dict[str, int] = {}
        for row in results:
            counts[row.action] = counts.get(row.action, 0) + 1
            suffix = f" ({row.detail})" if row.detail else ""
            self.stdout.write(f"  {row.action}: {row.title}{suffix}")

        summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        self.stdout.write(self.style.SUCCESS(f"Done. {summary or 'no files'}"))
