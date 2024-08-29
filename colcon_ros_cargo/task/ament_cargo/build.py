# Licensed under the Apache License, Version 2.0

import os
from pathlib import Path

from colcon_cargo.task.cargo import CARGO_EXECUTABLE
from colcon_cargo.task.cargo.build import CargoBuildTask
from colcon_core.logging import colcon_logger
from colcon_core.plugin_system import satisfies_version
from colcon_core.shell import create_environment_hook
from colcon_core.task import TaskExtensionPoint
import toml


logger = colcon_logger.getChild(__name__)

# Some logic needs to be executed once per run.
# There are no colcon hooks for this, so it is shoehorned into the build step
# with a global.
package_paths = None


class AmentCargoBuildTask(CargoBuildTask):
    """A build task for packages with Cargo.toml + package.xml.

    The primary problem that needs to be solved is that dependencies on other
    packages in the same workspace are expressed by just a name in colcon, but
    by a full path in Cargo.

    That means, when building a Cargo package, all the packages it depends on
    need to be resolved to full paths, and those need to be written into the
    Cargo.toml, or alternatively into a [patch] section of a .cargo/config.toml
    file. Here the latter approach is used.
    """

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(TaskExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

    def add_arguments(self, *, parser):  # noqa: D102
        parser.add_argument(
            '--lookup-in-workspace',
            action='store_true',
            help='Look up dependencies in the workspace directory. '
            'By default, dependencies are looked up only in the installation '
            'prefixes. This option is useful for setting up a '
            '.cargo/config.toml for subsequent builds with cargo.')

    def _prepare(self, env, additional_hooks):
        args = self.context.args

        global package_paths
        if package_paths is None:
            if args.lookup_in_workspace:
                package_paths = find_workspace_cargo_packages(args.build_base, args.install_base)  # noqa: E501
            else:
                package_paths = {}

        # Scan the install dirs, aka prefixes. Note that only those prefixes
        # will be scanned that are a dependency of the current package.
        new_package_paths = find_installed_cargo_packages(env)
        # The new_package_paths cover only the dependencies of the
        # current package, but .cargo/config.toml should contain all Rust
        # packages seen during the build process (so that you can afterwards
        # use cargo for every package in the workspace).
        # Hence, the installed package paths need to be accumulated.
        new_package_paths.update(package_paths)
        package_paths = new_package_paths
        write_cargo_config_toml(package_paths)

        additional_hooks += create_environment_hook(
            'ament_prefix_path',
            Path(self.context.args.install_base),
            self.context.pkg.name,
            'AMENT_PREFIX_PATH',
            self.context.args.install_base,
            mode='prepend')

    def _build_cmd(self, cargo_args):
        args = self.context.args
        src_dir = Path(self.context.pkg.path).resolve()
        manifest_path = str(src_dir / 'Cargo.toml')
        return [
            CARGO_EXECUTABLE, 'ament-build',
            '--install-base', args.install_base,
            '--',
            '--manifest-path', manifest_path,
            '--target-dir', args.build_base,
            '--quiet'
        ] + cargo_args

    # Installation is done by cargo ament-build
    def _install_cmd(self, cargo_args):  # noqa: D102
        pass


def write_cargo_config_toml(package_paths):
    """Write the resolved package paths to config.toml.

    :param package_paths: A mapping of package names to paths
    """
    patches = {pkg: {'path': str(path)} for pkg, path in package_paths.items()}
    content = {'patch': {'crates-io': patches}}
    config_dir = Path.cwd() / '.cargo'
    config_dir.mkdir(exist_ok=True)
    cargo_config_toml_out = config_dir / 'config.toml'
    with cargo_config_toml_out.open('w') as toml_file:
        toml.dump(content, toml_file)


def find_installed_cargo_packages(env):
    """Find out which prefix contains each of the dependencies.

    :param env: Environment dict for this package
    :returns: A mapping of package names to paths
    :rtype dict(str, Path)
    """
    prefix_for_package = {}
    ament_prefix_path_var = env.get('AMENT_PREFIX_PATH')
    if ament_prefix_path_var is None:
        logger.warning('AMENT_PREFIX_PATH is empty. '
                       'You probably intended to source a ROS installation.')
        prefixes = []
    else:
        prefixes = ament_prefix_path_var.split(os.pathsep)
    for prefix in prefixes:
        prefix = Path(prefix)
        packages_dir = prefix / 'share' / 'ament_index' / 'resource_index' \
            / 'rust_packages'
        if packages_dir.exists():
            packages = {path.name for path in packages_dir.iterdir()}
        else:
            packages = set()
        for pkg in packages:
            prefix_for_package[pkg] = prefix
    return {pkg: str(prefix / 'share' / pkg / 'rust')
            for pkg, prefix in prefix_for_package.items()}


def find_workspace_cargo_packages(build_base, install_base):
    """Find Cargo packages in the workspace/current working directory.

    :param install_base: The install base of the current build
    :returns: A mapping of package names to paths
    :rtype dict(str, Path)
    """
    path_for_package = {}
    for (dirpath, dirnames, filenames) in os.walk(Path.cwd(), topdown=True):
        # Users will often build the workspace several times into differently
        # named install directories, and we don't know their names. So if we
        # just scan through the current working directory, we'll probably find
        # Rust packages in those install directories. That's not what we want,
        # so install directories (identified by a setup.sh file) should be
        # skipped.
        if dirpath == install_base or (Path(dirpath) / 'setup.sh').exists():
            # Do not descend into this directory
            dirnames[:] = []
        elif dirpath == build_base or (Path(dirpath) / 'COLCON_IGNORE').exists():  # noqa: E501
            # In particular, build dirs have a COLCON_IGNORE
            # Do not descend into this directory
            dirnames[:] = []
        elif 'Cargo.toml' in filenames:
            try:
                cargo_toml = toml.load(Path(dirpath) / 'Cargo.toml')
                name = cargo_toml['package']['name']
                path_for_package[name] = dirpath
            except toml.decoder.TomlDecodeError:
                pass
    return path_for_package
