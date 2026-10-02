"""Export API contracts without starting the application or contacting a provider."""

import json
from pathlib import Path

from rtagent.domain import RunEvent
from rtagent.main import create_app

schema = create_app().openapi()
schema["components"]["schemas"]["RunEvent"] = RunEvent.model_json_schema()
destination = Path(__file__).resolve().parents[1] / "web" / "src" / "api" / "schema.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(schema, indent=2), encoding="utf-8")
print("Exported web/src/api/schema.json")
