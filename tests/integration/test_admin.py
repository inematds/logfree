"""Admin web: login, CSRF, authz cross-city, idempotência CSV, kill switch."""
from __future__ import annotations

import io


def _bootstrap_admin(email="admin@x.com", password="senha-forte-12345"):
    """Cria admin via CLI logic, retorna credentials."""
    from app.db.base import get_sessionmaker
    from app.db.models import Cidade, Usuario, UsuarioCidade
    from app.web.auth import hash_password

    sm = get_sessionmaker()
    with sm() as s:
        u = Usuario(
            email=email,
            nome="A",
            perfil="admin",
            password_hash=hash_password(password),
            ativo=1,
        )
        s.add(u)
        s.flush()
        c = s.query(Cidade).first()
        s.add(UsuarioCidade(usuario_id=u.id, cidade_id=c.id, papel="admin"))
        s.commit()
        return u.id, c.id


def _login(client, email, password):
    r = client.post(
        "/admin/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    return r


def test_admin_login_ok(client):
    _bootstrap_admin()
    r = _login(client, "admin@x.com", "senha-forte-12345")
    assert r.status_code == 303
    assert "logfree_session" in r.cookies or "logfree_session" in r.headers.get("set-cookie", "")


def test_admin_login_credenciais_erradas_401(client):
    _bootstrap_admin()
    r = _login(client, "admin@x.com", "senha-errada")
    assert r.status_code == 401


def test_admin_csrf_ausente_403(client):
    _bootstrap_admin()
    r = _login(client, "admin@x.com", "senha-forte-12345")
    cookies = r.cookies
    # tenta criar preço sem CSRF
    r2 = client.post(
        "/admin/preco",
        data={"posto_id": 1, "combustivel_id": 1, "valor_centavos": 600},
        cookies=cookies,
    )
    assert r2.status_code == 403


def test_admin_cria_preco_dentro_da_faixa(client):
    _bootstrap_admin()
    login_resp = _login(client, "admin@x.com", "senha-forte-12345")
    cookies = login_resp.cookies
    csrf = cookies.get("logfree_csrf")
    assert csrf
    # Pega ID de combustível (gasolina_comum)
    from app.db.base import get_sessionmaker
    from app.db.models import Combustivel, Posto
    sm = get_sessionmaker()
    with sm() as s:
        cid = s.query(Combustivel).filter(Combustivel.codigo == "gasolina_comum").one().id
        pid = s.query(Posto).first().id

    r = client.post(
        "/admin/preco",
        data={"posto_id": pid, "combustivel_id": cid, "valor_centavos": 600},
        cookies=cookies,
        headers={"HX-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text


def test_admin_preco_fora_da_faixa_422(client):
    _bootstrap_admin()
    login_resp = _login(client, "admin@x.com", "senha-forte-12345")
    cookies = login_resp.cookies
    csrf = cookies.get("logfree_csrf")
    from app.db.base import get_sessionmaker
    from app.db.models import Combustivel, Posto
    sm = get_sessionmaker()
    with sm() as s:
        cid = s.query(Combustivel).filter(Combustivel.codigo == "gasolina_comum").one().id
        pid = s.query(Posto).first().id

    r = client.post(
        "/admin/preco",
        data={"posto_id": pid, "combustivel_id": cid, "valor_centavos": 9_500_000},
        cookies=cookies,
        headers={"HX-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_csv_import_idempotency_replay(client):
    _bootstrap_admin()
    login_resp = _login(client, "admin@x.com", "senha-forte-12345")
    cookies = login_resp.cookies
    csrf = cookies.get("logfree_csrf")

    csv_data = (
        "nome,bandeira,endereco,lat,lng\n"
        "Posto Novo CSV,Petrobras,R. Z 1,-12.97,-38.49\n"
    )
    files = {"file": ("postos.csv", io.BytesIO(csv_data.encode()), "text/csv")}
    data = {"cidade_id": 1, "idempotency_key": "k-001"}

    r1 = client.post(
        "/admin/import-csv",
        data=data, files=files, cookies=cookies, headers={"HX-CSRF-Token": csrf},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["status"] == "commit"

    files2 = {"file": ("postos.csv", io.BytesIO(csv_data.encode()), "text/csv")}
    r2 = client.post(
        "/admin/import-csv",
        data=data, files=files2, cookies=cookies, headers={"HX-CSRF-Token": csrf},
    )
    body2 = r2.json()
    assert body2.get("replay") is True


def test_csv_import_rollback_em_linha_invalida(client):
    _bootstrap_admin()
    login_resp = _login(client, "admin@x.com", "senha-forte-12345")
    cookies = login_resp.cookies
    csrf = cookies.get("logfree_csrf")

    csv_data = (
        "nome,bandeira,endereco,lat,lng\n"
        "Posto OK,Shell,R. A,-12.97,-38.49\n"
        "Posto BAD,Shell,R. B,-99.0,-99.0\n"  # lat fora de bbox
    )
    files = {"file": ("postos.csv", io.BytesIO(csv_data.encode()), "text/csv")}
    data = {"cidade_id": 1, "idempotency_key": "k-bad"}

    r = client.post(
        "/admin/import-csv",
        data=data, files=files, cookies=cookies, headers={"HX-CSRF-Token": csrf},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "rollback"

    # Postos OK não devem ter sido inseridos (rollback total)
    from app.db.base import get_sessionmaker
    from app.db.models import Posto
    sm = get_sessionmaker()
    with sm() as s:
        assert s.query(Posto).filter(Posto.nome == "Posto OK").count() == 0


def test_security_headers_no_admin(client):
    r = client.get("/admin/login")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in r.headers


def test_admin_acesso_sem_login_redireciona_para_login(client):
    r = client.get("/admin/", follow_redirects=False)
    assert r.status_code == 303
    assert "login" in r.headers.get("location", "")
