import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import boto3
import pytest
from moto import mock_aws

from previewctl import cli, previews

REGION = "us-east-2"
FUTURE = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
PAST = (datetime.now(UTC) - timedelta(hours=1)).isoformat()


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def make_service(ecs, name, tags, spot=True):
    ecs.register_task_definition(
        family=f"prenv-{name}",
        containerDefinitions=[{"name": "app", "image": "app:latest", "memory": 256}],
    )
    ecs.create_service(
        cluster="prenv",
        serviceName=name,
        taskDefinition=f"prenv-{name}",
        desiredCount=1,
        capacityProviderStrategy=[
            {"capacityProvider": "FARGATE_SPOT" if spot else "FARGATE", "weight": 1}
        ],
        tags=[{"key": key, "value": value} for key, value in tags.items()],
    )


def preview_tags(pr, expires):
    return {
        "pr": str(pr),
        "url": f"https://pr-{pr}.preview.example.com",
        "image_tag": f"pr-{pr}-abc1234",
        "expires_at": expires,
    }


@pytest.fixture
def ecs():
    with mock_aws():
        client = boto3.client("ecs", region_name=REGION)
        client.create_cluster(clusterName="prenv")
        make_service(client, "pr-12", preview_tags(12, FUTURE))
        make_service(client, "pr-3", preview_tags(3, PAST), spot=False)
        make_service(client, "unrelated", {"team": "platform"})
        yield client


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket="prenv-tfstate",
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        for key in (
            "platform/terraform.tfstate",
            "preview/pr-3.tfstate",
            "preview/pr-3.tfstate.tflock",
            "preview/pr-40.tfstate",
        ):
            client.put_object(Bucket="prenv-tfstate", Key=key, Body=b"{}")
        yield client


def test_list_previews_reads_service_tags(ecs):
    rows = previews.list_previews(ecs, "prenv")
    assert [p.pr for p in rows] == [3, 12]
    old, new = rows
    assert old.expired and not new.expired
    assert new.url == "https://pr-12.preview.example.com"
    assert new.image_tag == "pr-12-abc1234"
    assert isinstance(new.spot, bool)
    assert new.desired == 1
    assert new.created_at is not None and new.created_at.tzinfo is not None
    assert new.to_dict()["expired"] is False


def test_state_prs_reads_state_keys_only(s3):
    assert previews.state_prs(s3, "prenv-tfstate") == {3, 40}


def test_sweep_targets_by_expiry_and_pr_state(ecs, s3):
    rows = previews.list_previews(ecs, "prenv")
    state = previews.state_prs(s3, "prenv-tfstate")
    closed = {40: "closed", 12: "open"}
    targets = previews.sweep_targets(
        rows, state, "owner/repo", "token", states=lambda repo, pr, token: closed.get(pr, "open")
    )
    assert targets == {3: "expired", 40: "closed"}


def test_sweep_targets_without_token_only_uses_expiry(ecs, s3):
    rows = previews.list_previews(ecs, "prenv")
    state = previews.state_prs(s3, "prenv-tfstate")
    assert previews.sweep_targets(rows, state, "owner/repo", None) == {3: "expired"}


def test_parse_time_handles_zulu_and_offsets():
    assert previews.parse_time("2026-09-21T10:00:00Z") == datetime(2026, 9, 21, 10, tzinfo=UTC)
    assert previews.parse_time("2026-09-21T06:00:00-04:00") == datetime(2026, 9, 21, 10, tzinfo=UTC)
    assert previews.parse_time("") is None


def test_summarize_costs_groups_by_pr_tag():
    response = {
        "ResultsByTime": [
            {
                "Groups": [
                    {"Keys": ["pr$12"], "Metrics": {"UnblendedCost": {"Amount": "0.10"}}},
                    {"Keys": ["pr$"], "Metrics": {"UnblendedCost": {"Amount": "1.50"}}},
                ]
            },
            {"Groups": [{"Keys": ["pr$12"], "Metrics": {"UnblendedCost": {"Amount": "0.05"}}}]},
        ]
    }
    totals = previews.summarize_costs(response)
    assert totals == {"12": pytest.approx(0.15), "base": pytest.approx(1.5)}


