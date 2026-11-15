<!--
Pflichtvorlage. Die Prüfung „PR-Vorlage“ schlägt fehl, solange ein Abschnitt
fehlt oder leer ist, ein Feld nach dem Doppelpunkt leer bleibt oder ein
Kästchen offen ist. Trifft ein Punkt nicht zu: Kästchen ankreuzen und
„entfällt: <Grund>“ dahinter schreiben. Kommentare wie dieser dürfen bleiben.
-->

## Zusammenfassung

<!-- Was ändert sich für Nutzer? 1–3 Sätze. -->

## Belege

<!-- Quellen für Endpunkte, Parameter und Feldnamen: Arcane-Quellcode (Tag und
Datei), OpenAPI-Spezifikation von Arcane, Doku auf getarcane.app, echte Antwort
(Diagnose). Ohne externe Quelle: „Keine: <Grund>“, auch als Listenpunkt. -->

## Änderungen

<!-- Ein Punkt je Datei oder Bereich. -->

## Verhaltensänderungen

<!-- Was ändert sich für bestehende Installationen? Neue oder entfallene
Entitäten, geänderte Entitäts-IDs, zusätzliche API-Abfragen, nötige Rechte des
API-Schlüssels, unterstützte Arcane-Versionen. Sonst: „Keine: <Begründung>“. -->

## Tests

- Automatische Tests:
- Gegenprobe:
- Live geprüft:

<!-- Automatische Tests: Anzahl gesamt und neu, z. B. „102, davon 8 neu“.
Gegenprobe: welche Schutzmaßnahmen einzeln entfernt, mit Ergebnis.
Live geprüft: „ja (HA-Version, Arcane-Version, was)“ oder „nein (Grund)“. -->

## Nicht geprüft / offen

<!-- Was ist unbelegt oder ungetestet? „Nichts“ nur, wenn alles live geprüft ist. -->

## Checkliste

- [ ] `ruff check .` und `ruff format --check .` ohne Befund
- [ ] `pytest tests` vollständig grün, auch `test_repo_konsistenz.py`
- [ ] Nur Endpunkte und Felder, die Arcane in der unterstützten Version anbietet
- [ ] Texte in `strings.json`, `en.json` und `de.json`
- [ ] Entity-IDs, `unique_id`s und Event-Namen unverändert oder die Änderung oben begründet
- [ ] README: Entitäten, Rechte und Teststand mit aktueller Testzahl
- [ ] Version in `manifest.json` erhöht, `.github/release-notes/v<Version>.md` angelegt
- [ ] Kein API-Schlüssel, keine echten Hostnamen, IP-Adressen oder Containernamen im Diff
