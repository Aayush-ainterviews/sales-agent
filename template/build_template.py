"""
Phase 1: build the real sandbox template on E2B.

What gets baked in (and why — docs/architecture-decisions.md):
- node 22 (spike finding: base image's v20.9 is too old for pi)
- pi, version-pinned (what the spike validated)
- pi-config/ -> /home/user/.pi/agent/   (settings, AGENTS.md, 3 skills)
- workspace/GOAL.md -> /home/user/GOAL.md  (AGENTS.md tells the agent to read it)

What is deliberately ABSENT:
- zeptomail-email skill — Q9: the agent has no send capability, ever
- any secret / env value — Q11: secrets enter only at daemon start via pty.create(envs=...)
  (env names the skills expect: GEMINI_API_KEY, ORIGAMI_API_KEY, APOLLO_API_KEY, APIFY_TOKEN)
- start command — Q8: the backend owns the daemon lifecycle

Run from repo root or template/:  .venv/bin/python template/build_template.py
Then verify:                      .venv/bin/python template/verify_template.py
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from e2b import Template

ALIAS = "sales-agent-v2"   # v2: adds the submit-batch skill + /home/user/outbox (Phase 4)
PI_VERSION = "0.80.6"

template = (
    Template()
    .from_base_image()
    .run_cmd("sudo npm install -g n && sudo n 22")
    .run_cmd(
        "sudo npm install -g --ignore-scripts "
        f"@earendil-works/pi-coding-agent@{PI_VERSION}"
    )
    .copy("pi-config/", "/home/user/.pi/agent/")
    .copy("workspace/GOAL.md", "/home/user/GOAL.md")
    .run_cmd("mkdir -p /home/user/outbox")   # where the agent writes Draft Batches (submit-batch skill)
    .run_cmd("sudo chown -R user:user /home/user/.pi /home/user/GOAL.md /home/user/outbox")
    .run_cmd("pi --version && ls /home/user/.pi/agent/skills/")  # build-time sanity check
)


def main() -> int:
    load_dotenv()
    os.chdir(Path(__file__).parent)  # copy() sources are relative to template/

    print(f"== building '{ALIAS}' on E2B cloud (few minutes, all steps run on their servers) ==")
    t0 = time.time()
    Template.build(
        template,
        ALIAS,
        cpu_count=2,
        memory_mb=2048,
        on_build_logs=lambda entry: print(f"   [build] {entry}"),
    )
    print(f"== done in {time.time() - t0:.0f}s — stored in our E2B account as '{ALIAS}' ==")
    print("next: .venv/bin/python template/verify_template.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
