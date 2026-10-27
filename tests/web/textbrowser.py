"""A browser for tests that uses the site the way the least capable
visitor does: no scripts, no styles, no images. It finds links and buttons
by the text they're read out with, and form fields by their labels, as a
screen reader user would. A page that can only be used another way fails.

Every page it loads is checked by check_page, see docs/web.md"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from django.core.files.uploadedfile import SimpleUploadedFile

_REQUIRED_RE = re.compile(r"\s*\(required\)$")
BASE = "http://testserver"
# Inputs that aren't filled in by typing
NOT_TYPED = {"hidden", "submit", "button", "image", "reset"}


def _space(text: str) -> str:
    return " ".join(text.split())


def text_of(element: Tag) -> str:
    """What an element is read out as: its text, with images by their alt
    text, and without what's hidden from everyone"""
    parts = []
    for node in element.descendants:
        if isinstance(node, Tag):
            if node.name == "img":
                parts.append(node.get("alt", ""))
            continue
        parent = node.parent
        if any(
            ancestor.has_attr("hidden") or ancestor.get("aria-hidden") == "true"
            for ancestor in [parent, *parent.parents]
            if isinstance(ancestor, Tag)
        ):
            continue
        if parent.name in ("script", "style", "template", "datalist"):
            continue
        parts.append(str(node))
    return _space(" ".join(parts))


def accessible_name(element: Tag, soup: BeautifulSoup) -> str:
    """The name a screen reader gives the element, near enough: aria-label
    or aria-labelledby, then its labels, then its own text"""
    if element.get("aria-labelledby"):
        names = [
            text_of(soup.find(id=ref))
            for ref in element["aria-labelledby"].split()
            if soup.find(id=ref)
        ]
        return _space(" ".join(names))
    if element.get("aria-label"):
        return _space(element["aria-label"])
    if element.name in ("input", "select", "textarea"):
        names = []
        if element.get("id"):
            names += [
                text_of(label)
                for label in soup.find_all(
                    "label", attrs={"for": element["id"]}
                )
            ]
        wrapping = element.find_parent("label")
        if wrapping is not None:
            names.append(text_of(wrapping))
        if element.name == "input" and element.get("type") in (
            "submit",
            "button",
        ):
            names.append(element.get("value", ""))
        if not names and element.get("title"):
            names.append(element["title"])
        return _space(" ".join(names))
    return text_of(element)


@dataclass
class Form:
    element: Tag
    values: dict[str, list] = field(default_factory=dict)

    @classmethod
    def read(cls, element: Tag) -> Form:
        form = cls(element)
        for control in element.find_all(["input", "select", "textarea"]):
            name = control.get("name")
            if not name or control.has_attr("disabled"):
                continue
            kind = (
                control.get("type", "text") if control.name == "input" else ""
            )
            if control.name == "textarea":
                form.values.setdefault(name, []).append(control.text)
            elif control.name == "select":
                selected = control.find_all("option", selected=True) or [
                    control.find("option")
                ]
                for option in filter(None, selected):
                    form.values.setdefault(name, []).append(
                        option.get("value", option.text)
                    )
            elif kind in ("checkbox", "radio"):
                if control.has_attr("checked"):
                    form.values.setdefault(name, []).append(
                        control.get("value", "on")
                    )
            elif kind in ("submit", "button", "image", "reset", "file"):
                continue
            else:
                form.values.setdefault(name, []).append(
                    control.get("value", "")
                )
        return form


class PageError(AssertionError):
    pass


