"""Admin web (HTMX + Jinja). MVP: login, lista de postos, edita preço, import CSV.

Headers de segurança aplicados via middleware no main.py.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.audit import write_event
from app.core.flags import set_flag
from app.core.telemetry import emit, hash_id, metrics_snapshot
from app.db.base import utcnow_iso
from app.db.models import (
    Cidade,
    Combustivel,
    FaixaPreco,
    ImportBatch,
    Posto,
    Preco,
    Usuario,
    UsuarioCidade,
)
from app.web.auth import check_csrf, issue_csrf, session_user
from app.web.auth import login as do_login

router = APIRouter(prefix="/admin", tags=["admin"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _require_admin(request: Request, db: Session = Depends(get_db)) -> Usuario:
    u = session_user(db, request)
    if u is None:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return u


def _set_session_cookies(response, cookie_value: str, csrf: str):
    response.set_cookie(
        "logfree_session",
        cookie_value,
        httponly=True,
        secure=False,  # True em prod (HTTPS)
        samesite="lax",
        max_age=12 * 3600,
        path="/admin",
    )
    response.set_cookie(
        "logfree_csrf", csrf, httponly=False, secure=False,
        samesite="lax", max_age=12 * 3600, path="/admin"
    )


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "0.0.0.0"
    try:
        cookie = do_login(db, email, password, ip)
    except HTTPException as exc:
        db.commit()
        return templates.TemplateResponse(
            request, "login.html", {"error": exc.detail}, status_code=401
        )
    db.commit()
    csrf = issue_csrf(0)
    response = RedirectResponse(url="/admin/", status_code=303)
    _set_session_cookies(response, cookie, csrf)
    return response


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    u = _require_admin(request, db)
    cidades = list(db.execute(select(Cidade)).scalars())
    metrics = metrics_snapshot()
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": u,
            "cidades": cidades,
            "metrics": metrics,
        },
    )


@router.get("/postos/{cidade_id}", response_class=HTMLResponse)
async def listar_postos(cidade_id: int, request: Request, db: Session = Depends(get_db)):
    u = _require_admin(request, db)
    cidade = db.get(Cidade, cidade_id)
    if cidade is None:
        raise HTTPException(404)
    postos = db.execute(select(Posto).where(Posto.cidade_id == cidade_id)).scalars().all()
    combustiveis = db.execute(select(Combustivel)).scalars().all()
    return templates.TemplateResponse(
        request,
        "postos.html",
        {
            "user": u,
            "cidade": cidade,
            "postos": postos,
            "combustiveis": combustiveis,
            "csrf": request.cookies.get("logfree_csrf", ""),
        },
    )


@router.post("/preco")
async def criar_preco(
    request: Request,
    posto_id: int = Form(...),
    combustivel_id: int = Form(...),
    valor_centavos: int = Form(...),
    db: Session = Depends(get_db),
):
    u = _require_admin(request, db)
    csrf = request.cookies.get("logfree_csrf", "")
    if not check_csrf(request, csrf):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF inválido")

    posto = db.get(Posto, posto_id)
    if posto is None:
        raise HTTPException(404)
    # Authz: operador só pode editar postos da sua cidade
    if u.perfil != "admin":
        autorizado = db.execute(
            select(UsuarioCidade).where(
                UsuarioCidade.usuario_id == u.id,
                UsuarioCidade.cidade_id == posto.cidade_id,
                UsuarioCidade.papel.in_(["operador", "admin"]),
            )
        ).first()
        if autorizado is None:
            emit("authz.deny", usuario_id_hash=hash_id(u.id),
                 recurso="preco", acao="create", cidade_alvo=posto.cidade_id)
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="cross-city")

    # Validar faixa
    faixa = db.execute(
        select(FaixaPreco).where(
            FaixaPreco.cidade_id == posto.cidade_id,
            FaixaPreco.combustivel_id == combustivel_id,
        )
    ).scalar_one_or_none()
    if faixa is not None and not (faixa.min_centavos <= valor_centavos <= faixa.max_centavos):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"valor fora da faixa ({faixa.min_centavos}-{faixa.max_centavos})",
        )

    now = utcnow_iso()
    p = Preco(
        posto_id=posto_id,
        combustivel_id=combustivel_id,
        valor_centavos=valor_centavos,
        observed_at=now,
        recorded_at=now,
        registrado_por_usuario_id=u.id,
        fonte="operador",
    )
    db.add(p)
    db.flush()
    write_event(
        db,
        usuario_id=u.id,
        acao="preco.create",
        recurso="preco",
        recurso_id=p.id,
        payload={"posto": posto_id, "combustivel": combustivel_id, "valor": valor_centavos},
    )
    db.commit()
    return {"ok": True, "preco_id": p.id}


@router.post("/import-csv")
async def import_csv(
    request: Request,
    file: UploadFile,
    cidade_id: int = Form(...),
    idempotency_key: str = Form(...),
    db: Session = Depends(get_db),
):
    u = _require_admin(request, db)
    csrf = request.cookies.get("logfree_csrf", "")
    if not check_csrf(request, csrf):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF inválido")

    if u.perfil not in ("admin", "operador"):
        raise HTTPException(status.HTTP_403_FORBIDDEN)

    # Idempotency: já importou com essa key?
    existing = db.execute(
        select(ImportBatch).where(ImportBatch.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "import_batch_id": existing.id, "replay": True,
                "status": existing.status, "linhas_ok": existing.linhas_ok}

    raw = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    linhas = list(reader)

    batch = ImportBatch(
        cidade_id=cidade_id,
        idempotency_key=idempotency_key,
        criado_por_usuario_id=u.id,
        status="staging",
        total_linhas=len(linhas),
    )
    db.add(batch)
    db.flush()

    erros: list[dict] = []
    novos_postos = 0

    cidade = db.get(Cidade, cidade_id)
    if cidade is None:
        raise HTTPException(404, detail="cidade não encontrada")

    try:
        for i, row in enumerate(linhas, start=1):
            try:
                lat = float(row["lat"])
                lng = float(row["lng"])
                if not (cidade.bbox_min_lat <= lat <= cidade.bbox_max_lat):
                    raise ValueError("lat fora da bbox")
                if not (cidade.bbox_min_lng <= lng <= cidade.bbox_max_lng):
                    raise ValueError("lng fora da bbox")
                # chave natural: cidade + nome (versão simples; lat/lng arredondado fica para fase 2)
                nome = (row.get("nome") or "").strip()
                if not nome:
                    raise ValueError("nome vazio")
                existing_p = db.execute(
                    select(Posto).where(
                        Posto.cidade_id == cidade_id,
                        Posto.nome == nome,
                    )
                ).scalar_one_or_none()
                if existing_p is None:
                    db.add(
                        Posto(
                            cidade_id=cidade_id,
                            nome=nome[:160],
                            bandeira=(row.get("bandeira") or "")[:60],
                            endereco=(row.get("endereco") or "")[:240],
                            lat=lat,
                            lng=lng,
                            ativo=1,
                        )
                    )
                    novos_postos += 1
                batch.linhas_ok += 1
            except Exception as exc:
                erros.append({"linha": i, "erro": str(exc)})
                batch.linhas_erro += 1
        if batch.linhas_erro > 0:
            db.rollback()
            # registrar batch como rollback (transação separada)
            db.add(
                ImportBatch(
                    cidade_id=cidade_id,
                    idempotency_key=idempotency_key,
                    criado_por_usuario_id=u.id,
                    status="rollback",
                    total_linhas=len(linhas),
                    linhas_erro=batch.linhas_erro,
                    erros_json=json.dumps(erros)[:8000],
                )
            )
            db.commit()
            return {"ok": False, "status": "rollback", "erros": erros}
        batch.status = "commit"
        write_event(
            db,
            usuario_id=u.id,
            acao="csv.import.commit",
            recurso="import_batch",
            recurso_id=batch.id,
            payload={"cidade": cidade_id, "linhas": len(linhas), "novos": novos_postos},
        )
        db.commit()
        return {
            "ok": True,
            "status": "commit",
            "import_batch_id": batch.id,
            "novos_postos": novos_postos,
            "linhas": len(linhas),
        }
    except Exception:
        db.rollback()
        raise


@router.post("/flag")
async def admin_set_flag(
    request: Request,
    chave: str = Form(...),
    valor: str = Form(...),  # "true"/"false"/JSON
    escopo: str = Form("global"),
    alvo: str = Form("*"),
    db: Session = Depends(get_db),
):
    u = _require_admin(request, db)
    csrf = request.cookies.get("logfree_csrf", "")
    if not check_csrf(request, csrf):
        raise HTTPException(403, detail="CSRF inválido")
    if u.perfil != "admin":
        raise HTTPException(403, detail="apenas admin")
    try:
        decoded = json.loads(valor)
    except json.JSONDecodeError:
        decoded = valor
    set_flag(db, chave, decoded, escopo=escopo, alvo=alvo, atualizado_por=u.email or "admin")
    db.commit()
    return {"ok": True}
