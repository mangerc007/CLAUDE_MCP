# tests/test_unitarios.py — Pruebas sin red: agenda, herramientas y cerebro
# Correr con: python -m pytest tests/test_unitarios.py

import asyncio
import json
import os
import sys
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from agent import avisos, brain, calendario, tools  # noqa: E402
from agent.cuentas import CAMPOS_VISIBLES, normalizar_telefono  # noqa: E402

LIMA = ZoneInfo("America/Lima")
AGENDA = tools.cargar_config()["agenda"]


# ── Agenda ───────────────────────────────────────────────────────────────


def test_horarios_respetan_dias_y_rango():
    # Lunes 12 de octubre de 2026, 7:00 en Lima (con 2 h de anticipacion, 9:30 entra)
    ahora = datetime(2026, 10, 12, 7, 0, tzinfo=LIMA)
    agenda = {**AGENDA, "maximo_por_dia": 0, "dias_a_buscar": 6}
    horarios = calendario.calcular_horarios(agenda, [], ahora)

    assert all(h.weekday() < 5 for h in horarios)
    # 9:30 a 16:00 en bloques de 30 minutos = 13 por dia, de lunes a viernes
    assert len(horarios) == 13 * 5
    assert horarios[0] == datetime(2026, 10, 12, 9, 30, tzinfo=LIMA)
    assert max(h.time() for h in horarios).strftime("%H:%M") == "15:30"


def test_horarios_saltean_ocupados_y_anticipacion():
    ahora = datetime(2026, 10, 12, 9, 0, tzinfo=LIMA)  # +2 h de anticipacion -> 11:00
    ocupado = (
        datetime(2026, 10, 12, 11, 0, tzinfo=LIMA),
        datetime(2026, 10, 12, 12, 0, tzinfo=LIMA),
    )
    agenda = {**AGENDA, "maximo_por_dia": 0, "dias_a_buscar": 0}
    horarios = calendario.calcular_horarios(agenda, [ocupado], ahora)
    horas = [h.strftime("%H:%M") for h in horarios]

    assert horas[0] == "12:00"
    assert "11:00" not in horas and "11:30" not in horas


def test_maximo_por_dia_reparte_horarios():
    ahora = datetime(2026, 10, 12, 8, 0, tzinfo=LIMA)
    agenda = {**AGENDA, "maximo_por_dia": 4, "dias_a_buscar": 0}
    horarios = calendario.calcular_horarios(agenda, [], ahora, date(2026, 10, 12))
    assert len(horarios) == 4
    assert horarios[-1].hour >= 14  # no son los primeros 4 seguidos


def test_horario_valido():
    ahora = datetime(2026, 10, 12, 8, 0, tzinfo=LIMA)
    ok = datetime(2026, 10, 13, 10, 0, tzinfo=LIMA)
    assert calendario.es_horario_valido(AGENDA, ok, ahora)
    assert not calendario.es_horario_valido(AGENDA, ok.replace(hour=16), ahora)  # cierre
    assert not calendario.es_horario_valido(AGENDA, ok.replace(minute=15), ahora)  # desalineado
    assert not calendario.es_horario_valido(AGENDA, ok.replace(day=17), ahora)  # sabado


# ── Seguridad de datos ───────────────────────────────────────────────────


def test_notas_internas_nunca_se_leen():
    assert "notas_internas" not in CAMPOS_VISIBLES


def test_normalizar_y_detectar_dueno(monkeypatch):
    assert normalizar_telefono("+51 987-654-321") == "51987654321"
    monkeypatch.setenv("OWNER_PHONE", "+51 987 654 321")
    assert avisos.es_dueno("51987654321")
    assert not avisos.es_dueno("51900000000")
    assert not avisos.es_dueno("")


def test_plazos_sin_completar_no_llegan_al_modelo():
    prompt = brain.construir_system_prompt(None)
    assert "COMPLETAR" not in prompt
    assert "NO está registrado" in prompt


# ── Herramientas y ciclo del modelo ──────────────────────────────────────


def test_agendar_valida_email_antes_de_llamar_a_google(monkeypatch):
    monkeypatch.setattr(calendario, "esta_configurado", lambda: True)
    args = json.dumps({"inicio": "2030-01-07T10:00", "email": "no-es-email", "nombre": "A", "motivo": "x"})
    resultado = json.loads(asyncio.run(tools.ejecutar_herramienta("agendar_reunion", args, {})))
    assert resultado["ok"] is False and "email" in resultado["error"]


def test_avisar_usa_el_telefono_del_webhook(monkeypatch):
    recibido = {}

    async def falso_avisar(telefono, categoria, urgencia, resumen, empresa):
        recibido.update(telefono=telefono, categoria=categoria, empresa=empresa)
        return True

    monkeypatch.setattr(tools, "avisar", falso_avisar)
    args = json.dumps({"categoria": "precio", "urgencia": "normal", "resumen": "Pide precio"})
    contexto = {"telefono": "51911111111", "cuenta": {"empresa": "Acme"}}
    resultado = json.loads(asyncio.run(tools.ejecutar_herramienta("avisar_a_manger", args, contexto)))

    assert resultado["ok"] is True
    assert recibido == {"telefono": "51911111111", "categoria": "precio", "empresa": "Acme"}


class _OpenAIFalso:
    """Primero pide la herramienta avisar_a_manger, despues contesta texto."""

    def __init__(self):
        self.llamadas = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, messages, **kwargs):
        self.llamadas.append(messages)
        if len(self.llamadas) == 1:
            llamada = SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(
                    name="avisar_a_manger",
                    arguments=json.dumps({"categoria": "precio", "urgencia": "normal", "resumen": "x"}),
                ),
            )
            mensaje = SimpleNamespace(
                content=None,
                tool_calls=[llamada],
                model_dump=lambda **_: {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=mensaje, finish_reason="tool_calls")], usage=None)
        mensaje = SimpleNamespace(content="Manger te escribe pronto 🙌", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=mensaje, finish_reason="stop")], usage=None)


def test_ciclo_de_herramientas(monkeypatch):
    falso = _OpenAIFalso()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(brain, "_openai", lambda: falso)

    async def falso_avisar(*_):
        return True

    monkeypatch.setattr(tools, "avisar", falso_avisar)
    texto, real = asyncio.run(brain.generar_respuesta("¿Cuánto cuesta?", [], {"telefono": "51"}))

    assert real is True and texto == "Manger te escribe pronto 🙌"
    segunda = falso.llamadas[1]
    assert segunda[-1]["role"] == "tool" and segunda[-1]["tool_call_id"] == "call_1"
