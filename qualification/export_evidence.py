"""Project existing observer records into bounded public evidence; never raw logs/state."""
import base64
import hashlib
import json
import sys
from pathlib import Path
from xml.etree import ElementTree

root = Path(sys.argv[1]).resolve()
out = root / "f042b-receipts"
out.mkdir(exist_ok=True)
summary = {"pytest": [], "stages": [], "limit_bytes": 104857600}
xml = root / "f042b-paired.xml"
if xml.exists():
    for case in ElementTree.parse(xml).findall(".//testcase"):
        summary["pytest"].append({"name": case.get("name"), "seconds": case.get("time"),
            "status": next((key for key in ("failure", "error", "skipped") if case.find(key) is not None), "passed")})
for path in sorted((root / "f042b-paired").rglob("*-diagnostic.json")):
    raw = json.loads(path.read_text())
    stage = {"input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "checks": [],
             **{key: raw.get(key) for key in ("returncode", "timed_out", "cleanup_failed", "output_truncated", "contained")}}
    for line in raw.get("stdout", "").splitlines():
        prefix = "@@mozaiks-runtime-smoke@@ "
        if not line.startswith(prefix):
            continue
        event = json.loads(line[len(prefix):])
        if event.get("event") != "outcome":
            continue
        item = {key: event.get(key) for key in ("check", "status", "request_digest", "source_content_sha256",
            "observer_run_id", "run_id", "image_id", "exit_code", "browser_execution", "coverage",
            "observer_resources", "observer_storage_peaks", "browser_failure_signals") if key in event}
        if event.get("check") == "requirement.browser":
            # Finite public identity and outcome projection, excluding error text,
            # stdout, request URLs, auth storage, realm import and callback state.
            tests = []
            def visit(suite, tests=tests):
                for spec in suite.get("specs", []):
                    for test in spec.get("tests", []):
                        pictures = []
                        for result in test.get("results", []):
                            for attachment in result.get("attachments", []):
                                if attachment.get("contentType") != "image/png" or not attachment.get("body"):
                                    continue
                                picture = base64.b64decode(attachment["body"], validate=True)
                                assert picture.startswith(b"\x89PNG\r\n\x1a\n") and len(picture) <= 5 * 1024 * 1024
                                name = hashlib.sha256(picture).hexdigest() + ".png"
                                (out / name).write_bytes(picture)
                                pictures.append(name)
                        tests.append({"title": spec.get("title"), "project": test.get("projectName"),
                            "screenshots": pictures,
                            "annotations": [a for a in test.get("annotations", []) if a.get("type") in {
                                "mozaiks.case_id", "mozaiks.route", "mozaiks.run_id", "mozaiks.candidate", "mozaiks.requirement_ids",
                                "mozaiks.requirement_inventory_digest", "mozaiks.requirement_accounting_digest"}],
                            "status": test.get("status"),
                            "results": [{key: result.get(key) for key in ("status", "duration", "retry")} for result in test.get("results", [])]})
                for child in suite.get("suites", []):
                    visit(child)
            visit(event.get("report", {}))
            item["tests"] = tests
        stage["checks"].append(item)
    summary["stages"].append(stage)
summary["complete_paired_pytest"] = (len(summary["pytest"]) == 2 and
    {row["name"] for row in summary["pytest"]} == {"test_real_paired_projects_tasks_acceptance[A]", "test_real_paired_projects_tasks_acceptance[B]"} and
    all(row["status"] == "passed" for row in summary["pytest"]))
(out / "paired-summary.json").write_text(json.dumps(summary, indent=2))
assert sum(path.stat().st_size for path in out.rglob("*") if path.is_file()) <= 104857600, "Evidence exceeds authorized cap; upload refused"
print(json.dumps({"pytest": summary["pytest"], "stages": len(summary["stages"]), "complete_paired_pytest": summary["complete_paired_pytest"]}))
