#!/usr/bin/env python3
"""Lambda Cloud control plane: run possum table cells off-cluster.

WHY THIS EXISTS. Killarney's backfill window (`bf_max_job_user=20`) only ever
examines your top 20 pending jobs, and a long multi-GPU job fits almost no
backfill gap. The campaign's remaining heavy work (5-day cyber AutoDAN, 3-day
32B AutoDAN, 10-22h 32B evals) therefore starves while short jobs would run.
Lambda bills per hour with instant capacity, so the split is:

    Killarney  ->  short, single-GPU, <=3h  (backfills well, already free)
    Lambda     ->  long or multi-GPU        (would otherwise wait days)

DESIGN NOTE. This does NOT define new jobs. It reuses the EXISTING generated
sbatch scripts verbatim: `#SBATCH` lines are comments to bash, so the same file
that Slurm runs also runs under `bash` once the env-setup line is swapped. One
job definition, two executors -- so a cell computed on Lambda is byte-identical
in protocol to one computed on Killarney.

Stdlib only, so it runs on a login node with no venv.

    export LAMBDA_API_KEY=...            # or put it in .env (see docs/LAMBDA.md)
    python -m experiments.lambda_cloud doctor
    python -m experiments.lambda_cloud types
    python -m experiments.lambda_cloud up --type gpu_1x_h100_sxm5
    python -m experiments.lambda_cloud run --sbatch <path> --instance <ip>
    python -m experiments.lambda_cloud down --all
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://cloud.lambdalabs.com/api/v1"

# Endpoint paths kept as named constants: if Lambda renames one, this is the
# only place to edit, and `doctor` will say which call failed.
EP_TYPES = "/instance-types"
EP_INSTANCES = "/instances"
EP_LAUNCH = "/instance-operations/launch"
EP_TERMINATE = "/instance-operations/terminate"
EP_SSH_KEYS = "/ssh-keys"

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- credentials
def _load_dotenv() -> None:
    """Read KEY=VALUE lines from <repo>/.env without overriding real env vars."""
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def api_key() -> str:
    _load_dotenv()
    key = os.environ.get("LAMBDA_API_KEY", "").strip()
    if not key:
        sys.exit(
            "LAMBDA_API_KEY is not set.\n"
            f"  Put it in {REPO/'.env'} as   LAMBDA_API_KEY=secret_...\n"
            "  or export it. Get one at https://cloud.lambda.ai/api-keys\n"
            "  NOTE: .env is gitignored; never commit the key."
        )
    return key


def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    """One API call. Lambda uses HTTP Basic with the key as username."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    token = base64.b64encode(f"{api_key()}:".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        sys.exit(f"Lambda API {method} {path} -> HTTP {e.code}\n{detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach {API}{path}: {e.reason}\n"
                 "Killarney login nodes may block egress; run this from a host with internet.")


# --------------------------------------------------------------------- actions
def cmd_doctor(a) -> int:
    print(f"repo      : {REPO}")
    print(f"api key   : {'set (' + api_key()[:9] + '...)' if api_key() else 'MISSING'}")
    keys = call(EP_SSH_KEYS).get("data", [])
    print(f"ssh keys  : {[k.get('name') for k in keys] or 'NONE -- add one, see docs/LAMBDA.md'}")
    inst = call(EP_INSTANCES).get("data", [])
    print(f"instances : {len(inst)} running")
    for i in inst:
        print(f"    {i.get('id')}  {i.get('instance_type',{}).get('name')}  "
              f"{i.get('ip')}  {i.get('status')}")
    avail = [(n, d) for n, d in call(EP_TYPES).get("data", {}).items()
             if d.get("regions_with_capacity_available")]
    print(f"available : {len(avail)} instance types have capacity right now")
    return 0


def cmd_types(a) -> int:
    data = call(EP_TYPES).get("data", {})
    rows = []
    for name, d in data.items():
        regions = [r.get("name") for r in d.get("regions_with_capacity_available", [])]
        spec = d.get("instance_type", {})
        rows.append((bool(regions), name,
                     spec.get("price_cents_per_hour", 0) / 100.0,
                     spec.get("description", ""), regions))
    rows.sort(key=lambda r: (not r[0], r[2]))
    print(f"{'avail':6s} {'type':26s} {'$/hr':>7s}  regions")
    for ok, name, price, desc, regions in rows:
        if a.all or ok:
            print(f"{'YES' if ok else '-':6s} {name:26s} {price:7.2f}  "
                  f"{','.join(regions) if regions else '(no capacity)'}")
    return 0


def cmd_up(a) -> int:
    body = {"region_name": a.region, "instance_type_name": a.type,
            "ssh_key_names": [a.ssh_key], "quantity": a.count}
    if a.filesystem:
        body["file_system_names"] = [a.filesystem]
    out = call(EP_LAUNCH, "POST", body)
    ids = out.get("data", {}).get("instance_ids", [])
    print(f"launched {len(ids)}: {ids}")
    print("Instances bill from launch. Run `down --all` when finished.")
    return 0


