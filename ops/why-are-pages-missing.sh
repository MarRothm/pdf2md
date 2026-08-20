#!/usr/bin/env bash
# Print why each missing page range is missing, straight from the job registry.
#
#   ./ops/why-are-pages-missing.sh [job-id] [stack-name]     default stack name: pdf2md
#
# A document reported as *Converted — pages … are missing* has a recorded reason for every
# gap, one per part. Until 1.7.0 nothing displayed it: the page showed the ranges and the
# reasons stayed in the database. This reads them out of the running stack, so a diagnosis
# needs no redeploy and no guesswork.
#
# Also reports the two container facts that a fast, size-dependent failure usually turns
# out to be: an out-of-memory kill, and how often the service has restarted.
#
# THIS REPOSITORY IS NOT ON THE MAC MINI. Portainer deploys from GitHub and clones nothing
# to the host, so this script runs from a machine with a Docker context pointing at the
# mini — or not at all. When it cannot, the same answer is one paste into Portainer:
# Containers -> web -> Console -> Connect, then
#
#   python3 -c "import sqlite3;c=sqlite3.connect('file:/data/db/pdf2md.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row;j=c.execute('select * from conversion_job where part_count>1 order by created_at desc limit 1').fetchone();print(j['submitted_filename'],j['status'],j['created_at'],j['ended_at']);[print(p['first_page'],p['last_page'],p['status'],'|',p['failure_reason']) for p in c.execute('select * from conversion_part where job_id=? order by first_page',(j['id'],))]"
#
# and Containers -> web -> Inspect for `OOMKilled` and `RestartCount`. Keep the two in
# step: what this script prints is what that line prints, with the container facts added.
set -uo pipefail

JOB="${1:-}"
STACK="${2:-pdf2md}"

find_service() {
  docker ps -q \
    --filter "label=com.docker.compose.project=${STACK}" \
    --filter "label=com.docker.compose.service=$1" | head -n1
}

WEB="$(find_service web)"
DOCLING="$(find_service docling)"

if [ -z "$WEB" ]; then
  echo "could not find the web service of stack '${STACK}' — is it running?" >&2
  echo "usage: $0 [job-id] [stack-name]" >&2
  exit 2
fi

echo "== containers =="
for name in web docling; do
  id="$(find_service "$name")"
  [ -z "$id" ] && { echo "  ${name}: not running"; continue; }
  docker inspect -f "  ${name}: restarts={{.RestartCount}} oom_killed={{.State.OOMKilled}} \
memory_limit={{.HostConfig.Memory}} status={{.State.Status}}" "$id"
done

echo
echo "== the parts =="
docker exec -i "$WEB" python3 - "$JOB" <<'PY'
import os
import sqlite3
import sys

job_id = sys.argv[1] if len(sys.argv) > 1 else ""
path = os.environ.get("PDF2MD_DB_PATH", "/data/db/pdf2md.sqlite")
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

if not job_id:
    # The newest document that reported a gap — which is the one someone is asking about.
    row = conn.execute(
        "SELECT id FROM conversion_job WHERE missing_page_ranges IS NOT NULL"
        " ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id FROM conversion_job WHERE part_count > 1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        print("no split document in the registry")
        raise SystemExit(0)
    job_id = row["id"]

job = conn.execute("SELECT * FROM conversion_job WHERE id = ?", (job_id,)).fetchone()
if job is None:
    print(f"no job {job_id}")
    raise SystemExit(1)

print(f'{job["submitted_filename"]}  job={job["id"]}')
print(f'  status={job["status"]}  parts={job["parts_completed"]}/{job["part_count"]}'
      f'  submitted={job["created_at"]}  ended={job["ended_at"]}')
if job["failure_reason"]:
    print(f'  reason={job["failure_reason"]}')
print()

parts = conn.execute(
    "SELECT * FROM conversion_part WHERE job_id = ? ORDER BY first_page", (job_id,)
).fetchall()

reasons: dict[str, int] = {}
for part in parts:
    pages = f'{part["first_page"]}-{part["last_page"]}'
    size = ""
    if part["part_path"] and os.path.exists(part["part_path"]):
        size = f'  {os.path.getsize(part["part_path"]) / 1_048_576:.1f} MB'
    seconds = ""
    if part["started_at"] and part["ended_at"]:
        seconds = f'  {part["started_at"][11:19]}→{part["ended_at"][11:19]}'
    print(f'  pages {pages:<12} {part["status"]:<10}{seconds}{size}')
    if part["failure_reason"]:
        print(f'      {part["failure_reason"]}')
        reasons[part["failure_reason"]] = reasons.get(part["failure_reason"], 0) + 1

if reasons:
    print("\n== how the gaps break down ==")
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"  {count:>3} × {reason}")
PY
