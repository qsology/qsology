"""Recording what background tasks do

Every celery task run is recorded as a TaskRun by the signal handlers here,
whatever the task, so nothing needs adding to a task for staff to see when
it ran and how it went. Tasks call record() for each thing they do for a
user, which is linked to the run.

Recording never stops a task: if it can't be saved it's logged and the
task carries on"""

import json
import logging
import sys
import traceback

from apps.audit.models import AuditEntry, TaskRun, TaskRunStatus
from celery.exceptions import Retry
from celery.signals import task_failure, task_postrun, task_prerun, task_retry
from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from celery import current_task

logger = logging.getLogger(__name__)

# Where the run's id is kept on the task's request while it runs. Each try
# of a task, and each task run inside another (eagerly), has its own
RUN_ID = "audit_run_id"

STATUSES = {
    "SUCCESS": TaskRunStatus.SUCCEEDED,
    "RETRY": TaskRunStatus.RETRYING,
    "FAILURE": TaskRunStatus.FAILED,
    "IGNORED": TaskRunStatus.IGNORED,
    "REJECTED": TaskRunStatus.IGNORED,
}

# Results and errors past these are cut short
MAX_RESULT = 10_000
MAX_ERROR = 20_000


def record(
    user_id,
    action: str,
    summary: str = "",
    *,
    obj=None,
    visible: bool = False,
    data: dict | None = None,
) -> AuditEntry:
    """Record something done for a user by the task that's running

    visible entries are shown to the user, so the summary should say what
    happened in their terms. data and, for entries the user can't see, the
    summary stay after the account is deleted, so neither should hold
    anything personal: ids and counts, not names"""
    return AuditEntry.objects.create(
        run_id=current_run_id(),
        user_id=user_id,
        user_pk=user_id,
        action=action,
        summary=summary[:500],
        visible_to_user=visible,
        object_type=obj._meta.label_lower if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        data=data or {},
    )


def current_run_id():
    """The id of the TaskRun for the task that's running, or None outside
    a task"""
    if not current_task:
        return None
    return getattr(current_task.request, RUN_ID, None)


def _jsonable(value):
    try:
        text = json.dumps(value, default=str)
    except ValueError:
        text = json.dumps(repr(value))
    if len(text) > MAX_RESULT:
        return {"truncated": text[:MAX_RESULT]}
    return json.loads(text)


def _describe(exception, trace: str = "") -> str:
    text = f"{type(exception).__name__}: {exception}"
    if trace:
        text = f"{text}\n\n{trace}"
    return text[:MAX_ERROR]


@task_prerun.connect
def _started(sender=None, task_id=None, task=None, args=None, kwargs=None, **_):
    try:
        run = TaskRun.objects.create(
            task=task.name,
            task_id=task_id or "",
            attempt=task.request.retries or 0,
            args=_jsonable(list(args or ())),
            kwargs=_jsonable(dict(kwargs or {})),
            worker=task.request.hostname or "",
        )
    except Exception:
        logger.exception("Couldn't record %s starting", task.name)
        return
    setattr(task.request, RUN_ID, run.pk)


@task_failure.connect
def _failed(sender=None, exception=None, einfo=None, **_):
    _finish(sender.request, error=_describe(exception, einfo.traceback))


@task_retry.connect
def _retrying(sender=None, request=None, reason=None, einfo=None, **_):
    _finish(request, error=_describe(reason, einfo.traceback))


@task_postrun.connect
def _finished(sender=None, task=None, retval=None, state=None, **_):
    if state is None:
        _raised(task.request, sys.exception())
        return
    status = STATUSES.get(state, TaskRunStatus.IGNORED)
    result = _jsonable(retval) if status == TaskRunStatus.SUCCEEDED else None
    _finish(task.request, status=status, result=result)


def _raised(request, exception) -> None:
    """Run eagerly with errors raised (CELERY_TASK_EAGER_PROPAGATES), as in
    tests, celery raises before sending task_failure or task_retry, and
    task_postrun is sent while the exception is on its way out"""
    if isinstance(exception, Retry):
        status, cause = TaskRunStatus.RETRYING, exception.exc or exception
    else:
        status, cause = TaskRunStatus.FAILED, exception
    trace = "".join(traceback.format_exception(cause)) if cause else ""
    _finish(request, status=status, error=_describe(cause, trace))


def _finish(request, **fields) -> None:
    run_id = getattr(request, RUN_ID, None)
    if run_id is None:
        return
    if "status" in fields:
        fields["finished_at"] = timezone.now()
    try:
        TaskRun.objects.filter(pk=run_id).update(**fields)
    except Exception:
        logger.exception("Couldn't record task run %s finishing", run_id)


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
def _forget_user(sender, instance, **kwargs) -> None:
    """What the user could see goes with their account, staff keep the
    rest, by the user's id only (user is set to null)"""
    AuditEntry.objects.filter(user=instance, visible_to_user=True).delete()
