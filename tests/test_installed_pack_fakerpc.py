"""Opt-in C4 regression against built Core and builtin Pack wheels.

Set DRONE_CORE_WHEEL and DRONE_PACK_WHEEL to the exact artifacts under review.
The child process imports those installed wheels, with repository tests visible
only for the FakeRpc fixture. No simulator connection is made.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import contextlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

installed, repository, workspace = map(Path, sys.argv[1:])
sys.path.insert(0, str(installed))
sys.path.insert(1, str(repository))

import core
import builtin_pack
import backends.airsim
from backends.airsim import AirSimBackend
from contracts import BackendConfig, PositionNed, TaskSpec, TerminationReason
from core.assembly import discover_plugins
from core.cli import main
from core.replay import read_episode
from simulator_contract.legacy_airsim import AirSimLegacyAdapter
from tests.test_airsim_backend import FakeRpc

for package in (core, builtin_pack, backends.airsim):
    assert Path(package.__file__).resolve().is_relative_to(installed.resolve()), package.__file__
registry = discover_plugins()
assert not registry.issues
assert registry.resolve("drone.mock/backend").type == "backend"
assert registry.resolve("drone.airsim/backend").type == "backend"

task = TaskSpec("installed-fakerpc", "reach_point", 15, PositionNed(0, 0, 0),
                "relative_to_home", {"target_position_ned": PositionNed(0, 1, -1).to_dict(),
                                     "tolerance_m": 0.25})

def write_config(kind, *, sensors=False):
    connection = {"position_tolerance_m": 0.1}
    if sensors:
        connection["sensors"] = [{"sensor_id": "0", "kind": "rgb"}]
    config = BackendConfig(kind, "drone-1", connection)
    path = workspace / f"{kind}.json"
    path.write_text(json.dumps({"task_spec": task.to_dict(),
                                "backend_config": config.to_dict()}), encoding="utf-8")
    return path

mock_config = write_config("mock")
mock_output = workspace / "mock-runs"
with contextlib.redirect_stdout(io.StringIO()) as printed:
    code = main(["--config", str(mock_config), "--agent", "fixed",
                 "--output", str(mock_output)])
assert code == 0
mock_result = json.loads(printed.getvalue())
mock_episode = mock_output / mock_result["episode_id"]
mock_replay = read_episode(mock_episode)
assert mock_replay.result.success
assert json.loads((mock_episode / "run.json").read_text(encoding="utf-8"))["plugins"]["backend"]["id"] == "drone.mock/backend"

airsim_config = write_config("airsim", sensors=True)
rpc = FakeRpc()
def fake_backend():
    return AirSimBackend(lambda **options: AirSimLegacyAdapter(
        rpc_factory=lambda host, port: rpc, **options))

guard_errors = io.StringIO()
with patch("backends.airsim.AirSimBackend") as constructor:
    with contextlib.redirect_stderr(guard_errors):
        try:
            main(["--config", str(airsim_config), "--output", str(workspace / "guard")])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("AirSim backend was allowed without explicit enable")
    constructor.assert_not_called()
assert "--enable-airsim" in guard_errors.getvalue()

airsim_output = workspace / "airsim-runs"
with patch("backends.airsim.AirSimBackend", side_effect=fake_backend):
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        code = main(["--config", str(airsim_config), "--agent", "fixed",
                     "--output", str(airsim_output), "--enable-airsim"])
assert code == 0
result = json.loads(printed.getvalue())
episode = airsim_output / result["episode_id"]
replay = read_episode(episode)
assert replay.result.termination_reason is TerminationReason.SUCCESS
assert replay.result.cleanup.succeeded
assert json.loads((episode / "run.json").read_text(encoding="utf-8"))["plugins"]["backend"]["id"] == "drone.airsim/backend"
assert (episode / replay.steps[0].observation_before.sensors[0].relative_path).read_bytes() == b"pngdata"
assert (episode / replay.steps[0].observation_after.sensors[0].relative_path).read_bytes() == b"pngdata"
assert rpc.closed

failed_rpc = FakeRpc()
failed_rpc.fail_method = "moveByVelocityBodyFrame"
def failing_backend():
    return AirSimBackend(lambda **options: AirSimLegacyAdapter(
        rpc_factory=lambda host, port: failed_rpc, **options))
failed_output = workspace / "failed-runs"
with patch("backends.airsim.AirSimBackend", side_effect=failing_backend):
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        code = main(["--config", str(airsim_config), "--agent", "fixed",
                     "--output", str(failed_output), "--enable-airsim"])
assert code == 1
failed_result = json.loads(printed.getvalue())
failed_replay = read_episode(failed_output / failed_result["episode_id"])
assert failed_replay.result.termination_reason is TerminationReason.BACKEND_ERROR
assert failed_replay.result.cleanup.succeeded
assert failed_replay.steps[0].execution.error.code == "simulator"
assert failed_rpc.closed

print(json.dumps({"installed_core": core.__file__, "installed_pack": builtin_pack.__file__,
                  "mock": mock_replay.result.termination_reason.value,
                  "fakerpc": replay.result.termination_reason.value,
                  "fakerpc_failure": failed_replay.result.termination_reason.value,
                  "plugin_count": len(registry.list())}))
'''


@unittest.skipUnless(os.environ.get("DRONE_CORE_WHEEL") and os.environ.get("DRONE_PACK_WHEEL"),
                     "set DRONE_CORE_WHEEL and DRONE_PACK_WHEEL for installed-wheel regression")
class InstalledPackFakeRpcTests(unittest.TestCase):
    def test_mock_and_fakerpc_use_the_same_installed_core_wheel(self):
        core_wheel = Path(os.environ["DRONE_CORE_WHEEL"]).resolve()
        pack_wheel = Path(os.environ["DRONE_PACK_WHEEL"]).resolve()
        self.assertTrue(core_wheel.is_file())
        self.assertTrue(pack_wheel.is_file())
        with TemporaryDirectory(prefix="drone-installed-fakerpc-") as temporary:
            base = Path(temporary)
            installed = base / "installed"
            install = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                 "--target", str(installed), str(core_wheel), str(pack_wheel)],
                cwd=base, capture_output=True, text=True, check=False,
            )
            self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
            child = subprocess.run(
                [sys.executable, "-c", CHILD, str(installed), str(ROOT), str(base)],
                cwd=base, capture_output=True, text=True, check=False,
            )
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            evidence = json.loads(child.stdout)
            self.assertEqual(evidence["mock"], "success")
            self.assertEqual(evidence["fakerpc"], "success")
            self.assertEqual(evidence["fakerpc_failure"], "backend_error")
            self.assertGreaterEqual(evidence["plugin_count"], 2)
            self.assertTrue(Path(evidence["installed_core"]).is_relative_to(installed))
            self.assertTrue(Path(evidence["installed_pack"]).is_relative_to(installed))


if __name__ == "__main__":
    unittest.main()
