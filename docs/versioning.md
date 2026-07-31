# Versioning, sync automation, and compatibility

## `proto/` is generated — never edit it

Cursor's internal SDK release pipeline owns the `proto/` directory of this
repository. On every SDK release it:

1. deletes `proto/` and rewrites `proto/sdk/v1/*.proto` from the internal
   source of truth;
2. writes `proto/manifest.json` describing the sync:

   ```json
   {
     "protocol": "sdk.v1",
     "sdkVersion": "<released version>",
     "sourceRepo": "anysphere/everysphere",
     "sourceCommit": "<source commit>"
   }
   ```

3. commits directly to `main`; and
4. pushes an annotated tag `vX.Y.Z` matching the released `@cursor/sdk` npm /
   `cursor-sdk` PyPI version.

Consequences:

- **Pull requests must not touch `proto/`.** Any hand edit is overwritten by
  the next sync. CI lints `proto/` (see
  [`.github/workflows/proto-check.yml`](../.github/workflows/proto-check.yml))
  but everything under it is machine-written.
- Everything **outside** `proto/` — docs, examples, CI — is human-owned and
  never modified by the sync.
- The sync tolerates an empty repository, so `proto/` may be briefly absent
  (before the first release sync). CI and tooling skip gracefully in that
  state.

## Tags and picking a version

Tags `vX.Y.Z` track SDK releases one-to-one. For a given version you get, all
mutually consistent:

- the protos at the tag in this repo;
- `@cursor/sdk@X.Y.Z` on npm and `cursor-sdk==X.Y.Z` on PyPI;
- the prebuilt bridge archives at
  `https://downloads.cursor.com/sdk-bridge/X.Y.Z/<os>/<arch>/cursor-sdk-bridge-package.tar.gz`.

Pin your adapter's codegen to a tag, and prefer running a bridge whose
`manifest.json` `sdkVersion` matches it. That said, exact matching is not
required — see the compatibility promise below.

## The `sdk.v1` compatibility promise

`sdk.v1` evolves **additively**. Within the `v1` protocol:

- existing fields are never renumbered, retyped, or repurposed;
- removals are handled with `reserved` statements, not reuse;
- new RPCs, messages, fields, enum values, stream envelope cases, and
  capability strings may be added at any time.

CI enforces this with `buf breaking` semantics (`WIRE_JSON`) on the lint
config at the repository root, and the same checks run upstream before a sync
is cut. An incompatible change would ship as a new `sdk.v2` package alongside
`sdk.v1`, not as an edit to it.

What this demands of adapters (the standard proto3 rules):

- ignore unknown fields when deserializing — **including JSON**, where some
  runtimes reject unknown keys by default (pass the equivalent of
  `ignore_unknown_fields`);
- tolerate unrecognized enum values, envelope cases, `SdkMessage.type`
  discriminators, and capability strings;
- treat discovery-line JSON the same way: unknown keys are additions.

An adapter generated from an older tag keeps working against a newer bridge,
and vice versa; new functionality simply is not visible until you regenerate.
Use `SdkBridgeControlService.GetVersion` (`protocol_version`, `capabilities`)
when you need to gate on bridge features at runtime.

## Repository conventions

- No release automation may be added to this repo that pushes to `main` or
  creates `v*` tags — those are reserved for the sync. CI is checks-only.
- The root `buf.yaml` is human-owned; it configures lint/breaking rules for
  the synced module and must keep working against whatever the sync writes.
