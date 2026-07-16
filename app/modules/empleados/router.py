import uuid

from fastapi import APIRouter, Depends, Query, status

from app.common.crud_router import build_crud_router
from app.core.context import RequestContext
from app.core.deps import DbSession, require_permission
from app.core.pagination import Page, PageParams, page_params
from app.modules.empleados.schemas import (
    EmpleadoCreate,
    EmpleadoRead,
    EmpleadoUpdate,
    PagoEmpleadoCreate,
    PagoEmpleadoRead,
)
from app.modules.empleados.service import EmpleadoService, PagoEmpleadoService

router = build_crud_router(
    modulo="empleados",
    service_cls=EmpleadoService,
    read_schema=EmpleadoRead,
    create_schema=EmpleadoCreate,
    update_schema=EmpleadoUpdate,
    tags=["Empleados"],
)


# --------------------------------------------------- nómina (pagos por jornal)
# Se monta en un prefijo propio (/nomina) para no chocar con /empleados/{id}.
pagos_router = APIRouter(tags=["Empleados"])


@pagos_router.get("", response_model=Page[PagoEmpleadoRead], summary="Listar pagos a empleados")
def listar_pagos(
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("empleados", "consultar")),
    params: PageParams = Depends(page_params),
    empleado_id: uuid.UUID | None = Query(None),
) -> Page[PagoEmpleadoRead]:
    items, total = PagoEmpleadoService(db, ctx).listar(
        params, filters={"empleado_id": empleado_id}
    )
    return Page.build(items, total, params)


@pagos_router.post(
    "", response_model=PagoEmpleadoRead, status_code=status.HTTP_201_CREATED,
    summary="Registrar pago a un empleado (días × valor por día)",
)
def crear_pago(
    payload: PagoEmpleadoCreate,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("empleados", "crear")),
) -> PagoEmpleadoRead:
    return PagoEmpleadoService(db, ctx).crear(payload)


@pagos_router.delete(
    "/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar un pago"
)
def eliminar_pago(
    entity_id: uuid.UUID,
    db: DbSession,
    ctx: RequestContext = Depends(require_permission("empleados", "eliminar")),
) -> None:
    PagoEmpleadoService(db, ctx).eliminar(entity_id)
