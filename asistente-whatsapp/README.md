# Asistente de Manger — WhatsApp

Asistente virtual que atiende por WhatsApp a los clientes de emBlue que acompaña Manger (CSM).
Basado en [AgentKit](https://github.com/Hainrixz/whatsapp-agentkit), adaptado a OpenAI, Supabase y Google Calendar.

## Qué hace

| | |
|---|---|
| **Responde** | Dudas generales y plazos estándar (`config/business.yaml`) |
| **Avance de cuenta** | Lo lee de Supabase (`cuentas`) según el número que escribe: cada cliente ve solo su cuenta |
| **Agenda** | Reuniones de 30 min, lun–vie 9:30–16:00 (Lima), en Google Calendar con Meet e invitación |
| **Filtra** | Precios, quejas, riesgo de baja y upsell → aviso a Manger por WhatsApp + registro en `alertas` |
| **Nunca** | Da precios, inventa plazos, ni muestra `notas_internas` |

## Cómo llegan los avisos

WhatsApp solo deja escribirte libremente durante 24 h después de que **tú** le escribes al asistente.

1. Cada aviso se guarda en la tabla `alertas` de Supabase.
2. Si le escribiste al asistente en las últimas 24 h desde `OWNER_PHONE`, te llega al instante.
3. Si no, queda pendiente: mándale un "hola" al asistente y te llegan todos juntos.

Para recibirlos siempre al instante hace falta una plantilla aprobada por Meta (paso siguiente).

## Puesta en marcha

```bash
cd asistente-whatsapp
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # y completa las claves
```

1. **Supabase**: aplica `supabase/migrations/001_cuentas_y_alertas.sql` y carga tus cuentas
   (`telefono` solo dígitos con código de país, ej. `51987654321`).
2. **Google Calendar**: crea un cliente OAuth "App de escritorio" y corre
   `python scripts/obtener_token_google.py CLIENT_ID CLIENT_SECRET` en tu compu.
3. **Zernio**: crea la API key, activa el sandbox y apunta el webhook a `https://TU-URL/webhook`
   con el mismo `ZERNIO_WEBHOOK_SECRET` del `.env`.

## Probar

```bash
python -m pytest tests/test_unitarios.py      # pruebas sin red
python tests/test_local.py                    # chat en la terminal, como contacto no registrado
python tests/test_local.py 51987654321        # chat como un cliente de la tabla cuentas
uvicorn agent.main:app --reload               # servidor; GET / muestra el estado
```

## Archivos

```
agent/main.py        servidor + webhook (responde 200 y procesa en segundo plano)
agent/brain.py       OpenAI con herramientas (modelo en OPENAI_MODEL)
agent/tools.py       ver_horarios_disponibles, agendar_reunion, avisar_a_manger
agent/calendario.py  horarios libres y creación de eventos en Google Calendar
agent/cuentas.py     lectura de cuentas y registro de alertas en Supabase
agent/avisos.py      entrega de avisos a Manger
agent/memory.py      historial por conversación (SQLite local / PostgreSQL en prod)
config/              business.yaml (agenda, plazos) y prompts.yaml (personalidad y reglas)
```

> Antes de usarlo con clientes reales, confirma con emBlue que puedes atenderlos por este canal.
