# agent/cuentas.py — Cuentas de clientes y alertas en Supabase

"""
Lee el avance de cada cuenta desde la tabla "cuentas" de Supabase, y deja registro de
cada aviso en la tabla "alertas".

Se habla con la API REST de Supabase (PostgREST) usando la service role key. Esa key
salta el Row Level Security, asi que vive SOLO en el servidor: nunca en un frontend.
Las tablas tienen RLS activado y sin politicas, para que la anon key no pueda leerlas.
"""

import logging
import os
import re

import httpx

logger = logging.getLogger("agentkit")

# Campos que el asistente puede contarle al cliente. "notas_internas" NO esta aca a
# proposito: es para Manger y nunca debe llegar al modelo.
CAMPOS_VISIBLES = (
    "empresa,contacto_nombre,email,plan,estado,avance,proximo_paso,"
    "fecha_proximo_paso,actualizado_en"
)


def normalizar_telefono(telefono: str) -> str:
    """Deja solo los digitos: '+51 987 654 321' -> '51987654321'."""
    return re.sub(r"\D", "", telefono or "")


def _config() -> tuple[str, str] | None:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not key:
        return None
    return url, key


def _headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


async def buscar_cuenta(telefono: str) -> dict | None:
    """
    Retorna la cuenta asociada a ese numero de WhatsApp, o None si no esta registrado.

    La busqueda es SIEMPRE por el numero del remitente que entrega el webhook, nunca
    por algo que diga el cliente: asi nadie puede consultar la cuenta de otro.
    """
    config = _config()
    telefono = normalizar_telefono(telefono)
    if config is None or not telefono:
        if config is None:
            logger.warning("Supabase no esta configurado: no se pueden consultar cuentas")
        return None

    url, key = config
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            r = await cliente.get(
                f"{url}/rest/v1/cuentas",
                params={"telefono": f"eq.{telefono}", "select": CAMPOS_VISIBLES, "limit": "1"},
                headers=_headers(key),
            )
    except httpx.HTTPError as e:
        logger.error(f"No se pudo consultar Supabase: {e}")
        return None

    if r.status_code != 200:
        logger.error(f"Supabase respondio {r.status_code} al buscar la cuenta: {r.text[:300]}")
        return None

    filas = r.json()
    return filas[0] if filas else None


async def registrar_alerta(
    telefono: str, categoria: str, urgencia: str, resumen: str, empresa: str | None
) -> int | None:
    """
    Guarda el aviso en la tabla "alertas", para que nada se pierda aunque falle WhatsApp.
    Retorna el id de la alerta, o None si no se pudo guardar.
    """
    config = _config()
    if config is None:
        return None

    url, key = config
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            r = await cliente.post(
                f"{url}/rest/v1/alertas",
                json={
                    "telefono": normalizar_telefono(telefono) or telefono,
                    "empresa": empresa,
                    "categoria": categoria,
                    "urgencia": urgencia,
                    "resumen": resumen,
                },
                headers={**_headers(key), "Prefer": "return=representation"},
            )
    except httpx.HTTPError as e:
        logger.error(f"No se pudo guardar la alerta en Supabase: {e}")
        return None

    if r.status_code not in (200, 201):
        logger.error(f"Supabase respondio {r.status_code} al guardar la alerta: {r.text[:300]}")
        return None
    filas = r.json()
    return filas[0]["id"] if filas else None


async def alertas_pendientes(limite: int = 10) -> list[dict]:
    """Alertas que todavia no se le pudieron entregar a Manger por WhatsApp."""
    config = _config()
    if config is None:
        return []

    url, key = config
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            r = await cliente.get(
                f"{url}/rest/v1/alertas",
                params={
                    "entregada": "eq.false",
                    "select": "id,creada_en,empresa,telefono,categoria,urgencia,resumen",
                    "order": "creada_en.asc",
                    "limit": str(limite),
                },
                headers=_headers(key),
            )
    except httpx.HTTPError as e:
        logger.error(f"No se pudieron leer las alertas pendientes: {e}")
        return []

    return r.json() if r.status_code == 200 else []


async def marcar_alertas_entregadas(ids: list[int]):
    """Marca alertas como entregadas a Manger."""
    config = _config()
    if config is None or not ids:
        return

    url, key = config
    try:
        async with httpx.AsyncClient(timeout=10.0) as cliente:
            await cliente.patch(
                f"{url}/rest/v1/alertas",
                params={"id": f"in.({','.join(str(i) for i in ids)})"},
                json={"entregada": True},
                headers={**_headers(key), "Prefer": "return=minimal"},
            )
    except httpx.HTTPError as e:
        logger.error(f"No se pudieron marcar las alertas como entregadas: {e}")
