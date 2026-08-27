# Projektarbeit: Autonomous Intelligence

## 1. Projekttitel

**Autonomous Intelligence – Entwicklung eines Multi-Agenten-Systems für autonome Recherche und Entscheidungsunterstützung mit Generative AI, Agentic AI und RAG**

## 2. Forschungsfrage

**Wie kann ein Multi-Agenten-System auf Basis generativer KI komplexe Rechercheaufgaben autonom bearbeiten und gleichzeitig Nachvollziehbarkeit, Evidenzqualität und kontrollierbare Autonomie sicherstellen?**

## 3. Zielsetzung

Ziel ist die Entwicklung eines AI-Engineering-Prototyps, der eine natürlichsprachliche Forschungsfrage automatisch zerlegt, externe Informationen recherchiert, Evidenz semantisch speichert, Aussagen überprüft, Ergebnisse analysiert und einen strukturierten Management-Report generiert.

Das Projekt soll nicht nur die Ausgabe eines LLM demonstrieren, sondern einen vollständigen Engineering-Lifecycle: Orchestrierung, Tool-Nutzung, RAG, strukturierte Datenmodelle, Evaluation, API-Bereitstellung, Tests, Containerisierung und CI.

## 4. Systemarchitektur

```text
Nutzer
  |
  v
FastAPI
  |
  v
Research Orchestrator
  |
  +--> Planner Agent
  |
  +--> Research Agent --> Web Search
  |          |
  |          +--> Embeddings --> Vector Store (RAG)
  |
  +--> Fact-Checking Agent
  |
  +--> Analysis Agent
  |
  +--> Report Agent
  |
  +--> Evaluator
  |
  v
Research Report + Qualitätsmetriken
```

## 5. Agentische Komponenten

Der Planner Agent zerlegt das Ziel in Teilfragen und Suchanfragen. Der Research Agent nutzt Tools zur Evidenzgewinnung und speichert Inhalte als Embeddings. Der Fact-Checking Agent bewertet zentrale Claims anhand der vorhandenen Evidenz. Der Analysis Agent synthetisiert Chancen, Risiken und offene Fragen. Der Report Agent erzeugt eine managementtaugliche Zusammenfassung. Der Orchestrator steuert die Reihenfolge, Abbruchbedingungen und den Datenaustausch zwischen diesen Komponenten.

## 6. Generative-AI-Komponenten

Generative KI wird für Aufgabenplanung, Query-Generierung, Informationssynthese, Claim-Analyse und Report-Generierung eingesetzt. Alle Agenten kommunizieren über explizite Pydantic-Datenmodelle, sodass unstrukturierter LLM-Text möglichst früh in validierte Daten überführt wird.

## 7. RAG

Für Retrieval-Augmented Generation werden Evidenztexte eingebettet und in einem Vector-Store-Adapter gespeichert. Die MVP-Version enthält einen transparenten In-Memory-Store mit Cosine Similarity. Für die Produktionsversion ist PostgreSQL mit pgvector vorgesehen.

## 8. Evaluation

Der Prototyp berechnet zur Laufzeit unter anderem Evidenzanzahl, durchschnittliche Evidenz-Konfidenz, Claim-Support-Rate, Widerspruchsrate und Gesamtlatenz. Für eine wissenschaftlich stärkere Evaluation können ein gelabeltes Benchmark-Dataset, Retrieval Precision@K, Recall@K, Groundedness, Citation Correctness, Kosten und Task-Success-Rate ergänzt werden.

## 9. Sicherheitskonzept

Externe Inhalte werden als nicht vertrauenswürdig behandelt. Produktionssysteme sollten Tool-Berechtigungen explizit allow-listen, Prompt-Injection-Abwehr implementieren, sensible Aktionen über Human-in-the-Loop freigeben und Agentenaufrufe vollständig protokollieren.

## 10. Abgrenzung

Das MVP ist kein vollständig autonomes Produktivsystem. Persistente Sessions, echtes pgvector, PDF-Ingestion, Source-Normalisierung, Authentifizierung, Kostenbudgetierung, Tracing und umfassende Benchmarks sind bewusst als nächste Ausbaustufe definiert.

## 11. Erwarteter Nutzen

Das Projekt demonstriert Fähigkeiten in Python, LLM-Anwendungen, Generative AI, Agentic AI, RAG, Embeddings, API Engineering, Datenmodellierung, Evaluation, Testing, Docker und CI/CD und eignet sich damit als technische Projektarbeit sowie als AI-Engineering-Portfolio-Projekt.
