# agent/tools.py — Herramientas que el asistente puede usar

"""
Acciones reales que el modelo puede ejecutar: ver horarios, agendar y avisar a Manger.

La informacion (plazos, datos de la cuenta) le llega por el system prompt. Aca solo
estan las ACCIONES. Ninguna recibe el telefono como argumento del modelo: el telefono
sale siempre del webhook (contexto), asi el cliente no puede hacerse pasar por otro.
"""

import json
import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import yaml

from agent import calendario
from agent.avisos import avisar

logger = logging.getLogger("agentkit")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
EMAIL_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def cargar_config() -> dict:
    """Carga config/business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def formatear_horario(momento: datetime) -> str:
    """'jueves 16 de octubre, 10:00'"""
    return f"{DIAS[momento.weekday()]} {momento.day} de {MESES[momento.month - 1]}, {momento:%H:%M}"


# ── Definicion para el modelo (formato tools de OpenAI) ──────────────────

HERRAMIENTAS = [
    {
        "type": "function",
        "function": {
            "name": "ver_horarios_disponibles",
            "description": (
                "Devuelve los horarios libres de Manger para una reunion de 30 minutos, "
                "en hora de Lima. Usala antes de ofrecer horarios."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "desde_fecha": {
                        "type": "string",
                        "description": "Primer dia a buscar, YYYY-MM-DD. Omitir para buscar desde hoy.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agendar_reunion",
            "description": (
                "Crea la reunion en el calendario de Manger con link de Google Meet y envia "
                "la invitacion al email del cliente. Solo cuando el cliente ya eligio horario."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "inicio": {
                        "type": "string",
                        "description": "Inicio en hora de Lima, formato YYYY-MM-DDTHH:MM. Debe ser uno de los horarios ofrecidos.",
                    },
                    "email": {"type": "string", "description": "Email que recibe la invitacion."},
                    "nombre": {"type": "string", "description": "Nombre del cliente."},
                    "motivo": {"type": "string", "description": "Motivo de la reunion en una frase."},
                },
                "required": ["inicio", "email", "nombre", "motivo"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avisar_a_manger",
            "description": "Le manda un aviso a Manger por WhatsApp sobre algo importante de este cliente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "categoria": {
                        "type": "string",
                        "enum": ["precio", "queja", "riesgo_baja", "upsell", "otro"],
                    },
                    "urgencia": {"type": "string", "enum": ["alta", "normal"]},
                    "resumen": {
                        "type": "string",
                        "description": "Que pasa y que pide el cliente, en 1 a 3 frases, para que Manger entienda sin leer el chat.",
                    },
                },
                "required": ["categoria", "urgencia", "resumen"],
                "additionalProperties": False,
            },
        },
    },
]


# ── Ejecucion ────────────────────────────────────────────────────────────


async def ejecutar_herramienta(nombre: str, argumentos: str, contexto: dict) -> str:
    """
    Ejecuta la herramienta que pidio el modelo y devuelve el resultado como JSON (texto).
    Nunca lanza excepciones: un error se le devuelve al modelo para que se lo explique
    al cliente, en vez de cortar la conversacion.

    contexto: {"telefono": ..., "cuenta": dict | None}
    """
    try:
        args = json.loads(argumentos or "{}")
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "error": "Argumentos invalidos"})

    try:
        if nombre == "ver_horarios_disponibles":
            resultado = await _ver_horarios(args)
        elif nombre == "agendar_reunion":
            resultado = await _agendar(args, contexto)
        elif nombre == "avisar_a_manger":
            resultado = await _avisar(args, contexto)
        else:
            resultado = {"ok": False, "error": f"Herramienta desconocida: {nombre}"}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Fallo la herramienta {nombre}: {e}")
        resultado = {"ok": False, "error": "Fallo tecnico. No confirmes nada al cliente."}

    logger.info(f"Herramienta {nombre}({args}) -> {resultado}")
    return json.dumps(resultado, ensure_ascii=False)


async def _ver_horarios(args: dict) -> dict:
    if not calendario.esta_configurado():
        return {"ok": False, "error": "El calendario no esta conectado. Ofrece que Manger le escriba para coordinar y avisale con categoria 'otro'."}

    agenda = cargar_config()["agenda"]
    desde = None
    if args.get("desde_fecha"):
        try:
            desde = date.fromisoformat(args["desde_fecha"])
        except ValueError:
            return {"ok": False, "error": "desde_fecha debe ser YYYY-MM-DD"}

    horarios = await calendario.horarios_disponibles(agenda, desde)
    if not horarios:
        return {"ok": True, "horarios": [], "nota": "No hay horarios libres en los proximos dias."}
    return {
        "ok": True,
        "zona_horaria": agenda["zona_horaria"],
        "horarios": [
            {"inicio": h.strftime("%Y-%m-%dT%H:%M"), "texto": formatear_horario(h)}
            for h in horarios
        ],
    }


async def _agendar(args: dict, contexto: dict) -> dict:
    if not calendario.esta_configurado():
        return {"ok": False, "error": "El calendario no esta conectado."}

    agenda = cargar_config()["agenda"]
    zona = ZoneInfo(agenda["zona_horaria"])

    try:
        inicio = datetime.fromisoformat(args["inicio"]).replace(tzinfo=zona)
    except (KeyError, ValueError):
        return {"ok": False, "error": "inicio debe ser YYYY-MM-DDTHH:MM"}

    email = (args.get("email") or "").strip()
    if not EMAIL_VALIDO.match(email):
        return {"ok": False, "error": "El email no parece valido. Pidele que lo confirme."}

    if not calendario.es_horario_valido(agenda, inicio, datetime.now(zona)):
        return {"ok": False, "error": "Ese horario esta fuera de la agenda. Usa ver_horarios_disponibles."}

    cuenta = contexto.get("cuenta") or {}
    resultado = await calendario.crear_reunion(
        agenda,
        inicio,
        email,
        args.get("nombre") or cuenta.get("contacto_nombre") or "Cliente",
        args.get("motivo") or "Reunion de seguimiento",
        cuenta.get("empresa"),
    )
    if resultado.get("ok"):
        resultado["cuando"] = formatear_horario(inicio) + " (hora de Lima)"
    return resultado


async def _avisar(args: dict, contexto: dict) -> dict:
    cuenta = contexto.get("cuenta") or {}
    ok = await avisar(
        contexto.get("telefono", ""),
        args.get("categoria", "otro"),
        args.get("urgencia", "normal"),
        args.get("resumen", ""),
        cuenta.get("empresa"),
    )
    if ok:
        return {"ok": True, "nota": "Manger fue avisado. Dile al cliente que Manger le va a responder."}
    return {"ok": False, "error": "No se pudo avisar. No le digas al cliente que Manger ya sabe."}
