"""Almacenamiento de archivos adjuntos (logos, fotos, facturas de gastos)."""
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import BusinessError

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".xlsx", ".xls", ".csv"}


def save_upload(file: UploadFile, *, empresa_id: uuid.UUID | None, subdir: str) -> str:
    """Guarda el archivo bajo uploads/<empresa>/<subdir>/ y retorna la ruta relativa."""
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise BusinessError(f"Extensión no permitida: {extension or '(sin extensión)'}")

    content = file.file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise BusinessError(f"El archivo supera el máximo de {settings.MAX_UPLOAD_SIZE_MB} MB")

    tenant_dir = str(empresa_id) if empresa_id else "global"
    target_dir = Path(settings.UPLOADS_DIR) / tenant_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{extension}"
    (target_dir / filename).write_bytes(content)
    return f"{tenant_dir}/{subdir}/{filename}"
