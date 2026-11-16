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
[Herkunft](#herkunft). Version 0.11.2.

> **Arcane 3.x:** Ab Version 0.6.0 braucht die Integration Arcane 3.0 oder neuer.
> Arcane 3.0.0 hat die API-Pfade umbenannt (`/api/environments/…` heißt jetzt
> `/api/agents/…`). Gegen Arcane 2.x meldet die Integration „Arcane 2.x wird nicht
> unterstützt“; dafür bleibt Version 0.5.2.

> **KI-Hinweis:** Dieses Projekt ist mit Claude Code entstanden. Code, Tests und
> Dokumentation wurden von der KI geschrieben und mit automatischen Tests geprüft;
> Details im Abschnitt [KI-Hinweis](#ki-hinweis).

## Voraussetzungen

- **Home Assistant 2026.2.3 oder neuer** (gegen diese Version laufen die Tests)
- **Marketplace** (ab Home Assistant 2026.11 eingebaut, Einstellungen →
  Marketplace) oder bis 2026.10 **[HACS](https://hacs.xyz)**; ohne beides geht es
  auch von Hand
- eine **Arcane-Instanz** ab **Version 3.0**, die Home Assistant direkt erreicht
  (für Arcane 2.x: Version 0.5.2 dieser Integration)
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
Standardkatalog; beide zugleich installieren geht nicht. Wer von Arcane-HA kommt,
behält Eintrag, Geräte und Entity-IDs, weil Domain, `unique_id`s und
Entitätsnamen gleich geblieben sind. Wie der Marketplace den Tausch zweier
Repositories mit derselben Domain abwickelt, ist **noch nicht live geprüft**;
vorher ein Backup anlegen.

## Inhalt

- [Einrichten](#einrichten)
- [Entitäten](#entitäten)
- [Optionen](#optionen)
- [Updates](#updates)
- [Hintergrund-Jobs](#hintergrund-jobs)
- [Mehrere Hosts](#mehrere-hosts)
- [Ereignisse](#ereignisse)
- [Sofort aktualisieren](#sofort-aktualisieren)
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
| Steuerung | `containers:start`, `containers:stop`, `containers:restart`, `projects:deploy`, `projects:down`, `projects:restart` |
| Redeploy-Buttons | `containers:redeploy` |
| Container-Updates installieren, Button „Alle Updates installieren“ | `image-updates:apply` |
| Auto-Update-Schalter (Option) | `containers:autoupdate` |
| Arcane Updater: laufende Updates, letztes Update (Option) | `image-updates:read`, `image-updates:list` |
| Arcane Hintergrund-Jobs (Option) | `jobs:list` |
| Buttons „Job … ausführen“ | `jobs:run` |
| Prune-Button | `system:prune`, `activities:read` (verfolgt den Prune bis zum Ende) |
| Arcane selbst aktualisieren | `system:upgrade` |
| Versionshinweise der Container-Updates | `image-updates:read` |
| Button „Nach Updates suchen“ | `image-updates:check` |
| Freigebbarer Speicher (Option) | `system:read` |
| Bei Änderungen sofort aktualisieren, Arcane-Ereignisprotokoll (Optionen) | `events:list` |

Pflicht ist nur `environments:list`. Ein Schlüssel ohne die übrigen richtet sich
trotzdem ein; was er nicht erreicht, bleibt leer, und Aktionen scheitern mit einer
Meldung, die das fehlende Recht nennt. Lehnt Arcane einen Aufruf ab, erscheint das
Recht als Reparatur unter **Einstellungen → System → Reparaturen**.

## Entitäten

Namen wie auf einer deutschen Instanz; die Entity-IDs bildet Home Assistant daraus.

### Umgebung

| Entität | Art | Hinweis |
|---|---|---|
| Online | Binärsensor | Erreicht Arcane die Umgebung; bei einem Agenten mit Hostname, Agent- und Docker-Version, Betriebssystem, Kernel, Architektur, CPUs, Arbeitsspeicher, Transport und Sicherheitsmodus als Attribute |
| Agent letzter Kontakt, Agent verbunden seit, Agent-Latenz, Agent letzte Trennung | Sensor | Diagnose, nur bei entfernten Umgebungen (Arcane-Agent); letzte Trennung mit Attribut `reason`, siehe [Mehrere Hosts](#mehrere-hosts) |
| Container, Laufende Container, Gestoppte Container | Sensor | |
| Container-Updates verfügbar | Sensor | Container mit neuerem Image |
| Ungesunde Container | Sensor | fehlgeschlagener Healthcheck |
| Projekte, Laufende Projekte | Sensor | |
| Images, Ungenutzte Images, Image-Speicher | Sensor | |
| Volumes, Ungenutzte Volumes, Netzwerke | Sensor | |
| Arcane | Update | aktualisiert die Arcane-Instanz selbst |
| Host CPU, Host Arbeitsspeicher (belegt, gesamt), Host Festplatte (belegt) | Sensor | standardmäßig aus |
| Docker-Version, Arcane Version | Sensor | Diagnose, standardmäßig deaktiviert |
| Freigebbarer Image-Speicher, Freigebbarer Container-Speicher, Freigebbarer Volume-Speicher, Freigebbarer Build-Cache | Sensor | standardmäßig aus, siehe [Freigebbarer Speicher](#freigebbarer-speicher) |
| Ungenutztes entfernen | Button | standardmäßig aus |
| Nach Updates suchen | Button | mit Update-Entitäten und Steuerung |
| Updates werden installiert | Binärsensor | Option Arcane Updater |
| Letztes Image-Update | Sensor | Option Arcane Updater; Zeitpunkt, Attribute `resource_name`, `resource_type`, `status`, `update_applied`, `error` |
| Alle Updates installieren | Button | Option Arcane Updater, mit Steuerung |
| Job …, je Job von Arcane (etwa „Job Auto Update“) | Sensor | Option Arcane Hintergrund-Jobs, Diagnose; siehe [Hintergrund-Jobs](#hintergrund-jobs) |
| Job … ausführen | Button | Option Arcane Hintergrund-Jobs, mit Steuerung, nur für Jobs, die Arcane von Hand starten lässt |

### Container

| Entität | Art | Hinweis |
|---|---|---|
| *(Containername)* | Schalter | startet und stoppt |
| Image Update | Update | installiert über Arcanes Updater; Versionshinweise; Attribute `update_blocked`, `blocked_reason` |
| Automatisch aktualisieren | Schalter | Option Auto-Update-Schalter, Konfiguration |
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
  Compose-Projekte, Zähler, Hoststatistik und freigebbarer Speicher überhaupt
  abgefragt werden, ob bei Änderungen sofort aktualisiert wird (siehe
  [Sofort aktualisieren](#sofort-aktualisieren)); interne und versteckte Container
  nur auf Wunsch. Eine ausgeschaltete Option stoppt auch die
  Abfragen dahinter.
- **Entitäten:** Update-Entitäten, Health-Sensoren, Art vor dem Gerätenamen,
  Environment im Gerätenamen, Container unter ihrem Projekt, Ereignisse,
  Arcane-Ereignisprotokoll auf dem Bus, Arcane Updater und Arcane Hintergrund-Jobs
  (Environment im Gerätenamen und die letzten drei standardmäßig aus).
- **Steuerung:** ob Home Assistant überhaupt steuern darf, dazu Redeploy- und
  Prune-Buttons und Auto-Update-Schalter (alle standardmäßig aus).

### Gerätenamen

Container und Projekte heißen oft gleich, und Home Assistant bildet Entity-IDs aus
dem Gerätenamen; das zweite Gerät bekäme nur IDs mit `_2`. Mit Präfix heißen sie
`Container kopia` und `Project kopia`, also `switch.container_kopia` und
`switch.project_kopia`. Vorhandene Entity-IDs werden nie umgeschrieben.

Läuft ein Container unter demselben Namen auf zwei Hosts, gilt dasselbe. Mit der
Option **Environment im Gerätenamen** steht der Name eines entfernten Environments
vor dem Namen: `Container host-2 kopia`, also `switch.container_host_2_kopia`. Das
lokale Environment behält seine Namen. Schon vorhandene Geräte bekommen den neuen
Namen, ihre Entity-IDs bleiben; wer einheitliche IDs will, benennt sie in Home
Assistant um.

## Updates

Container-Updates und Arcane selbst erscheinen unter **Einstellungen → Updates**.
Ein Container-Update installiert Arcanes Updater (siehe unten). Das Arcane-Update lässt
Arcane sein neueres Image ziehen und neu starten; alles hier ist dann kurz nicht
verfügbar. Ob ein Update ansteht, entscheidet Arcane, nicht ein Versionsvergleich in
Home Assistant: Versionen sind oft Digests und lassen sich nicht ordnen. Mit
ausgeschalteter Steuerung zeigen die Entitäten Versionen, erscheinen aber nicht in
der Liste.

- **Installieren über den Updater:** Seit 0.9.0 installiert die Update-Entität
  über `containers/{id}/update`, so wie „Update“ in Arcane selbst. Der Updater
  folgt der Tag-Regel des Containers auf einen neueren Tag, zieht über Arcanes
  Schwachstellen-Prüfung, wenn sie eingeschaltet ist, und überspringt einen
  Container, dessen Image sich nicht geändert hat. Ein Redeploy (bis 0.8.1) zog
  die bisherige Referenz erneut und umging die Prüfung. Übersprungen oder
  fehlgeschlagen meldet Home Assistant mit dem Grund, den Arcane nennt. Ein Update
  darf bei Arcane bis zu 15 Minuten dauern; so lange zeigt die Entität „wird
  installiert“. Nötig ist `image-updates:apply` statt bisher `containers:redeploy`.
- **Gesperrte Updates:** Hält Arcane ein Update zurück, etwa wegen eines
  Schwachstellen-Limits, zeigt die Entität es weiter an, mit `update_blocked: true`
  und dem Grund in `blocked_reason`. Installieren bietet sie dann nicht an; Arcanes
  eigene Update-Regel gilt auch aus Home Assistant heraus.
- **Versionshinweise:** Der Update-Dialog zeigt die Hinweise, die Arcane in den
  GitHub-Releases der Image-Quelle findet, sonst einen Link dorthin. Sie werden erst
  beim Öffnen des Dialogs abgefragt. Gefragt wird wie in Arcanes eigener
  Update-Ansicht: mit dem Digest des neueren Images und, wenn das Update ein neuer
  Tag ist, mit dessen Namen. Für ein Image, das noch nicht gezogen ist, gibt es
  keine Hinweise.
- **Nach Updates suchen:** Der Button lässt Arcane sofort alle Images prüfen, statt
  auf den eigenen Zeitplan zu warten. Er wartet, bis Arcane jede Registry gefragt
  hat (bis zu fünf Minuten), und fragt danach neu ab.

### Automatische Updates und Updater

- **Auto-Update-Schalter** (Option unter **Steuerung**): je Container
  „Automatisch aktualisieren“. Der Schalter bearbeitet Arcanes Liste der Container,
  die automatische Updates auslassen (`autoUpdateExcludedContainers`); der
  Container selbst wird nicht angefasst. Die automatischen Updates laufen nur, wenn
  sie in Arcane unter den Einstellungen eingeschaltet sind (Standard aus). Ein
  Container mit dem Label `com.getarcaneapp.arcane.updater=false` (auch `0`, `no`,
  `off`) bleibt ausgenommen; Einschalten meldet das.
- **Arcane Updater** (Option unter **Entitäten**): je Umgebung ein Binärsensor
  „Updates werden installiert“ (`updater/status`), ein Sensor „Letztes
  Image-Update“ mit dem neuesten Eintrag aus `updater/history` und, mit Steuerung,
  ein Button „Alle Updates installieren“. Der Button startet einen Lauf wie Arcanes
  geplante Updates (`updater/run?automatic=true`): ausgenommene Container und
  solche mit dem Label bleiben, wie sie sind. Ein Lauf darf bei Arcane bis zu
  30 Minuten dauern; der Druck kehrt sofort zurück, der Lauf geht im Hintergrund
  weiter, und ein zweiter Druck während des Laufs wird abgelehnt. Das Ergebnis
  steht im Protokoll von Home Assistant und im Sensor.

### Freigebbarer Speicher

Vier Sensoren je Umgebung zeigen, wie viel Platz ungenutzte Images, gestoppte
Container, ungenutzte Volumes und der Build-Cache belegen. Arcane lässt Docker
dafür die Größe jedes Volumes ermitteln, was bei großen Volumes dauern kann. Die
Option ist deshalb standardmäßig aus, und die Integration fragt höchstens alle
15 Minuten, im Hintergrund, ohne die übrigen Abfragen aufzuhalten. Bis der erste
Bericht da ist, zeigen die Sensoren „unbekannt“. Der Prune-Button entfernt
Volumes und gestoppte Container nie; die beiden Sensoren dazu sind nur Auskunft.

Nach einem Prune fragt die Integration sofort einen neuen Bericht an, statt bis
zu 15 Minuten zu warten. Läuft gerade einer, der vor dem Prune begann, folgt ihm
ein zweiter. Arcane 3 räumt im Hintergrund auf und antwortet, bevor etwas gelöscht
ist; seit 0.10.1 verfolgt der Button deshalb Arcanes Aktivität
(`activities/{id}`, alle 2 Sekunden, höchstens 15 Minuten) und fragt Bericht und
Zähler erst danach ab. Der Druck kehrt erst zurück, wenn beides da ist; ein
Skript, das auf den Druck wartet, sieht den Stand nach dem Prune. Meldet Arcane
das Aufräumen als fehlgeschlagen oder abgebrochen, scheitert der Druck mit
Arcanes Grund, nachdem Bericht und Zähler aktualisiert sind. Ohne
`activities:read` geht es wie bis 0.10.0 weiter, und das Recht erscheint als
Reparatur. Das Attribut `report_started_at` der vier Sensoren nennt, wann der
gezeigte Bericht angefragt wurde (ISO 8601, UTC). Damit kann ein Dashboard einen
Bericht von nach dem Prune von einem älteren unterscheiden, auch wenn sich der
Wert nicht ändert. Die Zeit landet nicht in der Datenbank.

## Hintergrund-Jobs

Arcane erledigt vieles mit eigenen Hintergrund-Jobs: die automatischen Updates
(„Auto Update“), die Suche nach Image-Updates („Image Update Watcher“), Aufräumen,
Schwachstellen-Scans und andere. Mit der Option **Arcane Hintergrund-Jobs** (unter
**Entitäten**, standardmäßig aus) bekommt jeder Job, den Arcane für eine Umgebung
auflistet, einen Sensor am Gerät der Umgebung. Grundlage ist `agents/{id}/jobs`,
dieselbe Liste, die Arcane unter den Job-Einstellungen zeigt.

- **Zustand:** Ergebnis des letzten beendeten Laufs: `succeeded`, `partial`,
  `skipped`, `failed` oder `canceled` (deutsch: Erfolgreich, Teilweise erfolgreich,
  Übersprungen, Fehlgeschlagen, Abgebrochen). Einen Lauf, der Aufmerksamkeit
  braucht, meldet Arcane als `failed`. Ist der Job noch nie gelaufen, ist der
  Zustand unbekannt.
- **Attribute:** `next_run` (nächster geplanter Lauf), `last_run`, `last_finished`,
  `last_success`, `trigger` (`schedule`, `manual` …), `message` (Meldung des letzten
  Laufs), `last_error` (Meldung des letzten fehlgeschlagenen, teilweisen oder
  wiederholten Laufs), `running` (ein Lauf wartet oder läuft), `enabled`,
  `schedule`, `category`, `job_id`, bei Dauer-Jobs wie dem Image Update Watcher
  `worker_status` und `worker_error`.
- **Button „Job … ausführen“** (mit Steuerung): stellt einen Lauf ein
  (`?action=run`); Arcane antwortet sofort, der Sensor zeigt danach `running` und
  das Ergebnis. Es gibt ihn nur für Jobs, die Arcane von Hand starten lässt, und
  nicht für Jobs, die nur Arcane selbst ausführt, in entfernten Umgebungen. Er ist
  nicht verfügbar, solange der Job in Arcane ausgeschaltet ist oder eine
  Voraussetzung fehlt, etwa ein Scanner; Arcane würde den Lauf dann ablehnen.

Wofür: Seit die automatischen Updates bei Arcane liegen, zeigt „Job Auto Update“,
ob der letzte Lauf geklappt hat und wann der nächste kommt. Eine Automation kann
auf den Zustand `failed` reagieren oder darauf, dass `next_run` lange vorbei ist,
ohne dass ein neuer Lauf kam. Jobs, die Arcane unter einem anderen auflistet (etwa
einer je GitOps-Sync oder Volume-Backup), bekommen keine eigenen Entitäten.

## Mehrere Hosts

Arcane bindet weitere Docker-Hosts über den Arcane-Agenten an. Jeder gekoppelte
Agent ist in Arcane ein eigenes Environment. Die Integration liest bei jeder Abfrage
Arcanes Liste der Environments (`agents`) und legt für ein neues automatisch ein
Gerät mit allen Entitäten an, solange in den Optionen keine Auswahl gesetzt ist.
Alle Abfragen eines entfernten Environments reicht Arcane an dessen Agenten weiter;
Container, Projekte, Hoststatistik, Speicher, Jobs und Updates funktionieren wie
beim lokalen.

- **Gerät:** Modell „Arcane Agent“, als Version die des Agenten (aus
  `agentHost.agentVersion`); das lokale Environment zeigt die Version der
  Hauptinstanz. Nach einem Update des Agenten oder von Arcane zieht die
  Integration die Version am Gerät nach.
- **Zustand des Agenten:** Arcane liefert ihn in derselben Liste mit, ohne eigene
  Abfrage: Diagnose-Sensoren „Agent letzter Kontakt“ (letzter Heartbeat, sonst
  `lastSeen`; springt nie hinter einen schon gemeldeten Wert zurück), „Agent
  verbunden seit“, „Agent-Latenz“ (ms) und „Agent letzte
  Trennung“ (mit Grund). Hostname, Versionen und Verbindung stehen als Attribute am
  Binärsensor „Online“.
- **Getrennter Agent:** Arcane meldet ein entferntes Environment nur `online`,
  solange der Tunnel des Agenten steht. Ist es `offline` oder noch `pending`, fragt
  die Integration es in diesem Durchlauf nicht ab; Arcane würde jede Anfrage bis zu
  4 s halten und dann mit 503 ablehnen. „Online“ ist dann aus; „Agent letzter
  Kontakt“ und „Agent letzte Trennung“ bleiben sichtbar. Alle übrigen Entitäten des
  Environments sind nicht verfügbar, auch Zähler, Hoststatistik, Speicherwerte und
  Buttons: Sie hätten nur alte oder falsche Werte gezeigt (etwa 0 Container) bzw.
  eine Anfrage ausgelöst, die Arcane ablehnt. Sobald der Agent wieder verbunden ist,
  geht es mit dem nächsten Durchlauf weiter.
- **Jobs**, die nur die Hauptinstanz ausführt (`managerOnly`, etwa „Environment
  Health“), bekommen bei entfernten Environments keine Entitäten; dort laufen sie nie.
- **Auswahl in den Optionen:** Ist dort eine Auswahl gesetzt, erscheint für jedes
  Environment, das Arcane meldet, das aber nicht ausgewählt ist, eine Reparatur
  „Neues Arcane-Environment“. Zum Hinzufügen unter **Konfigurieren → Abfrage**
  auswählen, sonst ignorieren; ein weiteres neues Environment bekommt eine eigene.
- **Koppeln** eines Agenten geschieht in Arcane, nicht in Home Assistant.
- Gleich benannte Container auf zwei Hosts: siehe [Gerätenamen](#gerätenamen).

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

### Arcane-Ereignisprotokoll

Mit der Option **Arcane-Ereignisprotokoll auf dem Bus** (standardmäßig aus) liest
die Integration bei jeder Aktualisierung einmal die neuesten 100 Einträge aus
Arcanes eigenem Ereignisprotokoll, über alle Umgebungen, und feuert für jeden neuen
Eintrag `arcane_event`:

| Ereignis | Daten |
|---|---|
| `arcane_event` | `type`, `severity`, `title`, `description`, `resource_type`, `resource_id`, `resource_name`, `username`, `timestamp`, `metadata`, `event_id`, `environment`, `environment_id` |

Arcane schreibt dort Aktionen aus seiner Oberfläche und Ereignisse des
Docker-Daemons hinein, etwa `container.oom` (Schwere `error`), `container.die`
(`warning` bei Exit-Code ungleich 0), `container.unhealthy`, `image.pull`,
`project.deploy`, `system.auto_update`, `user.login` oder `user.logout`. `metadata` enthält bei
Daemon-Ereignissen unter anderem `exitCode`, `signal`, `composeProject` und
`composeService`. Anders als `oom_killed`, das aus Exit-Code 137 geschlossen wird,
kommt `container.oom` direkt von Docker.

Anmeldungen, Abmeldungen und Upgrades von Arcane legt Arcane ohne Umgebung ab; sie
kommen mit `environment_id: null` und `environment: null`. Einträge einer
Umgebung, die unter **Environments** abgewählt ist, werden nicht gemeldet, ebenso
Einträge einer Umgebung, die Arcane nicht listet und die die Integration nie kannte;
von ihr kommen nur Einträge `environment.*` über die Umgebung selbst, mit dem Namen
aus dem Eintrag. Fehlt eine bekannte Umgebung nur gerade in Arcanes Liste, etwa
während sie bearbeitet wird oder ausgeschaltet ist, kommen ihre Einträge weiter, mit
ihrem bisherigen Namen.

Jeder Eintrag wird einmal gemeldet, älteste zuerst. Der erste Abruf nach dem Start
markiert nur, was schon da ist; ein Neustart von Home Assistant spielt das
Protokoll also nicht erneut ab. Kommen zwischen zwei Abrufen mehr als 100 Einträge
dazu, fehlen die ältesten davon. Mit [Sofort aktualisieren](#sofort-aktualisieren)
kommt ein neuer Eintrag nach wenigen Sekunden, sonst mit der nächsten Abfrage.

## Sofort aktualisieren

Mit der Option **Bei Änderungen sofort aktualisieren** (standardmäßig aus) hält die
Integration je Umgebung eine Anfrage an `events/changes` offen. Arcane beantwortet
sie, sobald sich in der Umgebung etwas ändert, spätestens nach 50 Sekunden; dann
fragt die Integration sofort neu ab, statt auf das Intervall zu warten. Mehrere
Änderungen kurz hintereinander enden in einer Aktualisierung (höchstens eine alle
10 Sekunden). Die regelmäßige Abfrage läuft weiter; das Intervall kann mit der
Option größer werden.

- Fortschrittsmeldungen laufender Aktionen lösen keine Aktualisierung aus.
- Nach einem Neustart von Arcane fragt die Integration einmal neu ab.
- Fehlt das Recht `events:list` oder kennt Arcane den Pfad nicht, hört die
  Überwachung auf, bis die Integration neu geladen wird; ein fehlendes Recht
  erscheint als Reparatur. Bei anderen Fehlern versucht sie es nach 5 Sekunden
  wieder, danach mit doppelter Pause bis höchstens 5 Minuten.
- Die Diagnose zeigt unter `instant_updates` je Umgebung den Stand: Epoche,
  Revision, Zahl der angeforderten Aktualisierungen (`refresh_requests`), letzte
  Änderung, Fehler. Home Assistant fasst Anforderungen innerhalb von 10 Sekunden
  zu einer Aktualisierung zusammen; es laufen also weniger, als angefordert wurden.

## Abfragen

Alle Pfade unter `<Adresse>/api/`, Schlüssel im Header `X-Api-Key`. Listen werden
mit `limit=-1` vollständig geholt. Eine ausgeschaltete Option stoppt die Abfrage
dahinter.

| Pfad | Methode | Wann | Recht |
|---|---|---|---|
| `app/version` | GET | jede Abfrage, Einrichtung | keins |
| `app-version` | GET | nur wenn `app/version` mit 404 antwortet: erkennt Arcane 2.x | keins |
| `agents` | GET | jede Abfrage, Einrichtung, Optionen | `environments:list` |
| `agents/{environment}/containers` | GET | Option Container | `containers:list` |
| `agents/{environment}/projects` | GET | Option Compose-Projekte | `projects:list` |
| `agents/{environment}/images/counts` | GET | Option Images, Volumes und Netzwerke | `images:list` |
| `agents/{environment}/volumes/counts` | GET | Option Images, Volumes und Netzwerke | `volumes:list` |
| `agents/{environment}/networks/counts` | GET | Option Images, Volumes und Netzwerke | `networks:list` |
| `agents/{environment}/system/docker/info` | GET | Option Images, Volumes und Netzwerke | `system:read` |
| `agents/{environment}/ws/system/stats` | WebSocket, eine Nachricht | Option Hoststatistik | `system:read` |
| `agents/{environment}/version` | GET | Update-Entitäten, nur entfernte Umgebungen | `environments:read` |
| `agents/{environment}/containers/{container}` | POST `?action=start`, `stop`, `restart` | Schalter, Button „Neustarten“ | `containers:start`, `containers:stop`, `containers:restart` |
| `agents/{environment}/containers/{container}/redeploy` | POST | Button „Redeploy“ | `containers:redeploy` |
| `agents/{environment}/containers/{container}/update` | POST, wartet bis zu 16 Minuten | Image-Update installieren | `image-updates:apply` |
| `agents/{environment}/containers/{container}/autoupdate` | PUT `{"enabled": …}` | Auto-Update-Schalter | `containers:autoupdate` |
| `agents/{environment}/updater/run` | POST `?automatic=true`, wartet bis zu 31 Minuten | Button „Alle Updates installieren“, im Hintergrund | `image-updates:apply` |
| `agents/{environment}/updater/status` | GET | Option Arcane Updater, jede Abfrage | `image-updates:read` |
| `agents/{environment}/updater/history` | GET `?limit=1` | Option Arcane Updater, jede Abfrage | `image-updates:list` |
| `agents/{environment}/jobs` | GET; Antwort ohne `data`-Hülle | Option Arcane Hintergrund-Jobs, jede Abfrage | `jobs:list` |
| `agents/{environment}/jobs/{job}` | POST `?action=run`, Antwort 202 | Button „Job … ausführen“ | `jobs:run` |
| `agents/{environment}/projects/{project}/deploy` | POST `?action=up`, `redeploy`; Antwort als NDJSON-Stream | Schalter an, Button „Redeploy“ | `projects:deploy` |
| `agents/{environment}/projects/{project}` | POST `?action=down`, `restart` | Schalter aus, Button „Neustarten“ | `projects:down`, `projects:restart` |
| `agents/{environment}/system/prune` | POST; Arcane antwortet sofort mit `activityId` und räumt im Hintergrund auf | Button „Ungenutztes entfernen“ | `system:prune` |
| `agents/{environment}/activities/{activity}` | GET `?limit=1`, alle 2 s bis zum Ende, höchstens 15 Minuten | nach dem Prune | `activities:read` |
| `agents/{environment}/system/upgrade` | POST | Arcane-Update installieren | `system:upgrade` |
| `agents/{environment}/system/df` | GET | Option freigebbarer Speicher, höchstens alle 15 Minuten im Hintergrund, dazu sofort nach einem Prune | `system:read` |
| `agents/{environment}/images/updates/releasenotes` | GET `?imageRef=…&digest=…`, bei neuem Tag `&version=…` | Update-Dialog geöffnet | `image-updates:read` |
| `agents/{environment}/images/updates/check` | POST `?action=all` | Button „Nach Updates suchen“ | `image-updates:check` |
| `agents/{environment}/events/changes` | GET `?epoch=…&since=…&wait=50`, bleibt bis zu 50 s offen | Option sofort aktualisieren, dauerhaft im Hintergrund | `events:list` |
| `events` | GET `?sort=timestamp&order=desc&limit=100` | Option Arcane-Ereignisprotokoll, einmal je Abfrage für alle Umgebungen | `events:list` |

Die lokale Umgebung hat die ID `0`; ihre Arcane-Version kommt aus `app/version`
und kostet keine eigene Abfrage. Welche Pfade Arcane 3.0.0 anbietet, steht mit
Fundstelle im Quelltext in `tests/fixtures/arcane_routen_v3.0.0.json`, abgeglichen
mit der OpenAPI-Datei einer laufenden Arcane-3.0.0-Instanz
(`tests/fixtures/arcane_openapi_v3.0.0.json`). Arcane 3.0.1 bietet dieselben 474
Routen mit denselben Aktionen (Skript gegen Tag `v3.0.1` laufen lassen und
verglichen). Mit allen Abfrage-Optionen an sind das je Durchlauf und Umgebung neun
Anfragen, mit der Option Arcane Updater zwei mehr, mit Arcane Hintergrund-Jobs eine
mehr, und eine für das Ereignisprotokoll (ein entferntes Environment mit getrenntem
Agenten gar keine), dazu höchstens alle 15 Minuten und nach
jedem Prune `system/df` und mit der Option sofort aktualisieren je Umgebung eine
dauerhaft offene Anfrage.

## Gut zu wissen

- **Prune** entfernt ungenutzte Images, ungenutzte Netzwerke und den Build-Cache,
  nie Container oder Volumes. Die Option wählt: kein Button, nur Dangling-Images
  oder alle ungenutzten Images. Ein Button in Home Assistant fragt nicht nach.
  Der Druck dauert so lange wie das Aufräumen bei Arcane.
- **Hoststatistik** gibt es bei Arcane nur über einen WebSocket. Die Integration
  öffnet ihn je Abfrage, liest die erste Nachricht und schließt ihn wieder. CPU und
  RAM je Container liefert die Integration nicht.
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
Release dort und läuft nur mit Arcane 2.x. Version 0.6.0 dieses Forks stellt die
Pfade auf Arcane 3 um.

Aufbau, Prüfungen und Abläufe folgen
[HA-WOW](https://github.com/philf90/HA-WOW): Versionshinweise unter
`.github/release-notes/`, Pflicht-Vorlage für Pull Requests, Konsistenztest. Die
Versionen 0.1.0 bis 0.4.0 hatten kein Release; ihre Einträge stehen in
`CHANGELOG.md` im Git-Verlauf (Stand von Commit `d69d771`).

## Teststand

Automatische Tests laufen gegen Home Assistant 2026.2.3, die Pfade gegen die
Routen von Arcane 3.0.0. Live geprüft mit Arcane 3.0.0 und Home Assistant 2026.11.1:
Einrichtung und alle lesenden Abfragen; der Prune mit Version 0.7.0 und Arcane
3.0.1; die übrigen schreibenden Aktionen (Start, Stop, Neustart, Redeploy,
Arcane-Update) nur im Vertragstest.

- **0.11.x:** 297 automatische Tests. 0.11.2: 3 neue Tests in
  `test_sofort_und_ereignisse.py`: Einträge einer bekannten Umgebung, die Arcane
  gerade nicht listet, kommen mit ihrem bisherigen Namen; `environment.*` einer
  unbekannten Umgebung kommt mit dem Namen aus dem Eintrag; abgewählte Umgebung
  bleibt stumm, auch für `environment.*`. Gegenprobe: 7 Schutzmaßnahmen einzeln
  ausgebaut, jedes Mal schlug ein Test fehl. Live gefunden am 16.11.2026.
  0.11.1: 3 neue Tests in `test_agenten.py`:
  bei getrenntem Agenten Zähler, Hoststatistik, Speicherwerte, Prune-Button,
  „verbunden seit“ und Latenz nicht verfügbar, Online, letzter Kontakt und letzte
  Trennung verfügbar, lokales Environment unberührt, alles zurück nach der
  Rückkehr; letzter Kontakt springt nach der Trennung nicht zurück, ein neuerer
  Heartbeat gilt. Gegenprobe: 8 Schutzmaßnahmen einzeln ausgebaut, jedes Mal schlug
  ein Test fehl. Live gefunden am 16.11.2026 mit gestopptem Agent-Container.
  0.11.0: 15 neue Tests in `test_agenten.py`:
  Agent-Sensoren nur bei entfernten Environments, Heartbeat vor `lastSeen`, Werte
  bei getrenntem Agenten; Attribute am Online-Sensor; Modell und Version am Gerät,
  Version nach einem Update des Agenten; getrennter (auch `pending`) Agent ohne
  Abfragen, ohne Speicherbericht und ohne Warten auf Änderungen, Rückkehr, lokales
  Environment immer abgefragt; Environment im Gerätenamen mit und ohne Art, lokal
  unverändert; Manager-Jobs nur lokal; Reparatur für nicht ausgewählte Environments,
  nicht für ausgeschaltete, weg nach Verschwinden und Entladen, keine ohne Auswahl;
  das Modell liest Arcanes Felder. Gegenprobe: 18 Schutzmaßnahmen einzeln ausgebaut,
  jedes Mal schlug ein Test fehl. Live geprüft am 16.11.2026: Agent-Daten, Gerät,
  Namen, getrennter Agent (dabei die Fehler gefunden, die 0.11.1 behebt); die
  Reparatur für nicht ausgewählte Environments nicht.
- **0.10.x:** 276 automatische Tests. 0.10.1: 11 neue Tests in
  `test_aufraeumen.py`: Bericht und Zähler erst nach dem Ende des Prunes und vor
  dem Ende des Drucks, auch während der Abklingzeit des Coordinators;
  fehlgeschlagenes und abgebrochenes Aufräumen mit Grund, nach der
  Aktualisierung; Antwort ohne Aktivität, fehlendes Recht, verlorene Aktivität,
  Grenze von 15 Minuten; Recht der Option; der Client liest die Aktivität mit
  `limit=1`. Gegenprobe: 10 Schutzmaßnahmen einzeln ausgebaut, jedes Mal schlug
  ein Test fehl. Live gefunden: Der Bericht nach dem Prune begann 2 ms nach dem
  Druck und zeigte den Stand davor. 0.10.0: 25 neue Tests in `test_jobs.py`:
  Job-Sensor mit Ergebnis, Zeiten (Nanosekunden von Go), Fehler, laufendem Lauf,
  Job ohne Lauf, Status außerhalb der Optionen, Worker-Zustand; Kind-Jobs ohne
  Entität, Jobs kommen und gehen, abgelehnte Liste nennt `jobs:list`; Button stellt
  den Lauf ein und fragt neu ab, nur für von Hand startbare Jobs, nicht für
  Manager-Jobs in entfernten Umgebungen, nicht verfügbar bei ausgeschaltetem Job
  oder fehlender Voraussetzung, fehlendes Recht und Ablehnung durch Arcane; Rechte,
  Option im Dialog, Übersetzungen; der Client liest die Liste ohne Hülle und sendet
  `action=run` ohne Body. Gegenprobe: 17 Schutzmaßnahmen einzeln ausgebaut, jedes
  Mal schlug ein Test fehl. **Nicht live geprüft.**
- **0.9.x:** 240 automatische Tests. 0.9.0: 28 neue Tests in `test_updater.py`:
  Installieren über den Updater, übersprungene und abgelehnte Updates mit Grund,
  das eigene Ergebnis entscheidet, leeres Ergebnis kein Erfolg, fehlendes Recht;
  Label-Ausnahme wie in Arcane gelesen, Schalter mit und ohne Feld, Einschalten
  trotz Label abgelehnt; Updater-Sensoren, Lauf im Hintergrund, zweiter Druck
  abgelehnt, Fehler und fehlendes Recht; Rechte der Optionen; der Client sendet
  `automatic=true` und `{"enabled": …}`. Gegenprobe: 16 Schutzmaßnahmen einzeln
  ausgebaut, jedes Mal schlug ein Test fehl. **Nicht live geprüft.**
- **0.8.x:** 212 automatische Tests. 0.8.1: 5 neue Tests: Einträge ohne
  Umgebung (Anmeldung) werden gemeldet, Einträge nicht verfolgter Umgebungen
  nicht, ein Abruf je Aktualisierung für alle Umgebungen, Diagnose des
  Protokolls, und der Client liest gegen einen Stub, der wie Arcane nach Umgebung
  filtert, das Protokoll mit den Anmeldungen. Gegenprobe: Protokoll wieder je
  Umgebung gelesen (der Fehler aus 0.8.0), Einträge ohne Umgebung übergangen,
  fremde Umgebungen gemeldet, fehlendes Recht nicht gemeldet, Diagnose ohne
  Lesestand; jedes Mal schlug ein Test fehl. Live gefunden: mit 0.8.0 kam eine
  Anmeldung in Arcane nicht an. 0.8.0: 19 neue Tests in
  `test_sofort_und_ereignisse.py`: Änderung löst sofort eine Aktualisierung aus,
  die erste Antwort nicht, Fortschrittsmeldungen nicht, ein Neustart von Arcane
  schon; fehlendes Recht und fehlender Pfad beenden die Überwachung, andere Fehler
  führen zu wachsenden Pausen, eine Antwort ohne Epoche gilt als Fehler, schnelle
  Antworten ohne Neuigkeiten zu einer kurzen Pause; die Überwachung folgt den
  Umgebungen und endet mit dem Entladen; neue Einträge des Ereignisprotokolls
  einmal und älteste zuerst, nicht beim ersten Abruf, keine verspäteten alten,
  fehlerhafte übersprungen, nach einem Fehler nichts verloren. Gegenprobe: 16
  Schutzmaßnahmen einzeln ausgebaut, jedes Mal schlug ein Test fehl.
  **Nicht live geprüft.**
- **0.7.x:** 188 automatische Tests. 0.7.1: 6 neue Tests in `test_speicher.py`:
  neuer Bericht sofort nach dem Prune, danach wieder das Intervall; ein Bericht,
  der vor dem Prune begann, bekommt einen zweiten; `report_started_at` ist der
  Beginn des Berichts, nicht seine Ankunft, und fehlt vor dem ersten; ohne Option
  oder bei gescheitertem Prune kein Bericht; die Zeit wird nicht aufgezeichnet.
  Gegenprobe: acht Schutzmaßnahmen einzeln ausgebaut, jedes Mal schlug ein Test
  fehl. Live geprüft mit 0.7.0, Home Assistant 2026.11.1 und Arcane
  3.0.1: Prune-Button (3 ungenutzte Images, 1,56 GB, genau der vorher gemeldete
  freigebbare Image-Speicher); 0.7.1 **nicht live geprüft**. 0.7.0: 21 neue Tests in
  `test_image_updates.py` und `test_speicher.py`: gesperrtes Update angezeigt, aber
  nicht installierbar; Versionshinweise mit Digest bzw. Tag wie in Arcanes
  Update-Ansicht, nur `https://`-Links, abgelehntes Recht gemeldet, keine Hinweise
  für nicht gezogene Images; Button „Nach Updates suchen“ nur mit Update-Entitäten
  und Steuerung; freigebbarer Speicher nur auf Wunsch, höchstens einmal je
  Intervall, im Hintergrund, auch wenn der Bericht vor dem ersten Stand ankommt.
  Gegenprobe: zwölf Schutzmaßnahmen einzeln ausgebaut, jedes Mal schlug ein Test
  fehl. **Nicht live geprüft.**
- **0.6.x:** 161 automatische Tests. Nach 0.6.1: 7 neue Tests in
  `test_arcane_routen.py`; die Routen aus dem Arcane-Quelltext sind dieselben wie
  in der OpenAPI-Datei einer laufenden Arcane-3.0.0-Instanz (bis auf WebSocket-,
  versteckte und reine Agent-Routen), ebenso die erlaubten Werte von `?action=`,
  und das Skript löst Pfad-Konstanten und Hilfsfunktionen auf. Bis dahin fehlten
  in der Routenliste 72 Operationen. Gegenprobe: alte Routenliste, eine Aktion
  gestrichen, Markierung „versteckt“ entfernt, Konstanten bzw. Hilfsfunktionen im
  Skript abgeschaltet; jedes Mal schlug ein Test fehl. 0.6.1: 5 neue Tests (Geräte ohne das
  veraltete `via_device`, Suche über `async_get_device_by_identifier` mit
  Rückfall für ältere Home-Assistant-Versionen, Zertifikatswarnung nur bei
  `https://`); Gegenprobe: jede der drei Korrekturen einzeln zurückgenommen,
  jedes Mal schlug ein Test fehl. **Nicht live geprüft.** 0.6.0: 149 Tests, davon 17 neu: Vertrag mit Arcane 3.0.0
  (ein Stub kennt genau die Routen aus dem Arcane-Quelltext und antwortet auf
  unbekannte Pfade mit 404, auf unbekannte Aktionen mit 400; jede öffentliche
  Methode des Clients muss darin vorkommen), Erkennung von Arcane 2.x im Client,
  im Einrichtungsdialog und beim Start, unveränderte `unique_id`s, Gerätemodelle,
  deutsche Entitätsnamen und Ereignisnamen, keine URLs in Texten, Fehlermeldungen
  in der Form, die hassfest verlangt (`{"message": …}`). Gegenprobe:
  Pfadteil `agents` zurück auf `environments`, `?action=` weggelassen, Deploy
  wieder an `projects/{id}/up`, Erkennung von Arcane 2.x entfernt, Entitätsname
  `Neustarten` geändert, Beispiel-URL zurück in den Text, Fehlermeldung als blanker
  Text; jedes Mal schlug ein Test fehl. **Nicht live geprüft.**
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
`test_arcane_vertrag.py` lässt den Client gegen einen Stub laufen, der nur die
Routen von Arcane 3.0.0 kennt (`tests/fixtures/arcane_routen_v3.0.0.json`, erzeugt
mit `python3 .github/scripts/arcane_routen.py <arcane-clone> v3.0.0`; das Skript
liest den Go-Quelltext nur als Text). `test_arcane_routen.py` vergleicht diese
Routen mit der Kurzfassung der OpenAPI-Datei, die Arcane ohne Anmeldung unter
`<url>/api/openapi.json` ausliefert (`tests/fixtures/arcane_openapi_v3.0.0.json`,
erzeugt mit `python3 .github/scripts/arcane_openapi.py openapi.json`). `test_entitaets_ids.py` hält `unique_id`s,
Gerätemodelle, deutsche Entitätsnamen und Ereignisnamen fest, auf die Dashboards
bauen.
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
