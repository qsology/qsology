# The web interface

These rules apply to every page and every change to one. Each says how it's
met, and what checks it, so a page that breaks one fails `task check`.

## The rules

### 1. htmx and Pico CSS

- **htmx 4** (`static/vendor/htmx-4.0.0.esm.min.js`) for asking the server
  for part of a page. It's loaded as a module by `static/js/app.js`, only in
  browsers that can run it.
- **Pico CSS 2** (`static/vendor/pico-2.1.1.min.css`) for the look, with
  `static/css/qsology.css` for the little Pico doesn't do. Use Pico's
  elements and variables before adding a class. No Tailwind: if that ever
  changes, it comes with daisyUI.
- Both are served from here, never a CDN, as published. `vendor.json`
  records each file's package, version and hash, and
  `tests/web/test_delivery.py` checks them. To update one: download the
  package from npm and check its integrity hash, then copy the file in under
  its new version. Update `vendor.json` and the `{% static %}` names. The
  release must be at least two weeks old.
- htmx 4 differs from 2. Inheritance is explicit (`hx-target:inherited`),
  and events are `htmx:after:swap` style. `htmx:after:swap` fires on the
  element that made the request, not the target. Error responses are
  swapped in unless told otherwise. See the guidance in the npm package
  (`dist/skills/htmx-guidance.md`).

### 2. Everything works without scripts, styles or images

Pages are built as plain HTML forms and links first. htmx and our scripts
only add to them (progressive enhancement), so the page needs nothing from
them to work.

- Every action is a link or a form. Anything that changes something is a
  POST form with a button, never a link or a script-only control.
- Scripts are modules (`<script type="module">`), so browsers too old for
  them skip them entirely and get the plain pages. Nothing goes in the page
  as a script: the CSP (`SECURE_CSP`) allows no inline scripts or styles, no
  `style=` attributes, no `hx-on`, and nothing from another site.
- Every field has a label, every table a caption and headings, and every
  button and link reads sensibly on its own. A button repeated in a list
  carries a visually hidden name, e.g. "Revoke *Laptop logger*". Errors
  are listed at the top of the form, each linking to its field, and the
  field points at its error (`aria-describedby`). Messages say what they
  are in words; colour only helps.
- There's a skip link, landmarks (`header`, `nav`, `main`, `footer`), one
  `h1`, and headings in order. No positive `tabindex`.

**Checked by:** `tests/web/checks.py`, run on every page the tests load.
`tests/web/textbrowser.py` uses the site with no scripts, styles or images,
finding links and buttons by their text and fields by their labels, as a
screen reader user would. axe-core runs in real browsers on every page
(`tests/browser`).

### 3. Light and quick, even on an old machine

Assume a dual-core machine running Windows 7: the newest browsers it runs
are Chrome 109 and Firefox 115 ESR. Our scripts stick to what those have.

- A page has at most 1,500 elements and 12 KB of compressed HTML, with a
  full page of QSOs on it. Long lists are paged (50 to a page), never
  endless scrolling.
- The CSS and scripts together are under 40 KB compressed. They're cached
  for good: whitenoise names files by their content.
- No web fonts, no images needed, no frameworks beyond htmx.

**Checked by:** `TestBudget` in `tests/web/test_delivery.py` and
`MAX_ELEMENTS` in `tests/web/checks.py`.

### 4. Familiar, with more for those who want it

Anyone who's used a logging program should be able to log straight away.
Type the call and report, press Enter, and the form is ready for the next
QSO. What stays the same between QSOs (frequency, mode, your callsign) is
kept, and an empty date or time means now. Tab moves through the fields
that change each QSO first.

