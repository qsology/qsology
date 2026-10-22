from pathlib import Path

from apps.logbook import importing
from apps.logbook.models import ImportFormat
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Import a file of QSOs for a user"

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("path", type=Path)
        parser.add_argument(
            "--format",
            choices=ImportFormat.values,
            help="Default is to guess from the file",
        )
        parser.add_argument(
            "--accept-warnings",
            action="store_true",
            help="Log records with warnings, flagged for review, rather "
            "than holding them",
        )

    def handle(self, *args, username, path, format, accept_warnings, **opts):
        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user {username!r}") from exc
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        batch = importing.import_file(
            user,
            content,
            format=format,
            filename=path.name,
            accept_warnings=accept_warnings,
        )
        self.stdout.write(
            f"Import {batch.pk}: {batch.total} records, {batch.logged} "
            f"logged, {batch.duplicates} duplicates, {batch.held} held for "
            f"review, {batch.failed} failed"
        )
