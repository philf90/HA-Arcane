"""Liest die API-Routen einer Arcane-Version aus deren Quelltext.

Aufruf: python3 .github/scripts/arcane_routen.py <arcane-clone> <tag> > datei.json

Das Skript liest die Go-Dateien unter backend/ als Text ueber "git show"; es
baut und startet nichts aus dem Arcane-Repo. Erfasst werden:

- huma.Operation-Literale (Methode, Pfad) aller REST-Routen unter /api;
  steht der Pfad als Konstante oder als Verkettung aus Konstanten und Texten
  da (``Path: syncPath + "/files"``), wird er ueber die const-Definitionen
  desselben Pakets aufgeloest,
- Routen aus Aufrufen von Hilfsfunktionen mit der Signatur
  ``(operationID, method, path, ...)``, etwa ``handlerutil.Operation``,
- als ``Hidden`` markierte Routen mit "versteckt": true (sie fehlen in der
  OpenAPI-Datei, die Arcane ausliefert),
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


_IDENT = r"[A-Za-z_]\w*"
_TERM = rf'(?:"[^"\\]*"|{_IDENT})'
_EXPR = rf"{_TERM}(?:\s*\+\s*{_TERM})*"
_CONST_LINE = re.compile(
    rf"^\s*(?:const\s+)?({_IDENT})(?:\s+string)?\s*=\s*({_EXPR})\s*(?://.*)?$"
)
_SHORT_DECL = re.compile(rf"^\s*({_IDENT})\s*:=\s*({_EXPR})\s*(?://.*)?$")


def const_definitions(text: str) -> list[tuple[int, str, str]]:
    """(Position, Name, Ausdruck) aller String-Konstanten einer Go-Datei.

    Erfasst ``const name = ...``, Zeilen in ``const ( ... )``-Bloecken und
    ``name := "..."`` mit reinem Text-Ausdruck, auch innerhalb von Funktionen.
    """
    found = []
    in_block = False
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r"const\s*\($", stripped):
            in_block = True
        elif in_block and stripped == ")":
            in_block = False
        elif in_block or stripped.startswith("const "):
            match = _CONST_LINE.match(line.rstrip("\n"))
            if match:
                found.append((offset, match.group(1), match.group(2)))
        elif match := _SHORT_DECL.match(line.rstrip("\n")):
            found.append((offset, match.group(1), match.group(2)))
        offset += len(line)
    return found


class PathResolver:
    """Loest Pfad-Ausdruecke ueber die Konstanten eines Go-Pakets auf."""

    def __init__(self, files: dict[str, str]) -> None:
        self._local = {path: const_definitions(text) for path, text in files.items()}
        package: dict[str, set[str]] = {}
        for definitions in self._local.values():
            for _, name, expr in definitions:
                package.setdefault(name, set()).add(expr)
        self._package = package

    def _lookup(self, name: str, path: str, position: int) -> str | None:
        # Zuerst die letzte Definition davor in derselben Datei (Konstanten in
        # Funktionen), sonst eine eindeutige Definition im Paket.
        before = [
            e for p, n, e in self._local.get(path, []) if n == name and p < position
        ]
        if before:
            return before[-1]
        exprs = self._package.get(name, set())
        return next(iter(exprs)) if len(exprs) == 1 else None

    def resolve(
        self, expr: str, path: str, position: int, depth: int = 0
    ) -> str | None:
        if depth > 10 or not re.fullmatch(_EXPR, expr.strip()):
            return None
        parts = []
        for term in re.findall(_TERM, expr):
            if term.startswith('"'):
                parts.append(term[1:-1])
                continue
            definition = self._lookup(term, path, position)
            value = definition and self.resolve(definition, path, position, depth + 1)
            if value is None:
                return None
            parts.append(value)
        return "".join(parts)


def _method(raw: str) -> str:
    return _METHODS.get(raw, raw.strip('"').upper())


def routes_of_file(
    path: str, text: str, resolver: PathResolver | None = None
) -> list[dict]:
    found = []
    for match in re.finditer(r"huma\.Operation\{", text):
        start = match.end() - 1
        end = _block_end(text, start)
        body = text[start:end]
        method = re.search(r"Method:\s*([\w.\"]+)", body)
        route = re.search(rf"Path:\s*({_EXPR})\s*[,}}]", body + "}")
        if not (method and route):
            continue
        value = (
            resolver.resolve(route.group(1), path, match.start()) if resolver else None
        )
        if value is None and route.group(1).startswith('"'):
            value = route.group(1).strip('"')
        if value is None:
            # Hilfsfunktionen bauen Operationen aus ihren Parametern; die
            # Routen stehen dann bei deren Aufrufen (helper_routes).
            print(f"uebersprungen: Path {route.group(1)} in {path}", file=sys.stderr)
            continue
        entry = {
            "method": _method(method.group(1)),
            "path": value,
            "quelle": f"{path}:{text.count(chr(10), 0, match.start()) + 1}",
        }
        if re.search(r"Hidden:\s*true", body):
            entry["versteckt"] = True
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


_HELPER_DEF = re.compile(rf"func ({_IDENT})\(operationID, method, path\b")


def helper_names(sources: dict[str, str]) -> set[str]:
    """Funktionen wie handlerutil.Operation(operationID, method, path, ...)."""
    return {m.group(1) for text in sources.values() for m in _HELPER_DEF.finditer(text)}


def _arguments(text: str, start: int) -> list[str]:
    """Argumente des Aufrufs, dessen "(" an start steht (oberste Ebene)."""
    args, depth, current, i = [], 0, [], start + 1
    while i < len(text):
        char = text[i]
        if char in '"`':
            end = text.index(char, i + 1)
            while char == '"' and text[end - 1] == "\\":
                end = text.index(char, end + 1)
            current.append(text[i : end + 1])
            i = end + 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                args.append("".join(current).strip())
                return [a for a in args if a]
            depth -= 1
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    raise ValueError("Aufruf nicht geschlossen")


def helper_routes(
    path: str, text: str, names: set[str], resolver: PathResolver
) -> list[dict]:
    """Routen aus Aufrufen von Hilfsfunktionen wie handlerutil.Operation."""
    if not names:
        return []
    found = []
    pattern = rf"(?<![\w.])(?:\w+\.)?({'|'.join(sorted(names))})\("
    for match in re.finditer(pattern, text):
        if text[max(0, match.start() - 5) : match.start()].endswith("func "):
            continue
        args = _arguments(text, match.end() - 1)
        if len(args) < 3:
            continue
        value = resolver.resolve(args[2], path, match.start())
        if value is None:
            print(
                f"uebersprungen: {match.group(1)}(..., {args[2]}) in {path}",
                file=sys.stderr,
            )
            continue
        found.append(
            {
                "method": _method(args[1]),
                "path": value,
                "quelle": f"{path}:{text.count(chr(10), 0, match.start()) + 1}",
            }
        )
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
    sources = {
        path: _git(repo, "show", f"{tag}:{path}")
        for path in files
        if path.endswith(".go") and not path.endswith("_test.go")
    }
    packages: dict[str, dict[str, str]] = {}
    for path, text in sources.items():
        packages.setdefault(path.rsplit("/", 1)[0], {})[path] = text
    resolvers = {name: PathResolver(texts) for name, texts in packages.items()}
    helpers = helper_names(sources)
    routes: list[dict] = []
    for path, text in sources.items():
        resolver = resolvers[path.rsplit("/", 1)[0]]
        if path == "backend/api/ws/proxy_permissions.go":
            routes += websocket_routes(path, text)
            continue
        if "huma.Operation{" in text:
            routes += routes_of_file(path, text, resolver)
        routes += helper_routes(path, text, helpers, resolver)
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
