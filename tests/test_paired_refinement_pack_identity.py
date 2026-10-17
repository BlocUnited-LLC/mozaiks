"""A saved browser receipt cannot silently select another refinement pack."""
import json
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    verify_pack_integrity,
)
from tests.test_paired_browser_refinement import _baseline_packs


def baseline():
    source = Path(__file__).resolve().parents[1] / "factory_app/build_context/messaging"
    identity = verify_pack_integrity(source, "messaging")
    receipt = {"capability_packs": [{
        "id": "messaging", "pack_source_path": str(source),
        "version": identity["version"], "digest": identity["digest"],
    }]}
    return receipt, identity


def test_refinement_uses_the_exact_verified_saved_pack():
    receipt, identity = baseline()
    files = {".mozaiks/pack_provenance.json": json.dumps({"packs": [identity]})}
    assert _baseline_packs(receipt, files) == receipt["capability_packs"]


@pytest.mark.parametrize("field,value", [
    ("version", "0.0.0"), ("digest", "sha256:" + "0" * 64), ("source", "another-source"),
])
def test_refinement_rejects_provenance_that_disagrees_with_selected_source(field, value):
    receipt, identity = baseline()
    identity[field] = value
    with pytest.raises(AssertionError):
        _baseline_packs(receipt, {".mozaiks/pack_provenance.json": json.dumps({"packs": [identity]})})


def test_refinement_rejects_a_stale_selected_digest_even_when_saved_provenance_matches():
    receipt, identity = baseline()
    receipt["capability_packs"][0]["digest"] = identity["digest"] = "sha256:" + "0" * 64
    with pytest.raises(PackIntegrityError, match="digest"):
        _baseline_packs(receipt, {".mozaiks/pack_provenance.json": json.dumps({"packs": [identity]})})
