"""Bot webhook: secret token validation, handlers básicos."""
from __future__ import annotations


def test_webhook_503_se_secret_nao_configurado(client):
    # Default: TG_WEBHOOK_SECRET vazio → 503
    r = client.post("/bot/webhook/anything", json={"message": {"text": "/start"}})
    assert r.status_code == 503


def test_webhook_path_secret_invalido_401(client, monkeypatch):
    monkeypatch.setenv("TG_WEBHOOK_SECRET", "real-secret")
    from app.core.settings import get_settings
    get_settings.cache_clear()
    r = client.post(
        "/bot/webhook/wrong",
        json={"message": {"text": "/start"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert r.status_code == 401


def test_webhook_header_secret_invalido_401(client, monkeypatch):
    monkeypatch.setenv("TG_WEBHOOK_SECRET", "real-secret")
    from app.core.settings import get_settings
    get_settings.cache_clear()
    r = client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/start"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 401


def test_webhook_start_e_aceitar_termos(client, monkeypatch):
    monkeypatch.setenv("TG_WEBHOOK_SECRET", "real-secret")
    from app.core.settings import get_settings
    get_settings.cache_clear()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "real-secret"}
    r = client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/start", "chat": {"id": 555, "first_name": "Foo"}}},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "consentimento" in body["reply"].lower() or "lgpd" in body["reply"].lower()

    r = client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/aceitar_termos", "chat": {"id": 555}}},
        headers=headers,
    )
    assert r.status_code == 200
    assert "obrigado" in r.json()["reply"].lower()


def test_webhook_apagar_meus_dados_anonimiza(client, monkeypatch):
    monkeypatch.setenv("TG_WEBHOOK_SECRET", "real-secret")
    from app.core.settings import get_settings
    get_settings.cache_clear()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "real-secret"}
    # cria usuário
    client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/start", "chat": {"id": 777, "first_name": "Bar"}}},
        headers=headers,
    )
    client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/aceitar_termos", "chat": {"id": 777}}},
        headers=headers,
    )
    # apaga
    r = client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/apagar_meus_dados", "chat": {"id": 777}}},
        headers=headers,
    )
    assert r.status_code == 200

    # Verifica via DB que está tombstoned
    from app.db.base import get_sessionmaker
    from app.db.models import TombstoneUsuario, Usuario
    sm = get_sessionmaker()
    with sm() as s:
        u = s.query(Usuario).filter(Usuario.telegram_id == 777).one_or_none()
        # após apagar, telegram_id é zerado (=None) — busca por id da consulta de audit
        # ou simplesmente checa que não existe usuário ativo com tg_id 777
        assert u is None
        # tombstone existe
        assert s.query(TombstoneUsuario).count() >= 1


def test_webhook_postos_lista(client, monkeypatch):
    monkeypatch.setenv("TG_WEBHOOK_SECRET", "real-secret")
    from app.core.settings import get_settings
    get_settings.cache_clear()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "real-secret"}
    client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/start", "chat": {"id": 100002, "first_name": "Bar"}}},
        headers=headers,
    )
    r = client.post(
        "/bot/webhook/real-secret",
        json={"message": {"text": "/postos", "chat": {"id": 100002}}},
        headers=headers,
    )
    assert r.status_code == 200
    assert "postos" in r.json()["reply"].lower()
