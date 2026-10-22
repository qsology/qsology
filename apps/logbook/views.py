from apps.logbook import exports
from apps.logbook.models import Export
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from storages.backends.s3 import S3Storage


@require_GET
@never_cache
@login_required
def download_export(request, pk):
    """Download an export, only ever by its owner

    From S3 this hands out a link to the file that only works for a few
    minutes, see EXPORT_DOWNLOAD_URL_TTL, anywhere else the file is sent
    from here"""
    export = get_object_or_404(Export, pk=pk, owner=request.user)
    if not export.is_downloadable:
        return HttpResponse(
            "This export isn't available to download.", status=410
        )
    Export.objects.filter(pk=export.pk).update(
        downloads=F("downloads") + 1, last_downloaded_at=timezone.now()
    )
    content_type = exports.CONTENT_TYPES[export.format]
    storage = export.file.storage
    if isinstance(storage, S3Storage):
        url = storage.url(
            export.file.name,
            parameters={
                "ResponseContentDisposition": (
                    f'attachment; filename="{export.filename}"'
                ),
                "ResponseContentType": content_type,
            },
            expire=storage.querystring_expire,
        )
        response = HttpResponseRedirect(url)
    else:
        response = FileResponse(
            export.file.open("rb"),
            as_attachment=True,
            filename=export.filename,
            content_type=content_type,
        )
    # The signed link is as good as the file until it expires
    response["Referrer-Policy"] = "no-referrer"
    return response
