# Repository instructions

Use `综述.md` for route and `docs/planning/README.md` for active work order. Code and recorded verification outrank stale PRD claims.

`材料-无人机遥感实习-AirSIM部分/`, `airsim-settings/drone.json`, and `simulator_contract/legacy_ue4_airsim.manifest.json` are frozen baseline evidence. Preserve their paths and bytes; do not run the baseline writer unless intentionally replacing the baseline.

Platform code may use `contracts/` and backend abstractions. It must not import course materials or AirSim RPC directly. Keep platform observations separate from low-level sensor observations. Use no-simulator contract/Mock tests normally; record real simulator results independently.

Do not initialise Git or publish resources without explicit user authorization. The 2026-09-21 authorization establishes a private collaboration baseline; do not change repository visibility, add collaborators, publish resources, or invent GitHub owners, licence, or human approvals. Review the actual prospective file set before any future upload.
