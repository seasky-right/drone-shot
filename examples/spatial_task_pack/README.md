# Spatial task Pack (v0.2 candidate)

This independently installable Pack supplies ReachPoint and experimental SearchTarget tasks, evaluators, example Agents, and fixed three-seed Benchmarks. It requires the compatible World Pack. Agents read only public observations; Task/Evaluator receive each scenario's hidden truth through Core.

ReachPoint uses public `goal` and hidden `truth.goal`. Its evaluator reports `spatial/success_ratio` (0 or 1), `spatial/final_distance_m`, and `spatial/path_length_m`. Distances are straight-line local NED metres.

SearchTarget policy `search-target/v0.1-experimental` uses `sample.spatial-search`: hidden `truth.target_id` and exact `truth.target`, approximate public detections, and REPORT actions. A report matches only when its target ID equals hidden `target_id` and its NED distance is at most 0.75 m. There is one target and at most one counted match; all other accepted reports, including duplicates and wrong IDs, count as false positives. `spatial/search_localization_error_m` is the nearest geometric error among accepted reports with the correct target ID; with none, it is the explicit 100 m missing-report penalty and `spatial/search_localization_observed_ratio` is 0. An initial empty detection list means the detector ran without a hit; `missing_sensors.search-detector` means it did not run. These have separate metrics. Timeouts and completed failures remain in the denominator.

Result processors include every completed case in the success denominator. Benchmark execution currently aborts on an episode exception, so it does not define a denominator for cases with no completed result. These Mock results do not establish a real UE4 SearchTarget capability or approved F08/F10 scoring policy.

Use `sample-benchmark-direct.json` and `sample-benchmark-staged.json` for ReachPoint, or `sample-benchmark-search.json` and `sample-benchmark-search-offset.json` for SearchTarget, with both Pack manifests. Each Agent pair runs identical case IDs and seeds. The search offset Agent deliberately shifts a public clue by 2 m to exercise false-positive scoring.
