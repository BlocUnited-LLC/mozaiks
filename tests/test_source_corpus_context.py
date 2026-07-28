from __future__ import annotations

from pathlib import Path

from mozaiksai.core.app_context import (
    build_source_corpus_bundle,
    build_source_corpus_catalog,
    default_context_graph_scan_policy,
    get_related_source_files,
    read_source_file_from_bundle,
    search_source_corpus,
)
from mozaiksai.core.app_context.scan_policy import collect_source_scan_file_map


def test_source_corpus_preserves_retrievable_redacted_code_context(tmp_path: Path) -> None:
    (tmp_path / "src" / "routes").mkdir(parents=True)
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "server").mkdir()
    (tmp_path / "src" / "routes" / "listings.tsx").write_text(
        "import { fetchListings } from '../api/listings';\n"
        "export function ListingPage() {\n"
        "  return <section>{fetchListings()}</section>;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "api" / "listings.ts").write_text(
        "export async function fetchListings() {\n"
        "  return fetch('/api/listings');\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "config.ts").write_text(
        'export const OPENAI_API_KEY = "example-redacted-value";\n',
        encoding="utf-8",
    )
    (tmp_path / "server" / "main.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/api/listings')\n"
        "def list_listings():\n"
        "    return []\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("IGNORED_ENV_VALUE=must-not-index\n", encoding="utf-8")

    scan = collect_source_scan_file_map(
        [("", tmp_path)],
        policy=default_context_graph_scan_policy({"max_files": 50}),
    )
    bundle = build_source_corpus_bundle(app_id="app_1", scan_result=scan)

    assert bundle.schema_version == "mozaiks.source_context.bundle.v1"
    assert "src/routes/listings.tsx" in bundle.file_contents
    assert ".env" not in bundle.file_contents
    assert "example-redacted-value" not in bundle.file_contents["src/config.ts"]
    assert "[REDACTED]" in bundle.file_contents["src/config.ts"]

    files_by_path = {item.path: item for item in bundle.files}
    assert files_by_path["src/routes/listings.tsx"].role == "route"
    assert files_by_path["src/api/listings.ts"].role == "api_client"
    assert files_by_path["server/main.py"].role == "entrypoint"
    assert any(symbol.name == "ListingPage" for symbol in bundle.symbols)

    read_result = read_source_file_from_bundle(bundle, "src/routes/listings.tsx")
    assert read_result["present"] is True
    assert "fetchListings" in read_result["content"]
    assert read_result["symbols"]

    search_results = search_source_corpus(bundle, "listings API", max_results=5)
    assert search_results
    assert any(result["path"] == "src/api/listings.ts" for result in search_results)

    related = get_related_source_files(bundle, "src/routes/listings.tsx")
    assert any(item["path"] == "src/api/listings.ts" and item["relationship"] == "imports" for item in related)

    catalog = build_source_corpus_catalog(bundle, max_files=10, max_chunks=4)
    assert catalog["file_count"] == len(bundle.files)
    assert catalog["chunk_count"] == len(bundle.chunks)
    assert catalog["role_counts"]["route"] == 1
    assert catalog["important_files"][0]["path"] in {
        "server/main.py",
        "src/routes/listings.tsx",
        "src/api/listings.ts",
    }
