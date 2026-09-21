# Contributing

Use the planning index as the source of work order. Keep each change narrow and include the affected contract version, tests actually run, known limits, and next handoff action in its pull request.

Directory responsibility follows the three tracks: platform contracts and integration (`contracts/`, `core/`), simulator adapters (`backends/` and `simulator_contract/`), and task/evaluation consumers (`agents/`, `tasks/`, `evaluators/`, `benchmarks/`). A platform contract change needs recorded review from affected backend and task consumers before it is treated as cross-track change.

Do not modify frozen course material, `airsim-settings/drone.json`, or the legacy checksum manifest as ordinary platform work. Do not present MockBackend results as real-flight evidence. Run the relevant no-simulator tests from the README; document real-simulator results separately.
