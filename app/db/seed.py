"""Seed da cidade_demo (Salvador-BA-style fictícia, is_demo=true).

Idempotente — usar UPSERT por chave natural. Roda em CI e dev.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_sessionmaker
from app.db.models import (
    Cidade,
    Combustivel,
    FaixaPreco,
    Posto,
    Preco,
    Usuario,
    UsuarioCidade,
    UsuarioVeiculo,
    VeiculoPerfil,
    VeiculoTipo,
)

DEMO_CITY = {
    "nome": "Cidade Demo",
    "uf": "BA",
    "timezone": "America/Bahia",
    "bbox_min_lat": -13.05,
    "bbox_max_lat": -12.85,
    "bbox_min_lng": -38.60,
    "bbox_max_lng": -38.30,
    "preco_validade_horas_soft": 24,
    "preco_validade_horas_hard": 72,
    "stale_coverage_alert_pct": 30,
    "horizonte_default_km": 100.0,
    "fator_road_default": 1.35,
    "is_demo": 1,
}

VEICULO_TIPOS = [
    {"nome": "moto", "kml_default_p10": 22.0, "kml_default_p50": 30.0,
     "kml_default_p90": 40.0, "raio_maximo_km": 8.0},
    {"nome": "carro", "kml_default_p10": 8.0, "kml_default_p50": 11.0,
     "kml_default_p90": 14.0, "raio_maximo_km": 12.0},
    {"nome": "caminhao_leve", "kml_default_p10": 5.0, "kml_default_p50": 7.0,
     "kml_default_p90": 9.5, "raio_maximo_km": 15.0},
]

POSTOS = [
    ("Posto Central", "Petrobras", "Av. Sete, 100", -12.97, -38.50),
    ("Posto Praia", "Shell", "Av. Oceânica, 200", -12.99, -38.51),
    ("Posto Pelourinho", "Ipiranga", "Pç. Sé, 50", -12.97, -38.51),
    ("Posto Barra", "Shell", "Av. Sete, 800", -13.00, -38.53),
    ("Posto Pituba", "Petrobras", "Av. ACM, 1000", -12.99, -38.46),
    ("Posto Brotas", "Ale", "Av. Vasco, 500", -12.96, -38.47),
    ("Posto Iguatemi", "Ipiranga", "Av. Tancredo, 200", -12.98, -38.45),
    ("Posto Itaigara", "Shell", "R. Itaigara, 100", -12.99, -38.45),
    ("Posto Cabula", "Ale", "Av. ACM, 1500", -12.95, -38.43),
    ("Posto Liberdade", "Petrobras", "R. Lima, 300", -12.94, -38.49),
    ("Posto Bonfim", "Ipiranga", "Av. Bonfim, 50", -12.92, -38.50),
    ("Posto Calçada", "Shell", "Av. Joana, 100", -12.93, -38.50),
    ("Posto Mares", "Petrobras", "BR-324, 5", -12.91, -38.48),
    ("Posto Pirajá", "Ale", "BR-324, 12", -12.90, -38.46),
    ("Posto Lobato", "Ipiranga", "Av. Suburbana, 800", -12.89, -38.49),
]

# Preço em centavos por combustivel para o seed.
PRECOS_BASE_CENTS = {
    "gasolina_comum": 590,
    "gasolina_aditivada": 615,
    "etanol": 410,
    "diesel_s10": 595,
    "diesel_s500": 580,
    "gnv": 350,
}

FAIXAS_CENTS = {
    "gasolina_comum": (450, 900),
    "gasolina_aditivada": (470, 950),
    "etanol": (290, 700),
    "diesel_s10": (420, 900),
    "diesel_s500": (410, 880),
    "gnv": (200, 600),
}


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _upsert_cidade(s: Session) -> Cidade:
    c = s.execute(
        select(Cidade).where(Cidade.nome == DEMO_CITY["nome"], Cidade.is_demo == 1)
    ).scalar_one_or_none()
    if c is None:
        c = Cidade(**DEMO_CITY)
        s.add(c)
        s.flush()
    return c


def _upsert_tipos(s: Session) -> dict[str, VeiculoTipo]:
    out = {}
    for spec in VEICULO_TIPOS:
        t = s.execute(select(VeiculoTipo).where(VeiculoTipo.nome == spec["nome"])).scalar_one_or_none()
        if t is None:
            t = VeiculoTipo(**spec)
            s.add(t)
            s.flush()
        out[spec["nome"]] = t
    return out


def _combustiveis(s: Session) -> dict[str, Combustivel]:
    return {c.codigo: c for c in s.execute(select(Combustivel)).scalars()}


def _upsert_perfil_default(s: Session, tipos: dict[str, VeiculoTipo],
                            combs: dict[str, Combustivel]) -> dict[str, VeiculoPerfil]:
    """Perfil default por tipo, com bandas iguais às do tipo."""
    out = {}
    for tipo_nome, comb_codigo in [
        ("moto", "gasolina_comum"),
        ("carro", "gasolina_comum"),
        ("caminhao_leve", "diesel_s10"),
    ]:
        t = tipos[tipo_nome]
        c = combs[comb_codigo]
        existing = s.execute(
            select(VeiculoPerfil).where(
                VeiculoPerfil.tipo_id == t.id,
                VeiculoPerfil.combustivel_id == c.id,
                VeiculoPerfil.modelo == "default",
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = VeiculoPerfil(
                tipo_id=t.id,
                modelo="default",
                combustivel_id=c.id,
                km_por_litro=t.kml_default_p50,
                km_por_litro_p10=t.kml_default_p10,
                km_por_litro_p90=t.kml_default_p90,
            )
            s.add(existing)
            s.flush()
        out[tipo_nome] = existing
    return out


def _upsert_postos(s: Session, cidade: Cidade) -> list[Posto]:
    out = []
    for nome, bandeira, endereco, lat, lng in POSTOS:
        p = s.execute(
            select(Posto).where(Posto.cidade_id == cidade.id, Posto.nome == nome)
        ).scalar_one_or_none()
        if p is None:
            p = Posto(
                cidade_id=cidade.id,
                nome=nome,
                bandeira=bandeira,
                endereco=endereco,
                lat=lat,
                lng=lng,
                ativo=1,
            )
            s.add(p)
            s.flush()
        out.append(p)
    return out


def _upsert_faixas(s: Session, cidade: Cidade, combs: dict[str, Combustivel]) -> None:
    for codigo, (lo, hi) in FAIXAS_CENTS.items():
        c = combs.get(codigo)
        if c is None:
            continue
        existing = s.execute(
            select(FaixaPreco).where(
                FaixaPreco.cidade_id == cidade.id, FaixaPreco.combustivel_id == c.id
            )
        ).scalar_one_or_none()
        if existing is None:
            s.add(
                FaixaPreco(
                    cidade_id=cidade.id,
                    combustivel_id=c.id,
                    min_centavos=lo,
                    max_centavos=hi,
                    max_variacao_pct_24h=15,
                )
            )


def _upsert_admin_seed(s: Session, cidade: Cidade,
                        perfis: dict[str, VeiculoPerfil]) -> None:
    """Cria usuário admin do seed (sem senha real — admin de bootstrap vem do CLI).

    Também cria um motorista de demo para CI.
    """
    admin_email = "demo-admin@logfree.local"
    admin = s.execute(select(Usuario).where(Usuario.email == admin_email)).scalar_one_or_none()
    if admin is None:
        admin = Usuario(
            email=admin_email,
            nome="Demo Admin",
            perfil="admin",
            password_hash="seed-no-login",
            ativo=1,
        )
        s.add(admin)
        s.flush()
    if not s.execute(
        select(UsuarioCidade).where(
            UsuarioCidade.usuario_id == admin.id, UsuarioCidade.cidade_id == cidade.id
        )
    ).scalar_one_or_none():
        s.add(UsuarioCidade(usuario_id=admin.id, cidade_id=cidade.id, papel="admin"))

    motorista_tg = 100001
    mot = s.execute(
        select(Usuario).where(Usuario.telegram_id == motorista_tg)
    ).scalar_one_or_none()
    if mot is None:
        mot = Usuario(
            telegram_id=motorista_tg,
            nome="Motorista Demo",
            perfil="motorista",
            ativo=1,
            opt_in_lgpd=1,
            opt_in_at=_utc(datetime.now(tz=UTC)),
        )
        s.add(mot)
        s.flush()
        s.add(
            UsuarioVeiculo(usuario_id=mot.id, veiculo_perfil_id=perfis["carro"].id, ativo=1)
        )


def _upsert_precos(s: Session, postos: list[Posto], combs: dict[str, Combustivel]) -> None:
    """Para cada posto seed, gera preço atual + 1 histórico antigo (se ainda não houver)."""
    now = datetime.now(tz=UTC)
    for idx, posto in enumerate(postos):
        for codigo, base in PRECOS_BASE_CENTS.items():
            c = combs.get(codigo)
            if c is None:
                continue
            # spread por posto: pequena variação determinística para o seed
            valor = base + ((idx * 7) % 30) - 15
            existing = s.execute(
                select(Preco).where(
                    Preco.posto_id == posto.id,
                    Preco.combustivel_id == c.id,
                    Preco.fonte == "import_csv",
                )
            ).first()
            if existing is None:
                s.add(
                    Preco(
                        posto_id=posto.id,
                        combustivel_id=c.id,
                        valor_centavos=max(1, valor),
                        observed_at=_utc(now - timedelta(hours=2)),
                        recorded_at=_utc(now - timedelta(hours=2)),
                        fonte="import_csv",
                    )
                )


def run_seed() -> None:
    sm = get_sessionmaker()
    with sm() as s:
        cidade = _upsert_cidade(s)
        tipos = _upsert_tipos(s)
        combs = _combustiveis(s)
        if not combs:
            raise RuntimeError(
                "tabela combustivel vazia — rode `alembic upgrade head` antes do seed"
            )
        perfis = _upsert_perfil_default(s, tipos, combs)
        postos = _upsert_postos(s, cidade)
        _upsert_faixas(s, cidade, combs)
        _upsert_admin_seed(s, cidade, perfis)
        _upsert_precos(s, postos, combs)
        s.commit()
        print(f"seed ok: cidade_id={cidade.id} postos={len(postos)} tipos={len(tipos)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    run_seed()


if __name__ == "__main__":
    main()
