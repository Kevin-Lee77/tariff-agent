import ssl

import httpx


def ssl_context(settings):
    context = ssl.create_default_context()
    if settings.model_gateway_ca:
        context.load_verify_locations(cafile=str(settings.model_gateway_ca))
    return context


def gateway_headers(settings):
    key = settings.model_gateway_key.get_secret_value()
    return {"Authorization": "Bearer " + key} if key else {}


def gateway_health(settings):
    with httpx.Client(verify=ssl_context(settings), timeout=5) as client:
        response = client.get(
            settings.embedding_url.removesuffix("/v1") + "/health", headers=gateway_headers(settings)
        )
        response.raise_for_status()
        return response.json()
