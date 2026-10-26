"""Offline task transport proof. No provider, database or browser calls."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import export_evidence as export

AGE = Path(os.environ['F042BL_TEST_AGE'])
IDENTITY = Path(os.environ['F042BL_TEST_IDENTITY'])


@pytest.fixture
def selection(tmp_path):
    content = {
        'model/0001.json': b'{"prompt":"synthetic ordinary intent","completion":"unmodified answer","elapsed":0.000123}',
        'generated/app/ui/Board.jsx': b'export default function Board() { return <main>Exact bytes</main>; }\n',
        'usage.jsonl': b'{"event":"reserved","attempt":1,"reserved_usd":"0.03193344"}\n',
        'result.json': b'{"stage":"genesis","outcome":"failed","elapsed":0.12345}',
    }
    binding = {'source_sha': 'a'*40, 'source_tree': 'b'*40, 'package_sha256': 'c'*64,
               'controller_sha': 'd'*40, 'run_id': 'offline', 'stage': 'genesis'}
    for name, data in content.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    spec = {'binding': binding, 'status': 'failed', 'files': [
        {'path': name, 'kind': name.split('/')[0] if '/' in name else name.split('.')[0]} for name in content]}
    path = tmp_path / 'selection.json'
    path.write_text(json.dumps(spec))
    return path, content, spec


def encrypt(selection, tmp_path):
    path, _, spec = selection
    out = tmp_path / 'public'
    outer = export.export_private(path, out, AGE, export.RECIPIENT)
    return out, outer, spec


def test_round_trip_exact_failed_evidence_and_public_sanitization(selection, tmp_path, capsys):
    out, outer, spec = encrypt(selection, tmp_path)
    destination = tmp_path / 'recovered'
    result = export.recover_private(out/'private-evidence.age', out/'encrypted-evidence.json', IDENTITY, AGE, destination, spec['binding'])
    assert result['status'] == 'failed'
    for name, data in selection[1].items():
        assert (destination/name).read_bytes() == data
    assert set(p.name for p in out.iterdir()) == {'private-evidence.age', 'encrypted-evidence.json'}
    assert 'Board' not in json.dumps(outer) and 'prompt' not in json.dumps(outer)
    assert 'synthetic ordinary intent' not in (out/'private-evidence.age').read_bytes().decode('latin1')
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('corruption', ['wrong_key', 'bitflip', 'truncate', 'binding'])
def test_invalid_decryption_never_extracts(selection, tmp_path, corruption):
    out, outer, spec = encrypt(selection, tmp_path)
    identity = IDENTITY
    archive = out/'private-evidence.age'
    if corruption == 'wrong_key':
        identity = tmp_path/'wrong.agekey'
        result = subprocess.run([str(AGE.with_name('age-keygen.exe')), '-o', str(identity)], capture_output=True)
        assert result.returncode == 0
    elif corruption != 'binding':
        data = archive.read_bytes()
        data = data[:-1] if corruption == 'truncate' else data[:-1] + bytes([data[-1] ^ 1])
        archive.write_bytes(data)
        # Also update outer digest to test age authentication, not just hashing.
        outer.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        (out/'encrypted-evidence.json').write_text(json.dumps(outer))
    else:
        spec['binding']['run_id'] = 'wrong'
    dest = tmp_path/'must-not-exist'
    with pytest.raises(export.EvidenceError):
        export.recover_private(archive, out/'encrypted-evidence.json', identity, AGE, dest, spec['binding'])
    assert not dest.exists()


@pytest.mark.parametrize('name,data', [
    ('generated/.env', b'harmless'), ('generated/../outside.txt', b'harmless'),
    ('generated/app/id.pem', b'harmless'), ('generated/database_dump.json', b'{}'),
    ('model/secret.json', json.dumps({'text': 'sk-proj-'+'A'*35}).encode()),
    ('model/auth.json', b'{"headers":{"Authorization":"synthetic"}}'),
    ('semantic/state.json', b'{"nonce":"synthetic-nonce"}'),
    ('semantic/key.json', b'{"value":"-----BEGIN PRIVATE KEY-----"}'),
])
def test_prohibited_selection_has_no_upload(selection, tmp_path, name, data, capsys):
    path, _, spec = selection
    candidate = tmp_path/name
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(data)
    spec['files'].append({'path': name, 'kind': name.split('/')[0]})
    path.write_text(json.dumps(spec))
    with pytest.raises(export.EvidenceError):
        export.export_private(path, tmp_path/'public', AGE, export.RECIPIENT)
    assert not (tmp_path/'public').exists()
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('failure', ['missing', 'missing_recipient', 'wrong_recipient', 'age_failure', 'overflow', 'hardlink'])
def test_fail_closed(selection, tmp_path, monkeypatch, failure):
    path, _, spec = selection
    recipient, binary = export.RECIPIENT, AGE
    if failure == 'missing':
        (tmp_path/'result.json').unlink()
    elif failure == 'missing_recipient':
        recipient = None
    elif failure == 'wrong_recipient':
        recipient = 'age1wrong'
    elif failure == 'age_failure':
        binary = AGE.with_name('age-keygen.exe')
    elif failure == 'overflow':
        monkeypatch.setattr(export, 'LIMIT', 100)
    elif failure == 'hardlink':
        os.link(tmp_path/'result.json', tmp_path/'linked.json')
    with pytest.raises(export.EvidenceError):
        export.export_private(path, tmp_path/'public', binary, recipient)
    assert not (tmp_path/'public/private-evidence.age').exists()


def test_combined_public_private_bound(selection, tmp_path, monkeypatch):
    out = tmp_path/'public'
    out.mkdir()
    (out/'previous-summary.json').write_bytes(b'x'*5000)
    monkeypatch.setattr(export, 'LIMIT', 5200)
    with pytest.raises(export.EvidenceError, match='size_overflow'):
        export.export_private(selection[0], out, AGE, export.RECIPIENT)
    assert not (out/'private-evidence.age').exists()


def test_unselected_sensitive_files_never_read(selection, tmp_path):
    (tmp_path/'.env').write_text('sk-proj-'+'Z'*35)
    (tmp_path/'unrestricted.log').write_text('secret synthetic unrelated log')
    out, _, spec = encrypt(selection, tmp_path)
    result = export.recover_private(out/'private-evidence.age', out/'encrypted-evidence.json', IDENTITY, AGE, tmp_path/'recovered', spec['binding'])
    assert set(result['files']) == set(selection[1])


@pytest.mark.parametrize('name', ['generated/CON.txt', 'generated/aux/a.json', 'generated/file:stream.json', 'generated/./a.json'])
def test_nonportable_archive_paths_refused(name):
    with pytest.raises(export.EvidenceError):
        export.safe_name(name)


def test_cli_failure_does_not_echo_rejected_content(selection, tmp_path):
    sentinel = 'sk-proj-'+'Q'*35
    (tmp_path/'model/0001.json').write_text(json.dumps({'content': sentinel}))
    result = subprocess.run([sys.executable, str(Path(export.__file__)), str(tmp_path),
        '--selection', str(selection[0]), '--age', str(AGE), '--recipient', export.RECIPIENT],
        capture_output=True)
    assert result.returncode == 1
    assert sentinel.encode() not in result.stdout + result.stderr
    assert b'upload_allowed' in result.stdout
    assert not (tmp_path/'f042b-receipts/private-evidence.age').exists()
    assert all(sentinel.encode() not in p.read_bytes() for p in (tmp_path/'f042b-receipts').iterdir())


def test_cli_public_success_excludes_raw_logs(selection, tmp_path):
    sentinel = 'do not publish model words'
    (tmp_path/'f042bl-controller-local-only.log').write_text(sentinel)
    result = subprocess.run([sys.executable, str(Path(export.__file__)), str(tmp_path),
        '--selection', str(selection[0]), '--age', str(AGE), '--recipient', export.RECIPIENT], capture_output=True)
    assert result.returncode == 0
    assert sentinel.encode() not in result.stdout + result.stderr
    assert all(sentinel.encode() not in p.read_bytes() for p in (tmp_path/'f042b-receipts').iterdir())
