# CLAUDE.md

## Reference repositories

When writing code for qbutler, clone the following repositories locally for reference. Reading their source is essential for understanding the frameworks qbutler builds on:

- **icl_experiments** — the primary user of qbutler; a large collection of real experiments built with ndscan. Use this to understand how qbutler is actually used in practice.
  ```
  git clone https://gitlab.com/aion-physics/code/artiq/experiment-repositories/icl_experiments
  ```

- **ndscan** — defines the `Fragment` base class and the parameter/scan framework that qbutler integrates with.
  ```
  git clone https://github.com/OxfordIonTrapGroup/ndscan.git
  ```

- **ARTIQ** (fork) — the underlying experiment control framework; source of truth for `EnvExperiment`, kernels, and hardware abstractions.
  ```
  git clone https://gitlab.com/aion-physics/code/artiq/forks/artiq_fork.git
  ```
