# Licensed under the Apache License, Version 2.0

from flake8.api import legacy as flake8
from pathlib import Path

style_guide = flake8.get_style_guide(ignore=['D100', 'D104'], show_source=True)
plugin_path = str(Path(__file__).parents[1] / 'colcon_ros_cargo')
report = style_guide.check_files([plugin_path])
assert report.get_statistics('E') == [], 'Flake8 found violations'
