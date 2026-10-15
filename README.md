# OpenLigaDB Home Assistant Integration (HACS)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/custom-components/ha-openligadb)

A powerful Home Assistant Custom Integration for integrating match data, live scores, and league standings directly from [OpenLigaDB.de](https://www.openligadb.de).

---

## ⚽ Features

- **Team Sensors**:
  - Team match state (e.g. `Live: 2 - 1`, `Upcoming (@ FC Bayern)`, `Finished: 3 - 1`).
  - `last_match`: Last completed match with date, opponent, score, home/away status, goal scorers (separated by Home & Away) & card events.
  - `current_match`: Live match details including minute & live score.
  - `next_match`: Next upcoming match with ISO timestamp (ideal for HA countdown cards), opponent, venue & matchday.
  - `entity_picture`: Automatically embeds official team crest/logo in Home Assistant UI.

- **League Sensors**:
  - State: Current matchday (e.g. `Matchday 4`).
  - `table`: Full standings as a structured array (Rank, Team, Matches, Won, Draw, Lost, Goals For, Goals Against, Goal Diff, Points & Crest URL).

- **Adaptive Polling Rate**:
  - Default polling every 15 minutes.
  - Automatically detects active live matches and switches to **1-minute polling** during live games.

- **Event Triggers for Automations**:
  - Fires `openligadb_goal` event when goals are scored live.
  - Fires `openligadb_match_start` and `openligadb_match_end` events.

---

## 📦 Installation via HACS

**Quick Install:** Click the badge to directly add this repository to your HACS instance, or follow the manual steps below.
[![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Belgadon&repository=HA-OpenLigaDB&category=integration)

**Manual Installation:**
1. Open **HACS** in your Home Assistant dashboard.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add the URL of this GitHub repository:
   - **Repository**: `https://github.com/Belgadon/HA-OpenLigaDB`
   - **Category**: `Integration`
4. Click **Add** and then install the **OpenLigaDB** integration from the newly added repository.
5. Restart Home Assistant.

---

## ⚙️ Configuration in Home Assistant

1. Go to **Settings** -> **Devices & Services** -> **Add Integration**.
2. Search for **OpenLigaDB**.
3. Choose what to set up:
   - **Team Sensor**: Select the league (e.g. `bl1`) and your team (e.g. *FC Bayern München*, *BVB*, *Bayer 04 Leverkusen*).
   - **League Sensor**: Select the league (e.g. `bl1` for 1. Bundesliga, `bl2`, `bl3`, `dfb`, etc.).

---

## Wettbewerbsübergreifende Vereinsspiele

Die Ligaauswahl lädt alle von OpenLigaDB angebotenen Liga-Kürzel dynamisch. Beim Einrichten ist das aktuelle Kalenderjahr voreingestellt; die Saison bleibt editierbar. OpenLigaDB verwendet üblicherweise das Startjahr einer Saison: Für 2025/26 ist deshalb `2025` einzutragen, auch wenn das Kalenderjahr bereits 2026 ist. Historische Liga-Kürzel benötigen eine dazu passende Saison.

Bei Vereinssensoren dient die gewählte Liga zur Vereinsauswahl. Anschließend werden automatisch alle Wettbewerbe derselben Saison nach der Team-ID durchsucht, etwa Bundesliga, DFB-Pokal und Champions League. Voraussetzung ist, dass OpenLigaDB den Wettbewerb und den Verein mit derselben Team-ID führt. Fehlende Spielpläne können nicht ergänzt werden. Die Wettbewerbssuche wird 24 Stunden zwischengespeichert; beim ersten Laden entstehen zusätzliche API-Abfragen. Fehlerhafte Teilabfragen machen den Sensor vorübergehend nicht verfügbar, statt einen unvollständigen Spielplan als vollständig auszugeben.

- `last_match`, `current_match` und `next_match` berücksichtigen alle gefundenen Wettbewerbe.
- `upcoming_matches` enthält bis zu 20 kommende Spiele, chronologisch sortiert.
- `competitions` enthält die gefundenen Wettbewerbe.
- Jedes Spiel enthält zusätzlich `league_name`, `league_shortcut` und `season`.

Bestehende Einträge behalten ihre konfigurierte Saison und Entity-ID. Bei alten Vereinseinträgen wird die Team-ID über einen eindeutigen vollständigen Namen oder Kurznamen aufgelöst. Nicht eindeutig auflösbare alte Freitext-Namen müssen durch erneutes Einrichten mit der Vereinsauswahl ersetzt werden.

### Mehrere Wettbewerbe gleichzeitig anzeigen

In einer Markdown-Karte `sensor.openligadb_dein_verein` durch die tatsächliche Entity-ID ersetzen:

```yaml
type: markdown
content: >-
  {% for match in state_attr('sensor.openligadb_dein_verein', 'upcoming_matches') or [] %}
  **{{ match.league_name }}** · {{ as_timestamp(match.date) | timestamp_custom('%d.%m.%Y %H:%M') }}

  {{ match.home_team_name }} – {{ match.away_team_name }}

  {% endfor %}
```

### Diese lokale Änderung installieren

Den Ordner `custom_components/openligadb` aus dieser Version nach `/config/custom_components/openligadb` der Home-Assistant-Installation kopieren (vorher sichern) und Home Assistant neu starten. Ein HACS-Update aus dem ursprünglichen Repository kann diese lokalen Änderungen überschreiben; für dauerhafte Nutzung empfiehlt sich ein eigener Fork als HACS-Repository.

## 🖼️ Lovelace Dashboard Mardown Cards

### 1. Match Card with Crests, Scores & Goal Scorers / Cards

<img width="349" height="184" alt="Screenshot 2026-09-17 100612" src="https://github.com/user-attachments/assets/f3a83039-365b-4dc0-b424-5ec5c5ae9e4a" />

Add a **Markdown Card** in your dashboard and paste this code:

```yaml
type: markdown
title: "⚽ Matchday Card"
content: >-
  {% set team_sensor = 'sensor.openligadb_fortuna_dusseldorf' -%}

  {% set match = state_attr(team_sensor, 'current_match') or
  state_attr(team_sensor, 'next_match') or state_attr(team_sensor, 'last_match')
  -%}

  {% if match -%}


  {% set ns_home = namespace(events=[]) -%}

  {% for goal in match.home_goals -%}
    {% set ns_home.events = ns_home.events + ['⚽ **' ~ ('Goal' if goal.player == '' else goal.player) ~ '** (' ~ goal.minute ~ '\'' ~ (', Pen.' if goal.is_penalty else '') ~ (', OG' if goal.is_own_goal else '') ~ ')'] -%}
  {% endfor -%}

  {% for card in match.home_cards -%}
    {% set ns_home.events = ns_home.events + [('🟨' if card.card_type == 'yellow' else '🟥') ~ ' **' ~ ('Card' if card.player == '' else card.player) ~ '** (' ~ card.minute ~ '\')'] -%}
  {% endfor -%}

  {% set home_events_str = ns_home.events | join('<br>') -%}


  {% set ns_away = namespace(events=[]) -%}

  {% for goal in match.away_goals -%}
    {% set ns_away.events = ns_away.events + ['(' ~ goal.minute ~ '\'' ~ (', Pen.' if goal.is_penalty else '') ~ (', OG' if goal.is_own_goal else '') ~ ') **' ~ ('Goal' if goal.player == '' else goal.player) ~ '** ⚽'] -%}
  {% endfor -%}

  {% for card in match.away_cards -%}
    {% set ns_away.events = ns_away.events + ['(' ~ card.minute ~ '\') **' ~ ('Card' if card.player == '' else card.player) ~ '** ' ~ ('🟨' if card.card_type == 'yellow' else '🟥')] -%}
  {% endfor -%}

  {% set away_events_str = ns_away.events | join('<br>') -%}


  | | **{{ match.group or 'Matchday' }}** | |

  |:---|:---:|---:|

  | <img src="{{ match.home_team_icon }}" width="48" height="48"> | {% if
  match.is_live %}🔴 **LIVE {{ match.minute or '' }}**{% elif match.is_finished
  %}**FT**{% else %}**{{ as_timestamp(match.date) | timestamp_custom('%a.
  %d.%m.') }}**{% endif %} | <img src="{{ match.away_team_icon }}" width="48"
  height="48"> |

  | **{{ match.home_team_name }}** | {% if match.is_live or match.is_finished
  %}**{{ match.score_home }} : {{ match.score_away }}**{% else %}**{{
  as_timestamp(match.date) | timestamp_custom('%H:%M') }}**<br>_{{
  match.location }}_{% endif %} | **{{ match.away_team_name }}** |

  {% if home_events_str or away_events_str -%}

  | {{ home_events_str }} | | {{ away_events_str }} |

  {% endif -%}


  {% else -%}

  No match data available.

  {% endif -%}
```

---

### 2. Formatted Bundesliga Standings Table (Markdown Table Card)

<img width="375" height="348" alt="image" src="https://github.com/user-attachments/assets/259dfe74-ab0e-440a-a32b-b0180f752081" />

Uses a **Markdown Card**, rendering as a table in Home Assistant:

```yaml
type: markdown
title: ⚽ Table Card
content: >-
  | # | Team | P | Goals | Diff | Pts |

  |:---:|:---|:---:|:---:|:---:|:---:|

  {% for row in state_attr('sensor.openligadb_bl3_tabelle_2026', 'table') -%}

  | {{ row.rank }} | <img src="{{ row.team_icon_url }}" width="16" height="16">
  **{{ row.short_name or row.team_name }}** | {{ row.matches }} | {{ row.goals
  }}:{{ row.opponent_goals }} | {{ '+' if row.goal_diff > 0 else '' }}{{
  row.goal_diff }} | **{{ row.points }}** |

  {% endfor %}
```

---

## 🔔 Automations & Notifications

### Example: Smartphone Notification & Light Flash on Goal

```yaml
alias: "OpenLigaDB Goal Notification"
trigger:
  - platform: event
    event_type: openligadb_goal
condition: []
action:
  - service: notify.notify
    data:
      title: "⚽ GOAL FOR {{ trigger.event.data.team }}!"
      message: "{{ trigger.event.data.goal_getter or 'Goal' }} scored ({{ trigger.event.data.score }}) vs {{ trigger.event.data.opponent }}! (Minute: {{ trigger.event.data.minute }})"
  - service: light.turn_on
    target:
      entity_id: light.living_room
    data:
      flash: short
```
