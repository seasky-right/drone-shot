# Legacy UE4/AirSim local resource manifest

| Field | Value |
| --- | --- |
| Resource ID | `legacy-ue4-airsim-local-baseline` |
| Version | historic local baseline, 2026-09-20 manifest |
| Source / acquisition | Existing local course material; no public redistribution route confirmed |
| Expected layout | `材料-无人机遥感实习-AirSIM部分/` at repository root |
| Settings path | `airsim-settings/drone.json` |
| Integrity check | `python -B simulator_contract/freeze_baseline.py --verify` checks 1,389 files against the committed JSON manifest |
| Size | approximately 3.97 GiB, plus `.driver-cache/` approximately 5.61 GiB |
| SHA-256 source | Per-file digests in `simulator_contract/legacy_ue4_airsim.manifest.json` |
| Licence / access | Not determined; do not publish or redistribute without an authorised decision |

This document records a local prerequisite only. Normal install and CI do not require it.
