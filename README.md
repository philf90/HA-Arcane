<p align="center">
  <img src="https://raw.githubusercontent.com/philf90/HA-Arcane/main/assets/icon.png" alt="Arcane-Logo" width="120">
</p>

# Arcane

[![Erstellt mit Claude Code](https://img.shields.io/badge/erstellt%20mit-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)
[![KI-generiert](https://img.shields.io/badge/Code%20%26%20Doku-KI--generiert-6A2BB0)](#ki-hinweis)

Home-Assistant-Integration für [Arcane](https://github.com/getarcaneapp/arcane), die
Oberfläche zur Verwaltung von Docker: Container und Compose-Projekte als Geräte mit
Schaltern zum Starten und Stoppen, Buttons für Neustart und Redeploy, Sensoren für
Zustand und Zähler und Update-Entitäten, die unter *Einstellungen → Updates*
erscheinen, sobald ein neueres Image bereitsteht. Fork von
[Arcane-HA](https://github.com/ProfessorQuantumUniverse/Arcane-HA), siehe
[Herkunft](#herkunft). Version 0.5.2.

> **Arcane 3.x:** Diese Version läuft nur mit Arcane 2.x. Arcane 3.0.0 hat die
> API-Pfade umbenannt (`/api/environments/…` heißt jetzt `/api/agents/…`); gegen
> Arcane 3.x meldet die Einrichtung `HTTP 404 … /api/app-version`. Die Anpassung
> folgt mit Version 0.6.0.

> **KI-Hinweis:** Dieses Projekt ist mit Claude Code entstanden. Code, Tests und
> Dokumentation wurden von der KI geschrieben und mit automatischen Tests geprüft;
> Details im Abschnitt [KI-Hinweis](#ki-hinweis).

## Voraussetzungen

- **Home Assistant 2026.2.3 oder neuer** (gegen diese Version laufen die Tests)
- **Marketplace** (ab Home Assistant 2026.11 eingebaut, Einstellungen →
  Marketplace) oder bis 2026.10 **[HACS](https://hacs.xyz)**; ohne beides geht es
  auch von Hand
- eine **Arcane-Instanz**, die Home Assistant direkt erreicht, in Version 0.5.2 mit
  **Arcane 2.x**
- ein **API-Schlüssel** von Arcane mit den Rechten aus
  [Rechte des API-Schlüssels](#rechte-des-api-schlüssels)

## Installation

[![Im Marketplace bzw. in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=philf90&repository=HA-Arcane&category=integration)
[![Integration hinzufügen](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=arcane)

1. **Integration herunterladen**, je nach Home-Assistant-Version:
   - **Ab 2026.11 (Marketplace):** Einstellungen → **Marketplace** → oben rechts
     **Add from link** → `https://github.com/philf90/HA-Arcane` → **Add**, danach
     „Arcane“ öffnen und installieren.
   - **Bis 2026.10 (HACS):** Erste Schaltfläche oben, oder HACS → ⋮ →
     **Benutzerdefinierte Repositories** → `https://github.com/philf90/HA-Arcane`,
     Typ „Integration“ → „Arcane“ herunterladen.
   - **Von Hand:** Ordner `custom_components/arcane` nach
     `config/custom_components/` kopieren.
2. **Home Assistant neu starten** (mit HACS oder von Hand).
3. **Einrichten:** siehe [Einrichten](#einrichten).

Die Integration hat dieselbe Domain (`arcane`) wie Arcane-HA aus dem
Standardkatalog. Beide zugleich installieren geht nicht. Wie der Wechsel von
Arcane-HA zu diesem Fork bei bestehendem Eintrag abläuft, beschreibt Version 0.6.0,
sobald er geprüft ist.

## Inhalt

- [Einrichten](#einrichten)
- [Entitäten](#entitäten)
- [Optionen](#optionen)
- [Updates](#updates)
- [Ereignisse](#ereignisse)
- [Abfragen](#abfragen)
- [Gut zu wissen](#gut-zu-wissen)
- [Herkunft](#herkunft)
- [Teststand](#teststand)
- [Tests](#tests)
- [Releases](#releases)
- [KI-Hinweis](#ki-hinweis)
- [Lizenz](#lizenz)

## Einrichten

1. In Arcane unter **Settings → API keys** einen Schlüssel anlegen.
2. In Home Assistant: Einstellungen → Geräte & Dienste → Integration hinzufügen →
   **Arcane**.
3. Adresse der Instanz eingeben, z. B. `http://192.168.1.10:3552`, und den
   Schlüssel.

Die Adresse muss die sein, unter der Arcane tatsächlich antwortet. Weiterleitungen
folgt die Integration absichtlich nicht, damit der Schlüssel nie an einen anderen
Host geht. Meldet die Einrichtung eine Weiterleitung, die Zieladresse eintragen.

### Rechte des API-Schlüssels

Die Integration rechnet aus, was die gewählten Optionen brauchen: Integration öffnen
→ **Konfigurieren → Berechtigungen des API-Keys**. Die Liste in Arcane unter
**Settings → Roles** in die Rolle hinter dem Schlüssel übernehmen. Eine
ausgeschaltete Option nimmt ihre Rechte aus der Liste.

| Zweck | Rechte |
|---|---|
| Überwachung | `environments:list`, `containers:list`, `projects:list`, `images:list`, `volumes:list`, `networks:list`, `system:read` |
| Arcane-Version entfernter Umgebungen (Update-Entitäten) | `environments:read` |
| Steuerung | `containers:start`, `containers:stop`, `containers:restart`, `containers:redeploy`, `projects:deploy`, `projects:down`, `projects:restart` |
| Prune-Button | `system:prune` |
| Arcane selbst aktualisieren | `system:upgrade` |

Pflicht ist nur `environments:list`. Ein Schlüssel ohne die übrigen richtet sich
trotzdem ein; was er nicht erreicht, bleibt leer, und Aktionen scheitern mit einer
Meldung, die das fehlende Recht nennt. Lehnt Arcane einen Aufruf ab, erscheint das
Recht als Reparatur unter **Einstellungen → System → Reparaturen**.

## Entitäten

Namen wie auf einer deutschen Instanz; die Entity-IDs bildet Home Assistant daraus.

### Umgebung

| Entität | Art | Hinweis |
|---|---|---|
| Online | Binärsensor | Erreicht Arcane die Umgebung |
| Container, Laufende Container, Gestoppte Container | Sensor | |
| Container-Updates verfügbar | Sensor | Container mit neuerem Image |
| Ungesunde Container | Sensor | fehlgeschlagener Healthcheck |
| Projekte, Laufende Projekte | Sensor | |
| Images, Ungenutzte Images, Image-Speicher | Sensor | |
| Volumes, Ungenutzte Volumes, Netzwerke | Sensor | |
| Arcane | Update | aktualisiert die Arcane-Instanz selbst |
| Host CPU, Host Arbeitsspeicher (belegt, gesamt), Host Festplatte (belegt) | Sensor | standardmäßig aus |
| Docker-Version, Arcane Version | Sensor | Diagnose, standardmäßig deaktiviert |
| Ungenutztes entfernen | Button | standardmäßig aus |

### Container

| Entität | Art | Hinweis |
|---|---|---|
| *(Containername)* | Schalter | startet und stoppt |
| Image Update | Update | installiert per Redeploy auf das neuere Image |
| Läuft, Update verfügbar | Binärsensor | |
| Healthy | Binärsensor | nur mit Healthcheck |
| Zustand, Status | Sensor | `running`, `exited`, `paused` usw. |
| Image, Erstellt | Sensor | Diagnose |
| Neustarten | Button | |
| Redeploy | Button | standardmäßig aus |

### Compose-Projekt

| Entität | Art | Hinweis |
|---|---|---|
| *(Projektname)* | Schalter | an = `up`, aus = `down` |
| Läuft, Update verfügbar | Binärsensor | |
| Status | Sensor | `running`, `partially running`, `stopped` usw. |
| Services, Laufende Services | Sensor | |
| Neustarten | Button | |
| Redeploy | Button | standardmäßig aus |

Container erscheinen als Untergeräte ihres Compose-Projekts. Container und
Projekte, die in Arcane dazukommen oder verschwinden, werden angelegt bzw. entfernt,
während Home Assistant läuft.

## Optionen

Integration öffnen → **Konfigurieren**.

- **Abfrage:** Intervall (30 s, 10 bis 3600), welche Umgebungen, ob Container,
  Compose-Projekte, Zähler und Hoststatistik überhaupt abgefragt werden; interne und
  versteckte Container nur auf Wunsch. Eine ausgeschaltete Option stoppt auch die
  Abfragen dahinter.
- **Entitäten:** Update-Entitäten, Health-Sensoren, Art vor dem Gerätenamen,
  Container unter ihrem Projekt, Ereignisse.
- **Steuerung:** ob Home Assistant überhaupt steuern darf, dazu Redeploy- und
  Prune-Buttons (beide standardmäßig aus).

### Gerätenamen

Container und Projekte heißen oft gleich, und Home Assistant bildet Entity-IDs aus
dem Gerätenamen; das zweite Gerät bekäme nur IDs mit `_2`. Mit Präfix heißen sie
`Container kopia` und `Project kopia`, also `switch.container_kopia` und
`switch.project_kopia`. Vorhandene Entity-IDs werden nie umgeschrieben.

## Updates

Container-Updates und Arcane selbst erscheinen unter **Einstellungen → Updates**.
Ein Container-Update installiert per Redeploy von Arcane. Das Arcane-Update lässt
Arcane sein neueres Image ziehen und neu starten; alles hier ist dann kurz nicht
verfügbar. Ob ein Update ansteht, entscheidet Arcane, nicht ein Versionsvergleich in
Home Assistant: Versionen sind oft Digests und lassen sich nicht ordnen. Mit
ausgeschalteter Steuerung zeigen die Entitäten Versionen, erscheinen aber nicht in
der Liste.

## Ereignisse

Mit eingeschalteten Ereignissen feuern diese auf dem Bus und taugen als Auslöser:

| Ereignis | Daten |
|---|---|
| `arcane_container_state_changed` | `container`, `state`, `previous_state`, `exit_code`, `crashed`, `oom_killed`, `environment`, `environment_id` |
| `arcane_container_health_changed` | `container`, `health`, `previous_health`, `environment`, `environment_id` |
| `arcane_project_state_changed` | `project`, `project_id`, `status`, `previous_status`, `environment`, `environment_id` |

`crashed` heißt: Ein laufender Container endete mit Exit-Code ungleich 0.
`oom_killed` engt das auf Code 137 ein, den typischen Abdruck eines
Out-of-Memory-Kills; ein Stopp, den Docker nach Ablauf der Wartezeit mit SIGKILL
erzwingt, endet ebenfalls mit 137. Nach einem Neustart von Home Assistant und für
neue Container feuert nichts. Ein Container, der innerhalb eines Abfrageintervalls
abstürzt und neu startet, erscheint nicht.

## Abfragen

Alle Pfade unter `<Adresse>/api/`, Schlüssel im Header `X-Api-Key`. Listen werden
mit `limit=-1` vollständig geholt. Eine ausgeschaltete Option stoppt die Abfrage
dahinter.

| Pfad | Methode | Wann | Recht |
|---|---|---|---|
| `app-version` | GET | jede Abfrage, Einrichtung | keins |
| `environments` | GET | jede Abfrage, Einrichtung, Optionen | `environments:list` |
| `environments/{environment}/containers` | GET | Option Container | `containers:list` |
| `environments/{environment}/projects` | GET | Option Compose-Projekte | `projects:list` |
| `environments/{environment}/images/counts` | GET | Option Images, Volumes und Netzwerke | `images:list` |
| `environments/{environment}/volumes/counts` | GET | Option Images, Volumes und Netzwerke | `volumes:list` |
| `environments/{environment}/networks/counts` | GET | Option Images, Volumes und Netzwerke | `networks:list` |
| `environments/{environment}/system/docker/info` | GET | Option Images, Volumes und Netzwerke | `system:read` |
| `environments/{environment}/ws/system/stats` | WebSocket, eine Nachricht | Option Hoststatistik | `system:read` |
| `environments/{environment}/version` | GET | Update-Entitäten, nur entfernte Umgebungen | `environments:read` |
| `environments/{environment}/containers/{container}/{action}` | POST | Schalter, Buttons, Update (`start`, `stop`, `restart`, `redeploy`) | `containers:<action>` |
| `environments/{environment}/projects/{project}/{action}` | POST | Schalter, Buttons (`up`, `down`, `restart`, `redeploy`; `up` und `redeploy` antworten als NDJSON-Stream) | `projects:deploy`, `projects:down`, `projects:restart` |
| `environments/{environment}/system/prune` | POST | Button „Ungenutztes entfernen“ | `system:prune` |
| `environments/{environment}/system/upgrade` | POST | Arcane-Update installieren | `system:upgrade` |

Die lokale Umgebung hat die ID `0`; ihre Arcane-Version kommt aus `app-version`
und kostet keine eigene Abfrage. Mit allen Abfrage-Optionen an sind das je
Durchlauf und Umgebung neun Anfragen.

## Gut zu wissen

- **Prune** entfernt ungenutzte Images, ungenutzte Netzwerke und den Build-Cache,
  nie Container oder Volumes. Die Option wählt: kein Button, nur Dangling-Images
  oder alle ungenutzten Images. Ein Button in Home Assistant fragt nicht nach.
- **Hoststatistik** gibt es bei Arcane nur über einen WebSocket. Die Integration
  öffnet ihn je Abfrage, liest die erste Nachricht und schließt ihn wieder. CPU und
  RAM je Container liefert die Integration nicht: Arcane will dafür einen Socket je
  Container und lässt höchstens fünf zugleich zu.
- **Sicherheit:** Der Schlüssel steht im Header, nie in einer URL, und ist in der
  Diagnose geschwärzt. Nur `http://`- und `https://`-Adressen ohne Zugangsdaten,
  Query oder Fragment. Weiterleitungen werden nicht verfolgt. Zertifikatsprüfung
  lässt sich für selbst signierte Zertifikate abschalten; Home Assistant warnt dann
  beim Start.
- **Verhalten:** Eine unerreichbare Umgebung meldet sich als offline, statt die
  ganze Abfrage scheitern zu lassen. Container-Entitäten hängen am Namen und
  überleben deshalb ein Redeploy mit neuer Container-ID. `down` entfernt die
  Container eines Projekts; ihre Entitäten sind bis zum nächsten `up` nicht
  verfügbar. Einen Health-Sensor bekommt ein Container, wenn er beim Entdecken einen
  Healthcheck hat; kommt er später dazu, nach dem Neuladen der Integration.
- **Diagnose:** Integration öffnen → ⋮ → **Diagnose herunterladen**. Enthält
  Optionen, nötige und abgelehnte Rechte und den Stand jeder Umgebung, ohne
  API-Schlüssel.

## Herkunft

Dieses Repository ist ein Fork von
[ProfessorQuantumUniverse/Arcane-HA](https://github.com/ProfessorQuantumUniverse/Arcane-HA),
Stand Version 0.5.2 vom 10.10.2026. Die Git-Historie ist vollständig übernommen, die
Urheberschaft der bisherigen Commits bleibt erhalten. Anlass ist Arcane 3.0.0
(14.11.2026), das die API-Pfade umbenannt hat; Arcane-HA 0.5.2 ist das neueste
Release dort.

Aufbau, Prüfungen und Abläufe folgen
[HA-WOW](https://github.com/philf90/HA-WOW): Versionshinweise unter
`.github/release-notes/`, Pflicht-Vorlage für Pull Requests, Konsistenztest. Die
Versionen 0.1.0 bis 0.4.0 hatten kein Release; ihre Einträge stehen in
`CHANGELOG.md` im Git-Verlauf (Stand von Commit `d69d771`).

## Teststand

Automatische Tests laufen gegen Home Assistant 2026.2.3. Live geprüft ist im Fork
noch nichts.

- **0.5.x:** 132 automatische Tests. Stand 0.5.2 mit Repo-Struktur nach HA-WOW:
  88 Tests aus Arcane-HA (ohne die 9 entfallenen Prüfungen von Changelog und
  langen Strichen) und 44 neue in `test_repo_konsistenz.py`, `test_translations.py`
  und `test_pr_vorlage.py`. Gegenprobe: Pfad im README gestrichen, Schlüssel in
  `de.json` gestrichen, Fehlermeldung `prune_forbidden` gestrichen, Version im
  README geändert; jedes Mal schlug ein Test fehl. Die Tests bauen die Antworten
  von Arcane 2.x nach. **Nicht live geprüft.**

## Tests

```bash
pip install pytest-homeassistant-custom-component==0.13.316 ruff==0.16.9
ruff check . && ruff format --check .
pytest tests   # pytest.ini setzt asyncio_mode = auto
```

`test_repo_konsistenz.py` prüft, dass README und Versionshinweise die Version aus
`manifest.json` nennen, dass die Testzahl unter [Teststand](#teststand) stimmt, dass
`strings.json` gleich `translations/en.json` ist und `de.json` dieselben Schlüssel
hat und dass jeder Pfad des API-Clients unter [Abfragen](#abfragen) steht.
`test_translations.py` prüft, dass jeder Schlüssel aus Dialogen, Entitäten und
Fehlermeldungen übersetzt ist. `test_pr_vorlage.py` prüft die Prüfung der
PR-Vorlage selbst.

Die Prüfung „PR-Vorlage“ verlangt eine vollständig ausgefüllte
Beschreibung nach `.github/pull_request_template.md` und, sobald sich etwas unter
`custom_components/` ändert, eine neue Version mit Versionshinweisen. Verbindlich
wird sie erst als Pflicht-Prüfung im Branch-Schutz von `main`.

## Releases

1. Version in `custom_components/arcane/manifest.json` erhöhen und
   `.github/release-notes/v<Version>.md` anlegen: erster Absatz mit Migration und
   Rückweg, dann `## Neu`, `## Geändert` oder `## Behoben`, `## Gut zu wissen` und
   `## Teststand`.
2. README: `Version X.Y.Z.` und die Testzahl unter [Teststand](#teststand)
   nachziehen.
3. Nach dem Merge den Tag `v<Version>` pushen oder den Workflow „Release“ von Hand
   starten. Er prüft Tag und Manifest und legt das GitHub-Release mit den
   Versionshinweisen an; der Marketplace bzw. HACS zeigt diese Version an.

## KI-Hinweis

Dieser Fork wird mit [Claude Code](https://claude.com/claude-code) entwickelt, einem
KI-Assistenten von Anthropic. Recherche, Code, Tests und Dokumentation erstellt die
KI im Auftrag und unter Anleitung des Repo-Inhabers. Geprüft wird so:

- automatische Tests (`pytest`, siehe Abschnitt „Tests“) und Code-Prüfung mit `ruff`
- Gegenproben mit absichtlich eingebauten Fehlern, um zu sehen, ob die Tests sie
  finden
- Betrieb in einer echten Home-Assistant-Instanz (Stand im Abschnitt „Teststand“)

Endpunkte und Feldnamen von Arcane stammen aus dem Quellcode von Arcane
(`getarcaneapp/arcane`) und der Doku auf getarcane.app.

## Lizenz

GPL-3.0, siehe `LICENSE`, wie bei Arcane-HA; ein Fork behält diese Lizenz. Logo und
Icon stammen aus Arcane (`backend/resources/images/logo.svg` und `logo-full.svg` in
`getarcaneapp/arcane`, BSD-3-Clause).