The **advanced view** (the user's `ui_mode`) shows more fields and ways to
search. The simple view is the default. While in the advanced view, every
page has a "Switch back to the simple view" button. A page in the simple
view still shows any field that has a value, so nothing is ever hidden.

### 5. Light and dark

Pico follows the device's setting. A user can choose light or dark instead
(the colours control in the footer), which sets `data-theme` on `<html>`.
Signed out, the choice is kept in the session. Own colours come from Pico's
variables so they follow the theme.

**Checked by:** axe-core's contrast checks run in light, dark, and the
device's dark setting (`tests/browser/test_with_scripts.py`).

### 6. A slow, unreliable connection

The site may be used over a weak GPRS or 3G signal.

- Pages are compressed (`GZipMiddleware`). They're small, and static files
  are fetched once.
- Parts of pages are only asked for when needed. An export being made polls
  every 10 seconds with a few hundred bytes, and stops once it's done.
- Nothing waits on the network that doesn't have to. The log sends QSOs in
  the background, see below.

### 7. A QSO is never lost

The most important rule. However a send fails, what was typed survives.

**Without scripts:**

- The form has a uuid for the QSO, made when the page is drawn. Sending the
  form twice (Back and send again, after a response that never arrived)
  logs it once, and says so.
- A form that can't be logged (a mistake, warnings) comes back with
  everything in it.
- The log page may be kept in history, and its fields never have
  `autocomplete="off"`. Firefox doesn't keep those fields' values for Back.
  So after a server error or dropped connection, Back has everything still
  there. The error page says so.
- Signed out meanwhile: the QSO is kept in the session, and is on the log
  page, ready to send, once signed in again.
- Signed in again in another tab, so the page's CSRF token is stale: the
  QSO is shown with a button to send it again (`apps.web.views.csrf_failure`).
  This is only for forms from our own pages, and never with a password or
  file.

**With scripts** (`static/js/log.js`):

- A QSO is saved in the browser (`localStorage`) the moment Log QSO is
  pressed, then sent in the background. The form is ready for the next one
  straight away.
- Until the server has it, it's listed in a "not sent yet" box saying
  what's happening. It's retried on its own, and when the connection comes
  back. It survives closing the page, and is sent next time the log is
  opened.
- Everything waiting can be downloaded as an ADIF file. Each record carries
  its uuid, so importing it and the background send don't log it twice.
- A QSO with warnings, or one that can't be logged, waits in the box with
  the reasons. It can be logged anyway, put back into the form to fix, or
  removed, after asking.
- QSOs are kept per user, so someone else signing in on the same browser
  never sends them as theirs.
- If the browser can't store anything, the form is sent the plain way.

**Checked by:** the `TestNeverLost*` tests in `tests/web/test_log.py` and
`tests/browser`, including real Back after a real server error, and going
offline.

### 8. HTTP first, then server-sent events, websockets only if needed

Plain requests and htmx polling for now. The app runs on gunicorn's sync
workers, where a held-open SSE connection takes a whole worker. SSE is for
when there's an async (ASGI) setup to serve it. Websockets only where
two-way, live traffic really needs them.

### 9. Never take the focus from the user

Nothing that happens in the background (a look up arriving, a QSO being
sent, a row updating) may move the cursor or the focus, change the
selection, or change the field the user is in. If a feature can't avoid
it, the feature is turned off instead.

- `static/js/app.js` drops any htmx swap that would replace the element
  that has the focus. The exception is when the user started it from inside
  that element. Then the result is given the same id, so htmx puts the focus
  back.
- Fragments never have `autofocus`, as htmx focuses it when swapping it in.
  A full page may have one: the log puts the cursor in the callsign.
- A look up only fills in fields that are empty, that the user hasn't
  typed in, and that the cursor isn't in. It only fills them for the call
  still in the form. It's asked for when the cursor leaves the callsign
  (`change`), so moving about in the field with the arrow keys asks for
  nothing.
- Every `.focus()` in our scripts runs from the user's own key or click,
  and has a `// Focus:` comment above it saying which.

**Checked by:** `TestLookUpNeverMovesTheCursor` (typing on while a slow look
up arrives, caret position included), the import and export focus tests,
and `test_our_scripts_only_move_the_focus_for_the_user`.

### 10. Everything the pages do, the API does

The API is at `/api/v1/` (`config/api.py`, django-ninja), described at
`/api/v1/openapi.json`. Programs authenticate with an API key
(`Authorization: Bearer qsk_…`). A user can have any number of keys, each
named, revocable, and optionally expiring. `ApiKey.scopes` is there for
limiting keys later. The site's own scripts use the session and its CSRF
token.

- Each view is marked with the API operations that do the same thing:
  `@api("qso-create")`, or `@api(reason="…")` when there's genuinely none
  (a page that only says the step before worked).
- Pages and API call the same services and forms, so they agree on what's
  valid. QSOs go in and out as the same JSON as the JSON export.

**Checked by:** `tests/web/test_parity.py`, which fails for a page without
`@api`, or one naming an operation that doesn't exist.

## How it's tested

Each function is tested at several levels of browser:

| Level | How | Where |
|---|---|---|
| Text only: no scripts, styles or images | `TextBrowser`, links and buttons by text, fields by label | `tests/web` |
| htmx and the page's scripts | Requests with `HX-Request`, checking the fragments | `tests/web` |
| API | The API equivalent of each function | `tests/web` |
| Real browser, scripts off and no images | Firefox and Chromium (Playwright) | `tests/browser/test_without_scripts.py` |
| Real browser, scripts on | The same, plus offline, slow and broken server cases, and focus | `tests/browser/test_with_scripts.py` |
| Accessibility | `tests/web/checks.py` on every page, axe-core in light and dark | both |

`task test` runs everything. `task test:quick` skips the real browsers,
for while you're working on something. Install the browsers with `task
test:browsers`; their system libraries need root (`task test:browsers --
--with-deps`).

## Adding a page

1. Write the view so it works as plain forms and links, using the services
   the API uses. Mark it with `@api(...)`, adding the API operation if
   there isn't one.
2. Extend `base.html`. Fields go through `{{ field.as_field_group }}`
   (`templates/includes/field.html`), with `includes/form_errors.html` above
   the form. A form that fails returns 422 with everything that was typed.
3. Add htmx only once the plain version works. Give fragments no
   `autofocus`, and never swap anything the user could be in.
4. Test it in the text browser, and with htmx if it uses it. Test the API
   equivalent. Add the page to `PAGES` in `tests/web/test_delivery.py` and
   `tests/browser/test_with_scripts.py`.
5. Anything that takes typing (a QSO, above all) follows rule 7.
