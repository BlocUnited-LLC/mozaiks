"""Project existing observer records into bounded public evidence; never raw logs/state."""
import base64
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
import ntpath
from xml.etree import ElementTree
import argparse
import io
import subprocess
import tarfile

RECIPIENT = 'age1vvcnzq66ujctrj2cq2l9mnv4f7ce6x4t58dgnqfu3es57ns69ets6hjg7z'
RECIPIENT_SHA256 = hashlib.sha256(RECIPIENT.encode('ascii')).hexdigest()
LIMIT = 100 * 1024 * 1024
TEXT_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.yaml', '.yml', '.md', '.txt', '.css', '.html', '.svg'}
BINDINGS = {'source_sha', 'source_tree', 'package_sha256', 'controller_sha', 'run_id', 'stage'}
AUTH_FIELDS = {'authorization', 'cookie', 'set-cookie', 'access_token', 'refresh_token',
               'id_token', 'client_secret', 'api_key', 'password', 'code_verifier',
               'code_challenge', 'nonce', 'state', 'storage_state', 'headers'}
KINDS = {'model', 'semantic', 'generated', 'usage', 'result'}


class EvidenceError(ValueError):
    """Safe codes only: never include rejected paths, bytes or tool stderr."""


def require(condition, code):
    if not condition:
        raise EvidenceError(code)


def safe_name(name):
    require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_./-]+', name), 'unsafe_path')
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(p not in {'..', '.'} and not ntpath.isreserved(p)
            and (not p.startswith('.') or p == '.github' and name.startswith('generated/')) for p in path.parts), 'unsafe_path')
    require(str(path) == name and not any(p.lower() in {
        'node_modules', 'logs', 'auth-state', 'storage-state', 'recovery', 'dumps', 'cache'
    } for p in path.parts), 'prohibited_path')
    require(path.suffix.lower() in TEXT_SUFFIXES | {'.jsonl'} or path.name in {'Dockerfile', 'env.example'}, 'prohibited_type')
    require(not any('dump' in p.lower() or p.lower().startswith('env.') and p != 'env.example' for p in path.parts), 'prohibited_path')
    return path


def check_fields(value):
    if isinstance(value, dict):
        require(not AUTH_FIELDS.intersection(str(k).lower() for k in value), 'prohibited_field')
        for child in value.values():
            check_fields(child)
    elif isinstance(value, list):
        for child in value:
            check_fields(child)


def check_content(name, data):
    # Existing OSS publication and generated-app checks, not a new scanner.
    from scripts.package_content_guard import PROHIBITED_CONTENT_PATTERNS, PROHIBITED_PATH_PATTERNS
    from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import _SECRET_VALUE_PATTERNS
    require(not any(p.search(name) for p in PROHIBITED_PATH_PATTERNS), 'prohibited_path')
    text = data.decode('utf-8')
    require(not any(p.search(text) for _, p in PROHIBITED_CONTENT_PATTERNS)
            and not any(p.search(text) for p in _SECRET_VALUE_PATTERNS), 'prohibited_content')
    # The existing exporter's credential check also covers private selections.
    require(not re.search(r'(?:ghp_|gho_|github_pat_|sk-proj-|sk-ant-)[A-Za-z0-9_-]{25,}|https?://[^\s/]+:[^\s/]+@', text), 'prohibited_content')
    if name.endswith('.json'):
        check_fields(json.loads(text))
    elif name.endswith('.jsonl'):
        for line in text.splitlines():
            check_fields(json.loads(line))


