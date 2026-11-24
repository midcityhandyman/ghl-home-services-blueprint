"""Check that every workflow only references things the blueprint defines.

Errors mean the design is inconsistent. Warnings point at loose ends
(for example a tag that nothing uses) and do not fail the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# step type -> required keys
STEP_TYPES = {
    "add_tag": ["tag"],
    "remove_tag": ["tag"],
    "set_field": ["field", "value"],
    "move_stage": ["pipeline", "stage"],
    "send_sms": ["message"],
    "send_email": ["subject", "message"],
    "notify_team": ["message"],
    "create_task": ["title"],
    "wait": ["duration"],
    "condition": ["if", "then", "else"],
    "end": [],
}
TRIGGER_TYPES = {
    "form_submitted": ["form"],
    "missed_call": [],
    "stage_entered": ["pipeline", "stage"],
    "tag_added": ["tag"],
}
FIELD_TYPES = {"text", "number", "date", "dropdown"}
CONTACT_FIELDS = {"first_name", "last_name", "phone", "email"}
BUSINESS_FIELDS = {"name", "phone", "review_link"}
SYSTEM_TOKENS = {"today"}
TEXT_KEYS = ("message", "subject", "title", "value")
CONDITION_KINDS = ("has_tag", "field", "in_stage", "replied")

DURATION_RE = re.compile(r"^[1-9]\d*[mhd]$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MERGE_RE = re.compile(r"\{\{\s*([^}]*?)\s*\}\}")
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Issue:
    level: str  # "error" or "warning"
    where: str
    message: str

    def __str__(self):
        return f"{self.level.upper():7} {self.where}: {self.message}"


def next_steps(step, index, steps):
    """Ids a step can flow to. Falling off the end of the list ends the run."""
    kind = step.get("type")
    if kind == "end":
        return []
    if kind == "condition":
        return [step.get("then"), step.get("else")]
    if "next" in step:
        return [step["next"]]
    if index + 1 < len(steps):
        return [steps[index + 1].get("id")]
    return []


class _Checker:
    def __init__(self, blueprint):
        self.bp = blueprint
        self.issues = []
        self.fields = blueprint.field_map()
        self.tags = blueprint.tag_names()
        self.used_tags = set()
        self.used_fields = set()
        self.added_tags = set()

    def err(self, where, message):
        self.issues.append(Issue("error", where, message))

    def warn(self, where, message):
        self.issues.append(Issue("warning", where, message))

    # reference checks -----------------------------------------------------

    def check_tag(self, where, tag):
        self.used_tags.add(tag)
        if tag not in self.tags:
            self.err(where, f"tag {tag!r} is not defined in tags.yaml")

    def check_field(self, where, key):
        self.used_fields.add(key)
        if key not in self.fields:
            self.err(where, f"field {key!r} is not defined in fields.yaml")
            return None
        return self.fields[key]

    def check_stage(self, where, pipeline, stage):
        stages = self.bp.stage_ids(pipeline)
        if stages is None:
            self.err(where, f"pipeline {pipeline!r} is not defined in pipelines.yaml")
        elif stage not in stages:
            self.err(where, f"stage {stage!r} is not defined in pipeline {pipeline!r}")

    def check_merge_fields(self, where, text):
        for token in MERGE_RE.findall(str(text)):
            scope, _, name = token.partition(".")
            if scope == "custom":
                self.check_field(where, name)
            elif scope == "contact" and name in CONTACT_FIELDS:
                continue
            elif scope == "business" and name in BUSINESS_FIELDS:
                continue
            elif token in SYSTEM_TOKENS:
                continue
            else:
                self.err(where, f"unknown merge field {{{{{token}}}}}")

    def check_value(self, where, spec, value):
        kind, key = spec.get("type"), spec.get("key")
        if kind == "dropdown" and value not in spec.get("options", []):
            self.err(where, f"value {value!r} is not an option of dropdown field {key!r}")
        elif kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            self.err(where, f"field {key!r} is a number but value is {value!r}")
        elif kind == "date" and value != "{{today}}" and not DATE_RE.match(str(value)):
            self.err(where, f"field {key!r} is a date; use YYYY-MM-DD or {{{{today}}}}")

    # definitions ----------------------------------------------------------

    def check_definitions(self):
        self._unique("pipelines.yaml", [p.get("id") for p in self.bp.pipelines], "pipeline id")
        for pipeline in self.bp.pipelines:
            where = f"pipelines.yaml:{pipeline.get('id')}"
            stages = [s.get("id") for s in pipeline.get("stages", [])]
            if not stages:
                self.err(where, "pipeline has no stages")
            self._unique(where, stages, "stage id")
        self._unique("fields.yaml", [f.get("key") for f in self.bp.fields], "field key")
        for spec in self.bp.fields:
            where = f"fields.yaml:{spec.get('key')}"
            if spec.get("type") not in FIELD_TYPES:
                self.err(where, f"unknown field type {spec.get('type')!r}")
            if spec.get("type") == "dropdown" and not spec.get("options"):
                self.err(where, "dropdown field has no options")
        self._unique("tags.yaml", [t.get("name") for t in self.bp.tags], "tag name")
        self._unique("workflows", [w.get("id") for w in self.bp.workflows], "workflow id")

    def _unique(self, where, values, label):
        seen = set()
        for value in values:
            if not value:
                self.err(where, f"missing {label}")
            elif value in seen:
                self.err(where, f"duplicate {label} {value!r}")
            seen.add(value)

    def _missing(self, where, mapping, keys):
        missing = [k for k in keys if k not in mapping]
        for key in missing:
            self.err(where, f"missing required key {key!r}")
        return bool(missing)

    # workflows ------------------------------------------------------------

    def check_workflow(self, wf):
        name = wf.get("id") or wf.get("_file", "?")
        self.check_trigger(f"{name}:trigger", wf.get("trigger") or {})
        steps = wf.get("steps") or []
        if not steps:
            self.err(name, "workflow has no steps")
            return
        ids = [s.get("id") for s in steps]
        self._unique(name, ids, "step id")
        known = set(ids)
        for index, step in enumerate(steps):
            where = f"{name}:{step.get('id')}"
            if step.get("id") and not ID_RE.match(str(step["id"])):
                self.err(where, "step id must be snake_case")
            self.check_step(where, step)
            for target in next_steps(step, index, steps):
                if target not in known:
                    self.err(where, f"points to step {target!r}, which does not exist")
        self.check_graph(name, steps)

    def check_trigger(self, where, trigger):
        kind = trigger.get("type")
        if kind not in TRIGGER_TYPES:
            self.err(where, f"unknown trigger type {kind!r}")
            return
        if self._missing(where, trigger, TRIGGER_TYPES[kind]):
            return
        if kind == "stage_entered":
            self.check_stage(where, trigger["pipeline"], trigger["stage"])
        elif kind == "tag_added":
            self.check_tag(where, trigger["tag"])

    def check_step(self, where, step):
        kind = step.get("type")
        if kind not in STEP_TYPES:
            self.err(where, f"unknown step type {kind!r}")
            return
        if self._missing(where, step, STEP_TYPES[kind]):
            return
        for key in TEXT_KEYS:
            if key in step:
                self.check_merge_fields(where, step[key])
        if kind in ("add_tag", "remove_tag"):
            self.check_tag(where, step["tag"])
            if kind == "add_tag":
                self.added_tags.add(step["tag"])
        elif kind == "set_field":
            spec = self.check_field(where, step["field"])
            if spec:
                self.check_value(where, spec, step["value"])
        elif kind == "move_stage":
            self.check_stage(where, step["pipeline"], step["stage"])
        elif kind == "wait":
            self.check_duration(where, step["duration"])
        elif kind == "condition":
            self.check_condition(where, step["if"])
        if "due_in" in step:
            self.check_duration(where, step["due_in"])

    def check_duration(self, where, value):
        if not DURATION_RE.match(str(value)):
            self.err(where, f"duration {value!r} must look like 15m, 2h or 3d")

    def check_condition(self, where, cond):
        if not isinstance(cond, dict) or sum(k in cond for k in CONDITION_KINDS) != 1:
            self.err(where, "condition needs exactly one of has_tag, field, in_stage, replied")
            return
        if "has_tag" in cond:
            self.check_tag(where, cond["has_tag"])
        elif "in_stage" in cond:
            target = cond["in_stage"] or {}
            if not self._missing(where, target, ["pipeline", "stage"]):
                self.check_stage(where, target["pipeline"], target["stage"])
        elif "field" in cond:
            spec = self.check_field(where, cond["field"])
            if ("equals" in cond) == ("is_set" in cond):
                self.err(where, "field condition needs exactly one of equals, is_set")
            elif spec and "equals" in cond:
                self.check_value(where, spec, cond["equals"])
        elif not isinstance(cond["replied"], bool):
            self.err(where, "replied must be true or false")

    def check_graph(self, name, steps):
        """Every step reachable from the first one, and no loops."""
        index_of = {s.get("id"): i for i, s in enumerate(steps)}
        edges = {
            s.get("id"): [t for t in next_steps(s, i, steps) if t in index_of]
            for i, s in enumerate(steps)
        }
        state = {}  # id -> 1 while on the stack, 2 when finished

        def visit(node):
            state[node] = 1
            for target in edges[node]:
                if state.get(target) == 1:
                    self.err(f"{name}:{node}", f"loops back to step {target!r}; workflows must not cycle")
                elif target not in state:
                    visit(target)
            state[node] = 2

        visit(steps[0].get("id"))
        for step in steps:
            if step.get("id") not in state:
                self.warn(f"{name}:{step.get('id')}", "step is unreachable")

    # cross checks ---------------------------------------------------------

    def check_loose_ends(self):
        for tag in sorted(self.tags - self.used_tags):
            self.warn(f"tags.yaml:{tag}", "tag is defined but no workflow uses it")
        for key in sorted(set(self.fields) - self.used_fields):
            self.warn(f"fields.yaml:{key}", "field is defined but no workflow uses it")
        for wf in self.bp.workflows:
            trigger = wf.get("trigger") or {}
            if trigger.get("type") == "tag_added" and trigger.get("tag") not in self.added_tags:
                self.warn(
                    f"{wf.get('id')}:trigger",
                    f"no workflow adds tag {trigger.get('tag')!r}; this only starts by hand",
                )


def validate(blueprint):
    """Return a list of Issue objects, errors first."""
    checker = _Checker(blueprint)
    checker.check_definitions()
    for wf in blueprint.workflows:
        checker.check_workflow(wf)
    checker.check_loose_ends()
    return sorted(checker.issues, key=lambda i: i.level != "error")


def has_errors(issues):
    return any(i.level == "error" for i in issues)
