# Licensed under the Apache License, Version 2.0

from colcon_cargo.task.cargo.test import CargoTestTask
from colcon_core.plugin_system import satisfies_version
from colcon_core.task import TaskExtensionPoint


class AmentCargoTestTask(CargoTestTask):
    """A test task for packages with Cargo.toml + package.xml.

    Tests are already built by `colcon build` so this task only needs to
    run them and doesn't need to worry about dependency management
    (unlike the `AmentCargoBuildTask`).
    """

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(TaskExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

    def add_arguments(self, *, parser):  # noqa: D102
        pass
