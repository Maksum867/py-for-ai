"""Checks for the release-triggered PyPI workflow without publishing anything."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


@pytest.fixture
def workflow():
    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_only_published_release_triggers_workflow(workflow):
    assert workflow["on"] == {"release": {"types": ["published"]}}
    assert workflow["jobs"]["publish"]["needs"] == "build"
    assert workflow["jobs"]["build"]["needs"] == "verify"
    assert workflow["jobs"]["publish"]["permissions"]["id-token"] == "write"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="release build job uses Python 3.13")
@pytest.mark.parametrize(
    "tag,body,success",
    [
        ("v0.5.0", "Release notes", True),
        ("v0.5.0", "  ", False),
        ("v0.5.0", None, False),
        ("v0.4.0", "Release notes", False),
        ("0.5.0", "Release notes", False),
        ("v0.5.0", "$(touch exploit)", True),
    ],
)
def test_release_validation_script(workflow, tmp_path, tag, body, success):
    build = workflow["jobs"]["build"]
    step = next(s for s in build["steps"] if s.get("name") == "Check release description and tag")
    script = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.5.0"\n', encoding="utf-8")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"release": {"tag_name": tag, "body": body}}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_EVENT_PATH": str(event)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == success, result.stderr
    assert not (tmp_path / "exploit").exists()
