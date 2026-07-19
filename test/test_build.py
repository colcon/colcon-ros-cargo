# Copyright 2024 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

import asyncio
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import xml.etree.ElementTree as eTree

from colcon_core.event_handler.console_direct import ConsoleDirectEventHandler
from colcon_core.package_descriptor import PackageDescriptor
from colcon_core.subprocess import new_event_loop
from colcon_core.task import TaskContext
from colcon_ros.package_identification.ros import RosPackageIdentification
from colcon_ros_cargo.task.ament_cargo import build as ament_cargo_build
from colcon_ros_cargo.task.ament_cargo.build import AmentCargoBuildTask
from colcon_ros_cargo.task.ament_cargo.test import AmentCargoTestTask
import pytest
import toml

TEST_PACKAGE_NAME = 'rust-sample-package'

test_project_path = Path(__file__).parent / TEST_PACKAGE_NAME


@pytest.fixture(autouse=True)
def monkey_patch_put_event_into_queue(monkeypatch):
    event_handler = ConsoleDirectEventHandler()
    monkeypatch.setattr(
        TaskContext,
        'put_event_into_queue',
        lambda self, event: event_handler((event, 'cargo')),
    )


def test_package_identification():
    cpi = RosPackageIdentification()
    desc = PackageDescriptor(test_project_path)
    cpi.identify(desc)
    assert desc.type == 'ros.ament_cargo'
    assert desc.name == TEST_PACKAGE_NAME


@pytest.mark.skipif(
    not shutil.which('cargo'),
    reason='Rust must be installed to run this test')
def test_build_and_test_package():
    event_loop = new_event_loop()
    asyncio.set_event_loop(event_loop)

    try:
        cpi = RosPackageIdentification()
        package = PackageDescriptor(test_project_path)
        cpi.identify(package)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # TODO(luca) Also test clean build and cargo args
            context = TaskContext(pkg=package,
                                  args=SimpleNamespace(
                                      path=str(test_project_path),
                                      build_base=str(tmpdir / 'build'),
                                      install_base=str(tmpdir / 'install'),
                                      clean_build=None,
                                      cargo_args=None,
                                      lookup_in_workspace=None,
                                      cargo_config_per_package=None,
                                  ),
                                  dependencies={}
                                  )

            task = AmentCargoBuildTask()
            task.set_context(context=context)

            src_base = test_project_path / 'src'

            source_files_before = set(src_base.rglob('*'))
            rc = event_loop.run_until_complete(task.build())
            assert not rc
            source_files_after = set(src_base.rglob('*'))
            assert source_files_before == source_files_after

            # Make sure the binary is compiled
            install_base = Path(task.context.args.install_base)
            app_name = TEST_PACKAGE_NAME
            executable = TEST_PACKAGE_NAME
            # Executable in windows have a .exe extension
            if os.name == 'nt':
                executable += '.exe'
            assert (install_base / 'lib' / app_name / executable).is_file()

            # Now compile tests
            task = AmentCargoTestTask()
            task.set_context(context=context)

            # Expect tests to have failed but return code will still be 0
            # since testing run succeeded
            rc = event_loop.run_until_complete(task.test())
            assert not rc
            build_base = Path(task.context.args.build_base)

            # Make sure the testing files are built
            assert (build_base / 'debug' / 'deps').is_dir()
            assert len(os.listdir(build_base / 'debug' / 'deps')) > 0
            result_file_path = build_base / 'cargo_test.xml'
            assert result_file_path.is_file()
            check_result_file(result_file_path)

    finally:
        event_loop.close()


def test_used_packages_from_metadata():
    # Only packages that are both in the resolved graph and in the known
    # package paths should be returned. Excluded cases:
    #   - serde: in the graph but not a known (ROS) package
    #   - turtlesim: a known package but not in the resolved graph (would be
    #     an unused patch)
    #   - my_pkg: in the graph and known, but a workspace member -> resolved
    #     locally, so it must not be patched
    package_paths = {
        'std_msgs': '/prefix/share/std_msgs/rust',
        'builtin_interfaces': '/prefix/share/builtin_interfaces/rust',
        'turtlesim': '/prefix/share/turtlesim/rust',
        'my_pkg': '/workspace/my_pkg',
    }
    metadata = {
        'packages': [
            {'id': 'std_msgs@1', 'name': 'std_msgs'},
            {'id': 'builtin_interfaces@1', 'name': 'builtin_interfaces'},
            {'id': 'serde@1', 'name': 'serde'},
            {'id': 'turtlesim@1', 'name': 'turtlesim'},
            {'id': 'my_pkg@1', 'name': 'my_pkg'},
        ],
        'workspace_members': ['my_pkg@1'],
        'resolve': {
            'nodes': [
                {'id': 'std_msgs@1'},
                {'id': 'builtin_interfaces@1'},
                {'id': 'serde@1'},
                {'id': 'my_pkg@1'},
            ],
        },
    }
    used = ament_cargo_build.used_packages_from_metadata(
        metadata, package_paths)
    assert used == {'std_msgs', 'builtin_interfaces'}


