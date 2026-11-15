"""Die Routen aus dem Arcane-Quelltext stimmen mit der OpenAPI-Datei ueberein.

tests/fixtures/arcane_routen_v3.0.0.json liest .github/scripts/arcane_routen.py
aus dem Go-Quelltext von Arcane v3.0.0. tests/fixtures/arcane_openapi_v3.0.0.json
ist die Kurzfassung (.github/scripts/arcane_openapi.py) der OpenAPI-Datei, die
eine laufende Arcane-3.0.0-Instanz unter /api/openapi.json ausliefert. Bis 0.6.1
fehlten in der Routenliste 72 Operationen, deren Pfad als Konstante im Code
steht; der Vertragstest haette Aufrufe dorthin zu Unrecht abgelehnt.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
ROUTEN = json.loads(
    (FIXTURES / "arcane_routen_v3.0.0.json").read_text(encoding="utf-8")
)
OPENAPI = json.loads(
    (FIXTURES / "arcane_openapi_v3.0.0.json").read_text(encoding="utf-8")
)

_spec = importlib.util.spec_from_file_location(
    "arcane_routen", ROOT / ".github" / "scripts" / "arcane_routen.py"
)
arcane_routen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arcane_routen)

# Nur im Agent-Modus registriert (backend/internal/variable/module.go:32); die
# OpenAPI-Datei stammt von einer Instanz, die nicht als Agent laeuft.
NUR_AGENT = {
    ("GET", "/agents/{id}/templates/variables"),
    ("PUT", "/agents/{id}/templates/variables"),
}


def _openapi() -> dict[tuple[str, str], dict]:
    return {(o["method"], o["path"]): o for o in OPENAPI["operationen"]}


def test_same_arcane_version() -> None:
    assert ROUTEN["arcane"] == "v" + OPENAPI["arcane"]


def test_source_routes_match_openapi() -> None:
    """Jede dokumentierte Route steht im Quelltext-Auszug und umgekehrt."""
    quelltext = {
        (r["method"], r["path"])
        for r in ROUTEN["routen"]
        if not r.get("websocket") and not r.get("versteckt")
    }
    openapi = set(_openapi())
    assert sorted(openapi - quelltext) == []
    assert sorted(quelltext - openapi) == sorted(NUR_AGENT)


def test_hidden_routes_are_not_documented() -> None:
    """Als Hidden markierte Routen und WebSockets fehlen in der OpenAPI-Datei."""
    openapi = _openapi()
    ausgenommen = [
        r for r in ROUTEN["routen"] if r.get("websocket") or r.get("versteckt")
    ]
    assert ausgenommen
    assert [r for r in ausgenommen if (r["method"], r["path"]) in openapi] == []


def test_action_values_match_openapi() -> None:
    """Die erlaubten Werte von ?action= (bzw. ?view=) sind dieselben."""
    openapi = _openapi()
    mit_aktionen = [r for r in ROUTEN["routen"] if "actions" in r]
    assert len(mit_aktionen) > 20
    for route in mit_aktionen:
        enums = openapi[(route["method"], route["path"])].get("query_enums", {})
        assert enums.get(route["param"]) == route["actions"], route["path"]


GO_PAKET = {
    "backend/internal/demo/module.go": """package demo

const (
\tpodsPath = "/agents/{id}/podman/pods"
\tlistPath string = podsPath + "/list" // Kommentar
)

func Register(api huma.API) {
\tconst basePath = "/agents/{id}/gitops/syncs"
\tconst syncPath = basePath + "/{syncId}"
\trunsPath := "/agents/{id}/jobs/{jobId}"
\thuma.Register(api, huma.Operation{Method: http.MethodGet, Path: podsPath, Hidden: true}, h)
\thuma.Register(api, huma.Operation{Method: "POST", Path: syncPath + "/files"}, h)
\thuma.Register(api, huma.Operation{Method: http.MethodGet, Path: runsPath+"/runs"}, h)
\thandlerutil.RegisterSecured(api, handlerutil.Operation("listX", "GET", listPath,
\t\t"Summary, mit Komma", "Beschreibung", "Tag"), perm, h)
}
""",
    "backend/pkg/handlerutil/handlerutil.go": """package handlerutil

func Operation(operationID, method, path, summary, description string) huma.Operation {
\treturn huma.Operation{OperationID: operationID, Method: method, Path: path}
}
""",
}


def test_script_resolves_constants_and_helpers() -> None:
    """Das Skript loest Konstanten, Verkettungen und Hilfsfunktionen auf."""
    helpers = arcane_routen.helper_names(GO_PAKET)
    assert helpers == {"Operation"}
    gefunden = []
    for path, text in GO_PAKET.items():
        resolver = arcane_routen.PathResolver({path: text})
        gefunden += arcane_routen.routes_of_file(path, text, resolver)
        gefunden += arcane_routen.helper_routes(path, text, helpers, resolver)
    assert sorted(
        (r["method"], r["path"], bool(r.get("versteckt"))) for r in gefunden
    ) == [
        ("GET", "/agents/{id}/jobs/{jobId}/runs", False),
        ("GET", "/agents/{id}/podman/pods", True),
        ("GET", "/agents/{id}/podman/pods/list", False),
        ("POST", "/agents/{id}/gitops/syncs/{syncId}/files", False),
    ]


@pytest.mark.parametrize("expr", ["unbekannt", 'fmt.Sprintf("/x/%s", id)'])
def test_script_does_not_guess(expr: str) -> None:
    """Was sich nicht aufloesen laesst, ergibt keinen Pfad statt eines falschen."""
    resolver = arcane_routen.PathResolver({"a.go": 'const x = "/x"\n'})
    assert resolver.resolve(expr, "a.go", 100) is None
