# Licensed under the Apache License, Version 2.0

import subprocess

from catkin_pkg.package import parse_package
from colcon_cargo.package_identification.cargo \
    import CargoPackageIdentification
from colcon_core.logging import colcon_logger
from colcon_core.package_identification \
    import PackageIdentificationExtensionPoint
from colcon_core.plugin_system import satisfies_version
from colcon_ros.package_identification.ros import _get_package

logger = colcon_logger.getChild(__name__)


class AmentCargoPackageIdentification(CargoPackageIdentification):
    """Identify Cargo packages with `Cargo.toml` and `package.xml` files."""

    PRIORITY = 160

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageIdentificationExtensionPoint.EXTENSION_POINT_VERSION,
            '^1.0')

    def identify(self, metadata):  # noqa: D102

        if metadata.type is not None and metadata.type != 'ament_cargo':
            return

        package_xml = metadata.path / 'package.xml'
        if not package_xml.is_file():
            return

        pkg_desc = parse_package(package_xml)

        if pkg_desc.get_build_type() != 'ament_cargo':
            return

        cargo_toml = metadata.path / 'Cargo.toml'
        if not cargo_toml.is_file():
            logger.warn(
                'Got build type ament_cargo but could not find "Cargo.toml"')
            return

        ament_build = 'cargo ament-build --help'.split()
        if subprocess.run(ament_build, capture_output=True).returncode != 0:
            if _print_ament_cargo_warning_once():
                logger.error(
                    '\n\nament_cargo package found but cargo ament-build was '
                    'not detected.'
                    '\n\nPlease install it by running:'
                    '\n $ cargo install cargo-ament-build\n')
            return

        metadata.type = 'ament_cargo'
        pkg = _get_package(str(metadata.path))

        if metadata.name is None:
            metadata.name = pkg['name']
        metadata.dependencies['build'] = \
            {dep.name for dep in pkg.build_depends}
        metadata.dependencies['run'] = \
            {dep.name for dep in pkg.run_depends}
        metadata.dependencies['test'] = \
            {dep.name for dep in pkg.test_depends}


def _print_ament_cargo_warning_once():
    global has_printed_ament_cargo_warning
    try:
        # The following line will throw an exception if the global variable
        # has never been initialized
        has_printed_ament_cargo_warning
    except NameError:
        # We want to initialize the global variable to false the first time
        has_printed_ament_cargo_warning = False

    if not has_printed_ament_cargo_warning:
        has_printed_ament_cargo_warning = True
        return True

    return False
