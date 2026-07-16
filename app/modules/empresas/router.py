import uuid

from fastapi import Depends, UploadFile

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import Context, DbSession, require_permission
from app.modules.empresas.schemas import (
    EmpresaCreate,
    EmpresaRead,
    EmpresaUpdate,
    ReinicioEmpresa,
)
from app.modules.empresas.service import EmpresaService

router = build_crud_router(
    modulo="empresas",
    service_cls=EmpresaService,
    read_schema=EmpresaRead,
    create_schema=EmpresaCreate,
    update_schema=EmpresaUpdate,
    tags=["Empresas"],
)


@router.post("/{entity_id}/logo", response_model=EmpresaRead, summary="Subir logo de la empresa")
def subir_logo(
    entity_id: uuid.UUID,
    file: UploadFile,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("empresas", "editar")),
) -> EmpresaRead:
    return EmpresaService(db, ctx).subir_logo(entity_id, file)


@router.post(
    "/{entity_id}/reiniciar",
    summary="Reiniciar (borrar) los datos transaccionales de una empresa — solo superadmin",
)
def reiniciar(
    entity_id: uuid.UUID,
    payload: ReinicioEmpresa,
    db: DbSession,
    ctx: Context,
) -> dict[str, int]:
    return EmpresaService(db, ctx).reiniciar(entity_id, payload.confirmacion)
