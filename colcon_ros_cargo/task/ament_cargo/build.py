# Licensed under the Apache License, Version 2.0

import json
import os
from pathlib import Path
import subprocess

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
        parser.add_argument(
            '--cargo-config-per-package',
            action='store_true',
            help='Write a separate .cargo/config.toml into each package '
            'source directory, containing only the [patch] entries the '
            'package actually depends on, instead of a single workspace-wide '
            'config listing every discovered package. This keeps each '
            "package's Cargo.lock free of unused [[patch]] entries, so it "
            'stays deterministic across machines and can be committed. '
            'Implies --lookup-in-workspace.')

    def _prepare(self, env, additional_hooks):
        ament_build = 'cargo ament-build --help'.split()
        if subprocess.run(ament_build, capture_output=True).returncode != 0:
            logger.error(
                '\n\nament_cargo package found but cargo ament-build was '
                'not detected.'
                '\n\nPlease install it by running:'
                '\n $ cargo install cargo-ament-build\n')
            return 1

        args = self.context.args

        global package_paths
        if package_paths is None:
            if args.lookup_in_workspace or args.cargo_config_per_package:
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

        if args.cargo_config_per_package:
            self._write_per_package_config(env)
        else:
            write_cargo_config_toml(package_paths)

        additional_hooks += create_environment_hook(
            'ament_prefix_path',
            Path(self.context.args.install_base),
            self.context.pkg.name,
            'AMENT_PREFIX_PATH',
            '',
            mode='prepend')

    def _write_per_package_config(self, env):
        """Write a minimal [patch] table into the package's config.toml.

        The [patch.crates-io] table in the package's .cargo/config.toml is
        replaced with only the patches that this package (or, for a Cargo
        workspace, any of its members) actually resolves, so the resulting
        Cargo.lock does not accumulate unused [[patch]] entries and is stable
        regardless of which other packages are installed. The whole table is
        rewritten each build, so patches for dependencies that have since been
        removed do not linger. Any other content of an existing config.toml
        (e.g. [target.*] or [build] settings) is preserved.

        :param env: Environment dict for this package
        """
        pkg_dir = Path(self.context.pkg.path).resolve()
        # write the full patch set so that `cargo metadata` can
        # resolve every possible dependency of the package.
        write_package_patch_config(package_paths, pkg_dir)

        # let cargo report which of those patches are actually part
        # of the resolved dependency graph.
        used = find_used_packages(pkg_dir, package_paths, env)

        # rewrite the table with only the patches that are used.
        used_paths = {name: package_paths[name] for name in used}
        write_package_patch_config(used_paths, pkg_dir)

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


def write_cargo_config_toml(package_paths, config_base=None):
    """Write the resolved package paths to config.toml.

    :param package_paths: A mapping of package names to paths
    :param config_base: Directory in which to create the .cargo/config.toml.
        Defaults to the current working directory (the workspace root).
    """
    patches = {pkg: {'path': str(path)} for pkg, path in package_paths.items()}
    content = {'patch': {'crates-io': patches}}
    if config_base is None:
        config_base = Path.cwd()
    config_dir = Path(config_base) / '.cargo'
    config_dir.mkdir(parents=True, exist_ok=True)
    cargo_config_toml_out = config_dir / 'config.toml'
    with cargo_config_toml_out.open('w') as toml_file:
        toml.dump(content, toml_file)


def write_package_patch_config(package_paths, config_base):
    """Replace the [patch.crates-io] table in a package's config.toml.

    Only the [patch.crates-io] table is rewritten, with exactly the given
    patches. The whole table is replaced (never merged entry-by-entry), so
    patches written by a previous build, including ones for dependencies
    that have since been removed from the environment, do not linger. Every
    other part of an existing config.toml is preserved as-is, including other
    tables (e.g. [target.*], [build]), patches for non-crates.io sources, and
    comments. config.toml is created if it does not exist.

    :param package_paths: A mapping of package names to paths
    :param config_base: Directory that contains the .cargo directory
    """
    # Imported lazily so that the extension still loads (for the default,
    # workspace-wide mode) if tomlkit is not installed.
    import tomlkit

    config_dir = Path(config_base) / '.cargo'
    config_dir.mkdir(parents=True, exist_ok=True)
    config_toml = config_dir / 'config.toml'

    if config_toml.exists():
        document = tomlkit.parse(config_toml.read_text())
    else:
        document = tomlkit.document()

    patch = document.get('patch')
    if patch is None:
        patch = tomlkit.table()
        document['patch'] = patch

    crates_io = tomlkit.table()
    for name in sorted(package_paths):
        entry = tomlkit.table()
        entry['path'] = str(package_paths[name])
        crates_io[name] = entry

    patch['crates-io'] = crates_io
    config_toml.write_text(tomlkit.dumps(document))


def find_used_packages(pkg_dir, package_paths, env):
    """Find which patched packages are in the resolved dependency graph.

    Runs `cargo metadata`, which resolves dependencies using the patches
    written to ``pkg_dir``, and returns the subset of ``package_paths`` that
    actually appears in the resolved graph. For a Cargo workspace this is the
    union across all of its members. If `cargo metadata` fails, all known
    packages are returned so that the build can still proceed.

    :param pkg_dir: The package (or Cargo workspace) directory
    :param package_paths: A mapping of package names to paths (the universe of
        packages that can be patched)
    :param env: Environment dict for this package
    :returns: The set of package names that are actually used
    :rtype: set(str)
    """
    cmd = [CARGO_EXECUTABLE, 'metadata', '--format-version', '1']
    result = subprocess.run(
        cmd, cwd=str(pkg_dir), capture_output=True, env=env)

    if result.returncode != 0:
        logger.warning(
            "'cargo metadata' failed while computing the patch set for "
            "'{}'; falling back to patching all discovered packages.\n{}"
            .format(pkg_dir, result.stderr.decode(errors='replace')))
        return set(package_paths)

    metadata = json.loads(result.stdout.decode())
    return used_packages_from_metadata(metadata, package_paths)


def used_packages_from_metadata(metadata, package_paths):
    """Extract the used subset of ``package_paths`` from cargo metadata.

    Members of the current Cargo workspace are excluded: they are resolved
    locally by path rather than pulled from crates.io, so patching them would
    only add an unused [[patch]] entry (and a package must not patch itself).

    :param metadata: Parsed `cargo metadata` output
    :param package_paths: A mapping of package names to paths
    :returns: The set of package names present in the resolved graph
    :rtype: set(str)
    """
    id_to_name = {
        pkg['id']: pkg['name'] for pkg in metadata.get('packages', [])}

    workspace_members = {
        id_to_name.get(member)
        for member in metadata.get('workspace_members', [])}

    resolve = metadata.get('resolve') or {}

    resolved_names = {
        id_to_name.get(node['id']) for node in resolve.get('nodes', [])}

    return {
        name for name in resolved_names
        if name in package_paths and name not in workspace_members}


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
            except toml.decoder.TomlDecodeError:
                continue
            # Virtual manifests (a [workspace] with no [package]) have no
            # name and cannot be patched; skip them.
            name = cargo_toml.get('package', {}).get('name')
            if name is not None:
                path_for_package[name] = dirpath
    return path_for_package
