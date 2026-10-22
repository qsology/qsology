from pathlib import Path

from apps.locations import importers
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Load the DXCC, POTA, SOTA and IOTA reference lists"

    def add_arguments(self, parser):
        parser.add_argument(
            "sources",
            nargs="*",
            choices=importers.SOURCES,
            help="Lists to load, default all of them",
        )
        parser.add_argument(
            "--file",
            type=Path,
            help="Load from a file already downloaded, needs a single source",
        )

    def handle(self, *args, sources, file, **options):
        sources = sources or list(importers.SOURCES)
        if file is not None and len(sources) != 1:
            raise CommandError("--file needs exactly one source")
        for source in sources:
            try:
                if file is not None:
                    result = importers.import_data(source, file.read_bytes())
                else:
                    result = importers.refresh(source)
            except (importers.ImportAborted, OSError) as exc:
                raise CommandError(f"{source}: {exc}") from exc
            self.stdout.write(str(result))
            for error in result.errors[:20]:
                self.stderr.write(f"  {error}")
