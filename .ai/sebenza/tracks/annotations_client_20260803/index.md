# Track annotations_client_20260803 Context

- [Specification](./spec.md)
- [Implementation Plan](./plan.json)
- [Metadata](./metadata.json)

Upstream issue: [gts-guideline-agent#22 — Publish to comand for annotators](https://github.com/groundtruthsystems/gts-guideline-agent/issues/22)

The control-plane contract in `spec.md` was read from `gts-comand` at `8d64a64a`
(`crates/comand/src/handler/annotations.rs`, `service/annotation_service.rs`), cross-checked against
the reference client `crates/cli/src/annotations/{create,upload,push}.rs`. Two findings there
correct the issue's research comment: `create` returns the `{success, message, data:{id}}`
`CommandResponse` envelope rather than a bare `{id}`, and `GET /api/annotation-specs/{id}` resolves
its path segment by **`code`**, not by UUID.
