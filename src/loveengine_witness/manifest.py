"""Manifest verification compatible with the M0 package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .jsonio import read_json
from .schema import validate_schema


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "skills" / "loveengine-witness" / "skill-manifest.json"


def verify_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = read_json(path)
    source_root = ROOT if path == DEFAULT_MANIFEST else path.resolve().parents[2]

    schema_version = manifest.get("schema_version")
    if schema_version == "loveengine.skill-manifest/0.2":
        validate_schema(manifest, "skill-manifest-v2.schema.json")
    elif schema_version == "loveengine.skill-manifest/0.3":
        validate_schema(manifest, "skill-manifest-v3.schema.json")
    else:
        raise LoveEngineError(
            "unsupported_schema_version",
            str(schema_version),
        )

    refs = manifest.get("source_refs")
    hashes = manifest.get("source_hashes")
    if not isinstance(refs, list) or not isinstance(hashes, dict):
        raise LoveEngineError("invalid_manifest", "source_refs/source_hashes are required")

    for ref in refs:
        source_path = source_root / ref
        try:
            actual = sha256_prefixed(source_path.read_bytes())
        except FileNotFoundError as exc:
            raise LoveEngineError("missing_source_ref", ref, 3) from exc
        if hashes.get(ref) != actual:
            raise LoveEngineError("source_hash_mismatch", ref)

    package_view = dict(manifest)
    package_view["package_hash"] = "sha256:SELF"
    actual_package_hash = sha256_prefixed(canonical_json_bytes(package_view))
    if manifest.get("package_hash") != actual_package_hash:
        raise LoveEngineError("package_hash_mismatch", actual_package_hash)

    spec_ref = manifest.get("spec_ref")
    if spec_ref and manifest.get("spec_hash") != hashes.get(spec_ref):
        raise LoveEngineError("spec_hash_mismatch", str(spec_ref))

    return {
        "valid": True,
        "skill_id": manifest.get("skill_id"),
        "version": manifest.get("version"),
        "package_hash": actual_package_hash,
        "source_count": len(refs),
    }
