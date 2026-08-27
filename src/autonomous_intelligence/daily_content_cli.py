import argparse
import json
import sys

from .daily_content_orchestrator import DailyContentOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the daily content pipeline once.")
    parser.add_argument(
        "--company", default="default", help="Company slug to scope output under (default: 'default')."
    )
    args = parser.parse_args()

    report = DailyContentOrchestrator().run(company_slug=args.company)
    print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
    print(f"\nWrote daily content report to {report.output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
