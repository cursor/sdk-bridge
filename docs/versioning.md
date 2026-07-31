# Versioning and compatibility

## `proto/` is generated — never edit it

The `proto/` directory is machine-written: Cursor's release automation
regenerates it on every SDK release and tags the result. Any hand edit is
overwritten by the next release, so **pull requests must not touch
`proto/`**. `proto/manifest.json` records which release a checkout carries
(`protocol`, `sdkVersion`, and the source commit it was generated from).

Everything **outside** `proto/` — docs, examples, CI — is human-owned and
never modified by a release.

`proto/` may be briefly absent (before the first release is published). CI
and tooling skip gracefully in that state.

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

Compatibility is checked with `buf breaking` semantics (`WIRE_JSON`,
configured in the root `buf.yaml`) before every release. An incompatible
change would ship as a new `sdk.v2` package alongside `sdk.v1`, not as an
edit to it.

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
