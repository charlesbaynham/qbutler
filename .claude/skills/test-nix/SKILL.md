---
name: test-nix
description: Run qbutler tests inside the Nix dev shell with the ARTIQ emulator
---

Run the full qbutler test suite inside the Nix dev shell:

```bash
nix develop . --command python -m pytest tests/ -v
```

For tests that require the ARTIQ kernel emulator + tooling, add `--withartiq`:

```bash
nix develop . --command python -m pytest tests/ -v --withartiq
```

For a specific test file or directory:

```bash
nix develop . --command python -m pytest tests/unit/ -v
```
