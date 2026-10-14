# agent/calendario.py — Agenda de Manger en Google Calendar

"""
Calcula horarios libres y crea reuniones con link de Google Meet.

Se usa OAuth con refresh token (no service account): asi el evento se crea en el
calendario real de Manger, con Meet incluido, y la invitacion le llega al cliente.
El refresh token se obtiene una sola vez con scripts/obtener_token_google.py.
"""

import logging
import os
import time
import uuid
from datetime import date, datetime, time as hora_del_dia, timedelta
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("agentkit")

API = "https://www.googleapis.com/calendar/v3"

_token: dict = {"valor": "", "expira": 0.0}


# ── Calculo de horarios (logica pura, sin red) ──────────────────────────


def _hora(texto: str) -> hora_del_dia:
    h, m = texto.split(":")
    return hora_del_dia(int(h), int(m))


def calcular_horarios(
    agenda: dict,
    ocupados: list[tuple[datetime, datetime]],
    ahora: datetime,
    desde: date | None = None,
) -> list[datetime]:
    """
    Horarios de inicio libres, con zona horaria de la agenda.

    agenda:   la seccion "agenda" de business.yaml
    ocupados: intervalos (inicio, fin) que devuelve Google como ocupados
    ahora:    hora actual con zona horaria (se recibe para poder testear)
    desde:    primer dia a buscar; por defecto hoy
    """
    zona = ZoneInfo(agenda["zona_horaria"])
    duracion = timedelta(minutes=int(agenda["duracion_minutos"]))
    inicio_dia = _hora(agenda["hora_inicio"])
    fin_dia = _hora(agenda["hora_fin"])
    dias_laborales = set(agenda["dias_laborales"])
    limite = ahora + timedelta(hours=float(agenda.get("anticipacion_minima_horas", 0)))
    maximo_por_dia = int(agenda.get("maximo_por_dia", 0)) or None

    hoy = ahora.astimezone(zona).date()
    dia = max(desde or hoy, hoy)
    ultimo_dia = hoy + timedelta(days=int(agenda.get("dias_a_buscar", 7)))

    libres: list[datetime] = []
    while dia <= ultimo_dia:
        if dia.weekday() in dias_laborales:
            del_dia: list[datetime] = []
            slot = datetime.combine(dia, inicio_dia, tzinfo=zona)
            cierre = datetime.combine(dia, fin_dia, tzinfo=zona)
            while slot + duracion <= cierre:
                fin = slot + duracion
                choca = any(slot < o_fin and fin > o_ini for o_ini, o_fin in ocupados)
                if slot >= limite and not choca:
                    del_dia.append(slot)
                slot = fin
            if maximo_por_dia and len(del_dia) > maximo_por_dia:
                # Repartidos a lo largo del dia, no los primeros N seguidos
                paso = len(del_dia) / maximo_por_dia
                del_dia = [del_dia[int(i * paso)] for i in range(maximo_por_dia)]
            libres.extend(del_dia)
        dia += timedelta(days=1)
    return libres


def es_horario_valido(agenda: dict, inicio: datetime, ahora: datetime) -> bool:
    """Confirma que un horario cae dentro de la agenda (dia, rango y anticipacion)."""
    zona = ZoneInfo(agenda["zona_horaria"])
    inicio = inicio.astimezone(zona)
    duracion = timedelta(minutes=int(agenda["duracion_minutos"]))
    apertura = datetime.combine(inicio.date(), _hora(agenda["hora_inicio"]), tzinfo=zona)
    cierre = datetime.combine(inicio.date(), _hora(agenda["hora_fin"]), tzinfo=zona)
    alineado = (inicio - apertura) % duracion == timedelta(0)
    limite = ahora + timedelta(hours=float(agenda.get("anticipacion_minima_horas", 0)))
    return (
        inicio.weekday() in set(agenda["dias_laborales"])
        and apertura <= inicio
        and inicio + duracion <= cierre
        and alineado
        and inicio >= limite
    )


