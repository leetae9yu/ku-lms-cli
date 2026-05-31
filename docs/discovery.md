# Discovery Artifact Contract

Discovery must run before final provider/stack choice and produce redacted artifacts only.

Required outputs:

- `route-map`: login, courses, materials, assignments/deadlines, recordings flow.
- `network-api-inventory`: endpoint/request/response shapes with tokens, cookies, IDs, names, and sensitive values removed.
- `selector-map`: stable selectors and text anchors for browser fallback.
- `fixtures`: redacted HTML/JSON snapshots usable in tests.
- `command-feasibility-matrix`: API/browser/mixed/infeasible classification for each required v1 command.

Raw screenshots and raw private traces are forbidden. Temporary raw working data must stay in gitignored private paths and be deleted or redacted before retention.

## DevTools/CDP observation import

Use DevTools MCP or a CDP script only for discovery, then retain a shape-only JSON fixture. The retained observation may contain:

- `page.url` and `page.title` after route patterning/redaction.
- `probes[]` with endpoint URL patterns, HTTP status, content type, and response shape keys only.
- `observed_nav_controls[]` with masked text/ids/classes for selector fallback.
- optional aggregated `network_inventory_sample[]` with URL patterns and counts.

It must not contain cookies, request/response headers, HAR files, hidden form values, raw HTML, raw screenshots, local/session storage, tokens, OAuth/SAML values, user names, emails, course names, or credential values.

The CLI normalization path is:

```bash
PYTHONPATH=src python -m ku_lms_cli.cli discover \
  --devtools-observation discovery/redacted/<run>/fixtures/devtools-auth-shapes.json
```

This writes the standard redacted artifact set under `.ku-lms/discovery/redacted/<timestamp>/` by default. The importer rejects private/raw keys and applies the shared redaction layer again before writing fixtures.

## Latest authenticated findings

The authenticated DevTools/CDP probe reached Canvas `마이페이지` from the provided global navigation URL. Shape-only evidence indicates:

- `GET /api/v1/courses?enrollment_state=active&per_page=100`: API-feasible for course listing.
- `GET /api/v1/courses/{course_id}/assignments?per_page=10`: API-feasible for assignment/deadline metadata.
- `GET /api/v1/courses/{course_id}/modules?per_page=10`: API-feasible starting point for materials traversal.
- direct `files` and `pages` endpoints are course-dependent in the sampled course, so material downloads need browser/module fallback.
- recordings did not expose a stable JSON endpoint from the main page and should remain browser/LTI playback-only.
