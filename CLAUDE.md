# CLAUDE.md

## Reference repositories

Clone these locally before working on qbutler — reading their source is essential:

- **icl_experiments** — the only current user of qbutler; a large collection of real experiments. Use it to understand how qbutler is used in practice.
  ```
  git clone https://gitlab.com/aion-physics/code/artiq/experiment-repositories/icl_experiments
  ```

- **ndscan** (fork) — defines the `Fragment` / `ExpFragment` base classes and the parameter/scan framework that qbutler builds on. Use the fork, not the upstream, as qbutler depends on fork-specific behaviour.
  ```
  git clone https://gitlab.com/aion-physics/code/artiq/forks/ndscan.git
  cd ndscan && git checkout transitive-rebinding-qt5
  ```

- **ARTIQ** (fork) — the underlying experiment control framework; source of truth for `EnvExperiment`, kernels, and hardware abstractions.
  ```
  git clone https://gitlab.com/aion-physics/code/artiq/forks/artiq_fork.git
  ```

## What qbutler does

qbutler manages chains of interdependent physics calibrations that drift over time. Calibrations form a DAG, can time out, propagate failure to dependents, and auto-fix themselves by optimising parameters. Each `Calibration` is simultaneously an ndscan `ExpFragment` (so it can be scanned like any experiment) and a node in the dependency graph.

## Dev setup

```bash
poetry install
poetry run pytest              # fast tests
poetry run pytest --runslow    # include slow tests
poetry run pre-commit run --all-files  # lint + format
```

Tests are fully mocked — no live ARTIQ master needed (though Nix-based integration testing is likely coming).

## Architecture

### Calibration lifecycle

Users subclass `Calibration` and implement three methods:

- `build_calibration()` — declare parameters, dependencies, timeout, optimizer strategy
- `check_own_state() -> tuple[CalibrationResult, Any]` — measure the system; return a result and raw data
- `fix_own_state()` — optional; repair / optimise when state is BAD

`run_once()` is auto-generated from `check_own_state()` — do not override it.

All build-phase methods (`add_dependency`, `set_timeout`, `setattr_param_optimizable`, etc.) raise `TypeError` if called outside `build_calibration()`.

### CalibrationResult

A `Flag` enum: `OK`, `BAD_EXPIRED`, `BAD_DEPS`, `BAD_DATA`, `INVALID_DATA`. Values can be OR'd together. Check membership with bitwise AND: `if result & CalibrationResult.OK`.

### DAG (dag.py)

Uses NetworkX + weak references. Calibrations are deduplicated by default — calling `add_dependency(SomeClass)` from two different parents yields one shared instance. Pass `create_duplicates=True` to force separate instances. **Do not cache** the output of `get_graph()` or `get_dependencies()` — the graph is rebuilt from weak refs and stale references will include GC'd calibrations.

### Optimizers (optimizers.py)

Custom optimizers are generators: yield `{param_name: value}` dicts, receive `(CalibrationResult, data)` via `send()`. The default is `grid_search_optimizer`. The optimization target (maximise, minimise, or drive to zero) is set with `set_optimization_type()` in `build_calibration()`; the default is `"max"`.

Optimisable parameters auto-persist to ARTIQ datasets under the key `CalibrationName.param_name`.

### Timeout behaviour

`set_timeout(seconds)` sets how long a check result is valid. **`set_timeout(0)` means never expire** (re-checked every time), not "expire immediately". Monitors require timeout > 0.

## Known stubs

`build_interface_from_calibration()` in `entrypoints.py` is unimplemented. Leave it alone unless a task explicitly targets it.

## Patches on import

`qbutler/__init__.py` patches ndscan and sipyco at import time:
- **patch_ndscan.py** adds `reset_param()` to `Fragment` so overridden parameters can be restored
- **patch_sipyco.py** registers `CalibrationResult` with pyon so it survives ARTIQ dataset serialization
