"""Regenerate schemas/*.schema.json from the pydantic contracts (run after changing src/wbs/models)."""

from __future__ import annotations

from wbs.config import REPO_ROOT
from wbs.models import export_schemas

if __name__ == "__main__":
    for path in export_schemas(REPO_ROOT / "schemas"):
        print(path.relative_to(REPO_ROOT))
