import hashlib
import json
from pathlib import Path

import pytest
from hp_devtools.manifest import parse_manifest

from tools.publish import publish


def context() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "example/habits",
        "GITHUB_SHA": "b" * 40,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_NAME": "main",
        "GITHUB_WORKFLOW_REF": (
            "example/habits/.github/workflows/publish.yml@refs/heads/main"
        ),
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
    }


def test_final_publication_binds_only_exact_identity_app_assets(tmp_path: Path) -> None:
    output = tmp_path / "publication"
    proof = publish(
        Path("manifest.template.json"),
        output,
        "ghcr.io/example/habits@sha256:" + "a" * 64,
        context(),
    )
    assert {p.name for p in output.iterdir()} == {"manifest.json", "publication.json"}
    manifest = parse_manifest((output / "manifest.json").read_bytes())
    assert manifest["capabilities"] == ["identity"]
    assert manifest["source_commit"] == "b" * 40
    assert proof["assets"] == {
        "manifest.json": hashlib.sha256(
            (output / "manifest.json").read_bytes()
        ).hexdigest()
    }
    assert json.loads((output / "publication.json").read_bytes()) == proof
    with pytest.raises(ValueError):
        publish(
            Path("manifest.template.json"),
            output,
            "ghcr.io/example/habits@sha256:" + "a" * 64,
            context(),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF", "refs/heads/other"),
        ("GITHUB_SHA", "b" * 39),
        ("GITHUB_RUN_ATTEMPT", "0"),
        (
            "GITHUB_WORKFLOW_REF",
            "example/other/.github/workflows/publish.yml@refs/heads/main",
        ),
    ],
)
def test_other_workflow_contexts_create_no_publication(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        publish(
            Path("manifest.template.json"),
            tmp_path / "publication",
            "ghcr.io/example/habits@sha256:" + "a" * 64,
            {**context(), field: value},
        )
    assert not (tmp_path / "publication").exists()