# ── Google Calendar ──────────────────────────────────────────────────────


def esta_configurado() -> bool:
    return all(
        os.getenv(v)
        for v in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    )


def _calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID") or "primary"


async def _access_token(cliente: httpx.AsyncClient) -> str:
    """Canjea el refresh token por un access token y lo reutiliza hasta que vence."""
    if _token["valor"] and time.time() < _token["expira"] - 60:
        return _token["valor"]

    r = await cliente.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "refresh_token",
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
            "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN", ""),
        },
    )
    r.raise_for_status()
    datos = r.json()
    _token["valor"] = datos["access_token"]
    _token["expira"] = time.time() + int(datos.get("expires_in", 3600))
    return _token["valor"]


async def obtener_ocupados(
    cliente: httpx.AsyncClient, desde: datetime, hasta: datetime
) -> list[tuple[datetime, datetime]]:
    """Intervalos ocupados del calendario de Manger (consulta freeBusy)."""
    token = await _access_token(cliente)
    r = await cliente.post(
        f"{API}/freeBusy",
        json={
            "timeMin": desde.isoformat(),
            "timeMax": hasta.isoformat(),
            "items": [{"id": _calendar_id()}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    calendario = r.json().get("calendars", {}).get(_calendar_id(), {})
    if calendario.get("errors"):
        raise RuntimeError(f"Google no dejo leer el calendario: {calendario['errors']}")
    return [
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
        for b in calendario.get("busy", [])
    ]


async def horarios_disponibles(agenda: dict, desde: date | None = None) -> list[datetime]:
    """Horarios libres reales, cruzando la agenda con el calendario de Manger."""
    zona = ZoneInfo(agenda["zona_horaria"])
    ahora = datetime.now(zona)
    rango_fin = ahora + timedelta(days=int(agenda.get("dias_a_buscar", 7)) + 1)
    async with httpx.AsyncClient(timeout=15.0) as cliente:
        ocupados = await obtener_ocupados(cliente, ahora, rango_fin)
    return calcular_horarios(agenda, ocupados, ahora, desde)


async def crear_reunion(
    agenda: dict, inicio: datetime, email: str, nombre: str, motivo: str, empresa: str | None
) -> dict:
    """
    Crea el evento con Meet y le manda la invitacion al cliente.
    Antes vuelve a chequear que el horario siga libre: entre que se ofrecio y que el
    cliente eligio, Manger pudo haber agendado otra cosa.
    """
    duracion = timedelta(minutes=int(agenda["duracion_minutos"]))
    fin = inicio + duracion

    async with httpx.AsyncClient(timeout=20.0) as cliente:
        ocupados = await obtener_ocupados(cliente, inicio, fin)
        if any(inicio < o_fin and fin > o_ini for o_ini, o_fin in ocupados):
            return {"ok": False, "error": "Ese horario ya se ocupo. Ofrece otro."}

        token = await _access_token(cliente)
        titulo = f"Manger <> {empresa or nombre}"
        r = await cliente.post(
            f"{API}/calendars/{_calendar_id()}/events",
            params={"conferenceDataVersion": "1", "sendUpdates": "all"},
            json={
                "summary": titulo,
                "description": f"Agendado por el Asistente de Manger por WhatsApp.\n\nMotivo: {motivo}",
                "start": {"dateTime": inicio.isoformat(), "timeZone": agenda["zona_horaria"]},
                "end": {"dateTime": fin.isoformat(), "timeZone": agenda["zona_horaria"]},
                "attendees": [{"email": email, "displayName": nombre}],
                "conferenceData": {
                    "createRequest": {
                        "requestId": uuid.uuid4().hex,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    if r.status_code not in (200, 201):
        logger.error(f"Google rechazo el evento [{r.status_code}]: {r.text[:300]}")
        return {"ok": False, "error": "No se pudo crear el evento en el calendario."}

    evento = r.json()
    return {"ok": True, "link_meet": evento.get("hangoutLink", ""), "titulo": titulo}
