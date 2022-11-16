# Licensed under the Apache License, Version 2.0

from colcon_core.package_identification import PackageIdentificationExtensionPoint
from colcon_core.plugin_system import satisfies_version
from colcon_ros.package_identification.ros import _get_package
import toml


class AmentCargoPackageIdentification(PackageIdentificationExtensionPoint):
    """Identify Cargo packages with `Cargo.toml` and `package.xml` files."""

    PRIORITY = 160

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageIdentificationExtensionPoint.EXTENSION_POINT_VERSION, "^1.0"
        )

    def identify(self, metadata):  # noqa: D102
        if metadata.type is not None and metadata.type != "ament_cargo":
            return

        # Check if package.xml exists
        package_xml = metadata.path / "package.xml"
        if not package_xml.is_file():
            return

        # Check if Cargo.toml exists
        cargo_toml = metadata.path / "Cargo.toml"
        if not cargo_toml.is_file():
            return

        # Make sure Cargo.toml is not a virutal manifest
        content = toml.load(str(cargo_toml))

        metadata.type = "ament_cargo"
        pkg = _get_package(str(metadata.path))

        if metadata.name is None:
            metadata.name = pkg["name"]
        metadata.dependencies["build"] = {dep.name for dep in pkg.build_depends}
        metadata.dependencies["run"] = {dep.name for dep in pkg.run_depends}
        metadata.dependencies["test"] = {dep.name for dep in pkg.test_depends}
