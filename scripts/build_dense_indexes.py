from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.history import History, catalog_settings
from tariff_agent.model_transport import gateway_health


def main():
    settings = Settings()
    if gateway_health(settings).get("status") != "ready":
        raise RuntimeError("Restore the real Qwen embedding/rerank gateway before dense indexing")
    models = Models(settings)
    try:
        for config, kind in [(catalog_settings(settings), "tariff_rule"), (settings, "historical_case")]:
            if kind == "historical_case" and not settings.history_dense_enabled:
                continue
            index = History(config, kind=kind)
            try:
                index.attach_dense(models.embed)
                print(index.status())
            finally:
                index.close()
    finally:
        models.close()


if __name__ == "__main__":
    main()
