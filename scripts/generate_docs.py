"""Generates docs/API.md, docs/openapi.json and docs/DATABASE.md FROM THE CODE.

Because they are generated, these docs cannot drift away from what the code really does.
Run after changing endpoints or models:  backend\\.venv\\Scripts\\python scripts\\generate_docs.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import Enum as SAEnum  # noqa: E402

from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

DOCS = ROOT / "docs"

# Who may call what (mirrors the dependencies in app/api/deps.py)
AUTH_HINT = {"AdminOnly": "admin", "StaffOnly": "admin, sales agent", "AnyRole": "any logged-in role"}


def api_docs() -> None:
    spec = app.openapi()
    (DOCS / "openapi.json").write_text(json.dumps(spec, indent=2), encoding="utf-8", newline="\n")
    groups: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            tag = (op.get("tags") or ["other"])[0]
            security = ("internal key (n8n)" if path.startswith("/api/v1/internal") or path == "/api/v1/agent/process"
                        else "Meta signature / verify token" if "/webhooks/" in path
                        else "public" if path == "/health" or path.endswith("/auth/login")
                        else "JWT")
            summary = (op.get("summary") or "").strip()
            desc = (op.get("description") or "").strip().split("\n")[0]
            groups[tag].append((method.upper(), path, security, desc or summary))
    lines = ["# API reference", "",
             "_Generated from the FastAPI code by `scripts/generate_docs.py`. Interactive version: "
             "`http://127.0.0.1:8000/docs` (development only). Machine-readable: [openapi.json](openapi.json)._", "",
             f"**{sum(len(v) for v in groups.values())} endpoints.** Base path `/api/v1`. "
             "Dashboard calls send `Authorization: Bearer <JWT>` from `POST /api/v1/auth/login`; roles are "
             "enforced on the server (admin · sales agent · viewer).", ""]
    for tag in sorted(groups):
        lines += [f"## {tag}", "", "| Method | Path | Auth | Purpose |", "|---|---|---|---|"]
        for method, path, sec, desc in sorted(groups[tag], key=lambda r: (r[1], r[0])):
            lines.append(f"| `{method}` | `{path}` | {sec} | {desc.replace('|', '/')} |")
        lines.append("")
    (DOCS / "API.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"docs/API.md: {sum(len(v) for v in groups.values())} endpoints, docs/openapi.json")


def col_type(col) -> str:
    t = col.type
    if isinstance(t, SAEnum):
        return "enum"
    name = type(t).__name__.lower()
    return {"uuid": "uuid", "string": "varchar", "text": "text", "integer": "int", "biginteger": "bigint",
            "boolean": "bool", "datetime": "timestamptz", "numeric": "numeric", "jsonb": "jsonb",
            "tsvector": "tsvector", "inet": "inet"}.get(name, name)


def db_docs() -> None:
    tables = Base.metadata.sorted_tables
    er = ["```mermaid", "erDiagram"]
    rels = []
    for t in tables:
        er.append(f"    {t.name} {{")
        for c in t.columns:
            key = "PK" if c.primary_key else ("FK" if c.foreign_keys else ("UK" if c.unique else ""))
            er.append(f"        {col_type(c)} {c.name}{' ' + key if key else ''}")
        er.append("    }")
        for c in t.columns:
            for fk in c.foreign_keys:
                one = "|o" if c.nullable else "||"
                rels.append(f"    {fk.column.table.name} {one}--o{{ {t.name} : \"{c.name}\"")
    er += sorted(set(rels)) + ["```"]

    lines = ["# Database schema", "",
             "_Generated from the SQLAlchemy models by `scripts/generate_docs.py`. Changes are applied with Alembic "
             "migrations in `database/migrations/versions/` (0001 initial, 0002 AI processing + lead signals + "
             "message order, 0003 error recovery)._", "",
             f"PostgreSQL 16/17 · **{len(tables)} tables** · UUID primary keys · `created_at`/`updated_at` on every "
             "table (audit_logs is append-only).", "", "## Entity-relationship diagram", "", *er, "",
             "## Tables", ""]
    for t in tables:
        doc = (Base.registry._class_registry.get(next((m.class_.__name__ for m in Base.registry.mappers
                                                        if m.local_table is t), ""), None))
        lines += [f"### `{t.name}`", ""]
        if doc is not None and doc.__doc__ and not doc.__doc__.startswith(t.name):
            lines += [doc.__doc__.strip().split("\n")[0], ""]
        lines += ["| Column | Type | Null | Notes |", "|---|---|---|---|"]
        for c in t.columns:
            notes = []
            if c.primary_key:
                notes.append("primary key")
            for fk in c.foreign_keys:
                notes.append(f"→ `{fk.target_fullname}` ({fk.ondelete or 'NO ACTION'})")
            if c.unique:
                notes.append("unique")
            if isinstance(c.type, SAEnum):
                notes.append("one of: " + ", ".join(f"`{v}`" for v in c.type.enums))
            if c.computed is not None:
                notes.append("computed by PostgreSQL")
            lines.append(f"| `{c.name}` | {col_type(c)} | {'yes' if c.nullable else 'no'} | {'; '.join(notes)} |")
        idx = sorted(t.indexes, key=lambda i: i.name)   # a set: sort for a stable, repeatable output
        if idx:
            lines += ["", "Indexes: " + " · ".join(
                f"`{i.name}` ({', '.join(c.name for c in i.columns)}"
                + (", unique" if i.unique else "")
                + (f", where {i.dialect_options['postgresql'].get('where')}" if i.dialect_options['postgresql'].get('where') is not None else "")
                + ")" for i in idx)]
        lines.append("")
    (DOCS / "DATABASE.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"docs/DATABASE.md: {len(tables)} tables, {len(rels)} relationships")


if __name__ == "__main__":
    api_docs()
    db_docs()
