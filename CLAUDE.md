# CLAUDE.md

## Reference repositories

The Nix-pinned dependencies (`artiq`, `ndscan`, `oitg`) are shallow-cloned at session start into `.claude/deps/<name>/` at the exact revs from `flake.lock`. This is done by the `claude-code-vendored-refs` plugin, which reads `.claude/vendored-refs.yaml`; add a repo there to have it mirrored too. The clone is idempotent — it compares `HEAD` against the target rev and skips if already current. PyYAML is required.

Do not edit anything under `.claude/deps/` — it is reference material only, and changes are lost on the next clone.

`icl_experiments` is qbutler's only current downstream user and the best reference for how qbutler is used in practice. It isn't pinned in `flake.lock`, so clone it manually if you need it:

```
git clone https://gitlab.com/aion-physics/code/artiq/experiment-repositories/icl_experiments
```

## What qbutler does

qbutler manages chains of interdependent physics calibrations that drift over time. Calibrations form a DAG, can time out, propagate failure to dependents, and auto-fix themselves by optimising parameters. Each `Calibration` is simultaneously an ndscan `ExpFragment` (so it can be scanned like any experiment) and a node in the dependency graph.

## Dev setup

```bash
poetry install
poetry run pytest                # default suite
poetry run pytest --withartiq    # include tests that need the ARTIQ kernel emulator
poetry run pre-commit run --all-files  # lint + format
```

Tests are fully mocked by default — no live ARTIQ master needed. The `--withartiq` suite covers tests that exercise real ARTIQ tooling (kernel execution against the emulator, full-stack `artiq_master` runs); it requires `LIBARTIQ_EMULATOR` to be set, which the Nix dev shell does for you.

Some `--withartiq` tests are additionally marked `fullstack` — they spin up a real `artiq_master` process and submit jobs via `artiq_client`, which requires IPv6 socket support. These pass in CI (GitHub Actions) but fail in Claude Code Web sessions (`CLAUDE_CODE_REMOTE=true`), which run in a sandboxed container without IPv6. Skip them with `--no-fullstack`:

```bash
nix develop . --command python -m pytest tests/ --withartiq --no-fullstack
```

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

### Outcome hooks

