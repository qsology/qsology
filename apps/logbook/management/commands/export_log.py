import sys
from pathlib import Path

from apps.logbook import exports
from apps.logbook.models import Logbook
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Export a user's QSOs as ADI, ADX or JSON"

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--format", choices=sorted(exports.EXPORTERS), default="adi"
        )
        parser.add_argument(
            "--logbook", help="Logbook name, default the master log"
        )
        parser.add_argument(
            "--output", type=Path, help="File to write, default stdout"
        )

    def handle(self, *args, username, format, logbook, output, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user {username!r}") from exc
        if logbook:
            try:
                book = Logbook.objects.get(owner=user, name=logbook)
            except Logbook.DoesNotExist as exc:
                raise CommandError(f"No logbook {logbook!r}") from exc
        else:
            book = Logbook.objects.master_for(user)
        if output is None:
            exports.export_to(sys.stdout, book.qsos(), format)
            return
        with output.open("w", encoding="utf-8", newline="") as stream:
            exports.export_to(stream, book.qsos(), format)
        self.stderr.write(f"Exported {book.qsos().count()} QSOs to {output}")
