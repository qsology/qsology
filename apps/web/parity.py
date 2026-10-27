"""Every page function has an API equivalent

Each view says which API operations do the same thing, by their url_name in
config.api, or why there isn't one. tests/web/test_parity.py checks every
page URL says so and that the operations exist, so a page can't be added
without its API"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Equivalent:
    operations: tuple[str, ...]
    # Why a page has no API equivalent, e.g. it only shows other pages
    reason: str = ""


def api(*operations: str, reason: str = ""):
    """Mark a view with the API operations that do the same thing

    @api("qso-create") or, with none, @api(reason="Only links elsewhere")"""
    if not operations and not reason:
        raise ValueError("Name the API operations, or say why there are none")

    def decorate(view):
        view.api_equivalent = Equivalent(tuple(operations), reason)
        return view

    return decorate
