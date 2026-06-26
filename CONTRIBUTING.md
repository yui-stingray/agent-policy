# Contributing to agent-policy

`agent-policy` is intentionally small: a pure policy evaluator plus examples
for wrappers and hooks. Contributions should keep that boundary intact.

## Good first contributions

- Clarify README examples or policy-file documentation.
- Add tests for evaluator edge cases.
- Improve hook or wrapper examples without adding runtime side effects to the
  library.
- Report confusing behavior with a minimal policy, capability, context, and
  expected decision.

## Local setup

Use Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

If local pytest capture is unstable in your environment, run:

```bash
python -m pytest -s -q
```

## Pull request expectations

- Keep changes scoped to one behavior or documentation topic.
- Add or update tests for behavior changes.
- Preserve the public decision vocabulary unless the change is explicitly a
  versioned API change.
- Avoid adding network, disk, clock, logging, or process side effects to
  `agent_policy.evaluate`.

## Release notes

User-visible changes should update `CHANGELOG.md`. Version bumps should remain
separate from feature or fix patches unless the change is specifically a
release preparation patch.
