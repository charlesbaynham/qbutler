# Plan: Supporting kernel-mode optimisations in qbutler

> **Status (2026-07-09): implemented, but not via `KernelScanRunner`.**
> The shipped design is a hand-rolled *resident kernel loop*
> (`Calibration._optimizer_kernel_loop`): one kernel call per fix, with the
> kernel pulling each next point from the host optimizer generator over a
> sync RPC (`_kopt_next_point`, `[]` = done), applying it device-side via the
> stores' `@portable set_value()`, and finishing with an in-kernel
> verification pass. Any host generator works — feedback optimisers (BO)
> included — so the `batchable` flag and the earlier single-batched-sweep
> mechanism were removed. On top of it, `prepare_kernel_fix()` +
> `fix_state_kernel()` generate (via `kernel_from_string`) a driver that
> fixes an entire calibration DAG from within a single kernel, the walk
> decisions living host-side behind RPCs (`_fsk_*`). The `KernelScanRunner`
> route below was not taken: coupling to ndscan internals was judged
> disproportionate to the ~ms/point RPC saving. This document is kept for
> the verified ARTIQ facts and the design constraints, which still hold.

## Context

`qbutler` provides a `Calibration` class (`qbutler/calibration.py`) that drives parameter optimisation on top of `ndscan`/ARTIQ. Today the optimiser is *entirely host-side*: `Calibration._run_optimizer` (`calibration.py:573`) is a Python loop that, for each candidate point, mutates a `ParamStore` via `set_value()` and calls `_do_check_own_state()` → user's `check_own_state()` (a host method).

We want users to be able to write `check_own_state()` as kernel code so the per-shot timing is governed by ARTIQ's RTIO, not Python latency. The hard constraint is ARTIQ compilation cost. Verified facts from `/tmp/artiq` and `/tmp/ndscan`:

- **Compile**: every host→`@kernel` call recompiles from scratch (>10 s). `Core.run` → `compile()` → `target.compile_and_link()` (`/tmp/artiq/artiq/coredevice/core.py:130-178`).
- **Precompile** (`Core.precompile`, `core.py:238-281`) compiles once, but its returned callable still re-uploads the binary on every call (`comm_kernel.py:378-387` does `self._write_bytes(kernel_library)` unconditionally with no caching). Realistic per-call cost is ~1 s.
- **Kernel→kernel** calls inside the same compiled binary are direct device-side calls (~µs).
- **Kernel→host RPC** is blocking and synchronous (~ms range; `@rpc(flags={"async"})` is non-blocking but discards the return value). Async RPCs are FIFO-ordered with subsequent sync RPCs, so an async result push is observable before the next sync request returns.
- **ndscan parameters on the kernel**: `FloatParamHandle.get()` is `@portable` (`/tmp/ndscan/ndscan/experiment/parameters.py:289-291`), as is `ParamStore.get_value` / `set_value` (lines 78-80). The compiler captures a *reference* to the `ParamStore` object — not a constant fold of the value. The kernel reads `self._store._value` live each call, so host-side mutations (or kernel-side `set_from_rpc` / `set_value` calls) are visible to subsequent reads. This is exactly the trick ndscan's own `KernelScanRunner` uses (`scan_runner.py:294-333, 427-450`).
- **Per-shot wall-clock varies by lab**. Our reference experiment is ~2 s/shot (physics-dominated), but the framework should also remain efficient for groups with ~100 ms shots — at that scale, framework overhead per shot becomes proportionally significant.
- **Typical N for `fix_state`**: 10–100 points. Backward-compat scope: minor migration acceptable.
- **Python-side optimiser flexibility (Bayesian opt, custom generators, etc.) is essential** — anything that forces optimiser logic onto the kernel is a non-starter. BO and other adaptive optimisers are *not batchable*: the next point depends on the result of the previous one, so the design must serialise one shot at a time when needed.

The user's observation that this resembles an ndscan kernel scan is correct. ndscan's `KernelScanRunner` already solves the hard parts: ARTIQ-compatible code generation for the per-axis param setters, sync/async RPC choreography, RTIO-underflow retries, transitory-error retries, scheduler-pause integration, and result-channel batching. The only gap is that ndscan assumes a pre-determined points iterator; we need an iterator that may want to see the previous point's result before yielding the next.

