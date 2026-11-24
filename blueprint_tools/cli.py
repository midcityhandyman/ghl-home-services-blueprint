"""Command line entry point: validate, docs, simulate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import mermaid, simulate
from .loader import load_blueprint
from .validator import has_errors, validate

DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "BLUEPRINT.md"


def cmd_validate(args):
    bp = load_blueprint(args.blueprint)
    issues = validate(bp)
    for issue in issues:
        print(issue)
    errors = sum(1 for i in issues if i.level == "error")
    stages = sum(len(p.get("stages", [])) for p in bp.pipelines)
    print(
        f"Checked {len(bp.workflows)} workflows against {len(bp.pipelines)} pipelines "
        f"({stages} stages), {len(bp.fields)} fields, {len(bp.tags)} tags: "
        f"{errors} errors, {len(issues) - errors} warnings."
    )
    if errors or (args.strict and issues):
        return 1
    return 0


def cmd_docs(args):
    bp = load_blueprint(args.blueprint)
    if has_errors(validate(bp)):
        print("Blueprint has errors. Run validate first.")
        return 1
    text = mermaid.render_docs(bp)
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{out.name} is out of date. Run: python -m blueprint_tools docs")
            return 1
        print(f"{out.name} is up to date.")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"Wrote {out.name} ({len(bp.workflows)} workflow diagrams, {len(bp.pipelines)} pipeline diagrams).")
    return 0


def cmd_simulate(args):
    bp = load_blueprint(args.blueprint)
    if has_errors(validate(bp)):
        print("Blueprint has errors. Run validate first.")
        return 1
    with open(args.scenario, "r", encoding="utf-8") as handle:
        scenario = yaml.safe_load(handle)
    try:
        wf = bp.workflow(scenario["workflow"])
        log, contact = simulate.run(wf, scenario.get("contact"), scenario.get("during_wait"))
    except (KeyError, ValueError) as exc:
        print(f"Scenario error: {exc.args[0]}")
        return 1
    print(f"Scenario: {scenario.get('name', args.scenario)}")
    print(f"Workflow: {wf['name']} ({wf['id']})")
    for line in log:
        print(f"  {line}")
    print("Final contact:")
    for line in simulate.summary(contact):
        print(f"  {line}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="blueprint_tools", description=__doc__)
    parser.add_argument("--blueprint", default=None, help="blueprint folder (default: ./blueprint)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate", help="check every reference in every workflow")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("docs", help="generate docs/BLUEPRINT.md with mermaid diagrams")
    p.add_argument("--out", default=str(DOCS_PATH))
    p.add_argument("--check", action="store_true", help="fail if the file is out of date")
    p.set_defaults(func=cmd_docs)
    p = sub.add_parser("simulate", help="dry run a scenario file, offline")
    p.add_argument("scenario")
    p.set_defaults(func=cmd_simulate)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"File not found: {exc.filename}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
