from pathlib import Path

import hcl2
import pytest

INFRA = Path(__file__).resolve().parent.parent / "infra"
ROOTS = ["bootstrap", "platform", "preview"]


def unquote(value):
    if isinstance(value, dict):
        return {unquote(key): unquote(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unquote(item) for item in value]
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def load(root):
    merged = {}
    for path in sorted((INFRA / root).glob("*.tf")):
        with path.open() as handle:
            for key, entries in unquote(hcl2.load(handle)).items():
                merged.setdefault(key, []).extend(entries)
    return merged


def resources(config, kind):
    for entry in config.get("resource", []):
        yield from entry.get(kind, {}).items()


def data_sources(config, kind):
    for entry in config.get("data", []):
        yield from entry.get(kind, {}).items()


def blocks(config, key):
    for entry in config.get(key, []):
        yield from entry.items()


def policies(config):
    return dict(data_sources(config, "aws_iam_policy_document"))


@pytest.fixture(scope="module")
def configs():
    return {root: load(root) for root in ROOTS}


@pytest.mark.parametrize("root", ROOTS)
def test_every_variable_is_typed_described_and_guarded(configs, root):
    for name, body in blocks(configs[root], "variable"):
        assert body.get("description"), f"{root}.{name} has no description"
        assert body.get("type"), f"{root}.{name} has no type"
        guarded = "validation" in body or body.get("type") == "bool"
        assert guarded, f"{root}.{name} has no validation block"


@pytest.mark.parametrize("root", ROOTS)
def test_every_output_is_described(configs, root):
    for name, body in blocks(configs[root], "output"):
        assert body.get("description"), f"{root}.{name} has no description"


@pytest.mark.parametrize("root", ROOTS)
def test_versions_are_pinned(configs, root):
    settings = configs[root]["terraform"][0]
    assert settings["required_version"] == ">= 1.10"
    providers = settings["required_providers"][0]
    assert providers["aws"]["version"] == "~> 6.0"


def test_only_the_load_balancer_listens_to_the_internet(configs):
    for root in ROOTS:
        for name, rule in resources(configs[root], "aws_vpc_security_group_ingress_rule"):
            if rule.get("cidr_ipv4") == "0.0.0.0/0":
                assert name.startswith("alb_"), f"{name} opens a port to the internet"
                assert rule["from_port"] == 80
            if name.startswith("tasks_"):
                assert "referenced_security_group_id" in rule


def test_task_egress_is_limited_to_https_and_dns(configs):
    ports = set()
    for name, rule in resources(configs["platform"], "aws_vpc_security_group_egress_rule"):
        if name.startswith("tasks_"):
            ports.add(rule["from_port"])
            assert rule["from_port"] == rule["to_port"]
    assert ports == {443, 53}


def test_log_groups_have_retention(configs):
    for root in ROOTS:
        for name, group in resources(configs[root], "aws_cloudwatch_log_group"):
            assert "retention_in_days" in group, f"{root}.{name} keeps logs forever"


def test_buckets_are_private_versioned_and_encrypted(configs):
    for root in ("bootstrap", "platform"):
        config = configs[root]
        buckets = {name for name, _ in resources(config, "aws_s3_bucket")}
        assert buckets
        for kind in (
            "aws_s3_bucket_public_access_block",
            "aws_s3_bucket_versioning",
            "aws_s3_bucket_server_side_encryption_configuration",
            "aws_s3_bucket_lifecycle_configuration",
        ):
            covered = {name for name, _ in resources(config, kind)}
            assert covered == buckets, f"{root}: {kind} missing for {buckets - covered}"
        for _, block in resources(config, "aws_s3_bucket_public_access_block"):
            assert all(
                block[key] is True
                for key in (
                    "block_public_acls",
                    "block_public_policy",
                    "ignore_public_acls",
                    "restrict_public_buckets",
                )
            )


def test_ci_policies_never_grant_wildcard_actions(configs):
    documents = policies(configs["bootstrap"])
    for name in ("ci_preview", "ci_platform"):
        for statement in documents[name]["statement"]:
            for action in statement["actions"]:
                assert action != "*", f"{name} grants every action"
                assert not action.startswith("iam:*"), f"{name} grants iam:*"
                service, _, verb = action.partition(":")
                assert verb, f"{name} has a malformed action {action}"
                if verb == "*":
                    pytest.fail(f"{name} grants {service}:*")


def test_ci_write_statements_with_star_resources_carry_a_condition(configs):
    documents = policies(configs["bootstrap"])
    read_only = ("Describe", "List", "Get")
    for name in ("ci_preview", "ci_platform"):
        for statement in documents[name]["statement"]:
            if statement["resources"] != ["*"]:
                continue
            writes = [a for a in statement["actions"] if not a.split(":")[1].startswith(read_only)]
            if writes:
                assert "condition" in statement, f"{name}.{statement['sid']} writes with *"


def test_github_trust_is_scoped_to_one_repository_and_audience(configs):
    trust = policies(configs["bootstrap"])["github_trust"]["statement"][0]
    conditions = {c["variable"]: c for c in trust["condition"]}
    assert conditions["token.actions.githubusercontent.com:aud"]["values"] == ["sts.amazonaws.com"]
    assert conditions["token.actions.githubusercontent.com:sub"]["test"] == "StringLike"


def test_registry_is_immutable_and_scanned(configs):
    repositories = dict(resources(configs["platform"], "aws_ecr_repository"))
    assert repositories["app"]["image_tag_mutability"] == "IMMUTABLE"
    assert repositories["app"]["image_scanning_configuration"][0]["scan_on_push"] is True
    assert dict(resources(configs["platform"], "aws_ecr_lifecycle_policy"))


def test_listener_answers_unknown_paths_with_404(configs):
    listeners = dict(resources(configs["platform"], "aws_lb_listener"))
    assert list(listeners) == ["http"]
    action = listeners["http"]["default_action"][0]
    assert action["type"] == "fixed-response"
    assert action["fixed_response"][0]["status_code"] == "404"
    balancer = dict(resources(configs["platform"], "aws_lb"))["this"]
    assert balancer["drop_invalid_header_fields"] is True


def test_preview_routes_by_path_prefix(configs):
    rule = dict(resources(configs["preview"], "aws_lb_listener_rule"))["this"]
    assert rule["condition"][0]["path_pattern"][0]["values"] == [
        "${local.prefix}",
        "${local.prefix}/*",
    ]
    group = dict(resources(configs["preview"], "aws_lb_target_group"))["this"]
    assert group["health_check"][0]["path"] == "${local.prefix}/healthz"


def test_preview_service_rolls_back_and_waits_for_health(configs):
    service = dict(resources(configs["preview"], "aws_ecs_service"))["this"]
    breaker = service["deployment_circuit_breaker"][0]
    assert breaker["enable"] is True and breaker["rollback"] is True
    assert service["wait_for_steady_state"] is True
    assert service["desired_count"] == 1
    group = dict(resources(configs["preview"], "aws_lb_target_group"))["this"]
    assert group["health_check"][0]["path"].endswith("/healthz")
    assert group["deregistration_delay"] == 5


def test_preview_rule_priority_is_the_pull_request_number(configs):
    rule = dict(resources(configs["preview"], "aws_lb_listener_rule"))["this"]
    assert rule["priority"] == "${var.pr_number}"
    validation = dict(blocks(configs["preview"], "variable"))["pr_number"]["validation"][0]
    assert "49999" in validation["condition"]


def test_database_password_lives_in_a_secure_parameter(configs):
    parameter = dict(resources(configs["preview"], "aws_ssm_parameter"))["db_password"]
    assert parameter["type"] == "SecureString"
    assert parameter["name"].startswith("/prenv/preview/")
    password = dict(resources(configs["preview"], "random_password"))["db"]
    assert password["length"] >= 32


def test_every_root_uses_the_locked_s3_backend_or_bootstraps_it(configs):
    for root in ("platform", "preview"):
        backend = configs[root]["terraform"][0]["backend"][0]["s3"]
        assert backend["use_lockfile"] is True
    assert "backend" not in configs["bootstrap"]["terraform"][0]
