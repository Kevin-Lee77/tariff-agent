import json

from tariff_agent.config import Settings
from tariff_agent.history import History, catalog_settings
from tariff_agent.knowledge import Knowledge
from tariff_agent.retrieval import catalog_document


def main():
    settings = Settings()
    knowledge = Knowledge(settings.corpus, settings.allow_test_corpus)
    folder = settings.history_directory / "rules"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / ("catalog-enriched-v2-" + knowledge.fingerprint[:16] + ".jsonl")
    if not source.exists():
        source.write_text(
            "".join(
                json.dumps(
                    {
                        "description": catalog_document(knowledge, e.code),
                        "gold_code": e.code,
                        "source": e.source_id,
                        "split": "history",
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for e in knowledge.headings
            ),
            encoding="utf-8",
        )
    index = History(catalog_settings(settings), kind="tariff_rule")
    try:
        print(json.dumps(index.build(source, [], valid_codes=knowledge.formal_codes), ensure_ascii=False))
    finally:
        index.close()


if __name__ == "__main__":
    main()
