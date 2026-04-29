# qbutler

**qbutler** is a Python framework for managing calibrations in ARTIQ/ndscan-based quantum physics experiments. It automates the checking, optimization, and repair of experimental parameters that drift over time, organizing them into dependency graphs so that downstream calibrations are only validated after their prerequisites are known-good.

## Quick facts

| | |
|---|---|
| **Language** | Python 3.10+ |
| **Domain** | ARTIQ quantum experiment control |
| **Core deps** | `artiq`, `ndscan`, `sipyco`, `networkx`, `numpy` |
| **Test runner** | pytest |
| **Env manager** | Nix flake (preferred) or Poetry |
| **Main branch** | `master` |

## Architecture

### Calibration

The central abstraction is `qbutler.calibration.Calibration`, a subclass of `ndscan.experiment.fragment.ExpFragment`. A Calibration represents one step in a calibration chain:

- **Status**: either `OK` or `BAD` (with sub-categories: expired, bad dependencies, bad data, invalid data)
- **Check**: `check_own_state()` measures the system and returns `(CalibrationResult, data)`
- **Repair**: `fix_own_state()` optimizes parameters to bring the system back to `OK`
- **Timeout**: calibrations expire after a configurable timeout, triggering re-checks
- **Dependencies**: calibrations can depend on other calibrations (see DAG below)

Key methods on `Calibration`:

| Method | Purpose |
|---|---|
| `build_calibration()` | Define parameters, timeouts, and dependencies (analogous to `build_fragment()`) |
| `check_own_state()` | **Must override.** Measure and return status + data |
| `fix_own_state()` | Optimize `setattr_param_optimizable` params to reach `OK`. Override for custom logic |
| `check_state()` | Check this calibration and all dependencies recursively |
| `fix_state()` | Fix this calibration and all dependencies recursively |
| `guess_state()` | Lightweight status estimate based on cached results (no device interaction) |

`Calibration` is also a valid ndscan `ExpFragment`, so it can be scanned over its parameters via the normal ndscan interface.

### DAG dependency system

`qbutler.dag` maintains a global directed acyclic graph of all instantiated Calibrations using `networkx`. When `add_dependency()` is called during `build_calibration()`, the relationship is recorded via weakrefs (so garbage collection still works). The DAG is rebuilt lazily when invalidated.

`check_state()` and `fix_state()` traverse dependencies from furthest to nearest, ensuring prerequisites are validated before dependents.

### Optimizers

`qbutler.optimizers` provides generator-based optimizer functions. The default is `grid_search_optimizer`, an N-dimensional grid search over `ParamSpec` ranges. Optimizers yield `{param_name: value}` dicts and receive `(result, data)` tuples via `generator.send()`.

Calibrations can set a custom optimizer via `set_optimizer()` during `build_calibration()`.

### Kernel support

`check_own_state` and `fix_own_state` can be `@kernel` methods. When `check_own_state` is a kernel:

- `_run_optimizer_kernel_loop()` is called **once** as a single kernel call
- Parameter values are applied inside the kernel via `set_from_rpc`
- The host optimizer generator is advanced via RPC (`_kernel_opt_next_rpc_send`)
- This avoids expensive host->kernel round trips per optimizer point

See `tests/func/test_kernel_optimization.py` and `tests/func/kernel_calibrations.py` for examples.

### Entrypoints

`qbutler.entrypoints` provides `setattr_calibration`, a method monkey-patched onto `ndscan.experiment.Fragment` that lets any Fragment embed a Calibration as a subfragment. This is the primary integration point: experiments consume calibrated subsystems by adding them as dependencies.

### Monitoring

`qbutler.monitoring.make_monitor_controller()` builds an ndscan experiment that runs a collection of Calibrations ("monitors") asynchronously, checking their state on a schedule and logging results. Monitors are Calibrations with no optimizable parameters.

### Patches

qbutler patches upstream classes at import time:

- `patch_ndscan.py`: adds `reset_param()` to `ndscan.experiment.Fragment`
- `patch_sipyco.py`: registers `CalibrationResult` encoding with `sipyco.pyon`

## File layout

```
qbutler/
  __init__.py          # Import-time patches
  calibration.py       # Calibration base class (main logic)
  dag.py               # Global dependency graph (networkx)
  entrypoints.py       # setattr_calibration monkey-patch
  optimizers.py        # Grid search optimizer
  monitoring.py        # Async monitor controller
  patch_ndscan.py      # reset_param patch for ndscan Fragment
  patch_sipyco.py      # CalibrationResult pyon encoder

tests/
  conftest.py          # pytest config, slow-test markers
  fixtures.py          # Mock ARTIQ device manager, dataset manager, scheduler
  unit/                # Fast unit tests (no kernel compilation)
  func/                # Functional tests including kernel tests
  func/kernel_calibrations.py  # Test Calibration subclasses with @kernel

example/               # Example experiments and device_db
notebooks/             # Jupyter notebooks (exploratory)
planning/              # Handoff documents for major features
flake.nix              # Nix dev shell with artiq, ndscan, oitg
pyproject.toml         # Poetry metadata
```

