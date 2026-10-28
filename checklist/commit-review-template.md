# Commit Review Template

Use this after each implementation commit. The implementer should provide the commit hash,
phase, claimed checklist items, commands run, and results.

## Review record

- Commit:
- Phase:
- Scope:
- Reviewer:
- Date:

## Checklist

- [ ] Diff is limited to the claimed phase and preserves unrelated work.
- [ ] Fixed external contracts remain compatible.
- [ ] Safety boundary is deterministic; model output cannot directly authorize money movement.
- [ ] Identity, idempotency, concurrency, and provider-failure behavior are covered.
- [ ] Trace/audit output is complete, ordered, and free of secrets.
- [ ] Tests cover the changed behavior and relevant boundary cases.
- [ ] Documentation/configuration is updated where behavior or run instructions changed.
- [ ] No secrets, generated artifacts, or accidental PaySwift changes are present.
- [ ] Commit follows the `phase-N:` convention and keeps `main` buildable.

## Commands and evidence

```text
<command>
<result>
```

## Findings

### Blockers

- None / ...

### High-value improvements

- None / ...

### Verified items

- ...

## Decision

- [ ] Green signal for next phase
- [ ] Changes required before next phase

Required follow-up:

1. ...

## Reviewer verdict

- [ ] 🟢 Proceed
- [ ] 🟡 Proceed after small fixes
- [ ] 🔴 Redo part of this phase

Watch-outs for the next phase:

- ...
