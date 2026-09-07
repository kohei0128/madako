import json
import warnings
from datetime import datetime
from pathlib import Path

from data_profile.exceptions import DataProfileError
from data_profile.models import ColumnMetadata, ModelProfile


class ArtifactError(DataProfileError):
    pass


def read_dbt_artifacts(project_dir: Path) -> list[ModelProfile]:
    target_dir = project_dir / "target"
    manifest_path = target_dir / "manifest.json"
    catalog_path = target_dir / "catalog.json"
    if not manifest_path.exists():
        raise ArtifactError(f"dbt manifest not found: {manifest_path}")

    manifest = _read_json(manifest_path)
    catalog = _read_json(catalog_path) if catalog_path.exists() else {"nodes": {}, "sources": {}}
    project_name = manifest.get("metadata", {}).get("project_name")
    if not project_name:
        raise ArtifactError("manifest metadata.project_name is missing")
    _warn_if_catalog_is_stale(manifest, catalog)

    tests_by_node = _collect_tests(manifest)
    resources: list[ModelProfile] = []
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "model" or node.get("package_name") != project_name:
            continue
        resources.append(_to_model(node, catalog.get("nodes", {}).get(node["unique_id"]), tests_by_node))
    for source in manifest.get("sources", {}).values():
        if source.get("package_name") != project_name:
            continue
        resources.append(_to_model(source, catalog.get("sources", {}).get(source["unique_id"]), tests_by_node))
    return sorted(resources, key=lambda item: (item.database, item.schema_name, item.name))


def _to_model(node: dict, catalog_node: dict | None, tests_by_node: dict[str, list[str]]) -> ModelProfile:
    resource_type = node["resource_type"]
    catalog_columns = (catalog_node or {}).get("columns", {})
    manifest_columns = node.get("columns", {})
    ordered_names = sorted(
        set(manifest_columns) | set(catalog_columns),
        key=lambda name: (catalog_columns.get(name, {}).get("index", 10**9), list(manifest_columns).index(name) if name in manifest_columns else 10**9),
    )
    columns = [ColumnMetadata(
        name=name,
        data_type=(catalog_columns.get(name, {}).get("type") or manifest_columns.get(name, {}).get("data_type") or "UNKNOWN").upper(),
        description=manifest_columns.get(name, {}).get("description", ""),
    ) for name in ordered_names]
    identifier = node.get("alias") if resource_type == "model" else node.get("identifier", node["name"])
    database = node.get("database") or ""
    schema = node.get("schema") or ""
    relation_name = f"`{database}.{schema}.{identifier}`" if database and schema else identifier
    config = node.get("config", {})
    profiling = config.get("meta", {}).get("profiling", {})
    upstream_ids = list(dict.fromkeys(
        dependency
        for dependency in node.get("depends_on", {}).get("nodes", [])
        if dependency.startswith(("model.", "source."))
    ))
    return ModelProfile(
        unique_id=node["unique_id"],
        resource_type=resource_type,
        name=node["name"],
        database=database,
        schema_name=schema,
        relation_name=relation_name,
        description=node.get("description", ""),
        materialization=config.get("materialized", "source") if resource_type == "model" else "source",
        tags=config.get("tags", node.get("tags", [])),
        tests=tests_by_node.get(node["unique_id"], []),
        upstream_ids=upstream_ids,
        columns=columns,
        profiling=profiling,
        profiles=[],
    )


def _collect_tests(manifest: dict) -> dict[str, list[str]]:
    tests: dict[str, list[str]] = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "test":
            continue
        metadata = node.get("test_metadata") or {}
        test_name = metadata.get("name") or node.get("name", "test")
        namespace = metadata.get("namespace")
        label = f"{namespace}.{test_name}" if namespace else test_name
        for dependency in node.get("depends_on", {}).get("nodes", []):
            tests.setdefault(dependency, []).append(label)
    return tests


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactError(f"could not read dbt artifact {path}: {error}") from error


def _warn_if_catalog_is_stale(manifest: dict, catalog: dict) -> None:
    manifest_time = manifest.get("metadata", {}).get("generated_at")
    catalog_time = catalog.get("metadata", {}).get("generated_at")
    if not manifest_time or not catalog_time:
        return
    if datetime.fromisoformat(catalog_time.replace("Z", "+00:00")) < datetime.fromisoformat(manifest_time.replace("Z", "+00:00")):
        warnings.warn("catalog.json is older than manifest.json; column types may be stale", stacklevel=2)
