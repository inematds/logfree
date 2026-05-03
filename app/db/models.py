from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow_iso

# Combustíveis aceitos no MVP. Replicado em CHECK + Pydantic.
COMBUSTIVEIS = (
    "gasolina_comum",
    "gasolina_aditivada",
    "etanol",
    "diesel_s10",
    "diesel_s500",
    "gnv",
)
PERFIS = ("motorista", "operador", "admin")
FONTES_PRECO = ("operador", "bot", "import_csv", "ocr")
MODOS = ("parcial", "completo")
PAPEIS_CIDADE = ("motorista", "operador", "admin")
IMPORT_STATUS = ("staging", "commit", "rollback", "erro")


def _utc_default():
    return utcnow_iso()


class Cidade(Base):
    __tablename__ = "cidade"
    __table_args__ = (
        CheckConstraint("uf GLOB '[A-Z][A-Z]'", name="ck_cidade_uf_format"),
        CheckConstraint(
            "bbox_min_lat BETWEEN -90 AND 90 AND bbox_max_lat BETWEEN -90 AND 90",
            name="ck_cidade_bbox_lat",
        ),
        CheckConstraint(
            "bbox_min_lng BETWEEN -180 AND 180 AND bbox_max_lng BETWEEN -180 AND 180",
            name="ck_cidade_bbox_lng",
        ),
        CheckConstraint(
            "preco_validade_horas_soft <= preco_validade_horas_hard",
            name="ck_cidade_validade_ordem",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    timezone: Mapped[str] = mapped_column(String(40), nullable=False, default="America/Sao_Paulo")
    bbox_min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_min_lng: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_max_lng: Mapped[float] = mapped_column(Float, nullable=False)
    preco_validade_horas_soft: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    preco_validade_horas_hard: Mapped[int] = mapped_column(Integer, default=72, nullable=False)
    stale_coverage_alert_pct: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    horizonte_default_km: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    fator_road_default: Mapped[float] = mapped_column(Float, default=1.35, nullable=False)
    is_demo: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Combustivel(Base):
    __tablename__ = "combustivel"
    __table_args__ = (
        CheckConstraint(
            "codigo IN ('gasolina_comum','gasolina_aditivada','etanol',"
            "'diesel_s10','diesel_s500','gnv')",
            name="ck_combustivel_codigo",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)


class Posto(Base):
    __tablename__ = "posto"
    __table_args__ = (
        CheckConstraint(
            "lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180", name="ck_posto_latlng_range"
        ),
        Index("ix_posto_cidade_ativo", "cidade_id", "ativo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidade_id: Mapped[int] = mapped_column(ForeignKey("cidade.id"), nullable=False)
    nome: Mapped[str] = mapped_column(String(160), nullable=False)
    bandeira: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    endereco: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    ativo: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)


class Preco(Base):
    __tablename__ = "preco"
    __table_args__ = (
        CheckConstraint(
            "valor_centavos > 0 AND valor_centavos < 5000000", name="ck_preco_valor_range"
        ),
        CheckConstraint(
            "fonte IN ('operador','bot','import_csv','ocr')", name="ck_preco_fonte"
        ),
        CheckConstraint("observed_at <= recorded_at", name="ck_preco_observed_le_recorded"),
        Index("ix_preco_posto_combustivel_obs", "posto_id", "combustivel_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    posto_id: Mapped[int] = mapped_column(ForeignKey("posto.id"), nullable=False)
    combustivel_id: Mapped[int] = mapped_column(ForeignKey("combustivel.id"), nullable=False)
    valor_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
    registrado_por_usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id"), nullable=True
    )
    fonte: Mapped[str] = mapped_column(String(16), nullable=False)
    import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batch.id"), nullable=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class FaixaPreco(Base):
    __tablename__ = "faixa_preco"
    __table_args__ = (
        UniqueConstraint("cidade_id", "combustivel_id", name="uq_faixa_preco"),
        CheckConstraint(
            "min_centavos > 0 AND max_centavos > min_centavos", name="ck_faixa_range"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidade_id: Mapped[int] = mapped_column(ForeignKey("cidade.id"), nullable=False)
    combustivel_id: Mapped[int] = mapped_column(ForeignKey("combustivel.id"), nullable=False)
    min_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    max_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    max_variacao_pct_24h: Mapped[int] = mapped_column(Integer, default=15, nullable=False)


class VeiculoTipo(Base):
    __tablename__ = "veiculo_tipo"
    __table_args__ = (
        CheckConstraint(
            "kml_default_p10 <= kml_default_p50 AND kml_default_p50 <= kml_default_p90",
            name="ck_kml_ordem",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    kml_default_p10: Mapped[float] = mapped_column(Float, nullable=False)
    kml_default_p50: Mapped[float] = mapped_column(Float, nullable=False)
    kml_default_p90: Mapped[float] = mapped_column(Float, nullable=False)
    raio_maximo_km: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)


class VeiculoPerfil(Base):
    __tablename__ = "veiculo_perfil"
    __table_args__ = (
        CheckConstraint("km_por_litro BETWEEN 0.5 AND 50", name="ck_perfil_kml_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo_id: Mapped[int] = mapped_column(ForeignKey("veiculo_tipo.id"), nullable=False)
    modelo: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    combustivel_id: Mapped[int] = mapped_column(ForeignKey("combustivel.id"), nullable=False)
    km_por_litro: Mapped[float] = mapped_column(Float, nullable=False)
    km_por_litro_p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    km_por_litro_p90: Mapped[float | None] = mapped_column(Float, nullable=True)


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (
        CheckConstraint("perfil IN ('motorista','operador','admin')", name="ck_usuario_perfil"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    nome: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    perfil: Mapped[str] = mapped_column(String(16), nullable=False)
    email: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ativo: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    opt_in_lgpd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opt_in_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tombstoned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tombstoned_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    bloqueado_ate: Mapped[str | None] = mapped_column(String(40), nullable=True)
    export_ultimo_em: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)


class UsuarioVeiculo(Base):
    __tablename__ = "usuario_veiculo"
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), primary_key=True)
    veiculo_perfil_id: Mapped[int] = mapped_column(
        ForeignKey("veiculo_perfil.id"), primary_key=True
    )
    ativo: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class UsuarioCidade(Base):
    __tablename__ = "usuario_cidade"
    __table_args__ = (
        CheckConstraint(
            "papel IN ('motorista','operador','admin')", name="ck_usuario_cidade_papel"
        ),
    )

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), primary_key=True)
    cidade_id: Mapped[int] = mapped_column(ForeignKey("cidade.id"), primary_key=True)
    papel: Mapped[str] = mapped_column(String(16), nullable=False)


class Consulta(Base):
    __tablename__ = "consulta"
    __table_args__ = (
        CheckConstraint("modo IN ('parcial','completo')", name="ck_consulta_modo"),
        CheckConstraint(
            "length(top_n_resultado_json) < 16384", name="ck_consulta_topn_size"
        ),
        CheckConstraint(
            "length(assumptions_json) < 4096", name="ck_consulta_assumptions_size"
        ),
        Index("ix_consulta_feita_em", "feita_em"),
        Index("ix_consulta_usuario", "usuario_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    combustivel_id: Mapped[int] = mapped_column(ForeignKey("combustivel.id"), nullable=False)
    autonomia_km: Mapped[float] = mapped_column(Float, nullable=False)
    modo: Mapped[str] = mapped_column(String(10), nullable=False)
    volume_alvo_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_n: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    feita_em: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
    top_n_resultado_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    assumptions_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    anonimizada: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lat_anon: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng_anon: Mapped[float | None] = mapped_column(Float, nullable=True)


class AbastecimentoRelatado(Base):
    __tablename__ = "abastecimento_relatado"
    __table_args__ = (
        CheckConstraint(
            "valor_centavos > 0 AND volume_l BETWEEN 0.5 AND 1000", name="ck_abast_range"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    consulta_id: Mapped[int | None] = mapped_column(ForeignKey("consulta.id"), nullable=True)
    posto_id: Mapped[int] = mapped_column(ForeignKey("posto.id"), nullable=False)
    combustivel_id: Mapped[int] = mapped_column(ForeignKey("combustivel.id"), nullable=False)
    valor_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    volume_l: Mapped[float] = mapped_column(Float, nullable=False)
    foto_storage_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_em: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = (
        CheckConstraint(
            "status IN ('staging','commit','rollback','erro')", name="ck_import_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidade_id: Mapped[int] = mapped_column(ForeignKey("cidade.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    criado_por_usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    criado_em: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="staging", nullable=False)
    total_linhas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    linhas_ok: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    linhas_erro: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    erros_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)


class TombstoneUsuario(Base):
    __tablename__ = "tombstone_usuario"

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), primary_key=True)
    criado_em: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
    motivo: Mapped[str] = mapped_column(String(40), default="user_request", nullable=False)


class OwnerLease(Base):
    __tablename__ = "owner_lease"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_uuid: Mapped[str] = mapped_column(String(40), nullable=False)
    host: Mapped[str] = mapped_column(String(120), nullable=False)
    started_at: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
    heartbeat_at: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)


class BotEstado(Base):
    __tablename__ = "bot_estado"

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), primary_key=True)
    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    valor_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expira_em: Mapped[str] = mapped_column(String(40), nullable=False)


class FeatureFlag(Base):
    __tablename__ = "feature_flag"
    __table_args__ = (
        UniqueConstraint("chave", "escopo", "alvo", name="uq_feature_flag_target"),
        CheckConstraint("escopo IN ('global','cidade','usuario')", name="ck_flag_escopo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave: Mapped[str] = mapped_column(String(60), nullable=False)
    escopo: Mapped[str] = mapped_column(String(10), nullable=False, default="global")
    alvo: Mapped[str] = mapped_column(String(60), default="*", nullable=False)
    valor_json: Mapped[str] = mapped_column(Text, default="false", nullable=False)
    ativo: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    atualizado_em: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
    atualizado_por: Mapped[str] = mapped_column(String(80), default="system", nullable=False)


class EventoAudit(Base):
    __tablename__ = "evento_audit"
    __table_args__ = (
        CheckConstraint("length(payload_json) < 8192", name="ck_audit_payload_size"),
        Index("ix_audit_criado", "criado_em"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)
    acao: Mapped[str] = mapped_column(String(40), nullable=False)
    recurso: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    recurso_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    criado_em: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
    hash_prev: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    hash_self: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_key"

    chave: Mapped[str] = mapped_column(String(80), primary_key=True)
    response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    criado_em: Mapped[str] = mapped_column(String(40), default=_utc_default, nullable=False)
