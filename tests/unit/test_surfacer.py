"""U8: surfacer SteeringBlock -> SurfacePlan projection. Offline (no LLM)."""
from __future__ import annotations

from semipy.models import SteeringBlock, SteeringEntry
from semipy.orchestration.artifacts import SurfacePlan
from semipy.orchestration.roles.surfacer import project_block


def _block():
    return SteeringBlock(
        intent=SteeringEntry(value="classify the log line"),
        given=[SteeringEntry(value="a raw apache line"), SteeringEntry(value="utf-8 text")],
        by=SteeringEntry(value="matching the level token"),
        unless=[SteeringEntry(value="line is blank")],
        yields=SteeringEntry(value="str"),
        verified=SteeringEntry(value="3/3 examples reproduced"),
    )


def test_project_block_extracts_scalar_and_list_values():
    plan = project_block(_block())
    assert isinstance(plan, SurfacePlan)
    assert plan.steering_values["intent"] == "classify the log line"
    assert plan.steering_values["by"] == "matching the level token"
    assert plan.steering_values["given"] == ["a raw apache line", "utf-8 text"]
    assert plan.steering_values["unless"] == ["line is blank"]
    assert plan.zones == ["P", "E"]


def test_verified_is_carried_from_the_rule_derived_block():
    # The surfacer must surface verified as-is from the block (rule-derived),
    # never synthesize it.
    plan = project_block(_block())
    assert plan.verified == "3/3 examples reproduced"
    assert plan.steering_values["verified"] == "3/3 examples reproduced"


def test_empty_block_projects_empty_plan():
    plan = project_block(SteeringBlock())
    assert plan.steering_values["intent"] == ""
    assert plan.steering_values["given"] == []
    assert plan.verified is None  # empty verified -> None, not ""


def test_surface_plan_is_json_serializable():
    import json

    plan = project_block(_block())
    reloaded = SurfacePlan.model_validate(json.loads(plan.model_dump_json()))
    assert reloaded == plan
