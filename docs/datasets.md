# Datasets: draft, push, publish

`AgencyDatasetsClient` (`client.dataset()`) writes datasets through the same lifecycle as
`gts-cli datasets ds draft|push|publish`. The server contract is in gts-agency
`documentation/cli/datasets/PYTHON_DRAFT_PUSH_PUBLISH.md`. This page covers what the SDK does
with that contract.

## Lifecycle

A dataset has many versions, and at most one of them is a draft (`status` 0). Published
versions (`status` 1) have no write path.

```
create  ->  v1 draft ── push ── publish ── draft (v2, server copies v1's objects) ── push ── publish
```

| Method | HTTP | Effect |
|--------|------|--------|
| `draft(dataset_id, org, root=None)` | `POST /{id}/_command` `{"organisation_id", "command": "draft", "payload": {}}` | Inserts `MAX(version)+1` as a draft and copies the latest version's objects into it on the server. 409 if a draft already exists. |
| `push(org, root, dataset_id=None)` | `GET /{id}?v=draft`, then one `PUT /{id}/filesystem/_upload?o=` per changed file, then one `POST /{id}/filesystem/_command` `{"organisation", "command": "del"}` per removed file | Uploads the tree's diff into the current draft. |
| `publish(dataset_id, org, comment, root=None)` | `POST /{id}/_command` `{"organisation_id", "command": "publish", "payload": {"comment"}}` | Sets the latest draft to published, in place. 400 if there is no draft, so a second publish is an error, not a no-op. |

Each endpoint reads the organisation from a different place: body `organisation_id`, query
`o`, or body `organisation`. The SDK copies each endpoint as it is and does not try to unify them.

## The `.agency/` workspace

`clone_dataset` writes this folder, and `gts-cli clone` writes the same layout, so either tool
can push a tree the other cloned.

- `.agency/dataset.json` holds the version GET body without `audit`. `push` reads `id` and `status` from it.
- `.agency/files.json` maps each relative path to its `{"hash": "<sha256 hex>", "last_modified": "<unix seconds>"}`.
  Only `hash` is used to detect changes.

Paths use forward slashes and are relative to the root. `.agency/` itself and symlinks are never
included. The clone manifest lists only the files that were downloaded. Files that were already
in the target directory are not listed, so the next push uploads them as new files.

When you pass `draft` and `publish` a `root` whose `dataset.json` has the same id, they update
that file. `draft` sets `status=0`, the new `version`, and `comment="Draft version"`. `publish`
sets `status=1` and the frozen `version`. Without this, a tree cloned from a published version
could never be pushed after `draft` (this is a gap in `gts-cli`). Neither method changes
`files.json`.

## What push guarantees

1. **Local checks run before any network call.** A push stops before it starts if: the workspace
   is missing; `dataset_id` differs from the tree's id; the local `status` is not 0; a name breaks
   the server's filename rule (it must start with an alphanumeric character and contain no `..`,
   `//` or `\`, so `.gitignore` is refused); a file is close to the 100 MiB upload cap; or a
   removed path is `metadata.json` or `README.md` (the server refuses to delete these). Each of
   these raises `ValueError`.
2. **A server draft must exist.** Before writing, push calls `GET ?v=draft`. A 404 becomes a
   `ValueError`, because a `del` with no draft makes the server panic and return a 500.
3. **Uploads run first, then deletes.** A rename is an upload of the new path plus a delete of the
   old one. Each upload sends `filename` and `checksum` before the `file` part, because the server
   ignores parts that arrive after it. No `state` part is sent, so existing files are overwritten.
4. **The manifest is written last.** `files.json` is rewritten only after every call succeeds,
   which makes a failed push safe to re-run. Re-running re-uploads the same files, which is
   harmless. A delete that already took effect comes back as
   `success: false` / `Path '…' not found`, and push treats that as success so the retry does not
   get stuck.
5. **A push with no changes does nothing.** It makes no calls and does not rewrite the manifest. Check `result.is_empty`.

## Errors

- HTTP errors propagate as `requests.HTTPError`, as everywhere else in the SDK. Examples: 409 when
  a draft already exists, 400 on a checksum mismatch, 403 without the `datasets` **write** bit.
- An HTTP 2xx response with `success: false` raises `DatasetCommandError` (a `RuntimeError`).
  The one exception is the "already deleted" case above.

## Publish comments are not stored

The server ignores `publish(comment=...)`. The version keeps the comment it was given when it
was created, which for a draft is the literal `Draft version`. To show a comment, read the
version back with `get(..., version=...)`. Do not echo the argument.

## Reading versions

`get(dataset_id, org, version)` accepts these values for `version`:

- `latest`: the newest **published** version. Between `draft` and `publish` this is still the previous version.
- `draft`: the version you can push to.
- `last`: the newest version of any status.
- A version number.
