import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
NAMES = ["ci", "preview", "platform", "sweep"]


def load(name):
    with (WORKFLOWS / f"{name}.yml").open() as handle:
        return yaml.safe_load(handle)


def steps(workflow):
    for job in workflow["jobs"].values():
        yield from job["steps"]


@pytest.fixture(scope="module")
def workflows():
    return {name: load(name) for name in NAMES}


@pytest.mark.parametrize("name", NAMES)
def test_permissions_are_declared_and_minimal(workflows, name):
    permissions = workflows[name]["permissions"]
    assert permissions["contents"] == "read"
    assert "write-all" not in str(permissions)
    if name == "ci":
        assert "id-token" not in permissions
    else:
        assert permissions["id-token"] == "write"


@pytest.mark.parametrize("name", NAMES)
def test_actions_are_pinned_to_a_major_version(workflows, name):
    for step in steps(workflows[name]):
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", step["uses"]), step["uses"]


@pytest.mark.parametrize("name", NAMES)
def test_no_long_lived_aws_keys(workflows, name):
    text = (WORKFLOWS / f"{name}.yml").read_text()
    assert "aws-access-key-id" not in text
    assert "AWS_SECRET_ACCESS_KEY" not in text
    assert "secrets.AWS" not in text


def test_preview_serializes_work_per_pull_request(workflows):
    preview = workflows["preview"]
    assert preview["concurrency"]["group"] == "preview-${{ github.event.pull_request.number }}"
    assert preview["concurrency"]["cancel-in-progress"] is False
    assert preview["jobs"]["deploy"]["if"] == "github.event.action != 'closed'"
    assert preview["jobs"]["destroy"]["if"] == "github.event.action == 'closed'"
    assert set(preview[True]["pull_request"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "closed",
    }


def test_preview_role_is_the_only_role_used_outside_platform(workflows):
    for name in ("preview", "sweep"):
        for step in steps(workflows[name]):
            if step.get("uses", "").startswith("aws-actions/configure-aws-credentials"):
                assert step["with"]["role-to-assume"] == "${{ vars.PREVIEW_ROLE_ARN }}"
    for step in steps(workflows["platform"]):
        if step.get("uses", "").startswith("aws-actions/configure-aws-credentials"):
            assert step["with"]["role-to-assume"] == "${{ vars.PLATFORM_ROLE_ARN }}"


def test_platform_applies_only_from_main_and_plans_on_pull_requests(workflows):
    platform = workflows["platform"]
    assert platform[True]["push"]["branches"] == ["main"]
    assert platform["jobs"]["apply"]["if"] == "github.event_name == 'push'"
    assert platform["jobs"]["plan"]["if"] == "github.event_name == 'pull_request'"
    apply_runs = [s["run"] for s in platform["jobs"]["apply"]["steps"] if "run" in s]
    assert any("terraform apply" in run for run in apply_runs)
    plan_runs = [s["run"] for s in platform["jobs"]["plan"]["steps"] if "run" in s]
    assert not any("terraform apply" in run for run in plan_runs)


def test_sweep_runs_hourly_and_can_be_triggered_by_hand(workflows):
    triggers = workflows["sweep"][True]
    assert triggers["schedule"] == [{"cron": "17 * * * *"}]
    assert "workflow_dispatch" in triggers


def test_ci_runs_tests_against_postgres_and_validates_every_root(workflows):
    ci = workflows["ci"]
    assert ci["jobs"]["python"]["services"]["postgres"]["image"].startswith("postgres:16")
    assert ci["jobs"]["terraform"]["strategy"]["matrix"]["root"] == [
        "bootstrap",
        "platform",
        "preview",
    ]
    runs = " ".join(s.get("run", "") for s in steps(ci))
    for command in (
        "ruff check",
        "pytest",
        "terraform fmt -check",
        "terraform -chdir",
        "tflint",
        "checkov",
    ):
        assert command in runs