def cmd_ls(a) -> int:
    for i in call(EP_INSTANCES).get("data", []):
        print(f"{i.get('id')}  {i.get('status'):10s} "
              f"{i.get('instance_type',{}).get('name'):22s} {i.get('ip')}")
    return 0


def cmd_down(a) -> int:
    ids = a.ids or [i["id"] for i in call(EP_INSTANCES).get("data", [])]
    if not ids:
        print("nothing running")
        return 0
    if not (a.all or a.ids):
        sys.exit("refusing to terminate without --all or explicit ids")
    out = call(EP_TERMINATE, "POST", {"instance_ids": ids})
    print(f"terminated: {[i.get('id') for i in out.get('data',{}).get('terminated_instances',[])]}")
    return 0


def cmd_run(a) -> int:
    """Run an EXISTING generated sbatch script on a Lambda box, unchanged."""
    script = Path(a.sbatch).resolve()
    if not script.exists():
        sys.exit(f"no such sbatch: {script}")
    remote = f"ubuntu@{a.instance}"
    ssh = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-i", a.identity, remote]
    print(f"[lambda] {script.name} -> {remote}")
    # The sbatch file is valid bash: #SBATCH lines are comments. Only the
    # env-setup source line is cluster-specific, so rewrite exactly that.
    body = script.read_text().replace(
        "source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'",
        "source ~/possum/clusters/lambda_env_setup.sh",
    ).replace("/project/6105494/markwang/possum/possum", "~/possum")
    subprocess.run(ssh + ["mkdir -p ~/possum/jobs"], check=True)
    p = subprocess.run(ssh + [f"cat > ~/possum/jobs/{script.name}"],
                       input=body.encode(), check=True)
    cmd = (f"cd ~/possum && nohup bash jobs/{script.name} "
           f"> ~/possum/jobs/{script.stem}.log 2>&1 & echo started $!")
    subprocess.run(ssh + [cmd], check=True)
    print(f"[lambda] started; tail with:\n  ssh -i {a.identity} {remote} "
          f"'tail -f ~/possum/jobs/{script.stem}.log'")
    return 0


def cmd_plan(a) -> int:
    """Which queued Killarney jobs should move to Lambda, and which should stay.

    The split rule is the measured one (F-backfill, 2026-08-31): Killarney's
    backfill only examines your top 20 pending jobs, and a long or multi-GPU job
    fits almost no gap. So anything >6h OR needing >1 GPU is a Lambda candidate;
    everything else backfills fine where it is.
    """
    out = subprocess.run(
        ["squeue", "-u", os.environ.get("USER", ""), "-h",
         "-o", "%i|%j|%T|%l|%b|%R"],
        capture_output=True, text=True).stdout.strip().splitlines()
    move, keep = [], []
    for line in out:
        f = line.split("|")
        if len(f) < 6 or "JobHeldUser" in f[5]:
            continue
        jid, name, state, limit, gres = f[0], f[1], f[2], f[3], f[4]
        if state == "RUNNING":
            continue
        days = 0
        lim = limit
        if "-" in lim:
            days, lim = lim.split("-", 1)
            days = int(days)
        hh = int(lim.split(":")[0]) + days * 24
        ngpu = 1
        if ":" in gres:
            try: ngpu = int(gres.rsplit(":", 1)[1])
            except ValueError: ngpu = 1
        (move if (hh > 6 or ngpu > 1) else keep).append((jid, name, limit, gres, hh, ngpu))
    move.sort(key=lambda r: -r[4])
    print(f"MOVE TO LAMBDA ({len(move)}): long (>6h) or multi-GPU -- these starve in backfill")
    for jid, name, limit, gres, hh, ngpu in move:
        print(f"   {jid}  {limit:>10s}  {ngpu}gpu  {name[7:]}")
    print(f"\nKEEP ON KILLARNEY ({len(keep)}): short single-GPU, backfills well")
    for jid, name, limit, gres, hh, ngpu in keep:
        print(f"   {jid}  {limit:>10s}  {ngpu}gpu  {name[7:]}")
    if move:
        print(f"\nEstimated Lambda GPU-hours if all moved: {sum(r[4]*r[5] for r in move)}")
        print("Cancel each from Slurm only AFTER its Lambda result lands and validates.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    t = sub.add_parser("types"); t.add_argument("--all", action="store_true")
    t.set_defaults(fn=cmd_types)
    u = sub.add_parser("up")
    u.add_argument("--type", required=True)
    u.add_argument("--region", required=True)
    u.add_argument("--ssh-key", required=True)
    u.add_argument("--count", type=int, default=1)
    u.add_argument("--filesystem", default=None)
    u.set_defaults(fn=cmd_up)
    sub.add_parser("ls").set_defaults(fn=cmd_ls)
    sub.add_parser("plan").set_defaults(fn=cmd_plan)
    d = sub.add_parser("down")
    d.add_argument("ids", nargs="*")
    d.add_argument("--all", action="store_true")
    d.set_defaults(fn=cmd_down)
    r = sub.add_parser("run")
    r.add_argument("--sbatch", required=True)
    r.add_argument("--instance", required=True, help="public IP")
    r.add_argument("--identity", default=str(Path.home() / ".ssh/lambda"))
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
