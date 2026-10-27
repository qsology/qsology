# qsology

A Django amateur radio log. `task --list` shows how to run things; `task
check` is what CI runs.

## The web interface

Before changing any page, template, view, script or style, read
`docs/web.md`. Its rules aren't optional, and the tests enforce them:

- Everything works without scripts, styles or images (progressive
  enhancement), and is accessible.
- htmx 4 and Pico CSS, vendored in `static/vendor`. No CDNs, no inline
  scripts or styles (strict CSP).
- Pages stay small: 1,500 elements and 12 KB compressed at most.
- A QSO is never lost, with or without scripts.
- Nothing in the background ever moves the focus or cursor. Turn a
  feature off rather than let it.
- Every page function has an API equivalent (`@api(...)` on the view).
