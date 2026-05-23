# Vendored frontend assets

Files in this directory are vendored from upstream.

They are embeded in the binary with embed.FS and MUST have a version number

## Current versions

| File                 | Version | Source                                                     |
|----------------------|---------|------------------------------------------------------------|
| `htmx-2.0.9.min.js`  | 2.0.9   | https://github.com/bigskysoftware/htmx/releases/tag/v2.0.9 |
| `pico-2.0.6.min.css` | 2.0.6   | https://github.com/picocss/pico/releases/tag/v2.0.6        |

## Updating

1. Download the new minified file from the upstream release.
2. Drop it in this directory under its versioned filename, e.g.
   `htmx-2.0.10.min.js`.
3. Update the references in `internal/web/views/layout.templ` (and re-run
   `templ generate`).
4. Delete the old file.
5. Commit. The PR diff will show exactly which bytes changed.

## Integrity

`assets.SRI("vendor/<file>")` computes a `sha256-...` integrity hash at
process start. Templates embed it as `<script integrity="...">` so a compromise
or in-flight tampering would be rejected by the browser — but since we 
serve these files ourselves and never fetch them at runtime, the
SRI is primarily defense against accidental file replacement.
MustSRI may also be used if build is to fail when a file does not have an SRI
