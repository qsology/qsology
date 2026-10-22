from apps.qrz.client import (
    QRZAuthError,
    QRZClient,
    QRZError,
    QRZNotFound,
    QRZResult,
)
from apps.qrz.models import QRZCredential


class QRZNotConfigured(QRZError):
    """The user hasn't set up a QRZ login"""


def _remember(credential: QRZCredential, session_key: str, error: str) -> None:
    if (credential.session_key, credential.last_error) != (session_key, error):
        credential.session_key = session_key
        credential.last_error = error[:255]
        credential.save(update_fields=["session_key", "last_error"])


def lookup(user, callsign: str, fetch=None) -> QRZResult:
    """Look up a callsign with the user's own QRZ login

    The session key is saved so the next lookup can reuse it, even when the
    callsign isn't found"""
    credential = QRZCredential.objects.filter(
        user=user, is_enabled=True
    ).first()
    if credential is None:
        raise QRZNotConfigured("No QRZ login has been set up")
    client = QRZClient(
        credential.username,
        credential.password,
        session_key=credential.session_key,
        fetch=fetch,
    )
    try:
        result = client.lookup(callsign)
    except QRZAuthError as exc:
        _remember(credential, "", str(exc))
        raise
    except QRZNotFound:
        # The login worked, it's only the callsign that is missing
        _remember(credential, client.session_key, "")
        raise
    except QRZError:
        _remember(credential, client.session_key, credential.last_error)
        raise
    _remember(credential, client.session_key, "")
    return result
