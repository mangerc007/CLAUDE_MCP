# tests/test_local.py — Chat de prueba en la terminal
# Basado en AgentKit (github.com/Hainrixz/whatsapp-agentkit)

"""
Prueba el asistente sin WhatsApp. Los avisos a Manger se imprimen en la terminal.

  python tests/test_local.py --demo             # solo necesita OPENAI_API_KEY
  python tests/test_local.py                    # contacto no registrado
  python tests/test_local.py 51987654321        # simula a un cliente de la tabla cuentas

--demo usa una cuenta ficticia y una agenda simulada (Google Calendar libre, las
reuniones no se crean de verdad), para probar todo sin Supabase ni Google.
"""

import asyncio
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import calendario  # noqa: E402
from agent.brain import generar_respuesta  # noqa: E402
from agent.cuentas import buscar_cuenta  # noqa: E402
from agent.memory import (  # noqa: E402
    guardar_mensaje,
    inicializar_db,
    limpiar_historial,
    obtener_historial,
)

CUENTA_DEMO = {
    "empresa": "Tienda Demo SAC",
    "contacto_nombre": "Lucía Pérez",
    "email": "lucia@tiendademo.pe",
    "plan": "Pro",
    "estado": "onboarding",
    "avance": "Integración con la tienda online lista; falta importar la base de contactos.",
    "proximo_paso": "Importar la base de contactos y lanzar la primera campaña de bienvenida.",
    "fecha_proximo_paso": "2026-11-05",
}


def activar_demo():
    """Agenda simulada: todos los horarios libres y las reuniones solo se imprimen."""

    async def horarios(agenda, desde=None):
        ahora = datetime.now(ZoneInfo(agenda["zona_horaria"]))
        return calendario.calcular_horarios(agenda, [], ahora, desde)

    async def crear(agenda, inicio, email, nombre, motivo, empresa):
        print(f"\n[DEMO — REUNIÓN QUE SE CREARÍA] {inicio:%Y-%m-%d %H:%M} · {email} · {motivo}")
        return {"ok": True, "link_meet": "https://meet.google.com/demo-demo-demo", "titulo": f"Manger <> {empresa}"}

    calendario.esta_configurado = lambda: True
    calendario.horarios_disponibles = horarios
    calendario.crear_reunion = crear


async def main():
    argumentos = [a for a in sys.argv[1:] if a != "--demo"]
    demo = "--demo" in sys.argv
    telefono = argumentos[0] if argumentos else ("51900000000" if demo else "test-local-001")
    await inicializar_db()
    if demo:
        activar_demo()
        cuenta = CUENTA_DEMO
    else:
        cuenta = await buscar_cuenta(telefono)

    print()
    print("=" * 55)
    print("   Asistente de Manger — Test local")
    print("=" * 55)
    if demo:
        print("  MODO DEMO: cuenta ficticia y agenda simulada")
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
