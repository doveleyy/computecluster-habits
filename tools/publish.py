"""Bind final release bytes to this repository's successful push workflow."""

import argparse
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from hp_devtools.manifest import MAX_MANIFEST_BYTES, parse_manifest


@dataclass(frozen=True)
class PublicationContext:
    repository: str
    source_commit: str
    run_id: int
    run_attempt: int
    branch: str = "main"
    workflow: str = ".github/workflows/publish.yml"

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "PublicationContext":
        repository = env.get("GITHUB_REPOSITORY", "")
        commit = env.get("GITHUB_SHA", "")
        run_id = env.get("GITHUB_RUN_ID", "")
        attempt = env.get("GITHUB_RUN_ATTEMPT", "")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or any(part in {".", ".."} for part in repository.split("/"))
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", attempt)
            or env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_REF_NAME") != "main"
            or env.get("GITHUB_WORKFLOW_REF")
            != repository + "/.github/workflows/publish.yml@refs/heads/main"
        ):
            raise ValueError(
                "Publication requires this repository's main push workflow"
            )
        return cls(repository, commit, int(run_id), int(attempt))


def encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def publish(
    template: Path,
    output: Path,
    image: str,
    env: Mapping[str, str],
) -> dict[str, object]:
    context = PublicationContext.from_environment(env)
    with template.open("rb") as source:
        manifest = parse_manifest(source.read(MAX_MANIFEST_BYTES + 1), template=True)
    manifest["source_commit"] = context.source_commit
    manifest["image"] = image
    files: dict[str, bytes] = {}
    if manifest.get("workloads"):
        raise ValueError("Habits does not publish compute workloads")
    raw_manifest = encoded(manifest)
    parse_manifest(raw_manifest)
    files["manifest.json"] = raw_manifest
    hashes = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())
    }
    proof: dict[str, object] = {
        "publication_version": 1,
        "repository": context.repository,
        "branch": context.branch,
        "workflow": context.workflow,
        "source_commit": context.source_commit,
        "run_id": context.run_id,
        "run_attempt": context.run_attempt,
        "manifest_sha256": hashes["manifest.json"],
        "assets": hashes,
    }
    files["publication.json"] = encoded(proof)
    if output.exists() or output.is_symlink():
        raise ValueError("Publication output must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".publication-", dir=output.parent) as directory:
        staging = Path(directory) / "assets"
        staging.mkdir()
        for name, raw in files.items():
            (staging / name).write_bytes(raw)
        staging.rename(output)
    return proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=Path("manifest.template.json"))
    parser.add_argument("--output", type=Path, default=Path("publication"))
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    publish(args.template, args.output, args.image, os.environ)


if __name__ == "__main__":
    main()
