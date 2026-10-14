# tests/test_local.py — Chat de prueba en la terminal
# Basado en AgentKit (github.com/Hainrixz/whatsapp-agentkit)

"""
Prueba el asistente sin WhatsApp. Los avisos a Manger se imprimen en la terminal.

  python tests/test_local.py                    # contacto no registrado
  python tests/test_local.py 51987654321        # simula a un cliente de la tabla cuentas
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta  # noqa: E402
from agent.cuentas import buscar_cuenta  # noqa: E402
from agent.memory import (  # noqa: E402
    guardar_mensaje,
    inicializar_db,
    limpiar_historial,
    obtener_historial,
)


async def main():
    telefono = sys.argv[1] if len(sys.argv) > 1 else "test-local-001"
    await inicializar_db()
    cuenta = await buscar_cuenta(telefono)

    print()
    print("=" * 55)
    print("   Asistente de Manger — Test local")
    print("=" * 55)
    print(f"  Escribiendo como: {telefono}")
    print(f"  Cuenta: {cuenta['empresa'] if cuenta else 'no registrada'}")
    print("  Comandos: 'limpiar' borra el historial, 'salir' termina")
    print("-" * 55)
    print()

    while True:
        try:
            mensaje = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue
        if mensaje.lower() == "salir":
            print("\nTest finalizado.")
            break
        if mensaje.lower() == "limpiar":
            await limpiar_historial(telefono)
            print("[Historial borrado]\n")
            continue

        historial = await obtener_historial(telefono)
        respuesta, es_respuesta_real = await generar_respuesta(
            mensaje, historial, {"telefono": telefono, "cuenta": cuenta}
        )
        print(f"\nAsistente: {respuesta}\n")

        if es_respuesta_real:
            await guardar_mensaje(telefono, "user", mensaje)
            await guardar_mensaje(telefono, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