## Dependencies and pinned versions

The project depends on three core upstream repositories that are not on PyPI:

| Package | Source |
|---|---|
| `artiq` | `github:dnadlinger/artiq` @ `dpn/emulator` |
| `ndscan` | `gitlab.com/aion-physics/code/artiq/forks/ndscan.git` |
| `oitg` | `github:OxfordIonTrapGroup/oitg` |

*Important*: These repos are also available as shallow clones in `.claude/deps/` for reference when understanding how their features work. If they are missing, they are cloned automatically by a hook.

## Testing

### Running tests

With Nix (recommended, includes ARTIQ emulator):

```bash
nix develop --impure . --command python -m pytest tests/
```

With a local Python environment:

```bash
pip install -e .
pytest tests/unit/          # Fast tests only
pytest tests/ --runslow     # Include slow tests
```

### Test categories

- **Unit tests** (`tests/unit/`): Fast, no kernel compilation. Test optimizers, DAG, import logic.
- **Functional tests** (`tests/func/`): Test Calibration lifecycle, optimization, monitoring.
- **Kernel tests** (`tests/func/test_kernel_optimization.py`, `tests/func/kernel_calibrations.py`): Require ARTIQ compiler and (optionally) `libartiq-emulator`.

### Fixtures

`tests/fixtures.py` provides:

- `device_mgr`: Mock `DeviceManager` with a fake `core` device. Uses `CoreEmulator` when `LIBARTIQ_EMULATOR` is set (from nix shell), otherwise uses `Core` with `host=None` and a patched `run()` method that compiles but does not execute kernels.
- `fragment_factory`: Builds ndscan Fragments with mocked environment.
- `experiment_factory`: Builds ARTIQ `EnvExperiment` instances.
- `mock_core`: Mock of the core's `comm.run()` for counting kernel executions.
- `build_and_run_experiment`: Builds, prepares, runs, and analyzes an experiment class.
- `artiq_master`: Spins up a real `artiq_master` process for full-stack tests.

### Slow tests

Tests marked with `@pytest.mark.slow` are skipped by default. Use `--runslow` to include them.

## Nix flake

`flake.nix` defines a dev shell that:

1. Builds `artiq` from the `dpn/emulator` fork (includes `libartiq-emulator`)
2. Builds `ndscan` and `oitg` from the pinned git sources
3. Installs pytest, numpy, networkx, matplotlib
4. Sets `LIBARTIQ_EMULATOR` and `PYTHONPATH` in the shell hook

The flake is the source of truth for dependency versions. `flake.lock` pins exact git revisions.

## Common tasks

### Adding a new Calibration

1. Subclass `qbutler.calibration.Calibration`
2. Override `build_calibration()` to define parameters, timeout, and dependencies
3. Override `check_own_state()` to measure and return `(CalibrationResult, data)`
4. Optionally override `fix_own_state()` for custom repair logic
5. Use `setattr_param_optimizable()` for parameters that should be auto-optimized

### Adding a kernel Calibration

Same as above, but decorate `check_own_state()` (and optionally `fix_own_state()`) with `@kernel`. The optimizer automatically detects kernel methods and uses the single-kernel-call optimization path.

### Writing tests

- Use `fragment_factory` to instantiate Calibrations in unit/functional tests
- Use `mock_core` to assert on the number of kernel calls
- For kernel compilation tests, use `build_and_run_experiment`
- Add `@pytest.mark.slow` to tests that take >1s

## Important implementation notes

- The DAG uses **weakrefs** to avoid keeping Calibration objects alive forever. Do not cache dependency lists long-term.
- `CalibrationResult` is an `int`/`Flag` hybrid so it can be used in ARTIQ kernels (which have limited type support).
- The kernel optimizer avoids dicts entirely (ARTIQ kernels don't support them) by using parallel lists and list indexing.
- `fix_state()` calls `_do_check_own_state()` before `fix_own_state()` for each dependency. If `check_own_state` is a kernel, each dependency check triggers a kernel call.
- qbutler monkey-patches `ndscan.experiment.Fragment` and `sipyco.pyon` at import time. Import order matters.
