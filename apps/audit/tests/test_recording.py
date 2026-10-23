from datetime import timedelta

import pytest
from apps.audit import recording
from apps.audit.models import AuditEntry, TaskRun, TaskRunStatus
from apps.audit.recording import record
from apps.audit.tasks import tidy_task_runs
from celery.exceptions import Retry
from config.celery import app
from django.utils import timezone

from celery import shared_task

pytestmark = pytest.mark.django_db


@shared_task(name="audit_tests.add")
def add(x, y):
    return x + y


@shared_task(name="audit_tests.broken")
def broken():
    raise ValueError("it broke")


@shared_task(name="audit_tests.for_user")
def for_user(user_id):
    record(user_id, "test.done", "Done for you", visible=True)
    # Eagerly, the inner task has run by the time delay returns
    return add.delay(1, 2).result


@shared_task(bind=True, name="audit_tests.flaky", max_retries=2)
def flaky(self):
    if self.request.retries < 2:
        raise self.retry(exc=OSError("blip"), countdown=0)
    return "worked"


@shared_task(name="audit_tests.huge")
def huge():
    return "x" * (recording.MAX_RESULT * 2)


class TestRuns:
    def test_every_run_is_recorded(self):
        assert add.delay(2, 3).get() == 5
        run = TaskRun.objects.get()
        assert run.task == "audit_tests.add"
        assert run.status == TaskRunStatus.SUCCEEDED
        assert run.args == [2, 3]
        assert run.result == 5
        assert run.attempt == 0
        assert run.finished_at >= run.started_at
        assert run.duration is not None

    def test_failures_are_recorded(self):
        with pytest.raises(ValueError):
            broken.delay()
        run = TaskRun.objects.get()
        assert run.status == TaskRunStatus.FAILED
        assert run.result is None
        assert run.error.startswith("ValueError: it broke")
        # With where it went wrong
        assert "Traceback" in run.error

    def test_each_try_is_a_run(self):
        # Eagerly, a retry is raised rather than run, so try again as a
        # worker would
        for retries in range(3):
            try:
                result = flaky.apply(task_id="flaky-1", retries=retries)
            except Retry:
                continue
        assert result.get() == "worked"
        runs = TaskRun.objects.order_by("attempt")
        assert [(r.attempt, r.status) for r in runs] == [
            (0, TaskRunStatus.RETRYING),
            (1, TaskRunStatus.RETRYING),
            (2, TaskRunStatus.SUCCEEDED),
        ]
        assert {r.task_id for r in runs} == {"flaky-1"}
        assert "OSError: blip" in runs[0].error

    def test_big_results_are_cut_short(self):
        huge.delay()
        run = TaskRun.objects.get()
        assert len(run.result["truncated"]) == recording.MAX_RESULT

    def test_not_being_able_to_record_doesnt_stop_the_task(
        self, monkeypatch, caplog
    ):
        def down(*args, **kwargs):
            raise ConnectionError("database down")

        monkeypatch.setattr(TaskRun.objects, "create", down)
        assert add.delay(1, 1).get() == 2
        assert not TaskRun.objects.exists()
        assert "Couldn't record audit_tests.add starting" in caplog.text


class TestEntries:
    def test_linked_to_the_run_doing_it(self, user):
        assert for_user.delay(user.pk).get() == 3
        entry = AuditEntry.objects.get()
        assert entry.run.task == "audit_tests.for_user"
        assert entry.user == user
        assert entry.user_pk == user.pk
        assert entry.action == "test.done"
        # The task it ran inside has a run of its own
        assert TaskRun.objects.filter(task="audit_tests.add").exists()

    def test_outside_a_task(self, user):
        entry = record(user.pk, "test.done", obj=user)
        assert entry.run is None
        assert entry.object_type == "accounts.user"
        assert entry.object_id == str(user.pk)

    def test_without_a_user(self):
        entry = record(None, "test.done")
        assert entry.user is None
        assert entry.user_pk is None


class TestVisibility:
    @pytest.fixture
    def entries(self, user, other_user):
        return {
            "mine": record(user.pk, "test.mine", visible=True),
            "staff only": record(user.pk, "test.staff"),
            "theirs": record(other_user.pk, "test.theirs", visible=True),
        }

    def test_users_see_only_their_own_visible_entries(self, user, entries):
        assert list(AuditEntry.objects.visible_to(user)) == [entries["mine"]]

    def test_staff_see_everything(self, make_user, entries):
        staff = make_user(is_staff=True)
        assert AuditEntry.objects.visible_to(staff).count() == 3

    def test_runs_are_for_staff(self, user, make_user):
        add.delay(1, 1)
        assert not TaskRun.objects.visible_to(user).exists()
        staff = make_user(is_staff=True)
        assert TaskRun.objects.visible_to(staff).count() == 1


class TestDeletingAccounts:
    def test_what_they_could_see_goes(self, user, other_user):
        record(user.pk, "test.mine", visible=True)
        record(other_user.pk, "test.theirs", visible=True)
        user.schedule_deletion()
        user._hard_delete()
        assert list(AuditEntry.objects.values_list("action", flat=True)) == [
            "test.theirs"
        ]

    def test_staff_keep_the_rest_by_id(self, user):
        record(user.pk, "test.staff")
        user_pk = user.pk
        user.schedule_deletion()
        user._hard_delete()
        entry = AuditEntry.objects.get()
        assert entry.user is None
        assert entry.user_pk == user_pk


class TestTidying:
    def test_old_runs_and_their_entries_go(self, user, settings):
        for_user.delay(user.pk)
        record(user.pk, "test.outside")
        later = timezone.now() + settings.TASK_AUDIT_RETENTION
        later += timedelta(seconds=1)
        assert tidy_task_runs(now=later) == {"runs": 2, "entries": 2}
        assert not TaskRun.objects.exists()
        assert not AuditEntry.objects.exists()

    def test_recent_ones_are_kept(self, user):
        for_user.delay(user.pk)
        assert tidy_task_runs() == {"runs": 0, "entries": 0}
        assert TaskRun.objects.count() == 2

    def test_runs_daily(self):
        entry = app.conf.beat_schedule["tidy-task-runs"]
        assert entry["task"] == "apps.audit.tasks.tidy_task_runs"
        assert entry["schedule"] == timedelta(hours=24)
