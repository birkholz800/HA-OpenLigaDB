# OpenLigaDB Home Assistant Integration (HACS)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/custom-components/ha-openligadb)

Vereinsspiele, Ergebnisse und Tabellen von [OpenLigaDB](https://www.openligadb.de) für Home Assistant.

---

## ⚽ Funktionen

| Attribut | Inhalt |
|---|---|
| `last_match` | Letztes abgeschlossenes Vereinsspiel |
| `current_match` | Laufendes Vereinsspiel (zeitbasiert erkannt) |
| `next_match` | Nächstes Vereinsspiel |
| `table` | Ligatabelle beim Tabellensensor |

- **Wettbewerbsübergreifend:** Bundesliga, Champions League, DFB-Pokal usw.
- Spiele mit Ergebnis, Gegner, Termin, Wettbewerb und verfügbaren Tordaten.
- Aktualisierung: **15 Minuten**, rund um Live-Spiele **1 Minute**.
- Ereignisse: `openligadb_goal`, `openligadb_match_start`, `openligadb_match_end`.

---

## 📦 Installation

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=birkholz800&repository=HA-OpenLigaDB&category=integration)

1. **HACS → Benutzerdefinierte Repositories** öffnen.
2. `https://github.com/birkholz800/HA-OpenLigaDB` hinzufügen, Kategorie **Integration**.
3. **OpenLigaDB** herunterladen und Home Assistant neu starten.

Alternativ: `custom_components/openligadb` nach `/config/custom_components/openligadb` kopieren (vorher sichern), dann neu starten.

## ⚙️ Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → OpenLigaDB**.
2. **Verein** oder **Ligatabelle** wählen.
3. Liga und Saison auswählen; beim Verein anschließend das Team wählen.

- **Ligen:** dynamisch aus OpenLigaDB.
- **Saison:** aktuelles Jahr vorausgewählt; für **2025/26 → `2025`** eintragen.
- **Verein:** Liga/Saison dienen zur Vereinsauswahl; Spiele kommen aus allen Wettbewerben, auch über Saisonwechsel hinweg.
- **Tabelle:** bleibt an die gewählte Liga/Saison gebunden.

## ⚠️ Wichtig

- Suchfenster: **26 Wochen zurück/voraus**; ohne passendes Spiel ist das Attribut leer.
- Nur vorhandene OpenLigaDB-Daten mit **derselben Team-ID** werden berücksichtigt.
- **Community-/Testwettbewerbe können enthalten sein** und die Spielauswahl beeinflussen.
- Live-Status ist zeitbasiert geschätzt, nicht von der API bestätigt.
- API-Schutz: gemeinsame Zwischenspeicherung, maximal eine Anfrage pro Sekunde; bei **429** mindestens 60 Sekunden Pause gemäß `Retry-After`.
- Abruffehler: Sensor vorübergehend nicht verfügbar, kein Bundesliga-Ersatzergebnis.

**Update bestehender Einträge:** Entity-IDs bleiben erhalten. Alte Karten mit `upcoming_matches` auf `next_match` umstellen; `competitions` und `discovery_complete` entfallen. Nicht eindeutig auflösbare Freitext-Vereine neu einrichten.

## 🖼️ Dashboard-Beispiele

### 1. Match Card with Crests, Scores & Goal Scorers / Cards

<img width="349" height="184" alt="Screenshot 2026-09-17 100612" src="https://github.com/user-attachments/assets/f3a83039-365b-4dc0-b424-5ec5c5ae9e4a" />

**Markdown-Karte** hinzufügen; `team_sensor` durch deine Entity-ID ersetzen:

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

**Markdown-Karte** hinzufügen und die Sensor-Entity-ID anpassen:

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

### Torbenachrichtigung und Lichtsignal

- Wettbewerbsübergreifend, **auch bei Gegentoren**; benötigt Tordaten von OpenLigaDB.
- Nach Neustart während eines Spiels sind erneute Meldungen möglich.
- `notify.notify` und `light.living_room` anpassen.

```yaml
alias: "OpenLigaDB Goal Notification"
trigger:
  - platform: event
    event_type: openligadb_goal
condition: []
action:
  - service: notify.notify
    data:
      title: "⚽ Tor im Spiel von {{ trigger.event.data.team }}"
      message: "{{ trigger.event.data.goal_getter or 'Goal' }} scored ({{ trigger.event.data.score }}) vs {{ trigger.event.data.opponent }}! (Minute: {{ trigger.event.data.minute }})"
  - service: light.turn_on
    target:
      entity_id: light.living_room
    data:
      flash: short
```
