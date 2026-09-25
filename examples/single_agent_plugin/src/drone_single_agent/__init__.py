"""A single external Agent using only the public v0.2 data contract."""

from contracts.data_v02 import ActionChannel, ActionV02, EpisodeSnapshotV02


class Agent:
    def __init__(self, target_north_m: float) -> None:
        self.target_north_m = target_north_m

    def act(self, snapshot: EpisodeSnapshotV02) -> tuple[ActionV02, ...]:
        return tuple(
            ActionV02(
                f"single-{snapshot.sequence}-{vehicle_id}", vehicle_id,
                "drone/move", ActionChannel.CONTROL, "drone.move/v1",
                {"north_m": self.target_north_m}, 1.0,
            )
            for vehicle_id in snapshot.observations
        )

    def close(self) -> None:
        pass


def create_agent(*, config, context):
    return Agent(float(config["target_north_m"]))
