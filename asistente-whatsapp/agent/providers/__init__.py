# agent/providers/__init__.py — Factory de proveedores
# Basado en AgentKit (github.com/Hainrixz/whatsapp-agentkit)

"""
Elige el proveedor de WhatsApp segun la variable WHATSAPP_PROVIDER del .env.
Este asistente solo trae el adaptador de Zernio.
"""

import os

from agent.providers.base import MensajeEntrante, ProveedorWhatsApp

PROVEEDORES_SOPORTADOS = ("zernio",)


def obtener_proveedor() -> ProveedorWhatsApp:
    """
    Retorna el proveedor configurado en .env.

    No se ejecuta al importar: si la configuracion esta mal, el servidor igual arranca
    y lo cuenta en el health check.
    """
    proveedor = os.getenv("WHATSAPP_PROVIDER", "").strip().lower()

    if proveedor == "zernio":
        from agent.providers.zernio import ProveedorZernio

        return ProveedorZernio()

    raise ValueError(
        f"WHATSAPP_PROVIDER invalido o vacio ('{proveedor}'). "
        f"Valores validos: {' | '.join(PROVEEDORES_SOPORTADOS)}"
    )


__all__ = [
    "MensajeEntrante",
    "ProveedorWhatsApp",
    "PROVEEDORES_SOPORTADOS",
    "obtener_proveedor",
]
