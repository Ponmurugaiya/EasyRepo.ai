import argparse
import json
import sys
from pathlib import Path

from src.extraction.entity_extractor import EntityExtractor


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Codebase Intelligence Platform CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract entities and relationships from a repository")
    extract_parser.add_argument("repo_path", type=str, help="Path to repository directory")
    extract_parser.add_argument("--output", "-o", type=str, default="entities.json", help="Output JSON file path")

    args = parser.parse_args()

    if args.command == "extract":
        extractor = EntityExtractor()
        entities, relationships = extractor.extract_repository(args.repo_path)

        entities_data = [e.model_dump(exclude={"source"}) for e in entities]
        relationships_data = [r.model_dump() for r in relationships]

        output_data = {
            "entities": entities_data,
            "relationships": relationships_data,
        }

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        print(f"Successfully extracted {len(entities)} entities and {len(relationships)} relationships to {output_path}")


if __name__ == "__main__":
    main()
