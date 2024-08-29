# Licensed under the Apache License, Version 2.0

from colcon_cargo.package_identification.cargo \
    import CargoPackageIdentification


from colcon_core.package_identification import logger
from colcon_core.package_identification \
    import PackageIdentificationExtensionPoint
from colcon_core.plugin_system import satisfies_version
from colcon_ros.package_identification.ros import _get_package


class AmentCargoPackageIdentification(CargoPackageIdentification):
    """Identify Cargo packages with `Cargo.toml` and `package.xml` files."""

    PRIORITY = 160

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageIdentificationExtensionPoint.EXTENSION_POINT_VERSION,
            '^1.0')

    def identify(self, metadata):  # noqa: D102
        from catkin_pkg.package import parse_package

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
            logger.warn('Got build type ament_cargo but could not find "Cargo.toml"')
            raise RuntimeError('Got build type ament_cargo but could not find "Cargo.toml"')
       
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
