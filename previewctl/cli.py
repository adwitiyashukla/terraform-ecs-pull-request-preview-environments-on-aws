import argparse
import json
import os
import sys

import boto3

from previewctl import previews


def build_parser():
    parser = argparse.ArgumentParser(prog="previewctl")
    parser.add_argument("--cluster", default=previews.CLUSTER)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"))
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")

    wait = commands.add_parser("wait")
    wait.add_argument("pr", type=int)
    wait.add_argument("--timeout", type=int, default=600)
    wait.add_argument("--interval", type=int, default=5)

    smoke = commands.add_parser("smoke")
    smoke.add_argument("pr", type=int)

    cost = commands.add_parser("cost")
    cost.add_argument("--days", type=int, default=14)

    sweep = commands.add_parser("sweep")
    sweep.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    sweep.add_argument("--state-bucket", default=os.environ.get("TF_STATE_BUCKET"))
    sweep.add_argument("--infra-dir", default="infra/preview")
    sweep.add_argument("--dry-run", action="store_true")

    destroy = commands.add_parser("destroy")
    destroy.add_argument("pr", type=int)
    destroy.add_argument("--state-bucket", default=os.environ.get("TF_STATE_BUCKET"))
    destroy.add_argument("--infra-dir", default="infra/preview")
    return parser


def client(service, region):
    return boto3.client(service, region_name=region)


def find(previews_list, pr):
    for preview in previews_list:
        if preview.pr == pr:
            return preview
    sys.exit(f"no preview for pr {pr}")


def print_table(rows):
    if not rows:
        print("no previews")
        return
    header = ["pr", "url", "image", "tasks", "spot", "expires", "expired"]
    lines = [header] + [
        [
            str(p.pr),
            p.url,
            p.image_tag,
            f"{p.running}/{p.desired}",
            "yes" if p.spot else "no",
            p.expires_at.strftime("%Y-%m-%d %H:%MZ") if p.expires_at else "",
            "yes" if p.expired else "no",
        ]
        for p in rows
    ]
    widths = [max(len(line[i]) for line in lines) for i in range(len(header))]
    for line in lines:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)).rstrip())


def run(args):
    if args.command == "list":
        rows = previews.list_previews(client("ecs", args.region), args.cluster)
        if args.json:
            print(json.dumps([p.to_dict() for p in rows], indent=2))
        else:
            print_table(rows)
        return 0

    if args.command == "wait":
        rows = previews.list_previews(client("ecs", args.region), args.cluster)
        preview = find(rows, args.pr)
        seconds = previews.wait_for(preview.url, args.timeout, args.interval)
        print(f"{preview.url} healthy after {seconds}s")
        return 0

    if args.command == "smoke":
        rows = previews.list_previews(client("ecs", args.region), args.cluster)
        preview = find(rows, args.pr)
        checks = previews.smoke(preview.url)
        for name, passed in checks.items():
            print(f"{name}: {'ok' if passed else 'FAIL'}")
        return 0 if all(checks.values()) else 1

    if args.command == "cost":
        totals = previews.costs(client("ce", "us-east-1"), args.days)
        if not totals:
            print("no cost data yet; activate the pr cost allocation tag and wait a day")
            return 0
        for key, amount in totals.items():
            print(f"{key:>10}  ${amount:.4f}")
        print(f"{'total':>10}  ${sum(totals.values()):.4f}")
        return 0

    if args.command == "sweep":
        ecs = client("ecs", args.region)
        s3 = client("s3", args.region)
        rows = previews.list_previews(ecs, args.cluster)
        state = previews.state_prs(s3, args.state_bucket)
        targets = previews.sweep_targets(rows, state, args.repo, os.environ.get("GITHUB_TOKEN"))
        if not targets:
            print("nothing to sweep")
            return 0
        for pr, reason in targets.items():
            if args.dry_run:
                print(f"would destroy pr-{pr} ({reason})")
                continue
            print(f"destroying pr-{pr} ({reason})")
            previews.destroy(pr, args.infra_dir, args.state_bucket, args.region, s3)
        return 0

    if args.command == "destroy":
        s3 = client("s3", args.region)
        previews.destroy(args.pr, args.infra_dir, args.state_bucket, args.region, s3)
        print(f"destroyed pr-{args.pr}")
        return 0

    return 2


def main(argv=None):
    args = build_parser().parse_args(argv)
    sys.exit(run(args))
