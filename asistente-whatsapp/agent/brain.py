# agent/brain.py — Cerebro del asistente: conexion con OpenAI

"""
Arma el contexto (prompt + plazos + cuenta del cliente + historial), llama a OpenAI y
ejecuta las herramientas que el modelo pida hasta tener la respuesta final.
"""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from openai import APIError, AsyncOpenAI

from agent.tools import HERRAMIENTAS, cargar_config, ejecutar_herramienta, formatear_horario

load_dotenv()
logger = logging.getLogger("agentkit")

# El modelo se cambia desde .env, sin tocar el codigo. El "or" cubre la variable
# declarada pero vacia, que os.getenv devolveria como "".
MODELO = os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
# low | medium | high. Vacio = no se envia (modelos sin razonamiento no lo aceptan).
ESFUERZO = (os.getenv("OPENAI_REASONING_EFFORT") or "").strip()
# El razonamiento interno tambien cuenta contra este tope: no lo bajes demasiado.
MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS") or "4096")
# Vueltas maximas de herramientas por mensaje, para que un error no quede en loop.
MAX_VUELTAS = 5

_cliente: AsyncOpenAI | None = None


def _openai() -> AsyncOpenAI:
    global _cliente
    if _cliente is None:
        _cliente = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _cliente


def cargar_config_prompts() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def obtener_mensaje_error() -> str:
    return cargar_config_prompts().get(
        "error_message", "Tuve un problema técnico. ¿Me escribes de nuevo en unos minutos?"
    )


def obtener_mensaje_fallback() -> str:
    return cargar_config_prompts().get("fallback_message", "Perdona, ¿me lo repites?")


def _plazos(config: dict) -> str:
    # Los ejemplos sin completar no se le pasan al modelo: mejor que no sepa a que invente
    plazos = [
        p for p in config.get("plazos_estandar") or []
        if p.get("plazo") and p["plazo"] != "COMPLETAR"
    ]
    if not plazos:
        return "Todavía no hay plazos cargados: cualquier plazo lo confirma Manger."
    return "\n".join(f"- {p['tema']}: {p['plazo']}" for p in plazos)


def _cuenta(cuenta: dict | None) -> str:
    if not cuenta:
        return "El número de WhatsApp que escribe NO está registrado como cliente."
    etiquetas = {
        "empresa": "Empresa",
        "contacto_nombre": "Contacto",
        "email": "Email",
        "plan": "Plan",
        "estado": "Estado",
        "avance": "Avance",
        "proximo_paso": "Próximo paso",
        "fecha_proximo_paso": "Fecha del próximo paso",
        "actualizado_en": "Última actualización",
    }
    return "\n".join(f"- {e}: {cuenta[k]}" for k, e in etiquetas.items() if cuenta.get(k))


def construir_system_prompt(cuenta: dict | None) -> str:
    config = cargar_config()
    zona = ZoneInfo(config.get("agenda", {}).get("zona_horaria", "America/Lima"))
    ahora = datetime.now(zona)
    base = cargar_config_prompts().get("system_prompt", "Eres un asistente útil.")
    return (
        f"{base}\n"
        f"## FECHA Y HORA ACTUAL\n{formatear_horario(ahora)} {ahora.year} (hora de Lima)\n\n"
        f"## PLAZOS ESTÁNDAR\n{_plazos(config)}\n\n"
        f"## CUENTA DEL CLIENTE\n{_cuenta(cuenta)}\n"
    )


async def generar_respuesta(
    mensaje: str, historial: list[dict], contexto: dict
) -> tuple[str, bool]:
    """
    Genera la respuesta para el cliente.

    contexto: {"telefono": ..., "cuenta": dict | None}
    Retorna (texto, es_respuesta_real). es_respuesta_real es False cuando el texto es un
    aviso tecnico: esos no se guardan en el historial.
    """
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("Falta OPENAI_API_KEY en el .env")
        return obtener_mensaje_error(), False

    mensajes = [
        {"role": "system", "content": construir_system_prompt(contexto.get("cuenta"))},
        *historial,
        {"role": "user", "content": mensaje},
    ]

    parametros = {"model": MODELO, "tools": HERRAMIENTAS, "max_completion_tokens": MAX_TOKENS}
    if ESFUERZO:
        parametros["reasoning_effort"] = ESFUERZO

    try:
        for _ in range(MAX_VUELTAS):
            r = await _openai().chat.completions.create(messages=mensajes, **parametros)
            eleccion = r.choices[0]
            respuesta = eleccion.message

            if not respuesta.tool_calls:
                texto = (respuesta.content or "").strip()
                if not texto:
                    logger.warning(f"Respuesta vacia (finish_reason={eleccion.finish_reason})")
                    return obtener_mensaje_fallback(), False
                if r.usage:
                    logger.info(
                        f"Respuesta con {MODELO} "
                        f"({r.usage.prompt_tokens} in / {r.usage.completion_tokens} out)"
                    )
                return texto, True

            # El modelo pidio herramientas: se ejecutan y se le devuelven los resultados
            mensajes.append(respuesta.model_dump(exclude_none=True))
            for llamada in respuesta.tool_calls:
                resultado = await ejecutar_herramienta(
                    llamada.function.name, llamada.function.arguments, contexto
                )
                mensajes.append(
                    {"role": "tool", "tool_call_id": llamada.id, "content": resultado}
                )

        logger.error(f"Se alcanzo el maximo de {MAX_VUELTAS} vueltas de herramientas")
        return obtener_mensaje_error(), False

    except APIError as e:
        logger.error(f"Error de OpenAI: {e}")
        return obtener_mensaje_error(), False
