"""Load the blueprint YAML files into one plain object."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "blueprint"


@dataclass
class Blueprint:
    pipelines: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    workflows: list = field(default_factory=list)

    def stage_ids(self, pipeline_id):
        """Return the stage ids of a pipeline, or None if it does not exist."""
        for pipeline in self.pipelines:
            if pipeline.get("id") == pipeline_id:
                return [s.get("id") for s in pipeline.get("stages", [])]
        return None

    def field_map(self):
        return {f.get("key"): f for f in self.fields}

    def tag_names(self):
        return {t.get("name") for t in self.tags}

    def workflow(self, workflow_id):
        for wf in self.workflows:
            if wf.get("id") == workflow_id:
                return wf
        raise KeyError(f"no workflow with id {workflow_id!r}")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_blueprint(root=None):
    """Read pipelines.yaml, fields.yaml, tags.yaml and workflows/*.yaml.

    The folder is the argument, else the BLUEPRINT_DIR environment
    variable, else the blueprint folder that ships with this repo.
    """
    root = Path(root or os.environ.get("BLUEPRINT_DIR") or DEFAULT_ROOT)
    workflows = []
    for path in sorted((root / "workflows").glob("*.yaml")):
        wf = _read(path)
        wf["_file"] = path.name
        workflows.append(wf)
    return Blueprint(
        pipelines=_read(root / "pipelines.yaml").get("pipelines", []),
        fields=_read(root / "fields.yaml").get("fields", []),
        tags=_read(root / "tags.yaml").get("tags", []),
        workflows=workflows,
    )
