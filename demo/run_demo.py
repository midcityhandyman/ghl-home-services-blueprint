"""Offline demo: validate, catch a broken edit, dry run each workflow.

Run from the repo root:  python demo/run_demo.py
No network, no keys. All contacts are synthetic.
"""

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blueprint_tools import load_blueprint, validate  # noqa: E402
from blueprint_tools.cli import main  # noqa: E402


def heading(text):
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def broken_copy(bp):
    """The kind of edits that slip in when someone renames things in a hurry."""
    bad = copy.deepcopy(bp)
    wf = bad.workflow("estimate_follow_up")
    steps = {s["id"]: s for s in wf["steps"]}
    wf["trigger"]["stage"] = "quote_sent"  # stage was renamed
    steps["tag_pending"]["tag"] = "estimate-open"  # tag never created
    steps["sms_check_in"]["message"] = "Your quote for ${{custom.quote_total}} is ready."
    steps["wait_2d"]["duration"] = "2 days"
    steps["still_open_3"]["else"] = "clean_up"  # typo in a step id
    bad.workflow("new_lead")["steps"][1]["value"] = "website"  # not a dropdown option
    return bad


def run():
    heading("1. Validate the blueprint as shipped")
    code = main(["validate"])
    print(f"exit code: {code}")

    heading("2. Validate a deliberately broken copy (in memory, files untouched)")
    issues = validate(broken_copy(load_blueprint()))
    for issue in issues:
        print(issue)
    print(f"{sum(1 for i in issues if i.level == 'error')} errors caught.")

    heading("3. Dry run every workflow with a synthetic contact")
    for scenario in sorted((ROOT / "demo" / "scenarios").glob("*.yaml")):
        print()
        main(["simulate", str(scenario)])

    heading("4. Check the generated docs match the definitions")
    code = main(["docs", "--check"])
    print(f"exit code: {code}")


if __name__ == "__main__":
    run()
