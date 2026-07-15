import uuid
from datetime import date, datetime, time, timezone

from app.common.service import BaseService
from app.core.pagination import PageParams
from app.modules.auditoria.models import Auditoria
from app.modules.auditoria.repository import AuditoriaRepository


class AuditoriaService(BaseService[Auditoria]):
    repository_cls = AuditoriaRepository
    modulo = "auditoria"

    def listar_filtrado(
        self,
        params: PageParams,
        *,
        modulo: str | None = None,
        accion: str | None = None,
        usuario_id: uuid.UUID | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> tuple[list[Auditoria], int]:
        extra = []
        if desde:
            extra.append(Auditoria.created_at >= datetime.combine(desde, time.min, tzinfo=timezone.utc))
        if hasta:
            extra.append(Auditoria.created_at <= datetime.combine(hasta, time.max, tzinfo=timezone.utc))
        return self.repo.list_paginated(
            params,
            filters={"modulo": modulo, "accion": accion, "usuario_id": usuario_id},
            extra_criteria=extra,
        )
