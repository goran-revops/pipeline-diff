import subprocess
import sys

from pipeline_diff import __version__


def test_version_runs_as_a_command():
    done = subprocess.run([sys.executable, "-m", "pipeline_diff", "--version"], capture_output=True, text=True)
    assert done.returncode == 0
    assert done.stdout.strip() == f"pipeline-diff {__version__}"


def test_every_code_folder_ships_in_the_package():
    from pathlib import Path

    package = Path(__file__).parent.parent / "pipeline_diff"
    folders = {p.parent for p in package.rglob("*.py") if "__pycache__" not in p.parts}
    assert all((folder / "__init__.py").exists() for folder in folders), "a folder without __init__.py is left out of the wheel"
