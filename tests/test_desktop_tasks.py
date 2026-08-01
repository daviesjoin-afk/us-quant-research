from dataclasses import dataclass

import pytest

from us_quant.desktop_tasks import DesktopTaskController


@dataclass
class Worker:
    resource_group: str
    running: bool = True

    def isRunning(self) -> bool:
        return self.running


def test_resource_group_admission_and_finish_are_deterministic() -> None:
    controller = DesktopTaskController[Worker]()
    first = Worker("broker")
    other = Worker("research")
    controller.register(first)

    assert not controller.can_start("broker")
    assert controller.can_start("research")
    controller.register(other)
    assert controller.active_count == 2
    assert controller.finish(first)
    assert controller.can_start("broker")
    assert not controller.finish(first)


def test_duplicate_group_registration_is_rejected() -> None:
    controller = DesktopTaskController[Worker]()
    controller.register(Worker("scan"))

    with pytest.raises(RuntimeError, match="scan"):
        controller.register(Worker("scan"))
