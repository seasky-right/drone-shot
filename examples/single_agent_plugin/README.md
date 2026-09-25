# Single-component Agent example

This distribution supplies only `sample.single/agent`. It depends on the
public Core wheel and uses the builtin v0.2 Mock Backend, Scenario, and Task
selected by `mixed-run.json`. The sample Agent emits `drone/move` actions using
`drone.move/v1`; a different Backend requires matching capabilities and schemas.

Build and install Core and builtin Pack first, then build this directory as a
separate wheel. Run these commands from outside the repository after installing
all three wheels into one Python 3.13 environment:

```powershell
drone-plugins list
drone-plugins preflight <path-to-mixed-run.json>
drone-plugins validate-plugin --component-cases <path-to-component-cases.json> --output <runs-dir>
drone-plugins run-multi <path-to-mixed-run.json> --output <runs-dir>
drone-plugins show-result single-agent-with-builtin-mock --output <runs-dir>
```

The episode ID in `mixed-run.json` must be changed before repeating `run-multi`
in the same output directory. `validate-plugin` checks only this Agent when
given this case file; other installed components remain `unchecked` or
`unsupported` in that report. See `docs/plugins/插件开发交接.md` for the full
candidate protocol and integration limits.
