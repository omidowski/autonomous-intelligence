import json
import sys

from .models import ResearchRequest
from .orchestrator import ResearchOrchestrator


def main() -> None:
    query = " ".join(sys.argv[1:]).strip() or "How will agentic AI change enterprise software?"
    report = ResearchOrchestrator().run(ResearchRequest(query=query))
    print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
