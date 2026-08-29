# Operations Governance

The shared ecosystem runbook is maintained in the `agent-guard` repository at
`docs/operations-governance.md`. This repository applies these local rules:

- `agent-policy required CI` remains the strict stable aggregate for `master`.
- Release tags are annotated, immutable `vX.Y.Z` tags that peel to protected
  `master` with successful CI.
- The candidate wheel must pass the current toolkit compatibility gate before
  PyPI publication.
- A required CI outage does not authorize a direct push, force push, tag move,
  or permanent bypass actor.
- A broken, compatibility-violating, or vulnerable PyPI release is yanked with
  a reason and replaced by a new patch version; the old version is not reuploaded.
- Toolkit pins and evidence move only after the replacement is publicly verified.
- Incident and review records do not contain raw tokens, URLs, event bodies,
  personal paths, or private payloads.

See [`integration-contract.md`](integration-contract.md) for the distinct
runtime approval and replay-prevention obligations owned by integrations.
