"""Tests for adopting stevenpenza.com's architecture standard (design/v0/v0.8.3.md): the generator is the pinned shared
copy, the release runs and uploads it, and the hand file has the standard's shape. The chart itself is tested by
infra/test/architecture.test.mjs, against the synthesized stack."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# stevenpenza.com's site/shared/manifest.json pins architecture v1.1.0's architecture.mjs to this digest.
PINNED = {"v1.1.0": "sha384-Ni83sqvIKH0+3i83NOqW5U5tEF/qOn9MiszYlt0L8S2kMn+/vOZnAWyBo8bxyl6t"}
KINDS = {"cdn", "api", "function", "workflow", "schedule", "mail", "queue", "topic", "table", "bucket", "key", "secret",
         "userpool", "alarm", "person", "device", "service", "project", "ci"}


def test_generator_is_the_pinned_copy():
    """Checks tools/architecture.mjs names its version in its header and is that version's bytes, as pinned in stevenpenza.com's manifest, so it was copied, not edited. Covers the copy; the generator's behaviour is stevenpenza.com's to test."""
    data = (ROOT / "tools" / "architecture.mjs").read_bytes()
    version = data.split(b"\n", 1)[0].decode().split()[2]
    assert version in PINNED, f"architecture {version} isn't a pinned version"
    assert "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode() == PINNED[version]


def test_release_draws_and_uploads_the_chart_after_the_deploy():
    """Checks tools/release.sh runs the generator for the key sos with the hand file, after the CDK deploy, outside a comment, and uploads it to architecture/sos.json. Covers the release script as written; the upload itself is checked after a release."""
    lines = [line for line in (ROOT / "tools" / "release.sh").read_text().splitlines() if not line.lstrip().startswith("#")]
    deploy = next(i for i, line in enumerate(lines) if "cdk deploy" in line)
    run = next(i for i, line in enumerate(lines) if "node tools/architecture.mjs" in line)
    assert run > deploy and "--project sos" in lines[run] and "--hand infra/architecture.hand.json" in lines[run]
    assert any("architecture/sos.json" in line and "aws s3 cp" in "\n".join(lines[i - 1:i + 1]) for i, line in enumerate(lines))


def test_hand_file_has_the_standards_shape():
    """Checks infra/architecture.hand.json is version 1, every node has an id, a known kind and a label with no id used twice, and every edge has both ends. Covers the file's form; that its ends exist in the chart is tested by infra/test/architecture.test.mjs."""
    hand = json.loads((ROOT / "infra" / "architecture.hand.json").read_text())
    assert hand["v"] == 1
    ids = [n["id"] for n in hand["nodes"]]
    assert len(ids) == len(set(ids))
    for n in hand["nodes"]:
        assert n["id"] and n["label"] and n["kind"] in KINDS, n
    for e in hand["edges"]:
        assert e["from"] and e["to"], e