def selected_payload(selection_path):
    selection_path = selection_path.resolve()
    spec = json.loads(selection_path.read_text(encoding='utf-8'))
    require(set(spec) == {'binding', 'status', 'files'}, 'invalid_selection')
    require(set(spec['binding']) == BINDINGS, 'invalid_binding')
    require(spec['status'] in {'partial', 'failed', 'complete'}, 'invalid_status')
    require(all(re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', str(v)) for v in spec['binding'].values()), 'invalid_binding')
    require(bool(spec['files']), 'missing_required_output')
    entries, hashes, total = {}, {}, 0
    for item in spec['files']:
        require(set(item) == {'path', 'kind'} and item['kind'] in KINDS, 'invalid_entry')
        name = item['path']
        rel = safe_name(name)
        require((item['kind'] in {'model', 'semantic', 'generated'} and rel.parts[0] == item['kind'])
                or (item['kind'] in {'usage', 'result'} and name == item['kind'] + ('.jsonl' if item['kind'] == 'usage' else '.json')), 'invalid_entry')
        target = selection_path.parent / Path(*rel.parts)
        require(not any(p.is_symlink() for p in [target, *target.parents]), 'link_refused')
        require(target.is_file() and target.stat().st_nlink == 1, 'missing_or_linked_output')
        require(target.stat().st_size <= LIMIT, 'size_overflow')
        data = target.read_bytes()
        total += len(data)
        require(total <= LIMIT, 'size_overflow')
        check_content(name, data)
        require(name.casefold() not in {p.casefold() for p in entries}, 'duplicate_entry')
        entries[name] = data
        hashes[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'kind': item['kind']}
    require({'usage.jsonl', 'result.json'}.issubset(entries), 'missing_required_output')
    manifest = {'schema': 'f042bl.private-evidence.v1', 'binding': spec['binding'], 'status': spec['status'], 'files': hashes}
    entries['manifest.json'] = json.dumps(manifest, sort_keys=True, allow_nan=False).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in sorted(entries.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), 0o600, 0
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue(), manifest


def age_run(binary, arguments, data):
    version = subprocess.run([str(binary), '--version'], capture_output=True, check=False)
    require(version.returncode == 0 and version.stdout.strip() == b'v1.3.2', 'unqualified_age')
    result = subprocess.run([str(binary), *arguments], input=data, capture_output=True, check=False)
    require(result.returncode == 0, 'age_failed')
    return result.stdout


def export_private(selection, out, binary, recipient):
    require(recipient == RECIPIENT, 'recipient_mismatch')
    payload, manifest = selected_payload(selection)
    ciphertext = age_run(binary, ['-r', RECIPIENT], payload)
    require(ciphertext.startswith(b'age-encryption.org/v1\n'), 'invalid_ciphertext')
    outer = {'schema': 'f042bl.encrypted-evidence.v1', 'artifact': 'private-evidence.age',
             'sha256': hashlib.sha256(ciphertext).hexdigest(), 'bytes': len(ciphertext),
             'recipient_sha256': RECIPIENT_SHA256, 'run_id': manifest['binding']['run_id'], 'status': manifest['status']}
    outer_bytes = json.dumps(outer, indent=2).encode()
    out.mkdir(exist_ok=True)
    require(not any((out / n).exists() for n in ('private-evidence.age', 'encrypted-evidence.json')), 'output_exists')
    require(not any(p.is_symlink() for p in out.rglob('*')), 'link_refused')
    existing = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    require(existing + len(ciphertext) + len(outer_bytes) <= LIMIT, 'size_overflow')
    with (out / 'private-evidence.age').open('xb') as stream:
        stream.write(ciphertext)
    with (out / 'encrypted-evidence.json').open('xb') as stream:
        stream.write(outer_bytes)
    return outer


def recover_private(archive_path, outer_path, identity, binary, destination, expected_binding):
    outer = json.loads(outer_path.read_text())
    data = archive_path.read_bytes()
    require(len(data) <= LIMIT and len(data) == outer['bytes'] and hashlib.sha256(data).hexdigest() == outer['sha256']
            and outer['recipient_sha256'] == RECIPIENT_SHA256, 'ciphertext_integrity')
    # age may emit partial plaintext before the final authentication check.
    # Never parse/write any of it unless the entire utility invocation succeeds.
    plaintext = age_run(binary, ['-d', '-i', str(identity)], data)
    require(len(plaintext) <= LIMIT, 'size_overflow')
    entries, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode='r:gz') as archive:
        for member in archive:
            safe_name(member.name)
            require(member.isfile() and member.name.casefold() not in {p.casefold() for p in entries}, 'unsafe_member')
            total += member.size
            require(total <= LIMIT, 'size_overflow')
            entries[member.name] = archive.extractfile(member).read()
    require('manifest.json' in entries, 'missing_manifest')
    manifest = json.loads(entries.pop('manifest.json'))
    require(manifest['schema'] == 'f042bl.private-evidence.v1' and manifest['binding'] == expected_binding
            and manifest['binding']['run_id'] == outer['run_id'] and manifest['status'] == outer['status']
            and set(manifest['files']) == set(entries), 'manifest_identity')
    for name, content in entries.items():
        metadata = manifest['files'][name]
        require(len(content) == metadata['bytes'] and hashlib.sha256(content).hexdigest() == metadata['sha256'], 'plaintext_integrity')
        check_content(name, content)
    require(not destination.exists(), 'destination_exists')
    destination.mkdir()
    for name, content in entries.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest

def export_public(root):
    root = Path(root).resolve()
    out = root / "f042b-receipts"
    out.mkdir(exist_ok=True)
    summary = {"pytest": [], "stages": [], "limit_bytes": 104857600}
    # Only the trusted dependency/source image build is eligible. Never copy pytest,
    # app, issuer or browser logs. No secret/build-arg/token is passed to this build.
    build_log = root / "f042b-image-build.log"
    if build_log.exists():
        data = build_log.read_bytes()
        assert len(data) <= 10 * 1024 * 1024, "Required build log exceeds bound; no silent truncation"
        assert not re.search(rb"(?:ghp_|gho_|github_pat_|sk-proj-|sk-ant-)[A-Za-z0-9_-]{25,}|https?://[^\s/]+:[^\s/]+@", data)
        (out / "trusted-image-build.log").write_bytes(data)
    xml = root / "f042b-paired.xml"
    if xml.exists():
        for case in ElementTree.parse(xml).findall(".//testcase"):
            failures = [child for child in case if child.tag in {"failure", "error"}]
            summary["pytest"].append({"name": case.get("name"), "seconds": case.get("time"),
                "failure_locations": sorted(set(re.findall(r"(?:tests|factory_app|mozaiksai)/[A-Za-z0-9_./-]+\.py:[0-9]+", "\n".join(child.text or "" for child in failures)))),
                "status": next((key for key in ("failure", "error", "skipped") if case.find(key) is not None), "passed")})
    for path in sorted((root / "f042bl-live").rglob("*-diagnostic.json")):
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
            details = event.get("details", {})
            item = {key: event.get(key) for key in ("check", "status")}
            item.update({key: details.get(key) for key in ("request_digest", "source_content_sha256",
                "observer_run_id", "run_id", "image_id", "exit_code", "browser_execution", "coverage",
                "observer_resources", "observer_storage_peaks", "browser_failure_signals") if key in details})
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
                visit(details.get("report", {}))
                item["tests"] = tests
            stage["checks"].append(item)
        summary["stages"].append(stage)
    summary["live_test_passed"] = (len(summary["pytest"]) == 1 and
        {row["name"] for row in summary["pytest"]} == {"test_live_genesis_campaign"} and
        all(row["status"] == "passed" for row in summary["pytest"]))
    (out / "paired-summary.json").write_text(json.dumps(summary, indent=2))
    assert sum(path.stat().st_size for path in out.rglob("*") if path.is_file()) <= 104857600, "Evidence exceeds authorized cap; upload refused"
    print(json.dumps({"pytest": summary["pytest"], "stages": len(summary["stages"]), "live_test_passed": summary["live_test_passed"]}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--age', type=Path)
    parser.add_argument('--recipient')
    args = parser.parse_args()
    try:
        export_public(args.root)
        if args.selection:
            export_private(args.selection, args.root / 'f042b-receipts', args.age, args.recipient)
    except Exception:
        print('{"evidence_export":"failed","upload_allowed":false}')
        sys.exit(1)
