"""Exports built in the background and downloaded later"""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
import requests
from apps.audit.models import AuditEntry, TaskRun, TaskRunStatus
from apps.logbook import exports, tasks
from apps.logbook.models import Export, ExportStatus, Logbook
from apps.notifications.models import Notification
from celery.exceptions import Retry
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from moto import mock_aws
from storages.backends.s3 import S3Storage

pytestmark = pytest.mark.django_db

BUCKET = "qsology-exports-test"


@pytest.fixture
def run(django_capture_on_commit_callbacks):
    """Run what's waiting on the transaction committing, as celery would
    pick it up once it had"""

    def _run(function, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return function(*args, **kwargs)

    return _run


@pytest.fixture
def three(log):
    return [
        log(),
        log(freq=Decimal("7.074")),
        log(mode="CW", rst_sent="599", rst_rcvd="599"),
    ]


@pytest.fixture
def ready(user, three, run):
    export = run(exports.request_export, user, "adi")
    export.refresh_from_db()
    assert export.status == ExportStatus.READY
    return export


def build_like_celery(export_id) -> int:
    """Run the task as a worker would, trying again until it gives up,
    returning how many times it ran"""
    for retries in range(tasks.build_export.max_retries + 1):
        try:
            tasks.build_export.apply(args=[str(export_id)], retries=retries)
        except Retry:
            continue
        except Exception:
            pass
        return retries + 1
    raise AssertionError("Gave up without saying so")


@pytest.fixture
def requested(user, monkeypatch, run):
    """Factory for an export that has been asked for, but not yet picked up
    by celery"""

    def _requested(format="adi", **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(tasks.build_export, "delay", lambda *args: None)
            return run(exports.request_export, user, format, **kwargs)

    return _requested


def content(export) -> str:
    with export.file.open("rb") as file:
        return file.read().decode()


class TestRequesting:
    def test_built_and_ready(self, user, three, run):
        export = run(exports.request_export, user, "adi")
        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        assert export.qso_count == 3
        assert export.attempts == 1
        assert export.started_at <= export.finished_at
        master = Logbook.objects.master_for(user)
        assert export.logbook == master
        assert export.name == master.name
        assert export.expires_at > timezone.now() + timedelta(days=6)

    @pytest.mark.parametrize("format", ["adi", "adx", "json"])
    def test_same_as_an_export_done_there_and_then(
        self, user, three, run, format
    ):
        export = run(exports.request_export, user, format)
        export.refresh_from_db()
        expected = exports.export_text(
            Logbook.objects.master_for(user).qsos(),
            format,
            created=export.created_at,
        )
        assert content(export) == expected
        assert export.size == len(expected.encode())

    def test_only_the_logbooks_qsos(self, user, three, run):
        logbook = Logbook(owner=user, name="40m")
        logbook.set_query("band:40m")
        logbook.save()
        export = run(exports.request_export, user, "json", logbook=logbook)
        export.refresh_from_db()
        assert export.qso_count == 1
        assert export.name == "40m"

    def test_logbook_as_it_was_when_asked_for(
        self, user, three, django_capture_on_commit_callbacks
    ):
        logbook = Logbook(owner=user, name="40m")
        logbook.set_query("band:40m")
        logbook.save()
        with django_capture_on_commit_callbacks() as callbacks:
            export = exports.request_export(user, "adi", logbook=logbook)
        logbook.set_query("band:20m")
        logbook.save()
        callbacks[0]()
        export.refresh_from_db()
        assert export.qso_count == 1

    def test_logbook_deleted_before_its_built(
        self, user, three, django_capture_on_commit_callbacks
    ):
        logbook = Logbook.objects.create(owner=user, name="All")
        with django_capture_on_commit_callbacks() as callbacks:
            export = exports.request_export(user, "adi", logbook=logbook)
        logbook.delete()
        callbacks[0]()
        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        assert export.logbook is None
        assert export.qso_count == 3

    def test_deleted_qsos_arent_exported(self, user, three, run):
        three[0].delete()
        export = run(exports.request_export, user, "adi")
        export.refresh_from_db()
        assert export.qso_count == 2

    def test_empty_log(self, user, run):
        export = run(exports.request_export, user, "adx")
        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        assert export.qso_count == 0
        assert content(export).startswith("<?xml")

    def test_asking_twice_while_waiting(
        self, user, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks():
            first = exports.request_export(user, "adi")
            again = exports.request_export(user, "adi")
            other_format = exports.request_export(user, "json")
        assert again == first
        assert other_format != first

    def test_asking_again_once_ready(self, user, ready, run):
        assert run(exports.request_export, user, "adi") != ready

    def test_someone_elses_logbook(self, user, other_user):
        logbook = Logbook.objects.master_for(other_user)
        with pytest.raises(ValidationError, match="someone else"):
            exports.request_export(user, "adi", logbook=logbook)
        assert not Export.objects.exists()

    def test_unknown_format(self, user):
        with pytest.raises(ValidationError, match="format"):
            exports.request_export(user, "csv")

    def test_queueing_failing_leaves_it_waiting(
        self, user, three, monkeypatch, run
    ):
        def broken(*args):
            raise ConnectionError("broker down")

        monkeypatch.setattr(tasks.build_export, "delay", broken)
        export = run(exports.request_export, user, "adi")
        export.refresh_from_db()
        assert export.status == ExportStatus.PENDING

    def test_filename(self, user, ready):
        stamp = ready.created_at.strftime("%Y%m%d-%H%M%S")
        assert ready.filename == f"{ready.name.lower()}-{stamp}.adi".replace(
            " ", "-"
        )


class TestNotifying:
    def test_told_when_ready(self, user, ready):
        notification = Notification.objects.get(kind="export.ready")
        assert notification.user == user
        assert notification.url == reverse(
            "logbook:export-download", args=[ready.pk]
        )
        assert "3 QSOs" in notification.body
        assert notification.data == {"export": str(ready.pk), "format": "adi"}
        assert notification.sent_via == ["log"]

    def test_told_when_it_fails(self, user, three, monkeypatch, requested, run):
        def broken(*args, **kwargs):
            raise OSError("disk full")

        export = requested()
        monkeypatch.setattr(exports, "export_to", broken)
        run(build_like_celery, export.pk)
        notification = Notification.objects.get()
        assert notification.kind == "export.failed"
        # Users aren't shown what went wrong inside
        assert "disk full" not in notification.body


class TestFailing:
    def test_tried_again_then_given_up(
        self, user, three, monkeypatch, requested, run
    ):
        calls = []

        def broken(*args, **kwargs):
            calls.append(1)
            raise OSError("disk full")

        export = requested()
        monkeypatch.setattr(exports, "export_to", broken)
        assert run(build_like_celery, export.pk) == 4
        export.refresh_from_db()
        assert len(calls) == tasks.build_export.max_retries + 1
        assert export.attempts == len(calls)
        assert export.status == ExportStatus.FAILED
        assert export.error == "disk full"
        assert not export.file
        assert not export.is_downloadable

    def test_works_on_a_second_try(
        self, user, three, monkeypatch, requested, run
    ):
        real = exports.export_to
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("blip")
            return real(*args, **kwargs)

        export = requested()
        monkeypatch.setattr(exports, "export_to", flaky)
        assert run(build_like_celery, export.pk) == 2
        export.refresh_from_db()
        assert export.status == ExportStatus.READY
        assert export.attempts == 2
        assert export.error == ""
        assert export.qso_count == 3
        assert list(Notification.objects.values_list("kind", flat=True)) == [
            "export.ready"
        ]

    def test_nothing_in_the_log_changes(
        self, user, three, monkeypatch, requested, run
    ):
        before = [(qso.pk, qso.version) for qso in three]

        def broken(*args, **kwargs):
            raise OSError("disk full")

        export = requested()
        monkeypatch.setattr(exports, "export_to", broken)
        run(build_like_celery, export.pk)
        after = [(qso.pk, qso.version) for qso in user.qsos.order_by("time_on")]
        assert after == before


class TestBuilding:
    def make(self, user, **kwargs):
        logbook = Logbook.objects.master_for(user)
        kwargs.setdefault("status", ExportStatus.PENDING)
        return Export.objects.create(
            owner=user,
            logbook=logbook,
            name=logbook.name,
            filter=logbook.filter,
            format="adi",
            **kwargs,
        )

    def test_only_built_once(self, user, three):
        export = self.make(user)
        assert exports.build_export(export.pk) is not None
        assert exports.build_export(export.pk) is None
        export.refresh_from_db()
        assert export.attempts == 1

    def test_not_while_its_being_built_elsewhere(self, user):
        export = self.make(
            user, status=ExportStatus.RUNNING, started_at=timezone.now()
        )
        assert exports.build_export(export.pk) is None

    def test_taken_over_once_stale(self, user, settings):
        export = self.make(
            user,
            status=ExportStatus.RUNNING,
            started_at=timezone.now() - settings.EXPORT_STALE_AFTER * 2,
        )
        assert exports.build_export(export.pk).status == ExportStatus.READY

    @pytest.mark.parametrize(
        "status",
        [ExportStatus.READY, ExportStatus.FAILED, ExportStatus.EXPIRED],
    )
    def test_finished_ones_are_left(self, user, status):
        export = self.make(user, status=status)
        assert exports.build_export(export.pk) is None
        export.refresh_from_db()
        assert export.status == status

    def test_failing_a_finished_export_changes_nothing(self, user, ready):
        exports.export_failed(ready.pk, "late", final=True)
        ready.refresh_from_db()
        assert ready.status == ExportStatus.READY
        assert ready.error == ""


class TestTidying:
    def test_expired_files_are_removed(self, user, ready, run):
        storage, name = ready.file.storage, ready.file.name
        assert storage.exists(name)
        result = run(exports.tidy_exports, now=ready.expires_at)
        ready.refresh_from_db()
        assert result["expired"] == 1
        assert ready.status == ExportStatus.EXPIRED
        assert not ready.file
        assert not storage.exists(name)

    def test_unexpired_files_are_kept(self, user, ready):
        result = exports.tidy_exports(
            now=ready.expires_at - timedelta(seconds=1)
        )
        ready.refresh_from_db()
        assert result["expired"] == 0
        assert ready.is_downloadable

    def test_stuck_exports_are_queued_again(
        self, user, three, settings, requested, run
    ):
        export = requested()
        later = timezone.now() + settings.EXPORT_STALE_AFTER
        assert run(exports.tidy_exports, now=later)["requeued"] == 1
        export.refresh_from_db()
        assert export.status == ExportStatus.READY

    def test_new_exports_are_left_alone(self, requested):
        requested()
        assert exports.tidy_exports()["requeued"] == 0

    def test_deleting_an_export_removes_its_file(self, ready, run):
        storage, name = ready.file.storage, ready.file.name
        run(ready.delete)
        assert not storage.exists(name)

    def test_deleting_the_user_removes_their_files(self, user, ready, run):
        storage, name = ready.file.storage, ready.file.name
        user.schedule_deletion()
        run(user._hard_delete)
        assert not Export.objects.exists()
        assert not storage.exists(name)


class TestDownloading:
    def url(self, export):
        return reverse("logbook:export-download", args=[export.pk])

    def test_owner_gets_the_file(self, client, user, ready):
        client.force_login(user)
        response = client.get(self.url(ready))
        assert response.status_code == 200
        assert b"".join(response.streaming_content).decode() == content(ready)
        assert response["Content-Disposition"] == (
            f'attachment; filename="{ready.filename}"'
        )
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert "no-store" in response["Cache-Control"]
        ready.refresh_from_db()
        assert ready.downloads == 1
        assert ready.last_downloaded_at is not None

    def test_must_be_signed_in(self, client, ready):
        response = client.get(self.url(ready))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_nobody_else(self, client, other_user, ready):
        client.force_login(other_user)
        assert client.get(self.url(ready)).status_code == 404
        ready.refresh_from_db()
        assert ready.downloads == 0

    def test_staff_neither(self, client, make_user, ready):
        client.force_login(make_user(is_staff=True, is_superuser=True))
        assert client.get(self.url(ready)).status_code == 404

    def test_no_such_export(self, client, user):
        client.force_login(user)
        url = reverse(
            "logbook:export-download",
            args=["00000000-0000-0000-0000-000000000000"],
        )
        assert client.get(url).status_code == 404

    def test_expired(self, client, user, ready):
        exports.tidy_exports(now=ready.expires_at)
        client.force_login(user)
        assert client.get(self.url(ready)).status_code == 410

    def test_past_its_time_but_not_tidied(self, client, user, ready):
        Export.objects.filter(pk=ready.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        client.force_login(user)
        assert client.get(self.url(ready)).status_code == 410

    def test_not_ready(self, client, user, requested):
        export = requested()
        client.force_login(user)
        assert client.get(self.url(export)).status_code == 410

    def test_only_get(self, client, user, ready):
        client.force_login(user)
        assert client.post(self.url(ready)).status_code == 405


@pytest.fixture
def s3(monkeypatch):
    """Exports stored in a (pretend) S3 bucket"""
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        storage = S3Storage(
            bucket_name=BUCKET,
            region_name="us-east-1",
            access_key="test",
            secret_key="test",
            location="exports",
            signature_version="s3v4",
            default_acl="private",
            querystring_auth=True,
            querystring_expire=300,
            custom_domain=None,
            file_overwrite=False,
        )
        monkeypatch.setattr(Export._meta.get_field("file"), "storage", storage)
        yield boto3.client("s3", region_name="us-east-1")


class TestS3:
    def test_stored_in_the_bucket(self, user, s3, ready):
        key = f"exports/{user.pk}/{ready.pk}/{ready.filename}"
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        assert body.decode() == content(ready)

    def test_isnt_public(self, user, s3, ready):
        key = f"exports/{user.pk}/{ready.pk}/{ready.filename}"
        grants = s3.get_object_acl(Bucket=BUCKET, Key=key)["Grants"]
        assert not [
            grant
            for grant in grants
            if "AllUsers" in grant["Grantee"].get("URI", "")
            or "AuthenticatedUsers" in grant["Grantee"].get("URI", "")
        ]

    def test_download_is_a_link_that_expires(self, client, user, s3, ready):
        client.force_login(user)
        response = client.get(
            reverse("logbook:export-download", args=[ready.pk])
        )
        assert response.status_code == 302
        assert response["Referrer-Policy"] == "no-referrer"
        link = urlparse(response["Location"])
        query = parse_qs(link.query)
        assert query["X-Amz-Expires"] == ["300"]
        assert "X-Amz-Signature" in query
        assert query["response-content-disposition"] == [
            f'attachment; filename="{ready.filename}"'
        ]
        fetched = requests.get(response["Location"], timeout=5)
        assert fetched.status_code == 200
        assert fetched.content.decode() == content(ready)

    def test_link_doesnt_work_unsigned(self, client, user, s3, ready):
        client.force_login(user)
        response = client.get(
            reverse("logbook:export-download", args=[ready.pk])
        )
        unsigned = response["Location"].split("?")[0]
        assert requests.get(unsigned, timeout=5).status_code == 403

    def test_expired_files_are_removed(self, user, s3, ready, run):
        run(exports.tidy_exports, now=ready.expires_at)
        listing = s3.list_objects_v2(Bucket=BUCKET)
        assert listing.get("KeyCount", 0) == 0


class TestAuditing:
    """Every run for an export is recorded, and its owner can see what was
    done"""

    def actions(self, user):
        return list(
            AuditEntry.objects.for_user(user)
            .order_by("created_at")
            .values_list("action", flat=True)
        )

    def test_built(self, user, ready):
        entry = AuditEntry.objects.for_user(user).get()
        assert entry.action == "export.built"
        assert entry.summary == (
            f"Your export of {ready.name} was built: 3 QSOs as ADIF (.adi)"
        )
        assert entry.object_id == str(ready.pk)
        assert list(AuditEntry.objects.about(ready)) == [entry]
        assert entry.run.task == "apps.logbook.tasks.build_export"
        assert entry.run.status == TaskRunStatus.SUCCEEDED

    def test_tried_again_then_given_up(
        self, user, three, monkeypatch, requested, run
    ):
        def broken(*args, **kwargs):
            raise OSError("disk full")

        export = requested()
        monkeypatch.setattr(exports, "export_to", broken)
        run(build_like_celery, export.pk)
        assert self.actions(user) == [
            "export.retrying",
            "export.retrying",
            "export.retrying",
            "export.failed",
        ]
        # Users aren't shown what went wrong inside, staff are
        summaries = AuditEntry.objects.values_list("summary", flat=True)
        assert not any("disk full" in summary for summary in summaries)
        runs = TaskRun.objects.filter(task="apps.logbook.tasks.build_export")
        assert [r.status for r in runs.order_by("attempt")] == [
            TaskRunStatus.RETRYING,
            TaskRunStatus.RETRYING,
            TaskRunStatus.RETRYING,
            TaskRunStatus.FAILED,
        ]
        assert all("OSError: disk full" in r.error for r in runs)

    def test_built_twice_is_for_staff(self, user, ready):
        tasks.build_export.apply(args=[str(ready.pk)])
        assert self.actions(user) == ["export.built"]
        skipped = AuditEntry.objects.get(action="export.skipped")
        assert not skipped.visible_to_user

    def test_expired(self, user, ready, run):
        run(exports.tidy_exports, now=ready.expires_at)
        assert self.actions(user) == ["export.built", "export.expired"]

    def test_queued_again_is_for_staff(
        self, user, three, settings, requested, run
    ):
        export = requested()
        later = timezone.now() + settings.EXPORT_STALE_AFTER
        run(exports.tidy_exports, now=later)
        assert self.actions(user) == ["export.built"]
        requeued = AuditEntry.objects.get(action="export.requeued")
        assert requeued.object_id == str(export.pk)
        assert not requeued.visible_to_user

    def test_nobody_else_sees_them(self, user, other_user, ready):
        assert not AuditEntry.objects.visible_to(other_user).exists()
