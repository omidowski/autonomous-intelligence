# Flask-API: Content-Varianten mit vergleichender Analyse

Schlanke, eigenstaendige Flask-App neben der bestehenden FastAPI-Plattform.
Sie teilt sich nur die LLM-Schicht (`autonomous_intelligence.llm.LLMProvider`),
damit dieselben Provider (`codex` / `openai` / `openrouter` / `demo`) greifen.
Die FastAPI-App unter `src/autonomous_intelligence/` bleibt unveraendert.

**Use case:** Ein Content-Team briefed einen Post. Die API erzeugt zwei Copy-
Varianten mit unterschiedlichem Winkel (A/B) und vergleicht sie anhand
plattformspezifischer Kriterien - ueber mehrere Turns hinweg, mit Gedaechtnis
fuer das bisherige Gespraech.

## Anforderungen und wo sie umgesetzt sind

| Anforderung | Umsetzung |
| --- | --- |
| Flask API, 2 POST | `POST /api/conversations`, `POST /api/conversations/<id>/generate` ([app.py](app.py)) |
| Flask API, 2 GET | `GET /api/conversations`, `GET /api/conversations/<id>` (+ `GET /api/health`) |
| SQLite, 2 Tabellen, schreiben und lesen | `conversations` und `messages` ([db.py](db.py)) |
| Text-Generation-Endpoint, der die DB aktualisiert | `POST /api/conversations/<id>/generate` schreibt Nutzer- und Assistant-Turn |
| Structured Output | Pydantic-Schemas + Validierung ([models.py](models.py)), Schema wird woertlich in den Prompt gerendert |
| Use-case-spezifische vergleichende Analyse | Gewichtete A/B-Auswertung pro Plattform ([analysis.py](analysis.py)) |
| 2 Prompt-Engineering-Techniken | Few-Shot und Chain-of-Thought ([prompts.py](prompts.py)), pro Request zuschaltbar |
| Konversationshistorie | Historie liegt in `messages` und wird in jeden Folge-Prompt gerendert |

## Starten

```bash
python -m pip install -e ".[dev]"
flask --app flask_app.wsgi run --debug --port 5001
```

Ohne konfigurierten LLM-Provider laeuft die App im Offline-Modus: sie antwortet
weiterhin vollstaendig und schema-konform, die Inhalte sind dann aber als
`[offline]` gekennzeichnete Platzhalter. `GET /api/health` zeigt den Modus an.

## Datenmodell

Zwei Tabellen, bewusst knapp gehalten:

- **`conversations`** - eine Beratungs-Session: Titel, Plattform, Zielgruppe,
  Markenstimme. Das ist der geteilte Kontext, gegen den jeder Turn laeuft.
- **`messages`** - jeder Turn dieser Session in Reihenfolge (`seq`), Rolle
  `user` oder `assistant`. Assistant-Turns tragen zusaetzlich das vollstaendige
  validierte JSON (`payload_json`) sowie die verwendeten Prompt-Techniken.

Weil die Historie in SQLite liegt und nicht im Prozessspeicher, ueberlebt sie
Neustarts, und mehrere Worker sehen denselben Verlauf.

## Konversationshistorie

Jeder `generate`-Aufruf liest die bisherigen Turns aus `messages` und rendert
sie in den Prompt (`prompts.render_history`). Assistant-Turns gehen dabei
verdichtet ein - Winkel, Empfehlung, naechster Schritt - statt als komplettes
JSON, damit der Kontext ueber viele Turns nicht explodiert. In den Prompt gehen
die letzten `HISTORY_TURN_LIMIT` (6) Eintraege; die vollstaendige Historie
bleibt ueber `GET /api/conversations/<id>` abrufbar.

Die Antwort weist unter `history_turns_used` aus, wie viele fruehere Turns in
den Prompt eingeflossen sind.

## Prompt-Engineering

Beide Techniken sind einzeln implementiert und pro Request waehlbar
(`"techniques": ["few_shot", "chain_of_thought"]`, Default sind beide):

1. **Few-Shot** (`few_shot_block`) - ein vollstaendig durchgerechnetes Beispiel
   (Briefing zu erwartetem JSON) steht im Prompt. Fixiert Format, Tonlage und
   Detailtiefe, bevor das Modell die echte Aufgabe sieht.
2. **Chain-of-Thought** (`chain_of_thought_block`) - erzwingt eine schrittweise
   Abwaegung *vor* dem Verdikt und verankert sie im Ausgabeschema
   (`analysis.reasoning_steps`), statt sie unsichtbar bleiben zu lassen.

Weil die Techniken einzeln abschaltbar sind, laesst sich ihr Effekt direkt
vergleichen - dieselbe Konversation einmal mit und einmal ohne Few-Shot.

## Structured Output

Die LLM-Antwort wird gegen `GenerationResult` validiert: genau zwei Varianten
(eine `A`, eine `B`), genau die fuenf Kriterien, Punktwerte 1-5, Pflichtfelder
mit Mindestlaengen. Schlaegt die Validierung fehl, bekommt das Modell genau
einen Repair-Turn mit dem konkreten Fehlertext; die Antwort weist das ueber
`attempts` und `repaired` aus.

## Vergleichende Analyse

Die subjektive Bewertung kommt vom Modell, die Auswertung laeuft deterministisch
im Code ([analysis.py](analysis.py)):

- **Plattformgewichte** - auf TikTok zaehlt `hook_strength` 1.8-fach, auf
  LinkedIn zaehlen `brand_voice_fit` und `audience_relevance` 1.5-fach. Dieselben
  Punktwerte koennen je nach Plattform zu unterschiedlichen Siegern fuehren.
- **Laengencheck** gegen das harte Zeichenlimit der Plattform (X: 280, LinkedIn:
  1300, ...).
- **Konsistenzpruefung** - `verdict_consistent` faellt auf `false`, wenn das
  Modellverdikt seinen eigenen Punktwerten widerspricht.
- **`decisive_criterion`** - das Kriterium mit dem groessten Abstand zwischen A
  und B.

## Beispielablauf

```bash
# 1. Session anlegen
curl -s -X POST localhost:5001/api/conversations \
  -H 'content-type: application/json' \
  -d '{"title":"Launch Fruehwarnsystem","platform":"linkedin",
       "audience":"Head of Operations im Mittelstand",
       "brand_voice":"sachlich, konkret, ohne Buzzwords"}'

# 2. Erster Turn: Varianten + Analyse
curl -s -X POST localhost:5001/api/conversations/1/generate \
  -H 'content-type: application/json' \
  -d '{"brief":"Wir starten ein Tool, das Lieferverzoegerungen 48h vorher meldet."}'

# 3. Folgeturn - greift auf die Historie zurueck, hier nur mit Few-Shot
curl -s -X POST localhost:5001/api/conversations/1/generate \
  -H 'content-type: application/json' \
  -d '{"brief":"Variante B war zu nuechtern. Mehr Kante, gleicher Beleg.",
       "techniques":["few_shot"]}'

# 4. Sessions auflisten (optional nach Plattform gefiltert)
curl -s 'localhost:5001/api/conversations?platform=linkedin'

# 5. Volle Historie einer Session lesen
curl -s localhost:5001/api/conversations/1
```

## Tests

```bash
pytest tests/test_flask_app.py -q
```

Die Testsuite laeuft deterministisch ohne LLM: `create_app(testing=True)` setzt
den Provider bewusst auf "keiner", sodass der Offline-Generator greift. Fuer
einen Lauf gegen den echten Provider `provider_factory=get_provider` an
`create_app` uebergeben.
