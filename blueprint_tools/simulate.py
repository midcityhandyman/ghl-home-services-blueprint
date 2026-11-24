"""Dry run a workflow against a synthetic contact. Nothing is sent anywhere.

A scenario is a small YAML file: which workflow to run, the starting
contact, and optional changes that happen while a wait step is sleeping
(for example the customer replies, or staff move the deal to Won).
"""

from __future__ import annotations

import copy
import re

from .validator import MERGE_RE, next_steps

BUSINESS = {
    "name": "Sample Home Services",
    "phone": "555-0100",
    "review_link": "https://example.com/review",
}
TODAY = "2026-01-15"  # fixed so runs are repeatable
MINUTES = {"m": 1, "h": 60, "d": 1440}
MAX_STEPS = 200


def new_contact(data=None):
    contact = {
        "first_name": "Alex",
        "last_name": "Sample",
        "phone": "555-0101",
        "email": "alex.sample@example.com",
        "tags": [],
        "fields": {},
        "stages": {},
        "replied": False,
    }
    contact.update(copy.deepcopy(data or {}))
    contact["tags"] = set(contact["tags"])
    return contact


def render(text, contact):
    def swap(match):
        token = match.group(1)
        scope, _, name = token.partition(".")
        if token == "today":
            return TODAY
        if scope == "contact":
            return str(contact.get(name, ""))
        if scope == "business":
            return BUSINESS.get(name, "")
        if scope == "custom":
            return str(contact["fields"].get(name, f"[{name} not set]"))
        return match.group(0)

    return MERGE_RE.sub(swap, str(text))


def evaluate(cond, contact):
    if "has_tag" in cond:
        return cond["has_tag"] in contact["tags"]
    if "in_stage" in cond:
        return contact["stages"].get(cond["in_stage"]["pipeline"]) == cond["in_stage"]["stage"]
    if "field" in cond:
        value = contact["fields"].get(cond["field"])
        if "is_set" in cond:
            return (value is not None) == cond["is_set"]
        return value == cond["equals"]
    return contact["replied"] == cond["replied"]


def apply_changes(changes, contact):
    """Apply things that happened in the outside world during a wait."""
    notes = []
    for tag in changes.get("add_tags", []):
        contact["tags"].add(tag)
        notes.append(f"tag {tag} added")
    for key, value in changes.get("set_fields", {}).items():
        contact["fields"][key] = value
        notes.append(f"{key} set to {value}")
    for pipeline, stage in changes.get("move_stages", {}).items():
        contact["stages"][pipeline] = stage
        notes.append(f"moved to {pipeline}.{stage}")
    if "replied" in changes:
        contact["replied"] = changes["replied"]
        notes.append("contact replied" if changes["replied"] else "no reply")
    return notes


def clock(minutes):
    days, rest = divmod(minutes, 1440)
    return f"day {days} {rest // 60:02d}:{rest % 60:02d}"


def run(wf, contact=None, during_wait=None):
    """Walk the workflow. Returns (log lines, final contact)."""
    contact = new_contact(contact)
    during_wait = during_wait or {}
    steps = wf["steps"]
    index_of = {s["id"]: i for i, s in enumerate(steps)}
    wait_ids = {s["id"] for s in steps if s["type"] == "wait"}
    unknown = sorted(set(during_wait) - wait_ids)
    if unknown:
        raise ValueError(f"during_wait names {unknown}, which are not wait steps in {wf['id']}")
    trigger = wf["trigger"]
    if trigger["type"] == "stage_entered":
        contact["stages"][trigger["pipeline"]] = trigger["stage"]
    elif trigger["type"] == "tag_added":
        contact["tags"].add(trigger["tag"])
    minutes = 0
    log = []

    def say(step_id, text):
        log.append(f"[{clock(minutes)}] {step_id:<20} {text}")

    current = steps[0]["id"]
    for _ in range(MAX_STEPS):
        step = steps[index_of[current]]
        kind = step["type"]
        targets = next_steps(step, index_of[current], steps)
        if kind == "add_tag":
            contact["tags"].add(step["tag"])
            say(current, f"add tag {step['tag']}")
        elif kind == "remove_tag":
            contact["tags"].discard(step["tag"])
            say(current, f"remove tag {step['tag']}")
        elif kind == "set_field":
            value = step["value"]  # only text is rendered, so a number stays a number
            contact["fields"][step["field"]] = render(value, contact) if isinstance(value, str) else value
            say(current, f"set {step['field']} = {contact['fields'][step['field']]}")
        elif kind == "move_stage":
            contact["stages"][step["pipeline"]] = step["stage"]
            say(current, f"move to {step['pipeline']}.{step['stage']}")
        elif kind == "send_sms":
            say(current, f"SMS to {contact['phone']}: {render(step['message'], contact)}")
        elif kind == "send_email":
            subject = render(step["subject"], contact)
            say(current, f"EMAIL to {contact['email']}: [{subject}] {render(step['message'], contact)}")
        elif kind == "notify_team":
            say(current, f"NOTIFY team: {render(step['message'], contact)}")
        elif kind == "create_task":
            due = f" (due in {step['due_in']})" if "due_in" in step else ""
            say(current, f"TASK: {render(step['title'], contact)}{due}")
        elif kind == "wait":
            amount, unit = re.match(r"(\d+)([mhd])", step["duration"]).groups()
            say(current, f"wait {step['duration']}")
            minutes += int(amount) * MINUTES[unit]
            for note in apply_changes(during_wait.get(current, {}), contact):
                say("(meanwhile)", note)
        elif kind == "condition":
            result = evaluate(step["if"], contact)
            say(current, f"{step.get('name', 'condition')} {'yes' if result else 'no'}")
            targets = [targets[0] if result else targets[1]]
        elif kind == "end":
            say(current, "end")
        if not targets:
            return log, contact
        current = targets[0]
    raise RuntimeError("step limit reached; the workflow probably loops")


def summary(contact):
    stages = ", ".join(f"{p}.{s}" for p, s in sorted(contact["stages"].items())) or "none"
    fields = ", ".join(f"{k}={v}" for k, v in sorted(contact["fields"].items())) or "none"
    return [
        f"tags:   {', '.join(sorted(contact['tags'])) or 'none'}",
        f"stages: {stages}",
        f"fields: {fields}",
    ]
