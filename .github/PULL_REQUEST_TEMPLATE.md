<!-- Keep this short. Delete anything that doesn't apply. -->

## What this changes

<!-- One paragraph. What was wrong or missing, and what the patch does about it. -->

## Why this way

<!-- Only if the approach isn't obvious: what you rejected and why. -->

## How you verified it

<!-- Which command, on what. "Tests pass" is weaker than "test X fails without
     this patch and passes with it". If you ran it on a device, say which. -->

```bash
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/plan jenny/runtime jenny/session jenny/snapshot jenny/gateway_runtime.py && pytest -q
```

## Checklist

- [ ] Every commit is signed off (`git commit -s`) — CI enforces DCO, see [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] `ruff check jenny/ tests/` is clean
- [ ] `pytest -q` is green
- [ ] A behaviour change comes with a test that fails without the patch
- [ ] New comments/docstrings follow the language convention (Italian for new code, English for identifiers and log messages)
- [ ] New user-facing WebUI strings go through the i18n JSON files, not hardcoded
