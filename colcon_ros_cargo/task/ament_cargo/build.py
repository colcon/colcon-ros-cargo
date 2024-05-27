# Licensed under the Apache License, Version 2.0

import os
from pathlib import Path
import shutil

from . import CARGO_EXECUTABLE
from colcon_core.logging import colcon_logger
from colcon_core.environment import create_environment_scripts
from colcon_core.plugin_system import satisfies_version
from colcon_core.shell import create_environment_hook, get_command_environment
from colcon_core.task import TaskExtensionPoint, run
import toml


logger = colcon_logger.getChild(__name__)

# Some logic needs to be executed once per run.
# There are no colcon hooks for this, so it is shoehorned into the build step
# with a global.
package_paths = None


class AmentCargoBuildTask(TaskExtensionPoint):
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
        satisfies_version(TaskExtensionPoint.EXTENSION_POINT_VERSION, "^1.0")

    def add_arguments(self, *, parser):  # noqa: D102
        parser.add_argument(
            "--cargo-args",
            nargs="*",
            metavar="*",
            type=str.lstrip,
            help="Pass arguments to Cargo projects. "
            "Arguments matching other options must be prefixed by a space,\n"
            'e.g. --cargo-args " --help"',
        )
        parser.add_argument(
            "--clean-build",
            action="store_true",
            help="Remove old build dir before the build.",
        )
        parser.add_argument(
            "--lookup-in-workspace",
            action="store_true",
            help="Look up dependencies in the workspace directory. "
            "By default, dependencies are looked up only in the installation "
            "prefixes. This option is useful for setting up a "
            ".cargo/config.toml for subsequent builds with cargo.",
        )

    async def build(  # noqa: D102
        self, *, additional_hooks=None, skip_hook_creation=False
    ):
        if additional_hooks is None:
            additional_hooks = []
        args = self.context.args

        logger.info("Building Cargo package in '{args.path}'".format_map(locals()))

        try:
            env = await get_command_environment(
                "build", args.build_base, self.context.dependencies
            )
        except RuntimeError as e:
            logger.error(str(e))
            return 1

        self.progress("prepare")
        rc = self._prepare(env, additional_hooks)
        if rc:
            return rc

        # Clean up the build dir
        build_dir = Path(args.build_base)
        if args.clean_build:
            if build_dir.is_symlink():
                build_dir.unlink()
            elif build_dir.exists():
                shutil.rmtree(build_dir)

        # Invoke build step
        if CARGO_EXECUTABLE is None:
            raise RuntimeError("Could not find 'cargo' executable")

        cargo_args = args.cargo_args
        if cargo_args is None:
            cargo_args = []
        cmd = self._build_cmd(cargo_args)

        self.progress("build")

        rc = await run(self.context, cmd, cwd=self.context.pkg.path, env=env)
        if rc and rc.returncode:
            return rc.returncode

        if not skip_hook_creation:
            create_environment_scripts(
                self.context.pkg, args, additional_hooks=additional_hooks
            )

    def _prepare(self, env, additional_hooks):
        args = self.context.args

        global package_paths
        if package_paths is None:
            if args.lookup_in_workspace:
                package_paths = find_workspace_cargo_packages(
                    args.build_base, args.install_base
                )  # noqa: E501
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
            "ament_prefix_path",
            Path(self.context.args.install_base),
            self.context.pkg.name,
            "AMENT_PREFIX_PATH",
            self.context.args.install_base,
            mode="prepend",
        )

    def _build_cmd(self, cargo_args):
        args = self.context.args
        src_dir = Path(self.context.pkg.path).resolve()
        manifest_path = str(src_dir / "Cargo.toml")
        return [
            CARGO_EXECUTABLE,
            "ament-build",
            "--install-base",
            args.install_base,
            "--",
            "--manifest-path",
            manifest_path,
            "--target-dir",
            args.build_base,
            "--quiet",
        ] + cargo_args


def write_cargo_config_toml(package_paths):
    """Write the resolved package paths to config.toml.

    :param package_paths: A mapping of package names to paths
    """
    patches = {pkg: {"path": str(path)} for pkg, path in package_paths.items()}
    content = {"patch": {"crates-io": patches}}
    config_dir = Path.cwd() / ".cargo"
    config_dir.mkdir(exist_ok=True)
    cargo_config_toml_out = config_dir / "config.toml"
    cargo_config_toml_out.unlink(missing_ok=True)
    toml.dump(content, cargo_config_toml_out.open("w"))


def find_installed_cargo_packages(env):
    """Find out which prefix contains each of the dependencies.

    :param env: Environment dict for this package
    :returns: A mapping of package names to paths
    :rtype dict(str, Path)
    """
    prefix_for_package = {}
    ament_prefix_path_var = env.get("AMENT_PREFIX_PATH")
    if ament_prefix_path_var is None:
        logger.warn(
            "AMENT_PREFIX_PATH is empty. "
            "You probably intended to source a ROS installation."
        )
        prefixes = []
    else:
        prefixes = ament_prefix_path_var.split(os.pathsep)
    for prefix in prefixes:
        prefix = Path(prefix)
        packages_dir = (
            prefix / "share" / "ament_index" / "resource_index" / "rust_packages"
        )
        if packages_dir.exists():
            packages = {path.name for path in packages_dir.iterdir()}
        else:
            packages = set()
        for pkg in packages:
            prefix_for_package[pkg] = prefix
    return {
        pkg: str(prefix / "share" / pkg / "rust")
        for pkg, prefix in prefix_for_package.items()
    }


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
        if dirpath == install_base or (Path(dirpath) / "setup.sh").exists():
            # Do not descend into this directory
            dirnames[:] = []
        elif dirpath == build_base or (Path(dirpath) / "COLCON_IGNORE").exists():
            # In particular, build dirs have a COLCON_IGNORE
            # Do not descend into this directory
            dirnames[:] = []
        elif "Cargo.toml" in filenames:
            try:
                cargo_toml = toml.load(Path(dirpath) / "Cargo.toml")
                name = cargo_toml["package"]["name"]
                path_for_package[name] = dirpath
            except toml.decoder.TomlDecodeError:
                pass
    return path_for_package
