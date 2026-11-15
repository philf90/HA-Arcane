"""Kurzfassung der OpenAPI-Datei einer laufenden Arcane-Instanz.

Aufruf: python3 .github/scripts/arcane_openapi.py openapi.json > datei.json

Die OpenAPI-Datei liefert Arcane ohne Anmeldung unter ``<url>/api/openapi.json``
aus (backend/api/api.go, SpecRoutes). Sie enthaelt nur die Routen, die huma
dokumentiert: keine WebSocket-Routen und keine als ``Hidden`` markierten.
Erfasst werden je Operation Methode, Pfad, operationId und die erlaubten Werte
von Query-Parametern mit ``enum`` (etwa ``?action=``). Adressen der Instanz
(``servers``) uebernimmt die Kurzfassung nicht.

tests/test_arcane_routen.py vergleicht sie mit den Routen, die
.github/scripts/arcane_routen.py aus dem Quelltext liest.
"""

from __future__ import annotations

import json
import sys

_METHODS = ("get", "post", "put", "patch", "delete", "head")


def _resolve(spec: dict, node: dict) -> dict:
    while isinstance(node, dict) and "$ref" in node:
        target = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target[part]
        node = target
    return node


def extract(spec: dict) -> dict:
    operations = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in _METHODS:
                continue
            entry = {
                "method": method.upper(),
                "path": path,
                "operationId": operation.get("operationId"),
            }
            enums = {}
            for parameter in operation.get("parameters", []):
                parameter = _resolve(spec, parameter)
                if parameter.get("in") != "query":
                    continue
                values = _resolve(spec, parameter.get("schema", {})).get("enum")
                if values:
                    enums[parameter["name"]] = sorted(values)
            if enums:
                entry["query_enums"] = enums
            operations.append(entry)
    operations.sort(key=lambda o: (o["path"], o["method"]))
    return {
        "arcane": spec["info"]["version"],
        "openapi": spec["openapi"],
        "operationen": operations,
    }


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as handle:
        spec = json.load(handle)
    json.dump(extract(spec), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