## Constraints / requirements

1. **One compile and one upload per `fix_state()` call**.
2. Optimiser still chooses points in host Python — must support custom generators / BO / scikit-optimize-style optimisers (`set_optimizer()`, `calibration.py:658`).
3. User-facing API for `check_own_state` should be the same as today, just allowed to carry `@kernel`. Detection via `hasattr(check_own_state, "artiq_embedded")`.
4. Existing host-only Calibrations keep working with no rewrite.
5. Dep-tree composition (`check_state` walking deps) must not multiply uploads gratuitously.
6. Non-batchable optimisers (BO) must be supported — i.e. the framework must serialise the "next-point" decision against the previous result when required.
7. Framework must scale across labs with very different shot times (~100 ms to several seconds). Per-shot framework overhead should not dominate even at 100 ms.

## Plan: reuse `ndscan.KernelScanRunner`

`KernelScanRunner.set_points(points: Iterator[tuple])` (`scan_runner.py:310`) accepts an arbitrary iterator. The runner pulls from it via `_get_param_values_chunk` (sync RPC), applies values via auto-generated `_param_setter_i` `@portable` methods, runs the user's `@kernel run_once()`, and acknowledges via `_point_completed` (async RPC). All the gnarly compilation-friendly codegen, error retry, scheduler integration, and result-batching is inherited from ndscan.

We add four pieces:

1. **An optimiser-driven points iterator**: a Python generator that yields a param tuple, and on resumption (after the runner has pushed the result) calls `optimiser.send((status, data))` to compute the next point. Standard `.send()` mechanics; this is exactly what `Calibration._run_optimizer` already does, just rephrased as a generator.
2. **Result feedback into the iterator**: subclass `KernelScanRunner` and override `_point_completed` to capture the just-pushed `data` value and call `iterator.send(result)` from there. Async RPCs are FIFO-ordered against sync RPCs, so the captured result is guaranteed to be available before the next `_get_param_values_chunk` call.
3. **Adaptive chunk size**: ndscan's hardcoded `CHUNK_SIZE = 10` (`scan_runner.py:434`) pre-fetches up to 10 points before any results are seen — fatal for BO. The runner subclass exposes `chunk_size` as a parameter:
   - **`chunk_size = 1`** is the default and is mandatory for non-batchable optimisers (BO, anything implemented via `optimiser.send(result)` feedback). One sync RPC per shot is ~ms; at the worst-case 100 ms shot length that is still well under 10% framework overhead.
   - **`chunk_size = 10`** (or higher) can be opted into by *batchable* optimisers (grid search, Latin hypercube, anything where the full point list is known up front). At 100 ms shots this turns the per-RPC tax from ~10% to ~1%; at 2 s shots it's noise either way.
   - The generator API exposes a `batchable: bool` flag; the runner picks chunk size accordingly. Default is conservative (1) so users never silently break BO by adding it to a previously grid-only Calibration.
4. **Fragment shape**: `KernelScanRunner` calls `fragment.run_once()` (`scan_runner.py:381`). `Calibration.run_once` (`calibration.py:174`) currently delegates to `_do_check_own_state`. We make `run_once` `@portable` so it dispatches to either kernel or host based on whether `check_own_state` is a kernel; it pushes the float into the `data` channel directly. Status defaults to OK with an optional `@rpc def report_bad(self, code: int)` for the rare bad-state case.

`KernelScanRunner` is a `HasEnvironment` that does `setattr_device("core", "scheduler")` in `build()`. We instantiate it as a child of the `Calibration`, identical to how ndscan instantiates it from the entry-point machinery.

For the non-optimising path (`check_state` single-shot, dep walks), leave the current host-side code alone in this iteration. Single-shot kernel calls during dep walks pay one compile + one upload each; at monitor-style cadences this is acceptable. Layer precompile-caching on later if profiling motivates it.

### Files to touch

