# GitHub Copilot Instructions for qbutler

## Project Overview

**qbutler** is a Python package for managing complex research experiments with ARTIQ (Advanced Real-Time Infrastructure for Quantum physics). It automatically handles drifting calibrations, dependencies between experiments, and provides monitoring capabilities.

### Core Concepts

- **Calibration Management**: Automatic handling of experimental calibrations that drift over time
- **Dependency DAG**: Uses NetworkX to build a directed acyclic graph tracking dependencies between calibration objects
- **ARTIQ Integration**: Patches and extends ARTIQ, ndscan, and sipyco functionality
- **Monitoring**: Real-time monitoring of experiments and calibrations using asyncio

## Technology Stack

- **Python**: 3.10+
- **Package Manager**: Poetry
- **Key Dependencies**:
  - ARTIQ (quantum physics control)
  - ndscan (experiment scanning framework)
  - sipyco (Simple Python Communications)
  - NetworkX (graph/network algorithms)
  - NumPy (numerical computing)
- **Testing**: pytest with pytest-timeout and coverage
- **Code Style**: Black formatter, pre-commit hooks

## Code Style Guidelines

1. **Formatting**: All code must be formatted with Black (line length 88)
2. **Type Hints**: Use type hints where appropriate, with `TYPE_CHECKING` imports to avoid circular dependencies
3. **Docstrings**: Use descriptive docstrings following reStructuredText format with proper Sphinx directives
4. **Logging**: Use the `logging` module with appropriate log levels (logger = logging.getLogger(__name__))
5. **Method Documentation**: Use `:param:`, `:return:`, `:raises:` tags in docstrings

## Architecture Patterns

### Calibration Objects (calibration.py)

The `Calibration` class is the core abstraction. Key implementation details:

- **Inherits from**: `ndscan.experiment.ExpFragment`
- **State Machine**: Uses `CalibrationResult` Flag enum (OK, BAD_EXPIRED, BAD_DEPS, BAD_DATA, INVALID_DATA)
- **Lifecycle Methods** (in order of call):
  1. `build_fragment()` - Called by ndscan, initializes internal state
  2. `build_calibration()` - USER IMPLEMENTS - Set up parameters, dependencies, timeout
  3. `check_own_state()` - USER IMPLEMENTS - Returns `(CalibrationResult, Any)`
  4. `fix_own_state()` - Optional override - Runs optimization to fix calibration
  5. `run_once()` - Makes Calibration scannable as an ExpFragment. Not part of
     the main workflow, but useful so that users can treat Calibrations as
     normal ndscan ExpFragments.

- **Key Private Attributes**:
  - `__timeout`: Expiry time for calibration data
  - `__optimizable_params`: List of tuples (min, max, ParamHandle)
  - `__most_recent_check_timestamp`: When last check occurred
  - `__most_recent_check_result`: Last CalibrationResult
  - `__most_recent_check_data`: Last data value
  - `__optimization_type`: "max", "min", or "zero"

- **Dependencies tracked in global DAG**: Always use weakrefs to prevent memory leaks
- **DAG is lazily rebuilt**: Cached with `_dag_valid` flag
- **Calibrations should be garbage-collectable** when no longer needed

### Key Public Methods Pattern

```python
# Check state of this calibration and all its dependents, without fixing
state, data = calibration.check_state(force=False, continue_on_fail=False)

# Fix state of this calibration and all dependents (if needed)
calibration.fix_state(force=False)

# Cheap guess without device interaction. Used by `check_state` and `fix_state` if we are within the allowed timeout for a Calibration
state = calibration.guess_state()
```

### Dependency Graph (dag.py)

- Uses `weakref.ref()` to store object references in the DAG
- Automatic cache invalidation when objects are garbage collected
- Graph operations use NetworkX DiGraph
- Always call `_filter_dependency_map()` before graph operations to clean up dead references
- **Global State Variables**:
  - `_dependency_map`: List of (weakref, weakref) tuples
  - `_dag`: The cached NetworkX DiGraph
  - `_dag_valid`: Boolean flag for cache validity

### Patching External Libraries

qbutler patches external libraries at import time to add functionality:

#### patch_ndscan.py
- Adds `reset_param()` method to `ndscan.experiment.Fragment`
- Allows resetting overridden parameters back to defaults
- Critical for optimization workflow where parameters are temporarily overridden

#### patch_sipyco.py
- Registers `CalibrationResult` enum with sipyco's pyon serialization
- Handles both old and new sipyco API versions
- Converts CalibrationResult to/from integers for network transmission

#### entrypoints.py
- Adds `setattr_calibration()` method to `ndscan.experiment.Fragment` class at import time
- This is the PRIMARY way users add calibrations to their experiments
- Pattern: Monkey-patching classes at module import level using `setattr()`. This pattern sucks - it will change in future to providing a class that users can use as their base class instead. That class might be Calibration, or something related.

