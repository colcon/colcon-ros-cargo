import pytest
from pathlib import Path
import toml
import os

from colcon_ros_cargo.task.ament_cargo.build import write_cargo_config_toml


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)


def test_write_cargo_config_toml_creates_new_file(temp_workspace):
    """Test that config.toml is created when it doesn't exist."""
    package_paths = {
        "my_package": Path("/path/to/my_package"),
        "other_package": Path("/path/to/other_package"),
    }

    write_cargo_config_toml(package_paths)

    config_file = temp_workspace / ".cargo" / "config.toml"
    assert config_file.exists()

    with config_file.open("r") as f:
        content = toml.load(f)

    assert "patch" in content
    assert "crates-io" in content["patch"]
    assert content["patch"]["crates-io"]["my_package"] == {
        "path": "/path/to/my_package"
    }
    assert content["patch"]["crates-io"]["other_package"] == {
        "path": "/path/to/other_package"
    }


def test_write_cargo_config_toml_merges_entries(temp_workspace):
    """Test that the it merges its changes and preserves existing config."""
    config_dir = temp_workspace / ".cargo"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.toml"

    existing_content = {
        "build": {
            "target": "x86_64-unknown-linux-gnu",
            "jobs": 4,
        },
        "patch": {
            "crates-io": {
                "existing_package": {"path": "/existing/path"},
            }
        },
    }

    with config_file.open("w") as f:
        toml.dump(existing_content, f)

    package_paths = {
        "new_package": Path("/path/to/new_package"),
    }

    write_cargo_config_toml(package_paths)

    with config_file.open("r") as f:
        content = toml.load(f)

    # Existing config is preserved
    assert content["build"]["target"] == "x86_64-unknown-linux-gnu"
    assert content["build"]["jobs"] == 4

    # Old crates-io patch is removed
    assert "existing_package" not in content["patch"]["crates-io"]

    # New crates-io patch is present
    assert content["patch"]["crates-io"]["new_package"] == {
        "path": "/path/to/new_package"
    }


def test_write_cargo_config_toml_updates_existing_patch(temp_workspace):
    """Test that updating an existing patch overwrites it."""
    config_dir = temp_workspace / ".cargo"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.toml"

    existing_content = {
        "patch": {
            "crates-io": {
                "my_package": {"path": "/old/path"},
            }
        }
    }

    with config_file.open("w") as f:
        toml.dump(existing_content, f)

    package_paths = {
        "my_package": Path("/new/path"),
    }

    write_cargo_config_toml(package_paths)

    with config_file.open("r") as f:
        content = toml.load(f)

    assert content["patch"]["crates-io"]["my_package"] == {"path": "/new/path"}