class Handler(BaseHTTPRequestHandler):
    healthy_after = 2
    hits = 0
    notes = []

    def log_message(self, *args):
        pass

    def reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/healthz":
            Handler.hits += 1
            if Handler.hits < Handler.healthy_after:
                self.reply(503, {"status": "starting"})
            else:
                self.reply(200, {"status": "ok"})
        elif self.path == "/api/info":
            self.reply(200, {"preview": "pr-1"})
        elif self.path == "/api/notes":
            self.reply(200, Handler.notes)
        elif self.path == "/":
            self.reply(200, {"page": True})
        else:
            self.reply(404, {})

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length))
        note = {"id": len(Handler.notes) + 1, "body": body["body"]}
        Handler.notes.append(note)
        self.reply(201, note)


@pytest.fixture
def server():
    Handler.hits = 0
    Handler.notes = []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_wait_for_returns_seconds_once_healthy(server):
    ticks = iter(range(0, 100, 3))
    seconds = previews.wait_for(
        server, timeout=60, interval=1, clock=lambda: next(ticks), sleep=lambda s: None
    )
    assert seconds == 6.0
    assert Handler.hits == 2


def test_wait_for_times_out(server):
    Handler.healthy_after = 1000
    ticks = iter(range(0, 1000, 10))
    with pytest.raises(TimeoutError):
        previews.wait_for(
            server, timeout=25, interval=1, clock=lambda: next(ticks), sleep=lambda s: None
        )
    Handler.healthy_after = 2


def test_smoke_exercises_every_endpoint(server):
    Handler.healthy_after = 1
    checks = previews.smoke(server)
    assert checks == {"healthz": True, "info": True, "create": True, "list": True, "index": True}
    Handler.healthy_after = 2


def test_cli_list_json(ecs, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["list", "--json"])
    assert exit_info.value.code == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["pr"] for row in rows] == [3, 12]
    assert rows[0]["expired"] is True


def test_cli_list_table(ecs, capsys):
    with pytest.raises(SystemExit):
        cli.main(["list"])
    out = capsys.readouterr().out
    assert "pr-12.preview.example.com" in out
    assert out.splitlines()[0].startswith("pr  url")


def test_cli_sweep_dry_run_lists_targets(ecs, s3, monkeypatch, capsys):
    monkeypatch.setattr(previews, "pr_state", lambda repo, pr, token: "open")
    calls = []
    monkeypatch.setattr(previews, "destroy", lambda *args: calls.append(args))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["sweep", "--repo", "o/r", "--state-bucket", "prenv-tfstate", "--dry-run"])
    assert exit_info.value.code == 0
    assert "would destroy pr-3 (expired)" in capsys.readouterr().out
    assert calls == []


def test_cli_sweep_destroys_targets(ecs, s3, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        previews, "pr_state", lambda repo, pr, token: "closed" if pr == 40 else "open"
    )
    calls = []
    monkeypatch.setattr(previews, "destroy", lambda pr, *rest: calls.append(pr))
    with pytest.raises(SystemExit):
        cli.main(["sweep", "--repo", "o/r", "--state-bucket", "prenv-tfstate"])
    assert calls == [3, 40]
    out = capsys.readouterr().out
    assert "destroying pr-3 (expired)" in out
    assert "destroying pr-40 (closed)" in out


def test_destroy_runs_terraform_and_removes_state(s3, monkeypatch):
    commands = []
    monkeypatch.setattr(previews, "terraform", lambda args, cwd: commands.append((args[0], cwd)))
    previews.destroy(3, "infra/preview", "prenv-tfstate", REGION, s3)
    assert commands == [("init", "infra/preview"), ("destroy", "infra/preview")]
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="prenv-tfstate")["Contents"]]
    assert keys == ["platform/terraform.tfstate", "preview/pr-40.tfstate"]


def test_cli_wait_reports_seconds(ecs, server, monkeypatch, capsys):
    Handler.healthy_after = 1
    monkeypatch.setattr(previews, "wait_for", lambda url, timeout, interval: 42.0)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["wait", "12", "--timeout", "5"])
    assert exit_info.value.code == 0
    assert "https://pr-12.preview.example.com healthy after 42.0s" in capsys.readouterr().out
    Handler.healthy_after = 2


def test_cli_unknown_pr_exits_with_message(ecs, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["wait", "99"])
    assert "no preview for pr 99" in str(exit_info.value.code)


def test_cli_cost_without_data(capsys):
    with mock_aws(), pytest.raises(SystemExit) as exit_info:
        cli.main(["cost", "--days", "3"])
    assert exit_info.value.code == 0
    assert "no cost data yet" in capsys.readouterr().out