class TextBrowser:
    def __init__(self, client, check=None):
        self.client = client
        self.page_check = check
        self.response = None
        self.url = ""
        self.soup = None
        self.forms: dict[str, Form] = {}
        self.history: list[tuple[str, dict]] = []
        self.files: dict[str, SimpleUploadedFile] = {}

    # Loading pages

    def _load(self, response, url: str):
        self.response = response
        self.url = url
        content_type = response.get("Content-Type", "")
        if "text/html" in content_type:
            self.soup = BeautifulSoup(response.content, "html.parser")
            if self.page_check is not None:
                self.page_check(self.soup, response)
        else:
            self.soup = None
        self.forms = {}
        self.files = {}
        return self

    def _follow(self, response, url: str):
        for _ in range(10):
            if response.status_code not in (301, 302, 303, 307, 308):
                break
            url = urljoin(url, response["Location"])
            response = self.client.get(url)
        return self._load(response, url)

    def get(self, url: str):
        if self.url:
            self.history.append((self.url, self._form_state()))
        return self._follow(self.client.get(url), url)

    def back(self):
        """Back to the page before, with what was typed into it still
        there, as browsers keep it"""
        url, saved = self.history.pop()
        self._follow(self.client.get(url), url)
        for element in self.soup.find_all("form"):
            key = self._form_key(element)
            if key in saved:
                self.forms[key] = Form(element, saved[key])
        return self

    def _form_key(self, element: Tag) -> str:
        """Which form it is, the same from one load of a page to the next"""
        if element.get("id"):
            return element["id"]
        return str(self.soup.find_all("form").index(element))

    def _form_state(self) -> dict[str, dict[str, list]]:
        return {
            key: {name: list(values) for name, values in form.values.items()}
            for key, form in self.forms.items()
        }

    @property
    def status(self) -> int:
        return self.response.status_code

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def text(self) -> str:
        body = self.soup.body or self.soup
        return text_of(body)

    def __contains__(self, text: str) -> bool:
        return _space(text) in self.text

    @property
    def title(self) -> str:
        return _space(self.soup.title.text) if self.soup.title else ""

    @property
    def h1(self) -> str:
        heading = self.soup.find("h1")
        return text_of(heading) if heading else ""

    # Finding things

    def _named(self, elements, name: str, what: str, exact: bool = False):
        found = []
        for element in elements:
            actual = accessible_name(element, self.soup)
            if (actual == name) if exact else (name.lower() in actual.lower()):
                found.append(element)
        if not found:
            names = [accessible_name(e, self.soup) for e in elements]
            raise PageError(f"No {what} named {name!r} on {self.url}: {names}")
        if len(found) > 1 and not exact:
            exact_found = [
                e
                for e in found
                if _REQUIRED_RE.sub("", accessible_name(e, self.soup)) == name
            ]
            if len(exact_found) == 1:
                return exact_found[0]
            # The same link twice (in the menu and the page, say) is fine
            targets = {(e.name, e.get("href")) for e in found}
            if len(targets) == 1 and found[0].get("href"):
                return found[0]
            names = [accessible_name(e, self.soup) for e in found]
            raise PageError(f"{len(found)} {what}s match {name!r}: {names}")
        return found[0]

    def link(self, name: str) -> Tag:
        return self._named(self.soup.find_all("a", href=True), name, "link")

    def button(self, name: str) -> Tag:
        buttons = self.soup.find_all("button") + self.soup.find_all(
            "input", type=["submit", "image"]
        )
        return self._named(buttons, name, "button")

    def field(self, label: str) -> Tag:
        controls = [
            control
            for control in self.soup.find_all(["input", "select", "textarea"])
            if control.get("type", "text") not in NOT_TYPED
        ]
        return self._named(controls, label, "field")

    def _form_for(self, element: Tag) -> Form:
        if element.get("form"):
            element_form = self.soup.find("form", id=element["form"])
        else:
            element_form = element.find_parent("form")
        if element_form is None:
            raise PageError(f"{element} isn't in a form")
        key = self._form_key(element_form)
        if key not in self.forms:
            self.forms[key] = Form.read(element_form)
        return self.forms[key]

    def value(self, label: str) -> str:
        control = self.field(label)
        if control.find_parent("form") is None and not control.get("form"):
            return control.get("value", "")
        values = self._form_for(control).values.get(control["name"], [])
        return values[0] if values else ""

    # Doing things

    def click(self, name: str):
        """Follow a link"""
        href = self.link(name)["href"]
        return self.get(urljoin(self.url, href))

    def fill(self, label: str, value: str):
        control = self.field(label)
        if control.get("readonly") is not None:
            raise PageError(f"{label!r} can't be typed in")
        self._form_for(control).values[control["name"]] = [value]
        return self

    def choose(self, label: str, option: str):
        """Pick an option from a list, or a radio button by its label"""
        control = self.field(label)
        form = self._form_for(control)
        if control.name == "select":
            for each in control.find_all("option"):
                if _space(each.text) == option or each.get("value") == option:
                    form.values[control["name"]] = [
                        each.get("value", each.text)
                    ]
                    return self
            raise PageError(f"{label!r} has no option {option!r}")
        if control.get("type") == "radio":
            form.values[control["name"]] = [control.get("value", "on")]
            return self
        raise PageError(f"{label!r} isn't a list or radio button")

    def check(self, label: str, checked: bool = True):
        control = self.field(label)
        form = self._form_for(control)
        name = control["name"]
        if checked:
            form.values[name] = [control.get("value", "on")]
        else:
            form.values.pop(name, None)
        return self

    def attach(self, label: str, filename: str, content: bytes):
        control = self.field(label)
        self._form_for(control)
        self.files[control["name"]] = SimpleUploadedFile(filename, content)
        return self

    def press(self, name: str, follow: bool = True):
        """Press a button, sending its form"""
        button = self.button(name)
        form = self._form_for(button)
        data = {key: list(values) for key, values in form.values.items()}
        if button.get("name"):
            data[button["name"]] = [button.get("value", "")]
        method = form.element.get("method", "get").lower()
        action = urljoin(
            self.url,
            button.get("formaction") or form.element.get("action") or "",
        )
        self.history.append((self.url, self._form_state()))
        if method == "post":
            data.update(self.files)
            # Browsers send where the form was, the site's Referrer-Policy
            # is same-origin
            response = self.client.post(
                action, data, headers={"Referer": urljoin(BASE, self.url)}
            )
        else:
            response = self.client.get(action, data)
        if not follow:
            return self._load(response, action)
        return self._follow(response, action)

    # Reading

    def table(self, caption: str | None = None) -> list[dict[str, str]]:
        """A table's rows, each as {column heading: text}"""
        for table in self.soup.find_all("table"):
            found = table.find("caption")
            if caption is None or (found and caption in text_of(found)):
                headings = [text_of(th) for th in table.select("thead th")]
                return [
                    dict(
                        zip(
                            headings,
                            (
                                text_of(cell)
                                for cell in row.find_all(["td", "th"])
                            ),
                            strict=False,
                        )
                    )
                    for row in table.select("tbody tr")
                ]
        raise PageError(f"No table captioned {caption!r}")

    def messages(self) -> list[str]:
        return [text_of(m) for m in self.soup.select(".message")]

    def errors(self) -> list[str]:
        summary = self.soup.select_one(".form-errors")
        return [text_of(li) for li in summary.find_all("li")] if summary else []

    def field_errors(self, label: str) -> list[str]:
        """The errors read out with a field (aria-describedby)"""
        control = self.field(label)
        ids = control.get("aria-describedby", "").split()
        return [
            text_of(self.soup.find(id=ref))
            for ref in ids
            if ref.endswith("_error") and self.soup.find(id=ref)
        ]


def visible_text(html: str) -> str:
    return text_of(BeautifulSoup(html, "html.parser"))