- **`qbutler/calibration.py`** (main change).
  - In `_run_optimizer` (`calibration.py:573`): detect `hasattr(self.check_own_state, "artiq_embedded")` and switch to `_run_optimizer_kernel`.
  - Add `_run_optimizer_kernel(self, optimizer_func)`:
    - Build `ScanAxis` entries from `self.__optimizable_params` (each spec already exposes a `ParamHandle`; the `ParamStore` is reachable via `handle._store`).
    - Build a points generator wrapping `optimizer_func(...)`'s `.send()` interface.
    - Detect optimiser batchability via `getattr(optimizer_func, "batchable", False)` (or an attribute on the generator); pass to runner as `chunk_size`.
    - Instantiate `QbutlerKernelScanRunner(self, chunk_size=...)`, run `setup` / `set_points` / `acquire`.
  - Existing finally-block (`calibration.py:635-638`) handles param reset and dataset writeback unchanged.
  - Make `Calibration.run_once` (`calibration.py:174`) `@portable` and have it call `self.check_own_state()` then push to the `data` channel directly.
  - Document the kernel-mode contract on `check_own_state` (`calibration.py:100`): if `@kernel`-decorated, must return a single float (the optimisation metric); status is OK by default; bad-state paths use an explicit RPC.
- **`qbutler/_kernel_scan_runner.py`** (new): `QbutlerKernelScanRunner(KernelScanRunner)` subclass. Overrides `_get_param_values_chunk` to honour configurable `chunk_size`, and `_point_completed` to feed the result back into the iterator. Keep this file small and well-commented — see the migration playbook below.
- **`qbutler/optimizers.py`**: mark `grid_search_optimizer` as `batchable = True` (it is). New BO optimiser stubs default to `batchable = False`.
- **`tests/`**: add a host-mock test that fakes `KernelScanRunner` (replace it with a stub whose `setup`/`set_points`/`acquire` simulate kernel→host calls in plain Python). Verify (a) kernel-mode is selected when `check_own_state` carries `artiq_embedded`; (b) the optimiser generator is consumed correctly via the simulated RPCs; (c) custom optimisers (`set_optimizer()`) are honoured; (d) chunk size matches optimiser batchability; (e) host-mode Calibrations are unaffected; (f) dep walks compose correctly.
- **`example/`**: add `example_kernel_gaussian.py` mirroring `example_gaussian3d.py` but with `@kernel check_own_state`.

### Alternatives considered and rejected

- **Hand-rolled driver kernel** (no ndscan dependency): same on-device behaviour but we re-implement the per-axis param-setter codegen, RTIO/transitory-error retries, scheduler integration, and result batching. Not worth it given how much ndscan already gets right. Stays available as a fallback if ndscan coupling becomes unworkable — the migration playbook below describes the vendoring path.
- **Precompile + host-side loop**: every shot pays the ~1 s binary re-upload cost (`comm_kernel.py:378-387`), giving a 50% tax at 2 s/shot and a 1000% tax at 100 ms/shot. Disqualified.
- **Optimiser implemented in kernel-compilable Python**: kills the BO requirement (constraint 2). Disqualified.
- **Documentation only**: doesn't answer the question. Disqualified.

## ndscan upgrade / vendoring playbook

We depend on these `ndscan.experiment.scan_runner` symbols:

- `KernelScanRunner` (subclassed)
- `ScanAxis`, `ScanSpec`, `ScanOptions` (constructed)
- `KernelScanRunner._get_param_values_chunk` (overridden)
- `KernelScanRunner._point_completed` (overridden)
- `KernelScanRunner._param_setter_{i}` synthesis (`scan_runner.py:301-302`) — used implicitly
- `KernelScanRunner._build_run_chunk` (`scan_runner.py:321-333`) — used implicitly
- `ScanAxis.param_store` attribute and `ParamStore.set_from_rpc` / `set_value` / `to_rpc_type` / `value_from_pyon` (`parameters.py`)
- `ResultBatcher` (used implicitly via `_install_result_batcher` / `_remove_result_batcher`)

The poetry pin (`pyproject.toml:13`) currently points at the lab's ndscan fork. Upgrading is *the* failure mode that breaks B.2.