qbutler records calibration outcomes to datasets, but stays agnostic about where else they go: two no-op hooks on `Calibration` let a downstream mix-in (in practice `icl_experiments`' `InfluxCalibrationLogMixin`) forward them to a database.

- `_on_recalibrated(committed_params, metric)` — a fix committed new optimal parameters. `metric` is the `check_own_state` data measured with those parameters applied, i.e. the quantity at its optimum.
- `_on_checked(result, metric, context)` — a check outcome was recorded, passing or failing. `context` says what kind of check it was: `check` (a real check, or a fix walk's re-check), `sweep` (an optimizer trial point), `verify` (the confirmation of the best point) or `fix_failed` (the synthetic BAD_DATA of a fix that gave up). Honour it — an optimizer sweeps through deliberately bad points, so counting `sweep` results as apparatus health makes a healthy system look broken every time it is fixed.

Both are dispatched best-effort (`_fire_*`), so an override that raises is logged and ignored rather than failing the run. `_on_checked` fires once per optimizer point in a host-mode fix, so it must be cheap. Kernel-mode fixes do not record trial points as checks, so they fire it for the verification measurement only.

The `context` label is set by `_checking_for()` around each call site; nested and always restored.

### DAG (dag.py)

Uses NetworkX + weak references. Calibrations are deduplicated by default — calling `add_dependency(SomeClass)` from two different parents yields one shared instance. Pass `create_duplicates=True` to force separate instances. **Do not cache** the output of `get_graph()` or `get_dependencies()` — the graph is rebuilt from weak refs and stale references will include GC'd calibrations.

### Optimizers (optimizers.py)

Custom optimizers are generators: yield `{param_name: value}` dicts, receive `(CalibrationResult, data)` via `send()`. The default is `grid_search_optimizer`. The optimization target (maximise, minimise, or drive to zero) is set with `set_optimization_type()` in `build_calibration()`; the default is `"max"`.

Optimisable parameters auto-persist to ARTIQ datasets under the key `CalibrationName.param_name`.

### Serialised measurement lifecycle

Calibration measurements share physical hardware (e.g. one camera), so their
`host_setup`s must not coexist. qbutler owns the lifecycle of every calibration
node a client declares (`setattr_calibration` detaches them from ndscan's tree
walk): at most one node is set up at a time, and the walk performs a full
handover — `host_cleanup` of the previously-active node, `host_setup` of the
next — immediately before each node's measurement runs (`Calibration._activate`).
Every walk deactivates on exit, so the main experiment fragment (which ndscan
alone manages, with its own setup/cleanup brackets around every escape) always
re-enters on untouched hardware. Kernels compile lazily, on a node's first
activation, since compilation embeds attributes `host_setup` creates.
Consequence: calibration fragments' `host_setup`/`host_cleanup` must tolerate
repeated cycles, and science kernels read calibration outputs via their own
dataset-defaulted parameters, not by reaching into the (detached) calibration
subtree.

### Pausing and termination

A fix walk yields to the ARTIQ scheduler between the shots of a recalibration, so a run can be preempted or terminated without waiting for the whole DAG to finish. `scheduler.check_pause()` is rate-limited by a process-wide gate (`PAUSE_CHECK_INTERVAL`, default 1 s) so fast calibrations don't pay for a round trip to the master per shot.

A kernel cannot pause itself — `scheduler.pause()` has to hand the core device over — so the resident optimizer kernel loop returns to the host when a pause is pending, the host pauses, and the loop is re-entered where it left off (`_drive_optimizer_kernel_loop`). Checks are *not* paused: `MonitorController` runs them on worker threads.

### Fix retries

A fix walk fixes a node and re-checks it; if the re-check is still not OK — or the fix gave up with a `CalibrationError`, e.g. an optimizer that found no valid point — **the node is simply fixed again, indefinitely by default** (`Calibration._fix_own_state_until_ok`). Crashing the experiment is opt-in: `set_max_fix_attempts(n)` bounds one calibration, `calibration.DEFAULT_MAX_FIX_ATTEMPTS` bounds the whole process (`= 1` restores the old fail-fast behaviour). The budget is read at fix time, not build time.

Only `CalibrationError` is retried — a `NotImplementedError`, `ValueError` etc. is a bug in the calibration and propagates immediately. The loop yields to the scheduler between attempts, so a run stuck on a hopeless calibration can still be terminated.

### SUSPECT: distrusting in-timeout OKs

When a node's fix attempt fails, the real culprit is often a dependency that still *looks* good — checked recently, inside its timeout — but has silently drifted. Each such dependency is marked **suspect**: the dependent's class name is added to its `suspected_by` set (persisted in `calibrations.status`, restored by `_recall_status`). SUSPECT is deliberately **not** a `CalibrationResult` member — a check can't *measure* "suspect"; it is trust metadata, and `check_own_state` can never return it.

A suspect node's `_guess_own_state()` returns `BAD_EXPIRED` (return-only — the cached OK is left intact), so every walk, monitor, and client escape check re-measures it with no special-casing. Suspicion clears when (a) the node is re-measured — any fresh result supersedes doubt — or (b) a suspector records an OK check, which retracts its name from live dependencies and prunes it from every `calibrations.status` entry (so it works cross-process).

The fix walks use this to backtrack: a failed attempt that newly marks suspects raises the internal `_SuspectDependencies`, and `fix_targets`/`fix_state` restart their deepest-first loop — suspect deps get re-measured, fixed if genuinely bad, and the failing node is then retried. **One backtrack per node per walk**; after that, the node retries on its own budget as usual (backtracking wins over the budget, so even `max_fix_attempts=1` gets its one chance to blame a dependency, at the cost of up to ~2n total attempts). `force=True` walks never backtrack. The DAG applet shows suspect nodes in purple with the suspector's name.

`set_timeout(seconds)` sets how long a check result is valid. **`set_timeout(0)` means never expire** (re-checked every time), not "expire immediately". Monitors require timeout > 0.

### Applets and dataset scoping (ccb.py, scoping.py, applets/)

Runs launch their own dashboard applets via the CCB — a DAG overview, plus one optimizer-trace plot per calibration class. Applets are created on every walk entry; creation is best-effort and never raises.

Two datasets are **scoped per pipeline** by `scoping.scoped_key()`: `calibrations.dag.<pipeline>` and `calibrations.optimizer.<pipeline>`. So are the applet groups (`Calibrations/<pipeline>[/Optimizers]`, via `scoping.applet_group()`). Without this, runs in two pipelines fight — the dashboard keys applets on (name, group) and replaces the existing spec, and both would publish over one dataset key.

`calibrations.status` is deliberately **not** scoped: when a calibration was last checked is a property of the apparatus, and `_recall_status()` relies on a walk in one pipeline seeing a check another already did. Where no pipeline can be determined (`artiq_run`, a bare unit test) the unscoped key and group are used.

The applets take their dataset keys as CLI arguments, so scoping needs no applet-side changes.

## Known stubs

`build_interface_from_calibration()` in `entrypoints.py` is unimplemented. Leave it alone unless a task explicitly targets it.

## Patches on import

`qbutler/__init__.py` patches ndscan and sipyco at import time:
- **patch_ndscan.py** adds `reset_param()` to `Fragment` so overridden parameters can be restored
- **patch_sipyco.py** registers `CalibrationResult` with pyon so it survives ARTIQ dataset serialization