### Monitoring System (monitoring.py)

The monitoring system uses **asyncio** to manage multiple calibrations concurrently:

- `make_monitor_controller()`: Factory function that creates a MonitorController class
- **MonitorController**: An ExpFragment that manages multiple Calibrations as "Monitors"
- **Requirements for Monitors**:
  - Must be a `Calibration` with `timeout != 0`
  - Should return a float from `check_own_state()`
  - No `fix_own_state()` needed (monitors don't fix, only observe)

**Async Architecture**:
```python
async def run_monitor(name, monitor):
    while True:
        state, data = await loop.run_in_executor(None, monitor.check_state)
        data_logger(name, state, data)
        await asyncio.sleep(timeout)
```

- Each monitor runs in its own asyncio task
- Failed monitors are automatically recovered after their timeout
- Uses `scheduler.check_pause()` for graceful shutdown
- Tasks can be cancelled cleanly

## Testing Guidelines

1. **Test Structure**:
   - Unit tests in `tests/unit/`
   - Functional/integration tests in `tests/func/`
2. **Fixtures**: Use pytest fixtures defined in `conftest.py` and `fixtures.py`
3. **Slow Tests**: Mark slow tests with `@pytest.mark.slow` - they only run on master branch or manual triggers
4. **Timeout**: Tests have timeout protection via pytest-timeout
5. **Coverage**: Aim for high test coverage, measured with coverage.py

## Common Patterns to Follow

### Implementing a New Calibration

```python
from qbutler.calibration import Calibration, CalibrationResult

class MyCalibration(Calibration):
    def build_calibration(self):
        # Set timeout (required if used as Monitor, otherwise suggested as will define how regularly a calibration should be checked for failure. If checking is cheap, e.g. measure a voltage, do it every time. If it's expensive, e.g. measure the Rabi frequency of the clock transition, do it rarely).
        self.set_timeout(10.0)  # 10 seconds

        # Add optimizable parameters (optional)
        self.setattr_param_optimizable(
            "frequency",
            description="Laser frequency",
            min=100e6, max=200e6, default=150e6
        )

        # Add dependencies (optional)
        self.add_dependency(SomeOtherCalibration, name="other_cal")

        # Set optimization strategy (optional, default="max")
        self.set_optimization_type("max")  # or "min" or "zero"

    def check_own_state(self) -> Tuple[CalibrationResult, Any]:
        # Measure something
        value = self.measure_something()

        # Determine if it's OK
        if value > threshold:
            return CalibrationResult.OK, value
        else:
            return CalibrationResult.BAD_DATA, value
```

### Using Calibrations in Experiments

```python
from ndscan.experiment import ExpFragment

class MyExperiment(ExpFragment):
    def build_fragment(self):
        # This method is added by qbutler's import-time patching
        self.setattr_calibration(MyCalibration)

    def run_once(self):
        # Check and fix if needed before experiment
        self.MyCalibration.fix_state()

        # Run your experiment knowing calibration is OK
        self.do_experiment()
```

### Creating a Monitor System

```python
from qbutler.monitoring import make_monitor_controller

MyMonitorMaster = make_monitor_controller(
    name="MyMonitorMaster",
    monitors={
        "laser_power": LaserPowerMonitor,
        "temperature": TemperatureMonitor,
    },
    data_logger=my_custom_logger,  # Optional
    devices=["core", "my_device"],  # Devices to request
    pipeline="monitors"  # ARTIQ pipeline name
)
```

### WeakRef Usage

```python
import weakref

def add_to_dependency_map(cal_object, dependent_cal_object):
    def invalidate_cache(r):
        global _dag_valid
        _dag_valid = False

    r = weakref.ref(cal_object, invalidate_cache)
    hash(r)  # Hash now for later debug calls
```

### Logging

```python
import logging
logger = logging.getLogger(__name__)

logger.debug("DAG cache invalid: rebuilding")
logger.info("Calibration completed")
logger.warning("Dependency issue detected")
```

### Type Checking Imports

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .calibration import Calibration  # Avoids circular imports
```

### Parameter Optimization Pattern

```python
# In fix_own_state():
# 1. Override parameter to scan over values
_, p_store = self.override_param("frequency", min_value)

# 2. Scan through points
for point in np.linspace(min_val, max_val, NUM_SCAN_POINT):
    p_store.set_value(point)
    state, data = self._do_check_own_state()
    # Collect results

# 3. Choose best value and save to dataset
best_value = points[argmax(results)]
self.set_dataset(
    self._param_dataset_key_from_name("frequency"),
    best_value,
    broadcast=True,
    persist=True
)

# 4. Reset parameter and recompute defaults
self.reset_param("frequency")
self.recompute_param_defaults()
```

## Directory Structure

- `qbutler/`: Main package source
  - `calibration.py`: Core Calibration class (600+ lines, critical)
  - `dag.py`: Dependency graph management with weakrefs
  - `monitoring.py`: Async monitor controller factory
  - `entrypoints.py`: Integration points (setattr_calibration, etc.)
  - `patch_ndscan.py`: Adds reset_param to Fragment
  - `patch_sipyco.py`: Registers CalibrationResult serialization
- `tests/`: Test suite
  - `unit/`: Unit tests (dag, imports, etc.)
  - `func/`: Functional tests (realistic workflows)
  - `conftest.py`, `fixtures.py`: Pytest fixtures
- `example/`: Example experiments and device configurations
- `docs/`: Sphinx documentation
- `notebooks/`: Jupyter notebooks for exploration

## Special Considerations

### ARTIQ/ndscan Context

This code runs in the context of quantum physics experiments where:
- Experiments take real time to execute (sometimes minutes)
- Calibrations drift and need periodic updates
- Dependencies between experiments must be respected
- Experiments may timeout or fail
- Code may run on **kernels** (compiled to run on FPGA timing hardware)

### CalibrationResult Flag Enum

`CalibrationResult` is an `int` Flag enum that can be combined with bitwise OR:
- `CalibrationResult.OK = 0`
- `CalibrationResult.BAD_EXPIRED` - timeout exceeded
- `CalibrationResult.BAD_DEPS` - dependency failed
- `CalibrationResult.BAD_DATA` - measurement failed
- `CalibrationResult.BAD` - combination of all BAD states
- `CalibrationResult.INVALID_DATA` - data format issue

Check with: `if state & CalibrationResult.OK:` or `if state == CalibrationResult.OK:`

### Performance

- DAG operations should be efficient (caching is critical)
- Avoid keeping references to calibration objects longer than necessary
- Always use weakrefs for storing calibration references in global state
- `_filter_dependency_map()` explicitly calls `gc.collect()`

### Garbage Collection

- Explicitly call `gc.collect()` when filtering the dependency map
- Understand that weakref callbacks trigger DAG invalidation
- The DAG automatically cleans up when calibrations are deleted

### Error Handling

- Use try/except for import-time patches
- Handle missing objects gracefully (they may have been garbage collected)
- Provide clear error messages for DAG/dependency issues
- Raise `CalibrationError` when fixes fail

### Async/Await in Monitoring

- Monitors use asyncio for concurrent execution
- Use `loop.run_in_executor(None, func)` to run blocking code
- Tasks can be cancelled with `task.cancel()`
- Always check `scheduler.check_pause()` for user interruption

## When Suggesting Code

- **Prefer NetworkX built-in functions** for graph operations
- **Always use weakref** for storing Calibration object references in global state
- **Include appropriate type hints** with TYPE_CHECKING imports
- **Add logging statements** at appropriate levels (debug for internal state, info for user actions)
- **Consider the functional testing approach** used in tests/func/
- **Remember this is scientific/research code** - clarity is more important than cleverness
- **Follow the existing pattern of lazy evaluation** with validity flags
- **Use the three-method pattern**: `check_state()` → `fix_state()` → `guess_state()`
- **Respect the build phase restrictions** - certain methods only work in `build_calibration()`
- **Handle asyncio properly** in monitoring code - don't block the event loop
- **Remember kernel constraints** - some code runs on FPGA hardware with limitations

## Important Method Signatures

```python
# User must implement:
def build_calibration(self) -> None
def check_own_state(self) -> Tuple[CalibrationResult, Any]

# Optional override:
def fix_own_state(self) -> None  # raises CalibrationError on failure

# Public API for users:
def check_state(self, force=False, continue_on_fail=False) -> Tuple[CalibrationResult, Any]
def fix_state(self, force=False) -> None
def guess_state(self) -> CalibrationResult

# Configuration (only in build_calibration):
def set_timeout(self, timeout: float) -> None
def set_optimization_type(self, optimization_type: str) -> None
def add_dependency(self, dep_calibration_class: Type["Calibration"], name: str = None, create_duplicates=False) -> None
def setattr_param_optimizable(self, name: str, description: str, min: float, max: float, default: float, *args, **kwargs) -> ParamHandle
```

## Documentation

- Use reStructuredText for docstrings and README files
- Documentation is built with Sphinx
- Include usage examples where appropriate
- Document the "why" not just the "what" for complex patterns (like weakrefs)
- Use Sphinx directives like `:meth:`, `:class:`, `:any:` for cross-references
- Mark parameters with `:param name:` and `:type name:`
- Mark return values with `:return:` and `:rtype:`
- Mark exceptions with `:raises ExceptionType:`
