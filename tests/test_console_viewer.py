"""Read-only live viewer and CLI integration without a simulator."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from contracts.data_v02 import EpisodeSnapshotV02, PlatformObservationV02, SensorReferenceV02
from core.console import _select
from core.console_viewer import LiveViewer
from core.plugin_cli import run_multi
from core.plugins import PluginRegistry


ROOT = Path(__file__).resolve().parents[1]


class ViewerTests(unittest.TestCase):
    def test_state_and_only_current_rgb_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "frame.png").write_bytes(b"png-bytes")
            (root / "private.png").write_bytes(b"private")
            viewer = LiveViewer(mock=False)
            try:
                snapshot = EpisodeSnapshotV02(0, {"Drone": PlatformObservationV02(
                    0, "Drone", 1_000_000_000,
                    {"north_m": 4.0, "east_m": 2.0, "down_m": -1.0,
                     "target": {"north_m": 5.0, "east_m": 3.0, "down_m": -2.0}},
                    (SensorReferenceV02("front", "drone/rgb", "frame.png", 1_000_000_000),))})
                viewer.observe(snapshot, root)
                with urlopen(viewer.url + "state") as response:
                    state = json.load(response)
                self.assertEqual(state["vehicles"]["Drone"]["track"], [[4.0, 2.0]])
                self.assertEqual(state["vehicles"]["Drone"]["target"], [5.0, 3.0, -2.0])
                self.assertEqual(state["frame"]["path"], "frame.png")
                with urlopen(viewer.url + "image?path=frame.png") as response:
                    self.assertEqual(response.read(), b"png-bytes")
                for path in ("private.png", "../private.png", "../../private.png"):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(viewer.url + "image?path=" + path)
                    self.assertEqual(caught.exception.code, 404)
                viewer.observe(EpisodeSnapshotV02(1, {"Drone": PlatformObservationV02(
                    1, "Drone", 2_000_000_000, {"north_m": 5.0, "east_m": 2.0})}), root)
                with self.assertRaises(HTTPError):
                    urlopen(viewer.url + "image?path=frame.png")
            finally:
                viewer.close()

    def test_select_mock_live_page_survives_through_result_then_closes(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        registry = PluginRegistry.discover(manifest_paths=(ROOT / "builtin_pack" / "drone_plugin.json",))
        answers = iter(("v", "r", ""))
        seen = []

        def prompt(message: str) -> str:
            if "保持打开" in message:
                url = next(line.split("实时页面: ", 1)[1] for line in seen[0].getvalue().splitlines()
                           if line.startswith("实时页面: "))
                with urlopen(url + "state") as response:
                    state = json.load(response)
                self.assertEqual(state["status"], "运行结束")
                self.assertTrue(state["mock"])
                self.assertEqual(state["vehicles"]["A"]["track"][-1], [5.0, 0.0])
                self.assertEqual(state["vehicles"]["B"]["track"][-1], [5.0, 0.0])
            return next(answers)

        with TemporaryDirectory() as directory, redirect_stdout(output := StringIO()):
            seen.append(output)
            with patch("core.console.webbrowser.open", return_value=False):
                self.assertEqual(_select(data, registry, Path(directory), prompt=prompt), 0)
            url = next(line.split("实时页面: ", 1)[1] for line in output.getvalue().splitlines()
                       if line.startswith("实时页面: "))
            with self.assertRaises(OSError):
                urlopen(url + "state", timeout=0.5)
        self.assertIn("步数       1", output.getvalue())

    def test_observer_failure_does_not_change_episode_result(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        registry = PluginRegistry.discover(manifest_paths=(ROOT / "builtin_pack" / "drone_plugin.json",))
        with TemporaryDirectory() as directory:
            result = run_multi(data, Path(directory), registry=registry,
                               on_snapshot=lambda *_: (_ for _ in ()).throw(RuntimeError("viewer failed")))
            self.assertTrue(result["success"])
            self.assertEqual(result["step_count"], 1)

    def test_failed_run_keeps_viewer_until_acknowledged(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        registry = PluginRegistry.discover(manifest_paths=(ROOT / "builtin_pack" / "drone_plugin.json",))
        answers = iter(("v", "r", ""))
        with TemporaryDirectory() as directory, redirect_stdout(output := StringIO()):
            def prompt(message: str) -> str:
                if "任务失败" in message:
                    url = next(line.split("实时页面: ", 1)[1] for line in output.getvalue().splitlines()
                               if line.startswith("实时页面: "))
                    with urlopen(url + "state") as response:
                        state = json.load(response)
                    self.assertIn("失败：RuntimeError", state["result"])
                return next(answers)

            with patch("core.console.webbrowser.open", return_value=False), \
                 patch("core.console.run_multi", side_effect=RuntimeError("run failed")):
                with self.assertRaisesRegex(RuntimeError, "run failed"):
                    _select(data, registry, Path(directory), prompt=prompt)

    def test_airsim_fake_rpc_exposes_rgb_reference_after_artifact_write(self) -> None:
        from backends.airsim import AirSimBackend
        from simulator_contract.legacy_airsim import AirSimLegacyAdapter
        from tests.test_airsim_v02_plugin import ImageRpc

        data = json.loads((ROOT / "builtin_pack" / "sample-airsim-v02.json").read_text(encoding="utf-8"))
        registry = PluginRegistry.discover(manifest_paths=(ROOT / "builtin_pack" / "drone_plugin.json",))
        rpc = ImageRpc()
        seen = []

        def factory():
            return AirSimBackend(lambda **options: AirSimLegacyAdapter(
                rpc_factory=lambda host, port: rpc, **options))

        def observe(snapshot, root):
            sensor = next(sensor for sensor in snapshot.observations["Drone"].sensors
                          if sensor.kind == "drone/rgb")
            self.assertTrue((root / sensor.relative_path).is_file())
            seen.append((snapshot.sequence, sensor.relative_path))

        with TemporaryDirectory() as directory:
            with patch("builtin_pack.airsim_v02.AirSimBackend", side_effect=factory):
                result = run_multi(data, Path(directory), registry=registry,
                                   enable_backend=True, on_snapshot=observe)
            self.assertTrue(result["success"])
            self.assertGreaterEqual(len(seen), 2)
            self.assertEqual(seen[0][0], 0)


if __name__ == "__main__":
    unittest.main()