To make this manageable for future maintainers (humans or agents), ship two things alongside the code change:

1. **A pinned-coupling integration test** in `tests/test_ndscan_coupling.py` that imports every symbol listed above by name and instantiates a minimal `QbutlerKernelScanRunner` with a stub `Calibration`. The test fails at import or instantiation if any of those symbols moves, is renamed, or changes signature. Run on every CI build. The test is a *tripwire*, not a behavioural test — its job is to detect drift early.

2. **`docs/ndscan-coupling.md`**: a short, agent-readable runbook describing exactly how to triage and resolve a tripwire failure. Sections:
    - **Symbols we depend on** (the list above, with file:line refs into the *currently pinned* ndscan commit).
    - **Diagnose the drift**: which symbol moved/changed/disappeared? Diff the two pinned commits with `git -C /path/to/ndscan log <old>..<new> -- ndscan/experiment/scan_runner.py ndscan/experiment/parameters.py`.
    - **Triage decision tree**:
      - *Renamed but otherwise equivalent* → update the import / override name in `qbutler/_kernel_scan_runner.py`. Re-run tripwire test. Done.
      - *Signature changed but semantics preserved* → update the override signature. Add a one-line comment citing the upstream commit. Done.
      - *Semantics changed* → STOP. Do not silently adapt. Open an issue. Either pin ndscan back, or vendor (next step).
      - *Symbol removed entirely* → vendor.
    - **Vendoring playbook**: copy the relevant ~250 lines of `KernelScanRunner` + helpers into `qbutler/_vendored_kernel_scan_runner.py`, add the upstream commit hash and license header to the file, swap `qbutler/_kernel_scan_runner.py` to import from the vendored module instead of `ndscan.experiment.scan_runner`. Update the tripwire test to point at the vendored module. Add a TODO with a recommended re-evaluation date (e.g. 6 months later — does upstream now meet our needs again?).
    - **Why we're coupled at all**: link to this plan so future readers understand the design choice rather than ripping it out.

3. **Optional: a dedicated skill** (`.claude/skills/ndscan-upgrade/SKILL.md`) that wraps the runbook for agent-driven upgrades. Triggers on tripwire-test failures or pyproject ndscan-version bumps. The skill walks an agent through the diagnose → triage → patch / vendor flow above. Recommend deferring this until after the first real upgrade incident — premature skill encoding tends to ossify around the wrong shape. A plain markdown runbook (item 2) is enough for now.

## Verification

1. **Unit tests (host-mock)**: stub `KernelScanRunner` so the `acquire` loop runs as plain Python and the simulated RPCs invoke the points iterator and result captor in the right order. Assert: kernel-mode is selected on `@kernel`; chunk_size is 1 by default and 10 when the optimiser declares `batchable = True`; BO-style optimisers (where the next point depends on the previous result) terminate with the right best-params; dep walks unaffected; host-mode tests still pass.
2. **Hardware integration test (`--runslow`)**: a 1-D `Calibration` whose `@kernel check_own_state` reads one optimisable param, performs a representative shot, and returns a float. Run `fix_state(force=True)` with 11 points. Expected wall-clock at 2 s shots: ~10 s compile + ~1 s upload + 11 × 2 s = ~33 s. Compare against host-mode equivalent and against a deliberately-broken "naive" kernel-mode build that recompiles per shot, to confirm the savings.
3. **Short-shot stress test (`--runslow`)**: same calibration but with a 100 ms-equivalent shot (e.g. a fast TTL pulse + readout). Run grid search (batchable, chunk=10) and a stub BO optimiser (non-batchable, chunk=1). Confirm wall-clock matches `compile + upload + N × shot + chunk_overhead`, with chunk_overhead ≤ 10% of total.
4. **BO smoke test**: register a custom optimiser via `set_optimizer()` that mimics a BO step (proposes points based on the previous result). Confirm it runs end-to-end against a kernel-mode Calibration with chunk_size=1 — proves serialised non-batchable optimisation works.
5. **ndscan-coupling tripwire** (`tests/test_ndscan_coupling.py`): described in the playbook above. Runs on every CI build.

End of plan.
