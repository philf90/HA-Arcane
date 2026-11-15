"""Liest die API-Routen einer Arcane-Version aus deren Quelltext.

Aufruf: python3 .github/scripts/arcane_routen.py <arcane-clone> <tag> > datei.json

Das Skript liest die Go-Dateien unter backend/ als Text ueber "git show"; es
baut und startet nichts aus dem Arcane-Repo. Erfasst werden:

- huma.Operation-Literale (Methode, Pfad) aller REST-Routen unter /api,
- bei RegisterWithActionPermissions die erlaubten Werte von ?action=,
- die WebSocket-Routen aus backend/api/ws/proxy_permissions.go.

Die Datei dient den Tests als Beleg, welche Pfade Arcane anbietet
(tests/test_arcane_vertrag.py). Arcane erzeugt seine OpenAPI-Datei erst zur
Laufzeit; dieser Auszug ersetzt sie fuer die Pfade, Methoden und Aktionen.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

_METHODS = {
    "http.MethodGet": "GET",
    "http.MethodPost": "POST",
    "http.MethodPut": "PUT",
    "http.MethodPatch": "PATCH",
    "http.MethodDelete": "DELETE",
    "http.MethodHead": "HEAD",
}


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    ).stdout


def _block_end(text: str, start: int) -> int:
    """Index der schliessenden Klammer zum "{" an start; Strings uebersprungen."""
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char in '"`':
            end = text.index(char, i + 1)
            while char == '"' and text[end - 1] == "\\":
                end = text.index(char, end + 1)
            i = end
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("Klammer nicht geschlossen")


def _method(raw: str) -> str:
    return _METHODS.get(raw, raw.strip('"').upper())


def routes_of_file(path: str, text: str) -> list[dict]:
    found = []
    for match in re.finditer(r"huma\.Operation\{", text):
        start = match.end() - 1
        end = _block_end(text, start)
        body = text[start:end]
        method = re.search(r"Method:\s*([\w.\"]+)", body)
        route = re.search(r'Path:\s*"([^"]+)"', body)
        if not (method and route):
            continue
        entry = {
            "method": _method(method.group(1)),
            "path": route.group(1),
            "quelle": f"{path}:{text.count(chr(10), 0, match.start()) + 1}",
        }
        before = text[max(0, match.start() - 80) : match.start()]
        if "RegisterWithActionPermissions" in before:
            rest = text[end:]
            perms = re.match(r'\}\s*,\s*"(\w+)"\s*,\s*map\[string\]string\{', rest)
            if perms:
                table_start = end + perms.end() - 1
                table = text[table_start : _block_end(text, table_start)]
                entry["param"] = perms.group(1)
                entry["actions"] = sorted(re.findall(r'"(\w+)"\s*:', table))
        found.append(entry)
    return found


def websocket_routes(path: str, text: str) -> list[dict]:
    found = []
    for match in re.finditer(
        r"Method:\s*([\w.]+),\s*Path:\s*\"(/agents/[^\"]+)\"", text
    ):
        found.append(
            {
                "method": _method(match.group(1)),
                "path": re.sub(r":(\w+)", r"{\1}", match.group(2)),
                "websocket": True,
                "quelle": f"{path}:{text.count(chr(10), 0, match.start()) + 1}",
            }
        )
    return found


def main() -> int:
    repo, tag = sys.argv[1], sys.argv[2]
    files = _git(repo, "ls-tree", "-r", "--name-only", tag, "backend").split()
    routes: list[dict] = []
    for path in files:
        if not path.endswith(".go") or path.endswith("_test.go"):
            continue
        text = _git(repo, "show", f"{tag}:{path}")
        if path == "backend/api/ws/proxy_permissions.go":
            routes += websocket_routes(path, text)
        elif "huma.Operation{" in text:
            routes += routes_of_file(path, text)
    commit = _git(repo, "rev-parse", f"{tag}^{{commit}}").strip()
    routes.sort(key=lambda r: (r["path"], r["method"]))
    json.dump(
        {"arcane": tag, "commit": commit, "routen": routes},
        sys.stdout,
        ensure_ascii=False,
        indent=1,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
