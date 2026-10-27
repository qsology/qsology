"""What every page must be, checked on every page the tests load

These are the rules in docs/web.md that can be read from the HTML. The
browser tests (tests/browser) add axe-core's checks and what only a real
browser shows, such as colour contrast in each theme"""

import itertools
from collections import Counter

from bs4 import BeautifulSoup

from tests.web.textbrowser import NOT_TYPED, accessible_name, text_of

# The most elements a page may have, so it stays quick on an old, slow
# machine. A page of 50 QSOs is about 1,000
MAX_ELEMENTS = 1500
# The most HTML a page may be, compressed, for a slow mobile connection
MAX_COMPRESSED_BYTES = 12 * 1024


class PageProblem(AssertionError):
    pass


def problems(soup: BeautifulSoup, fragment: bool = False) -> list[str]:
    found = []
    elements = soup.find_all(True)
    if len(elements) > MAX_ELEMENTS:
        found.append(f"{len(elements)} elements, the most is {MAX_ELEMENTS}")

    if not fragment:
        html = soup.find("html")
        if html is None or not html.get("lang"):
            found.append("<html> has no lang")
        if not soup.title or not soup.title.text.strip():
            found.append("No <title>")
        headings = soup.find_all("h1")
        if len(headings) != 1:
            found.append(f"{len(headings)} <h1>s, there should be one")
        for landmark in ("header", "main", "footer"):
            if soup.find(landmark) is None:
                found.append(f"No <{landmark}>")
        first = soup.find(["a", "button", "input", "select", "textarea"])
        if first is None or "skip-link" not in first.get("class", []):
            found.append("The first thing to Tab to isn't the skip link")
        autofocus = soup.find_all(autofocus=True)
        if len(autofocus) > 1:
            found.append("More than one autofocus")
    elif soup.find(autofocus=True) is not None:
        # htmx focuses it when it's swapped in, taking the focus away
        found.append("A fragment has autofocus")

    levels = [int(h.name[1]) for h in soup.find_all(["h1", "h2", "h3", "h4"])]
    for before, after in itertools.pairwise(levels):
        if after > before + 1:
            found.append(f"Heading level skips from h{before} to h{after}")

    ids = Counter(e["id"] for e in elements if e.has_attr("id"))
    found += [
        f"id {key!r} is used {n} times" for key, n in ids.items() if n > 1
    ]

    for script in soup.find_all("script"):
        if not script.get("src"):
            found.append("An inline <script>, the CSP blocks them")
        elif script.get("type") != "module":
            found.append(f"{script['src']} isn't a module")
    if soup.find("style") is not None:
        found.append("A <style> element, the CSP blocks them")
    for element in soup.find_all(style=True):
        found.append(f"<{element.name}> has a style attribute")
    for element in elements:
        on = [
            a
            for a in element.attrs
            if a.startswith("on") or a.startswith("hx-on")
        ]
        if on:
            found.append(f"<{element.name}> has inline handlers {on}")
        tabindex = element.get("tabindex")
        if tabindex and tabindex.lstrip("-").isdigit() and int(tabindex) > 0:
            found.append(f"<{element.name}> has a positive tabindex")

    for image in soup.find_all("img"):
        if not image.has_attr("alt"):
            found.append(f"<img src={image.get('src')!r}> has no alt")

    for control in soup.find_all(["input", "select", "textarea"]):
        if control.get("type", "text") in NOT_TYPED:
            continue
        if not accessible_name(control, soup):
            found.append(f"{control.name} {control.get('name')!r} has no label")
    for button in soup.find_all("button"):
        if not accessible_name(button, soup):
            found.append(f"A button has no name: {button}")
    for link in soup.find_all("a"):
        if not accessible_name(link, soup):
            found.append(f"A link has no name: {link}")
        if link.get("href", "").startswith("javascript:"):
            found.append("A javascript: link")

    for form in soup.find_all("form"):
        if form.get("method", "get").lower() == "post" and not form.find(
            "input", attrs={"name": "csrfmiddlewaretoken"}
        ):
            found.append(
                f"A POST form without a CSRF token: {form.get('action')}"
            )
        submit = form.find(
            "button", type=lambda kind: kind in (None, "submit")
        ) or form.find("input", type="submit")
        if submit is None:
            found.append(f"A form with no button: {form.get('action')}")

    for table in soup.find_all("table"):
        if table.find("caption") is None:
            found.append("A table with no caption")
        if table.find("th") is None:
            found.append("A table with no headings")

    for element in elements:
        role = element.get("role")
        if element.name == "a" and role == "button" and not element.get("href"):
            found.append("A link pretending to be a button")
        if element.has_attr("aria-describedby"):
            for ref in element["aria-describedby"].split():
                if soup.find(id=ref) is None:
                    found.append(f"aria-describedby points at missing {ref!r}")
        if element.has_attr("aria-labelledby"):
            for ref in element["aria-labelledby"].split():
                if soup.find(id=ref) is None:
                    found.append(f"aria-labelledby points at missing {ref!r}")
    for label in soup.find_all("label", attrs={"for": True}):
        if soup.find(id=label["for"]) is None:
            found.append(
                f"A label for missing {label['for']!r}: {text_of(label)}"
            )
    return found


def check_page(soup: BeautifulSoup, response=None) -> None:
    fragment = soup.find("html") is None
    found = problems(soup, fragment=fragment)
    if found:
        where = response.wsgi_request.path if response is not None else ""
        raise PageProblem(f"{where}: " + "; ".join(found))


def check_html(html: str | bytes) -> None:
    check_page(BeautifulSoup(html, "html.parser"))
