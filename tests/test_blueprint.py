import copy
import re
from pathlib import Path

import pytest
import yaml

from blueprint_tools import load_blueprint, validate
from blueprint_tools import mermaid, simulate
from blueprint_tools.cli import main

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_WORKFLOWS = {"new_lead", "missed_call", "estimate_follow_up", "job_complete", "review_ask"}


@pytest.fixture()
def bp():
    return copy.deepcopy(load_blueprint(ROOT / "blueprint"))


def step(bp, workflow_id, step_id):
    return next(s for s in bp.workflow(workflow_id)["steps"] if s["id"] == step_id)


def errors(bp):
    return [i.message for i in validate(bp) if i.level == "error"]


def warnings(bp):
    return [i.message for i in validate(bp) if i.level == "warning"]


# the shipped blueprint ------------------------------------------------------


def test_shipped_blueprint_is_clean(bp):
    assert validate(bp) == []


def test_all_five_workflows_exist(bp):
    assert {wf["id"] for wf in bp.workflows} == REQUIRED_WORKFLOWS


def test_cli_validate_strict_passes(capsys):
    assert main(["validate", "--strict"]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out


# the validator catches broken references ------------------------------------


def test_undefined_tag(bp):
    step(bp, "new_lead", "tag_new")["tag"] = "brand-new-lead"
    assert any("tag 'brand-new-lead' is not defined" in m for m in errors(bp))


def test_undefined_tag_in_condition(bp):
    step(bp, "review_ask", "opted_out")["if"] = {"has_tag": "dnc"}
    assert any("tag 'dnc' is not defined" in m for m in errors(bp))


def test_undefined_stage(bp):
    step(bp, "new_lead", "mark_contacted")["stage"] = "qualified"
    assert any("stage 'qualified' is not defined in pipeline 'sales'" in m for m in errors(bp))


def test_undefined_pipeline_in_trigger(bp):
    bp.workflow("job_complete")["trigger"]["pipeline"] = "production"
    assert any("pipeline 'production' is not defined" in m for m in errors(bp))


def test_undefined_field(bp):
    step(bp, "new_lead", "set_source")["field"] = "source"
    assert any("field 'source' is not defined" in m for m in errors(bp))


def test_undefined_field_inside_message(bp):
    step(bp, "job_complete", "thank_you")["message"] = "Thanks, {{custom.crew_lead}} says hi."
    assert any("field 'crew_lead' is not defined" in m for m in errors(bp))


def test_unknown_merge_field(bp):
    step(bp, "review_ask", "ask")["message"] = "Hi {{contact.nickname}}"
    assert any("unknown merge field {{contact.nickname}}" in m for m in errors(bp))


def test_dropdown_value_must_be_an_option(bp):
    step(bp, "missed_call", "set_source")["value"] = "walk_in"
    assert any("not an option of dropdown field 'lead_source'" in m for m in errors(bp))


def test_number_field_rejects_text(bp):
    bp.workflow("new_lead")["steps"].insert(
        0, {"id": "bad_amount", "type": "set_field", "field": "estimate_amount", "value": "lots"}
    )
    assert any("is a number" in m for m in errors(bp))


def test_bad_duration(bp):
    step(bp, "estimate_follow_up", "wait_2d")["duration"] = "2 days"
    assert any("duration '2 days'" in m for m in errors(bp))


def test_next_must_point_at_a_real_step(bp):
    step(bp, "new_lead", "mark_contacted")["next"] = "finished"
    assert any("points to step 'finished'" in m for m in errors(bp))


def test_condition_branch_must_point_at_a_real_step(bp):
    step(bp, "missed_call", "did_reply")["else"] = "nowhere"
    assert any("points to step 'nowhere'" in m for m in errors(bp))


def test_cycle_is_an_error(bp):
    step(bp, "new_lead", "flag_callback")["next"] = "wait_for_reply"
    assert any("must not cycle" in m for m in errors(bp))


def test_duplicate_step_id(bp):
    bp.workflow("review_ask")["steps"].append({"id": "done", "type": "end"})
    assert any("duplicate step id 'done'" in m for m in errors(bp))


def test_unknown_step_type_and_missing_key(bp):
    step(bp, "new_lead", "alert_office")["type"] = "send_fax"
    del step(bp, "new_lead", "instant_reply")["message"]
    found = errors(bp)
    assert any("unknown step type 'send_fax'" in m for m in found)
    assert any("missing required key 'message'" in m for m in found)


def test_condition_needs_exactly_one_test(bp):
    step(bp, "new_lead", "did_reply")["if"] = {"replied": True, "has_tag": "customer"}
    assert any("exactly one of" in m for m in errors(bp))


def test_unreachable_step_is_a_warning(bp):
    step(bp, "new_lead", "wait_for_reply")["next"] = "done"
    found = warnings(bp)
    assert any("unreachable" in m for m in found)
    assert errors(bp) == []


def test_unused_tag_and_field_are_warnings(bp):
    bp.tags.append({"name": "vip", "purpose": "unused"})
    bp.fields.append({"key": "gate_code", "name": "Gate Code", "type": "text"})
    found = warnings(bp)
    assert any("tag is defined but no workflow uses it" in m for m in found)
    assert any("field is defined but no workflow uses it" in m for m in found)


def test_tag_trigger_nobody_fires_is_a_warning(bp):
    step(bp, "job_complete", "tag_review_eligible")["tag"] = "customer"
    assert any("no workflow adds tag 'review-eligible'" in m for m in warnings(bp))


def test_cli_exit_code_on_broken_blueprint(tmp_path, capsys):
    import shutil

    shutil.copytree(ROOT / "blueprint", tmp_path / "bp")
    path = tmp_path / "bp" / "tags.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("needs-callback", "callback"), encoding="utf-8")
    assert main(["--blueprint", str(tmp_path / "bp"), "validate"]) == 1
    assert "tag 'needs-callback' is not defined" in capsys.readouterr().out


# diagrams and docs ----------------------------------------------------------


def test_workflow_diagram_has_every_step_and_branch(bp):
    for wf in bp.workflows:
        diagram = mermaid.workflow_diagram(wf)
        assert diagram.startswith("flowchart TD")
        for s in wf["steps"]:
            assert f"s_{s['id']}" in diagram
        conditions = sum(1 for s in wf["steps"] if s["type"] == "condition")
        assert diagram.count("-->|yes|") == conditions
        assert diagram.count("-->|no|") == conditions


def test_diagram_edges_only_use_declared_nodes(bp):
    for wf in bp.workflows:
        diagram = mermaid.workflow_diagram(wf)
        declared = {"trigger"} | {f"s_{s['id']}" for s in wf["steps"]}
        for line in diagram.splitlines():
            match = re.match(r"\s+(\w+) -->(?:\|\w+\|)? (\w+)$", line)
            if match:
                assert set(match.groups()) <= declared


def test_pipeline_diagram_lists_stages_in_order(bp):
    diagram = mermaid.pipeline_diagram(bp.pipelines[0])
    assert "sales_new_lead --> sales_contacted --> sales_estimate_scheduled" in diagram


def test_committed_docs_are_current(bp):
    committed = (ROOT / "docs" / "BLUEPRINT.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == mermaid.render_docs(bp)
    assert committed.count("```mermaid") == len(bp.workflows) + len(bp.pipelines)


# simulator ------------------------------------------------------------------


def test_new_lead_without_reply_gets_callback_flag(bp):
    log, contact = simulate.run(bp.workflow("new_lead"), {"fields": {"service_type": "plumbing"}})
    assert "needs-callback" in contact["tags"]
    assert contact["stages"]["sales"] == "new_lead"
    assert any("about plumbing" in line for line in log)


def test_new_lead_with_reply_moves_to_contacted(bp):
    _, contact = simulate.run(bp.workflow("new_lead"), None, {"wait_for_reply": {"replied": True}})
    assert contact["stages"]["sales"] == "contacted"
    assert "needs-callback" not in contact["tags"]


def test_missed_call_respects_do_not_contact(bp):
    log, contact = simulate.run(bp.workflow("missed_call"), {"tags": ["do-not-contact"]})
    assert not any("SMS" in line for line in log)
    assert contact["tags"] == {"do-not-contact"}


def test_estimate_follow_up_stops_when_deal_moves(bp):
    log, contact = simulate.run(
        bp.workflow("estimate_follow_up"), None, {"wait_2d": {"move_stages": {"sales": "won"}}}
    )
    assert not any("SMS" in line or "EMAIL" in line for line in log)
    assert "estimate-pending" not in contact["tags"]


def test_estimate_follow_up_runs_full_sequence(bp):
    log, contact = simulate.run(bp.workflow("estimate_follow_up"))
    assert sum("SMS" in line for line in log) == 1
    assert sum("EMAIL" in line for line in log) == 1
    assert "estimate-follow-up-done" in contact["tags"]
    assert log[-1].startswith("[day 12 00:00]")


def test_unhappy_customer_never_gets_review_ask(bp):
    changes = {"wait_2d": {"set_fields": {"job_satisfaction": "unhappy"}}}
    _, contact = simulate.run(bp.workflow("job_complete"), None, changes)
    assert "unhappy-customer" in contact["tags"]
    assert "review-eligible" not in contact["tags"]
    log, _ = simulate.run(bp.workflow("review_ask"), {"tags": ["unhappy-customer"]})
    assert not any("SMS" in line or "EMAIL" in line for line in log)


def test_neutral_customer_is_not_sent_to_the_review_ask(bp):
    changes = {"wait_2d": {"set_fields": {"job_satisfaction": "neutral"}}}
    log, contact = simulate.run(bp.workflow("job_complete"), None, changes)
    assert "review-eligible" not in contact["tags"]
    assert "unhappy-customer" not in contact["tags"]
    assert sum("TASK" in line for line in log) == 2  # invoice and check in, no overdue task


def test_happy_customer_is_handed_to_the_review_ask(bp):
    changes = {"wait_2d": {"set_fields": {"job_satisfaction": "happy"}}}
    _, contact = simulate.run(bp.workflow("job_complete"), None, changes)
    assert "review-eligible" in contact["tags"]


def test_promised_check_in_has_a_task_before_the_wait(bp):
    ids = [s["id"] for s in bp.workflow("job_complete")["steps"]]
    assert ids.index("thank_you") < ids.index("checkin_task") < ids.index("wait_2d")
    log, contact = simulate.run(bp.workflow("job_complete"))
    assert any("Check in is overdue" in line for line in log)  # nobody recorded feedback
    assert "review-eligible" not in contact["tags"]


def test_set_field_keeps_a_number_a_number(bp):
    wf = {
        "id": "t",
        "trigger": {"type": "missed_call"},
        "steps": [
            {"id": "set_amount", "type": "set_field", "field": "estimate_amount", "value": 4200},
            {"id": "big", "type": "condition", "if": {"field": "estimate_amount", "equals": 4200},
             "then": "yes", "else": "done"},
            {"id": "yes", "type": "add_tag", "tag": "customer"},
            {"id": "done", "type": "end"},
        ],
    }
    _, contact = simulate.run(wf)
    assert contact["fields"]["estimate_amount"] == 4200
    assert "customer" in contact["tags"]


def test_scenario_change_on_unknown_wait_step_is_rejected(bp):
    with pytest.raises(ValueError, match="wait_1h"):
        simulate.run(bp.workflow("new_lead"), None, {"wait_1h": {"replied": True}})


def test_cli_simulate_reports_scenario_errors(tmp_path, capsys):
    path = tmp_path / "s.yaml"
    path.write_text("workflow: new_lead\nduring_wait:\n  wait_1h: {replied: true}\n", encoding="utf-8")
    assert main(["simulate", str(path)]) == 1
    assert "Scenario error" in capsys.readouterr().out
    path.write_text("workflow: no_such_workflow\n", encoding="utf-8")
    assert main(["simulate", str(path)]) == 1
    assert "no workflow with id" in capsys.readouterr().out


def test_cli_missing_blueprint_folder_is_a_clean_error(tmp_path, capsys):
    assert main(["--blueprint", str(tmp_path / "nope"), "validate"]) == 2
    assert "File not found" in capsys.readouterr().out


def test_review_thanks_when_review_arrives(bp):
    log, _ = simulate.run(bp.workflow("review_ask"), None, {"wait_3d": {"add_tags": ["review-left"]}})
    assert any("Thank you for the review" in line for line in log)
    assert not any("EMAIL" in line for line in log)


def test_demo_scenarios_run_and_reference_real_workflows(bp, capsys):
    scenarios = sorted((ROOT / "demo" / "scenarios").glob("*.yaml"))
    assert {yaml.safe_load(p.read_text(encoding="utf-8"))["workflow"] for p in scenarios} == REQUIRED_WORKFLOWS
    for path in scenarios:
        assert main(["simulate", str(path)]) == 0
    assert "Final contact:" in capsys.readouterr().out


def test_committed_demo_output_is_current(capsys):
    import runpy

    demo = runpy.run_path(str(ROOT / "demo" / "run_demo.py"))
    capsys.readouterr()
    demo["run"]()
    committed = (ROOT / "demo" / "output.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert capsys.readouterr().out == committed


def test_readme_sample_output_is_real():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    output = (ROOT / "demo" / "output.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = readme.split("## What you will see", 1)[1].split("```", 2)[1].strip("\n")
    lines = [line for line in block.splitlines() if line.strip() and line.strip() != "..."]
    assert len(lines) >= 8
    for line in lines:
        assert line in output.splitlines(), f"README shows a line the demo does not print: {line}"


# repo hygiene ---------------------------------------------------------------


def test_only_synthetic_contact_data_and_no_em_dashes():
    skip = {".deps", ".venv", ".git", "__pycache__", ".pytest_cache"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or skip & set(path.parts) or path.suffix in {".pyc"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert chr(0x2014) not in text and chr(0x2013) not in text, f"dash in {path.name}"
        for phone in re.findall(r"\b\d{3}-\d{4}\b", text):
            assert phone.startswith("555-01"), f"non synthetic phone in {path.name}"
        for domain in re.findall(r"[\w.]+@([\w.]+\w)", text):
            assert domain in {"example.com", "v7"}, f"non example email in {path.name}"
