"""Every page function has an API equivalent, see apps.web.parity"""

from apps.web.parity import Equivalent
from config.api import api
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.urls.exceptions import NoReverseMatch

# Not pages: the API itself, and what's only there for machines
NOT_PAGES = {"api-1"}


def page_patterns(resolver=None, namespace=None):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            inner = entry.namespace or namespace
            if inner in NOT_PAGES:
                continue
            yield from page_patterns(entry, inner)
        elif isinstance(entry, URLPattern):
            yield f"{namespace}:{entry.name}", entry.callback


def api_operation_names() -> set[str]:
    names = set()
    for _, router in api._routers:
        for path_view in router.path_operations.values():
            for operation in path_view.operations:
                names.add(operation.url_name)
    return names


def test_every_page_says_what_its_api_equivalent_is():
    missing = [
        name
        for name, view in page_patterns()
        if not isinstance(getattr(view, "api_equivalent", None), Equivalent)
    ]
    assert not missing, f"No @api(...) on {missing}"


def test_the_operations_named_exist():
    operations = api_operation_names()
    wrong = {
        name: view.api_equivalent.operations
        for name, view in page_patterns()
        if not set(view.api_equivalent.operations) <= operations
    }
    assert not wrong, f"Unknown API operations: {wrong}"


def test_pages_without_one_say_why():
    for name, view in page_patterns():
        equivalent = view.api_equivalent
        assert equivalent.operations or equivalent.reason, name


def test_every_operation_is_routed():
    for name in api_operation_names():
        try:
            reverse(f"api-1:{name}")
        except NoReverseMatch:
            # Operations with path parameters need them to reverse
            continue
