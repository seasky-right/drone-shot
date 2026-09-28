# Spatial World Pack (v0.2 candidate)

This independently installable Pack supplies two no-simulator scenarios on the
same versioned `local_ned` world fixture (`src/drone_spatial_world/world.json`).
The fixture separates public bounds/obstacles from generator-only spawn and
goal site lists. Its SHA-256 is recorded in each scenario's initial state;
the scenario checksum additionally commits to scenario ID, seed, start, and
target. `ScenarioSpecV02.resources` is empty because the packaged JSON fixture
is not an episode artifact or a loadable simulator mesh.

| Scenario | Generator | Backend | Agent observation | Evaluator truth |
| --- | --- | --- | --- | --- |
| Public ReachPoint | `sample.spatial-grid/generator` | `sample.spatial-grid/backend` | Position and exact public `goal` | Same `goal` |
| Hidden SearchTarget | `sample.spatial-search/generator` | `sample.spatial-search/backend` | Position, public `search_area`, imperfect `detections`, echoed `reports` | Exact `target_id` and `target` |

Generation enumerates ordered spawn/target pairs, rejects pairs below the
minimum separation or whose straight path crosses an obstacle or boundary,
and chooses `seed % feasible_pair_count`. There is no sampling retry or
unbounded loop. ReachPoint seed changes the chosen pair. SearchTarget also
uses `seed % 5` to select a detector case: remainder 1 means sensor unavailable,
2 means a valid start beyond the initial detection range, and all other
remainders select a valid start within range. No feasible pair raises an error
before an episode starts. Seeds 7, 11, and 19 exercise those three SearchTarget
cases respectively.

The Mock backends declare `load` and `reset`, one vehicle, bounded execution,
and only their supported actions. They do not claim RGB/depth sensors or
simulator time. Every reset replaces vehicle position, target, detector mode,
reports, and sequence; there is no sensor or simulator cache. `spatial/move-to`
accepts three finite NED coordinates and rejects boundary/obstacle crossings.
SearchTarget additionally accepts `spatial/report-target` through a REPORT
handler. The handler echoes valid estimates without publishing correctness;
Core passes exact truth only to Task/Evaluator. An empty `detections` list with
no `missing_sensors` entry means the detector ran without a hit. The
`search-detector: unavailable` missing-sensor entry means no reading exists.

The world fixture and these rules demonstrate deterministic Mock reset and
generation only. A real simulator requires separate placement, sensor, and
cache reset evidence for its own map and backend.
