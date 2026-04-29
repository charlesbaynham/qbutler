# Handoff: Kernel methods for qbutler

## What was done

Implemented kernel support in `qbutler.calibration` so that `check_own_state` and `fix_own_state` can be kernel methods.

### Key changes

**`qbutler/calibration.py`:**
- `_run_optimizer` now detects if `check_own_state` is a kernel (via `ndscan.experiment.utils.is_kernel`)
- If kernel: uses `_run_optimizer_kernel` which calls `_run_optimizer_kernel_loop` (a `@kernel` method) exactly **once** per optimization
- Inside the kernel loop:
  - Param values are applied via `set_from_rpc` on param stores
  - `check_own_state` is called kernel-to-kernel (cheap)
  - The host optimizer generator is advanced via RPC (`_kernel_opt_next_rpc_send`)
  - Best params are tracked on the host side (avoiding kernel writeback issues with mutable lists)
- If host: uses the original `_run_optimizer_host` loop
- RPC methods use ARTIQ type annotations (`TList(TFloat)`) so the compiler knows return types

**Test fixtures (`tests/fixtures.py`):**
- Fixed `Core.compile()` return value unpacking for ARTIQ 8 (returns 5 values, not 4)
- Added `CoreEmulator` support when `LIBARTIQ_EMULATOR` env var is set (from nix shell)
- `comm.run` is wrapped with a delegating Mock so `mock_core` still works for counting kernel calls

**Tests:**
- `tests/func/test_kernel_optimization.py`:
  - `test_kernel_optimizer_uses_single_kernel_call` - verifies exactly 1 kernel call
  - `test_kernel_optimizer_finds_optimum` - verifies optimization converges
  - `test_kernel_fix_own_state` - verifies kernel `fix_own_state` works
  - `test_kernel_fix_own_state_experiment` - verifies experiment builds and runs
- `tests/func/kernel_calibrations.py` - added `KernelOptimizableCalibration` and `KernelFixOwnStateCalibration`

### Performance model

| Call direction | Cost | Usage in implementation |
|----------------|------|------------------------|
| Host → Kernel | Very expensive (compile + transfer) | Only once per optimization (`_run_optimizer_kernel_loop`) |
| Kernel → Host (RPC) | Much cheaper | Once per optimizer point (`_kernel_opt_next_rpc_send`) |
| Kernel → Kernel | Cheapest | `check_own_state` called inside the loop |

## Nix flake

The nix flake was also fixed and committed:
- `flake.nix` includes `libartiq-emulator` and sets `LIBARTIQ_EMULATOR`
- `flake.lock` is generated
- Tests run with: `nix develop --impure /workspace --command python -m pytest tests/`

## Known limitations / future work

1. **Custom optimizers with feedback**: The current kernel path works with any optimizer generator, but each point still requires a Kernel→Host RPC. For very fast `check_own_state` kernels and many optimizer points, the RPC overhead could add up. A future optimization could batch multiple points into a single RPC (like ndscan's `KernelScanRunner` chunks).

2. **`data` type in kernel `check_own_state`**: The kernel optimizer assumes `data` from `check_own_state` is a float (for comparison in `_is_better`). Non-float `data` in a kernel `check_own_state` may cause compilation or runtime issues. This is consistent with the host optimizer behavior (host `_is_better` skips non-numeric data).

3. **`fix_state` with kernel dependencies**: When `fix_state()` is called on a dependency chain, it calls `_do_check_own_state()` before `fix_own_state()` for each dependency. If `check_own_state` is a kernel, each dependency check triggers a kernel call. This is expected and documented behavior.

4. **Dict limitations in kernels**: The implementation avoids dicts entirely in kernel code by using parallel lists for param values and explicit list indexing. This is necessary because ARTIQ kernels don't support dicts.

## Files changed

- `qbutler/calibration.py` - kernel optimizer implementation
- `tests/fixtures.py` - ARTIQ 8 + emulator compatibility
- `tests/func/kernel_calibrations.py` - new test calibration classes
- `tests/func/test_on_kernel.py` - compilation tests for kernel calibrations
- `tests/func/test_kernel_optimization.py` - functional tests for kernel optimization
- `flake.nix` / `flake.lock` - nix dev shell with emulator

## Verification

All tests pass:
```bash
nix develop --impure /workspace --command python -m pytest tests/unit/ tests/func/ -v
# 107 passed, 7 skipped
```
