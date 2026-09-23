# ReachPoint Benchmark Pack v0.1

This pack freezes the local Track C baseline: one ReachPoint case, five seeds, three simulator-independent baseline agents, scoring policy, resource limits, and retry policy. Validate it with `python -B scripts/validate_reach_point_pack.py`.

The case coordinates and limits are development defaults. They have not been verified as safe in the legacy AirSim scene. `RandomAgent`, `FixedRouteAgent`, and `RuleBasedAgent` implement only the platform `Observation → Action` contract and never import AirSim. Their constructor parameters are stored in `baseline_manifest.json` and validated locally.

This directory is a versioned input for the repository's Runner/Experiment Manager. A manifest adapter and the evaluator's event/timing capture still need to be connected. Its presence does not mean that `3 agents × 5 repeats` has been flown. Current offline demonstrations use synthetic recorded episodes and are clearly labelled as such.
