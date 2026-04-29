# Handoff: Nix flake for qbutler unit tests

## Status: COMPLETE

The nix flake work is finished and committed.

## What was done

Created `/workspace/flake.nix` and `/workspace/flake.lock`.

### Structure

- **Inputs:** `artiq`, `src-ndscan`, `src-oitg` (same as ndscan emulator pattern)
- **Packages:** `oitg`, `ndscan` (with `dontWrapQtApps = true`), `qbutler-test-deps`
- **Dev shell:** Includes artiq, ndscan, oitg, pytest, etc.
- **Emulator support:** `libartiq-emulator` is included; `LIBARTIQ_EMULATOR` is set in shell hook

### Issues fixed

1. `self` in flake.nix resolved to a nix store path that didn't exist because flake files were untracked. Fixed by staging the files before running `nix develop`.
2. ndscan build failed with "wrapQtAppsHook is not used". Fixed with `dontWrapQtApps = true`.
3. `Core.compile()` in ARTIQ 8 returns 5 values, not 4. Fixed fixture unpacking.
4. Kernel tests failed because `host=None` uses `CommKernelDummy` (doesn't execute kernels). Fixed by using `CoreEmulator` when `LIBARTIQ_EMULATOR` is available.

### Running tests

```bash
nix develop --impure /workspace --command python -m pytest tests/unit/ tests/func/ -v
```

## Verification

All 107 tests pass (7 skipped).
