from django.dispatch import Signal

# Sent once a change to a QSO has been committed, with qso and event. Never
# sent for a change that was rolled back
qso_changed = Signal()
