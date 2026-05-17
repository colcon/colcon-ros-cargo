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
from colcon_ros_cargo.task.ament_cargo.build import AmentCargoBuildTask
from colcon_ros_cargo.task.ament_cargo.build import (
    find_installed_cargo_packages,
)
from colcon_ros_cargo.task.ament_cargo.test import AmentCargoTestTask
import pytest

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


def _make_rust_prefix(root, pkg_names):
    """Create a fake ament prefix containing the given Rust package markers."""
    root = Path(root)
    rust_packages_dir = (
        root / 'share' / 'ament_index' / 'resource_index' / 'rust_packages'
    )
    rust_packages_dir.mkdir(parents=True)
    for pkg in pkg_names:
        (rust_packages_dir / pkg).touch()
        (root / 'share' / pkg / 'rust').mkdir(parents=True)
    return root


def _rust_path(prefix, pkg):
    return str(prefix / 'share' / pkg / 'rust')


def test_find_installed_cargo_packages_overlay_first_wins():
    """First entry in AMENT_PREFIX_PATH must win for shared package names."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        overlay = _make_rust_prefix(
            tmpdir / 'overlay', ['std_msgs', 'my_pkg'])
        distro = _make_rust_prefix(
            tmpdir / 'distro', ['std_msgs', 'other_pkg'])

        env = {'AMENT_PREFIX_PATH': os.pathsep.join(
            [str(overlay), str(distro)])}
        result = find_installed_cargo_packages(env)

        # std_msgs is in both prefixes; the overlay (listed first) wins.
        assert result['std_msgs'] == _rust_path(overlay, 'std_msgs')
        # Packages present in only one prefix resolve to that prefix.
        assert result['my_pkg'] == _rust_path(overlay, 'my_pkg')
        assert result['other_pkg'] == _rust_path(distro, 'other_pkg')


def test_find_installed_cargo_packages_empty_ament_prefix_path():
    """An unset AMENT_PREFIX_PATH must return an empty mapping."""
    assert find_installed_cargo_packages({}) == {}


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
