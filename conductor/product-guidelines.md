# Product Guidelines — GTS Agency Python SDK

## Documentation & Prose Style

**Pragmatic and concise.** Documentation exists to get an engineer from zero to a
working call as fast as possible.

- README goes straight to installation, configuration, and usage; no marketing prose.
- Docstrings state behaviour in one line, followed by `Args:`/`Returns:` sections
  where signatures are not self-explanatory.
- Code samples favour copy-paste-runnable snippets over abstract fragments.

## Error Communication

**Transparent propagation, no wrapping.**

- HTTP errors surface as `requests.HTTPError` via `response.raise_for_status()`;
  the SDK never converts them into custom exception hierarchies.
- Client-side validation failures (e.g. a malformed `gtsf://` URI) raise standard
  Python exceptions (`ValueError`) before any network call is made.
- Error semantics of the underlying API (400/404/409) are documented in method
  docstrings where they carry meaning for the caller.

## Credentials in Examples

**Dev-environment credentials are acceptable in committed examples.** The team
accepts the convenience/risk trade-off of shipping example scripts with working
dev-environment defaults so they run out of the box. This acceptance is scoped to
dev-environment credentials only. For awareness: the repository is publicly
readable, so anything committed here is world-visible.

## Delegate Delivery Checklist

Every new API domain (delegate) ships with all three companion artifacts:

1. **Runnable example** — an `examples/quick_<domain>.py` script exercising the main
   flows end to end.
2. **README update** — the Delegate Clients list and a usage snippet are updated in
   the same change.
3. **CLAUDE.md sync** — the new delegate is registered in the architecture section so
   AI-assisted development context stays current.

## Versioning & Compatibility

**Pre-1.0 free evolution.** While the SDK is on 0.x (currently `0.0.1rcN`):

- Breaking changes are permitted and communicated through version increments.
- No deprecation-warning cycle is required before removing or renaming APIs.
- Strict SemVer (major-version bumps for breaking changes, deprecation windows)
  begins at 1.0.
