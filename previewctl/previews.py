import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

STATE_KEY = re.compile(r"^preview/pr-(\d+)\.tfstate$")
CLUSTER = os.environ.get("PRENV_CLUSTER", "prenv")


@dataclass
class Preview:
    pr: int
    service_arn: str
    url: str
    image_tag: str
    expires_at: datetime | None
    created_at: datetime | None
    running: int
    desired: int
    status: str
    spot: bool

    @property
    def expired(self):
        return self.expires_at is not None and self.expires_at <= now()

    def to_dict(self):
        data = asdict(self)
        for key in ("expires_at", "created_at"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        data["expired"] = self.expired
        return data


def now():
    return datetime.now(UTC)


def parse_time(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def service_arns(ecs, cluster):
    arns = []
    for page in ecs.get_paginator("list_services").paginate(cluster=cluster):
        arns.extend(page["serviceArns"])
    return arns


def list_previews(ecs, cluster=CLUSTER):
    previews = []
    for chunk in chunks(service_arns(ecs, cluster), 10):
        described = ecs.describe_services(cluster=cluster, services=chunk, include=["TAGS"])
        for service in described["services"]:
            tags = {t["key"]: t["value"] for t in service.get("tags") or tags_of(ecs, service)}
            if "pr" not in tags:
                continue
            strategy = service.get("capacityProviderStrategy") or []
            deployments = service.get("deployments") or [{}]
            previews.append(
                Preview(
                    pr=int(tags["pr"]),
                    service_arn=service["serviceArn"],
                    url=tags.get("url", ""),
                    image_tag=tags.get("image_tag", ""),
                    expires_at=parse_time(tags.get("expires_at")),
                    created_at=to_utc(service.get("createdAt") or deployments[0].get("createdAt")),
                    running=service.get("runningCount", 0),
                    desired=service.get("desiredCount", 0),
                    status=service.get("status", ""),
                    spot=any(s["capacityProvider"] == "FARGATE_SPOT" for s in strategy),
                )
            )
    return sorted(previews, key=lambda p: p.pr)


def tags_of(ecs, service):
    return ecs.list_tags_for_resource(resourceArn=service["serviceArn"]).get("tags", [])


def to_utc(stamp):
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def state_prs(s3, bucket):
    found = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="preview/"):
        for item in page.get("Contents", []):
            match = STATE_KEY.match(item["Key"])
            if match:
                found.add(int(match.group(1)))
    return found


def probe(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None


def wait_for(url, timeout=600, interval=5, clock=time.monotonic, sleep=time.sleep):
    started = clock()
    while True:
        if probe(f"{url}/healthz") == 200:
            return round(clock() - started, 1)
        if clock() - started > timeout:
            raise TimeoutError(f"{url} not healthy after {timeout}s")
        sleep(interval)


def fetch_json(url, data=None):
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.load(response)


def smoke(url):
    stamp = now().strftime("%H:%M:%S")
    checks = {}
    checks["healthz"] = probe(f"{url}/healthz") == 200
    status, info = fetch_json(f"{url}/api/info")
    checks["info"] = status == 200 and "preview" in info
    status, note = fetch_json(f"{url}/api/notes", {"body": f"smoke test {stamp}"})
    checks["create"] = status == 201 and note["body"] == f"smoke test {stamp}"
    status, listing = fetch_json(f"{url}/api/notes")
    checks["list"] = status == 200 and any(n["id"] == note["id"] for n in listing)
    checks["index"] = probe(url) == 200
    return checks


def pr_state(repo, pr, token):
    if not token:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/pulls/{pr}",
        headers={"authorization": f"Bearer {token}", "accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)["state"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "missing"
        raise


def terraform(args, infra_dir):
    subprocess.run(["terraform", *args], cwd=infra_dir, check=True)


def destroy(pr, infra_dir, bucket, region, s3):
    key = f"preview/pr-{pr}.tfstate"
    terraform(
        [
            "init",
            "-input=false",
            "-reconfigure",
            f"-backend-config=bucket={bucket}",
            f"-backend-config=key={key}",
            f"-backend-config=region={region}",
        ],
        infra_dir,
    )
    terraform(
        [
            "destroy",
            "-input=false",
            "-auto-approve",
            f"-var=pr_number={pr}",
            "-var=image_tag=destroyed",
            f"-var=state_bucket={bucket}",
            f"-var=aws_region={region}",
        ],
        infra_dir,
    )
    s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key}, {"Key": f"{key}.tflock"}]})


def sweep_targets(previews, state, repo, token, states=None):
    states = states or pr_state
    targets = {}
    for preview in previews:
        if preview.expired:
            targets[preview.pr] = "expired"
    for pr in sorted(set(state) | {p.pr for p in previews}):
        if pr in targets:
            continue
        if states(repo, pr, token) in ("closed", "missing"):
            targets[pr] = "closed"
    return targets


def summarize_costs(response):
    totals = defaultdict(float)
    for period in response.get("ResultsByTime", []):
        for group in period.get("Groups", []):
            key = group["Keys"][0].split("$", 1)[1] or "base"
            totals[key] += float(group["Metrics"]["UnblendedCost"]["Amount"])
    return dict(sorted(totals.items()))


def costs(ce, days=14):
    end = now().date()
    start = end - timedelta(days=days)
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "TAG", "Key": "pr"}],
    )
    return summarize_costs(response)