@pytest.mark.skipif(
    not shutil.which('cargo'),
    reason='Rust must be installed to run this test')
def test_cargo_config_per_package():
    # With --cargo-config-per-package the config is written into the package
    # source directory (not the workspace root) and only contains patches the
    # package actually resolves.
    ament_cargo_build.package_paths = None

    cpi = RosPackageIdentification()
    package = PackageDescriptor(test_project_path)
    cpi.identify(package)

    cargo_dir = test_project_path / '.cargo'
    lock_file = test_project_path / 'Cargo.lock'
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            context = TaskContext(
                pkg=package,
                args=SimpleNamespace(
                    path=str(test_project_path),
                    build_base=str(tmpdir / 'build'),
                    install_base=str(tmpdir / 'install'),
                    clean_build=None,
                    cargo_args=None,
                    lookup_in_workspace=None,
                    cargo_config_per_package=True,
                ),
                dependencies={},
            )
            task = AmentCargoBuildTask()
            task.set_context(context=context)

            rc = task._prepare(dict(os.environ), [])
            assert not rc

            config_file = cargo_dir / 'config.toml'
            assert config_file.is_file()
            patches = toml.load(config_file).get(
                'patch', {}).get('crates-io', {})
            # The sample package has no ROS dependencies, so it must not pull
            # in patches for itself or any unrelated package.
            assert TEST_PACKAGE_NAME not in patches
    finally:
        if cargo_dir.exists():
            shutil.rmtree(cargo_dir)
        if lock_file.exists():
            lock_file.unlink()
        ament_cargo_build.package_paths = None


def test_write_package_patch_config(tmp_path):
    config_toml = tmp_path / '.cargo' / 'config.toml'

    # 1. No config.toml -> created with just the patch table
    ament_cargo_build.write_package_patch_config(
        {'std_msgs': '/prefix/std_msgs'}, tmp_path)
    patches = toml.load(config_toml)['patch']['crates-io']
    assert set(patches) == {'std_msgs'}
    assert patches['std_msgs']['path'] == '/prefix/std_msgs'

    # 2. Existing unrelated content is preserved, comments survive
    config_toml.write_text(
        '# hand written\n'
        '[target.aarch64-unknown-linux-gnu]\n'
        'linker = "aarch64-linux-gnu-gcc"\n')
    ament_cargo_build.write_package_patch_config(
        {'std_msgs': '/p/std_msgs', 'geometry_msgs': '/p/geometry_msgs'},
        tmp_path)
    text = config_toml.read_text()
    assert '# hand written' in text
    content = toml.load(config_toml)
    assert content['target']['aarch64-unknown-linux-gnu']['linker'] == \
        'aarch64-linux-gnu-gcc'
    assert set(content['patch']['crates-io']) == {'std_msgs', 'geometry_msgs'}

    # 3. Removed dependency: the whole table is rewritten, so a patch from a
    #    previous build that is no longer requested is dropped entirely
    ament_cargo_build.write_package_patch_config(
        {'std_msgs': '/p/std_msgs'}, tmp_path)
    content = toml.load(config_toml)
    assert set(content['patch']['crates-io']) == {'std_msgs'}
    # unrelated content still preserved
    assert 'target' in content


# Check the testing result file, expect cargo test and doc test to fail
# but fmt to succeed
def check_result_file(path):
    tree = eTree.parse(path)
    root = tree.getroot()
    testsuite = root.find('testsuite')
    assert testsuite is not None
    unit_result = testsuite.find("testcase[@name='unit']")
    assert unit_result is not None
    assert unit_result.find('failure') is not None
    fmt_result = testsuite.find("testcase[@name='fmt']")
    assert fmt_result is not None
    assert fmt_result.find('failure') is None
