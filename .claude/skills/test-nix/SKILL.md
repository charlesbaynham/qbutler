---
name: test-nix
description: Run qbutler tests inside the Nix dev shell with the ARTIQ emulator
---

Run the full qbutler test suite inside the Nix dev shell:

```bash
nix develop --impure . --command python -m pytest tests/ -v
```

For slow tests, add `--runslow`:

```bash
nix develop --impure . --command python -m pytest tests/ -v --runslow
```

For a specific test file or directory:

```bash
nix develop --impure . --command python -m pytest tests/unit/ -v
```
