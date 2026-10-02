"""Unit tests for Autonomous Software Forge (Module L)."""
from pathlib import Path
import zipfile
import pytest

from software_forge import (
    SoftwareForge,
    forge_application,
    validate_project,
    package_distribution_zip,
    get_forge_project,
)


@pytest.fixture
def forge(tmp_path):
    forge_root = tmp_path / "test_forge"
    return SoftwareForge(forge_root=forge_root)


def test_forge_web_application_scaffolding(forge):
    proj = forge.forge_application(
        project_name="stark_landing",
        app_type="web_landing",
        description="Stark Industries Arc Reactor Showcase",
    )
    assert proj["project_name"] == "stark_landing"
    assert "index.html" in proj["files"]
    assert "styles.css" in proj["files"]
    assert "app.js" in proj["files"]
    assert proj["preview_url"] is not None

    p_dir = Path(proj["project_dir"])
    assert (p_dir / "index.html").exists()
    assert (p_dir / "styles.css").exists()


def test_forge_custom_files_and_validation(forge):
    custom = {
        "server.py": "import os\nprint('Server ready')\n",
        "config.json": '{"port": 8000}',
    }
    proj = forge.forge_application(
        project_name="custom_api",
        app_type="api_service",
        custom_files=custom,
    )
    assert len(proj["files"]) == 2

    val = forge.validate_project("custom_api")
    assert val["success"] is True
    assert val["status"] == "validated"


def test_package_distribution_zip(forge):
    proj = forge.forge_application(
        project_name="portable_app",
        app_type="web_landing",
    )
    zip_res = forge.package_distribution_zip("portable_app")
    assert zip_res["success"] is True
    assert zip_res["status"] == "packaged"

    zip_path = Path(zip_res["zip_path"])
    assert zip_path.exists()
    assert zip_path.suffix == ".zip"

    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        assert "index.html" in namelist
        assert "styles.css" in namelist
