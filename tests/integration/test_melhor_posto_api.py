"""Testes de integração para POST /melhor-posto."""
from __future__ import annotations

VALID_PAYLOAD = {
    "lat": -12.97,
    "lng": -38.50,
    "combustivel": "gasolina_comum",
    "autonomia_restante_km": 200,
    "modo_abastecimento": "completo",
    "top_n": 3,
}


def test_melhor_posto_sem_auth_401(client):
    r = client.post("/melhor-posto", json=VALID_PAYLOAD)
    assert r.status_code == 401


def test_melhor_posto_bot_token_invalido_401(client):
    r = client.post(
        "/melhor-posto",
        json=VALID_PAYLOAD,
        headers={"X-Bot-Token": "wrong", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 401


def test_melhor_posto_bot_token_sem_telegram_id_401(client):
    r = client.post(
        "/melhor-posto",
        json=VALID_PAYLOAD,
        headers={"X-Bot-Token": "test-bot-token"},
    )
    assert r.status_code == 401


def test_melhor_posto_telegram_id_inexistente_401(client):
    r = client.post(
        "/melhor-posto",
        json=VALID_PAYLOAD,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "999"},
    )
    assert r.status_code == 401


def test_melhor_posto_sucesso_motorista_demo(client):
    r = client.post(
        "/melhor-posto",
        json=VALID_PAYLOAD,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "top" in body and "assumptions" in body
    assert isinstance(body["top"], list)
    if body["top"]:
        first = body["top"][0]
        assert "posto_id" in first and "custo_p50" in first
    # assumptions sempre tem schema_version e modo
    assert "modo" in body["assumptions"]


def test_melhor_posto_combustivel_invalido_422(client):
    payload = {**VALID_PAYLOAD, "combustivel": "agua"}
    r = client.post(
        "/melhor-posto",
        json=payload,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 422


def test_melhor_posto_modo_parcial_sem_volume_422(client):
    payload = {**VALID_PAYLOAD, "modo_abastecimento": "parcial"}
    r = client.post(
        "/melhor-posto",
        json=payload,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 422


def test_melhor_posto_lat_fora_de_range_422(client):
    payload = {**VALID_PAYLOAD, "lat": 200.0}
    r = client.post(
        "/melhor-posto",
        json=payload,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 422


def test_melhor_posto_top_n_acima_de_10_422(client):
    payload = {**VALID_PAYLOAD, "top_n": 50}
    r = client.post(
        "/melhor-posto",
        json=payload,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 422


def test_melhor_posto_localizacao_fora_de_cidade_422(client):
    payload = {**VALID_PAYLOAD, "lat": -1.0, "lng": -50.0}
    r = client.post(
        "/melhor-posto",
        json=payload,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 422


def test_kill_switch_global_503(client):
    """Liga kill switch via DB direto e verifica 503."""
    from app.core.flags import set_flag
    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    with sm() as s:
        set_flag(s, "kill_switch.global", True)
        s.commit()

    r = client.post(
        "/melhor-posto",
        json=VALID_PAYLOAD,
        headers={"X-Bot-Token": "test-bot-token", "X-Bot-User-Telegram-Id": "100001"},
    )
    assert r.status_code == 503


def test_internal_ready_responde(client):
    r = client.get("/internal/ready")
    assert r.status_code == 200


def test_metrics_endpoint(client):
    r = client.get("/internal/metrics")
    assert r.status_code == 200
    assert "counters" in r.json()
