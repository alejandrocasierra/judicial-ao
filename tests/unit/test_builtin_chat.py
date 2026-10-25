"""UT-BCH — agentes de sistema del chat: prompt canónico y actualización idempotente.

Contrato F2 · R1 (protocolo de investigación) y R2 (los agentes del sistema se
actualizan; los del usuario no se tocan).
"""
import pytest

from app.services import builtin_agents

pytestmark = pytest.mark.unit

ORG = "org-1"


class _Row:
    """Fila mínima: acceso posicional (`row[0]`) y por atributo (`row.kind`)."""

    def __init__(self, *values, **data):
        self.__dict__.update(data)
        self._values = values

    def __getitem__(self, index):
        return self._values[index]


class _Result:
    def __init__(self, data):
        self._rows = data

    def first(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Conexión mínima con estado: agentes y skills en memoria."""

    def __init__(self, agents=None, skills=None):
        self.agents = {a["name"]: dict(a) for a in agents or []}
        self.skills = dict(skills or {})
        self.calls: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        values = dict(params or {})
        self.calls.append((sql, values))
        if "FROM skills" in sql:
            found = self.skills.get(values["n"])
            return _Result([_Row(found, id=found)] if found else [])
        if "INSERT INTO skills" in sql:
            ident = f"skill-{len(self.skills) + 1}"
            self.skills[values["n"]] = ident
            return _Result([_Row(ident, id=ident)])
        if "FROM agents" in sql:
            agent = self.agents.get(values["n"])
            return _Result([_Row(**agent)] if agent else [])
        if "INSERT INTO agents" in sql:
            ident = f"agent-{len(self.agents) + 1}"
            self.agents[values["n"]] = {
                "id": ident, "name": values["n"], "system_prompt": values["sp"],
                "skills": list(values["sk"]), "is_system": True, "kind": "chat",
            }
            return _Result([])
        if "UPDATE agents" in sql:
            for agent in self.agents.values():
                if agent["id"] == values["i"]:
                    agent["system_prompt"] = values["sp"]
                    agent["skills"] = list(values["sk"])
            return _Result([])
        return _Result([])

    def count(self, needle: str) -> int:
        return sum(1 for sql, _ in self.calls if needle in sql)


def _canonical(name: str) -> str:
    return next(prompt for agent_name, _skills, prompt in builtin_agents.CHAT_AGENTS
                if agent_name == name)


def test_bch_01_updates_a_system_chat_agent():
    conn = FakeConn(agents=[{"id": "a1", "name": "Asistente del expediente", "is_system": True,
                             "kind": "chat", "system_prompt": "prompt viejo", "skills": []}])
    builtin_agents.ensure_chat_agents(conn, ORG, None)
    agent = conn.agents["Asistente del expediente"]
    assert agent["system_prompt"] == _canonical("Asistente del expediente")
    assert agent["system_prompt"] != "prompt viejo"
    assert agent["skills"], "las skills canónicas deben quedar enlazadas"
    assert conn.count("UPDATE agents") == 1
    assert conn.count("INSERT INTO agents") == 2  # los otros dos agentes no existían


def test_bch_02_does_not_touch_a_user_agent():
    conn = FakeConn(agents=[{"id": "u1", "name": "Asistente del expediente", "is_system": False,
                             "kind": "custom", "system_prompt": "mío", "skills": ["s-mia"]}])
    builtin_agents.ensure_chat_agents(conn, ORG, None)
    agent = conn.agents["Asistente del expediente"]
    assert agent["system_prompt"] == "mío"
    assert agent["skills"] == ["s-mia"]
    updates = [params for sql, params in conn.calls if "UPDATE agents" in sql]
    assert updates == []
    # Solo se crean los agentes de sistema que faltan.
    assert conn.count("INSERT INTO agents") == 2


def test_bch_03_creates_the_three_chat_agents():
    conn = FakeConn()
    builtin_agents.ensure_chat_agents(conn, ORG, None)
    assert set(conn.agents) == {name for name, _skills, _prompt in builtin_agents.CHAT_AGENTS}
    assert all(agent["is_system"] and agent["kind"] == "chat" for agent in conn.agents.values())
    assert conn.count("INSERT INTO agents") == 3


def test_bch_04_is_idempotent_and_does_not_duplicate_skills():
    conn = FakeConn()
    builtin_agents.ensure_chat_agents(conn, ORG, None)
    first_skills, first_agents = len(conn.skills), len(conn.agents)
    builtin_agents.ensure_chat_agents(conn, ORG, None)
    assert (len(conn.skills), len(conn.agents)) == (first_skills, first_agents)
    assert first_agents == len(builtin_agents.CHAT_AGENTS)
    assert conn.count("INSERT INTO agents") == first_agents
    skills_per_agent = [agent["skills"] for agent in conn.agents.values()]
    assert all(len(values) == len(set(values)) for values in skills_per_agent)


def test_bch_05_prompt_covers_the_research_protocol():
    """A1: facetas, herramientas de ASR/hechos y la estructura de síntesis."""
    prompt = _canonical("Asistente del expediente")
    for token in ("facetas", "search_transcripts", "search_facts", "Síntesis",
                  "## Posición de la parte demandante", "## Interrogatorios y audiencias",
                  "DEEP_MAX_STEPS", "mm:ss"):
        assert token in prompt, token
    # La frase prohibida solo puede aparecer como prohibición, nunca como apertura.
    assert not prompt.strip().startswith("Encontré material relacionado")
    offending = [line for line in prompt.splitlines()
                 if "Encontré material relacionado" in line]
    assert offending and all(line.strip().startswith("- No abras con") for line in offending)


def test_bch_06_cronista_keeps_the_protocol_and_adds_chronology():
    prompt = _canonical("Cronista probatorio")
    for token in ("facetas", "search_transcripts", "search_facts", "## Síntesis",
                  "cronológico", "contradicciones"):
        assert token in prompt, token


def test_bch_07_prompt_forbids_the_sanitized_closing():
    prompt = _canonical("Asistente del expediente")
    assert "Se omitieron afirmaciones" in prompt
    assert "No cierres con «Se omitieron afirmaciones" in prompt
