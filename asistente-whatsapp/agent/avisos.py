# agent/avisos.py — Avisos a Manger por WhatsApp

"""
Como llega un aviso a Manger:

1. Siempre se guarda en la tabla "alertas" de Supabase, asi nunca se pierde.
2. Si Manger le escribio al asistente en las ultimas 24 horas, se le manda por WhatsApp
   en esa misma conversacion y se marca como entregada.
3. Si no, queda pendiente. La proxima vez que Manger le escriba al asistente (un "hola"
   alcanza), recibe todas las pendientes juntas.

El paso 3 existe por la ventana de 24 horas de WhatsApp: fuera de ella solo se pueden
mandar plantillas aprobadas por Meta. Cuando tengas la plantilla aprobada en Zernio, el
envio fuera de ventana se agrega en enviar_a_manger().
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from agent.cuentas import (
    alertas_pendientes,
    marcar_alertas_entregadas,
    normalizar_telefono,
    registrar_alerta,
)
from agent.memory import obtener_conversacion_dueno

logger = logging.getLogger("agentkit")

ETIQUETAS = {
    "precio": "💰 Consulta de precio",
    "queja": "😠 Queja",
    "riesgo_baja": "🚨 Riesgo de baja",
    "upsell": "📈 Oportunidad de upsell",
    "otro": "📌 Para revisar",
}

VENTANA_WHATSAPP = timedelta(hours=24)

# main.py registra aca el proveedor al arrancar. En el test local queda en None y los
# avisos se imprimen en la terminal.
_proveedor = None


def registrar_proveedor(proveedor):
    global _proveedor
    _proveedor = proveedor


def es_dueno(telefono: str) -> bool:
    """
    True si el mensaje viene de Manger. OWNER_PHONE es su numero con codigo de pais; si
    WhatsApp no entrega el numero (usuarios con username), puede ser el id que llega en
    el webhook en su lugar.
    """
    dueno = os.getenv("OWNER_PHONE", "").strip()
    if not dueno or not telefono:
        return False
    if telefono == dueno:
        return True
    digitos = normalizar_telefono(dueno)
    return bool(digitos) and normalizar_telefono(telefono) == digitos


def formatear_alerta(alerta: dict) -> str:
    quien = alerta.get("empresa") or "Contacto no registrado"
    telefono = alerta.get("telefono") or "?"
    urgente = " — URGENTE" if alerta.get("urgencia") == "alta" else ""
    etiqueta = ETIQUETAS.get(alerta.get("categoria", ""), ETIQUETAS["otro"])
    return f"{etiqueta}{urgente}\n{quien} (+{telefono})\n{alerta.get('resumen', '')}"


async def enviar_a_manger(texto: str) -> bool:
    """Le manda un WhatsApp a Manger si la ventana de 24 horas esta abierta."""
    if _proveedor is None:
        print(f"\n[AVISO A MANGER]\n{texto}\n")
        return True

    conversacion = await obtener_conversacion_dueno()
    if conversacion is None:
        logger.warning(
            "Manger todavia no le escribio al asistente: el aviso queda pendiente. "
            "Mandale un 'hola' desde OWNER_PHONE para activar los avisos."
        )
        return False

    contexto, ultima_vez = conversacion
    if datetime.now(timezone.utc) - ultima_vez > VENTANA_WHATSAPP:
        logger.info("Ventana de 24 h cerrada con Manger: el aviso queda pendiente")
        return False

    return await _proveedor.enviar_mensaje(os.getenv("OWNER_PHONE", ""), texto, contexto)


async def avisar(
    telefono: str, categoria: str, urgencia: str, resumen: str, empresa: str | None
) -> bool:
    """Registra la alerta y trata de entregarla. True si quedo registrada o entregada."""
    alerta = {
        "telefono": normalizar_telefono(telefono) or telefono,
        "empresa": empresa,
        "categoria": categoria,
        "urgencia": urgencia,
        "resumen": resumen,
    }
    alerta_id = await registrar_alerta(telefono, categoria, urgencia, resumen, empresa)
    entregada = await enviar_a_manger(formatear_alerta(alerta))

    if entregada and alerta_id is not None:
        await marcar_alertas_entregadas([alerta_id])
    if not entregada and alerta_id is None:
        # Ni Supabase ni WhatsApp: que por lo menos quede en los logs del servidor
        logger.error(f"AVISO PERDIDO (sin Supabase ni WhatsApp): {alerta}")
        return False
    return True


async def resumen_para_manger() -> tuple[str, list[int]]:
    """
    Lo que el asistente le contesta a Manger cuando le escribe, y los ids de las alertas
    incluidas. Se marcan como entregadas recien cuando el mensaje sale bien.
    """
    pendientes = await alertas_pendientes()
    if not pendientes:
        return "Hola Manger 👋 Avisos activos por 24 h. No tienes alertas pendientes.", []

    bloques = "\n\n".join(formatear_alerta(a) for a in pendientes)
    texto = (
        f"Hola Manger 👋 Avisos activos por 24 h. "
        f"Tienes {len(pendientes)} alerta(s) pendiente(s):\n\n{bloques}"
    )
    return texto, [a["id"] for a in pendientes]
