"""Local ASR -> constrained LLM tool plan pipeline.

Audio/model runtimes are optional adapters.  The core remains dependency-free
and testable without downloading weights or granting a model robot access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.request import Request, urlopen

from ..config import load_json


@dataclass(frozen=True, slots=True)
class LocalModelComponent:
    runtime: str
    model_id: str
    device: str
    endpoint: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocalModelComponent":
        return cls(
            runtime=str(data["runtime"]),
            model_id=str(data["model_id"]),
            device=str(data.get("device", "cpu")),
            endpoint=(None if data.get("endpoint") is None else str(data["endpoint"])),
            options=dict(data.get("options", {})),
        )


@dataclass(frozen=True, slots=True)
class LocalModelStack:
    name: str
    vad: LocalModelComponent
    asr: LocalModelComponent
    llm: LocalModelComponent
    tts: LocalModelComponent | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocalModelStack":
        tts = data.get("tts")
        return cls(
            name=str(data["name"]),
            vad=LocalModelComponent.from_dict(data["vad"]),
            asr=LocalModelComponent.from_dict(data["asr"]),
            llm=LocalModelComponent.from_dict(data["llm"]),
            tts=None if tts is None else LocalModelComponent.from_dict(tts),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "LocalModelStack":
        return cls.from_dict(load_json(path))


@dataclass(frozen=True, slots=True)
class PlannedToolCall:
    skill: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VoicePlan:
    transcript: str
    calls: tuple[PlannedToolCall, ...]
    planner: str
    rejected_calls: tuple[PlannedToolCall, ...] = ()

    @property
    def accepted(self) -> bool:
        return bool(self.calls) and not self.rejected_calls


class ASRAdapter(Protocol):
    async def transcribe(self, audio: bytes) -> str:
        """Return one finalized utterance after VAD endpointing."""


class ToolPlannerAdapter(Protocol):
    async def plan(self, transcript: str) -> tuple[PlannedToolCall, ...]:
        """Return only L2/L3 Skill calls, never actuator commands."""


@dataclass(frozen=True, slots=True)
class AgentToolPolicy:
    allowed_skills: frozenset[str]
    max_calls_per_turn: int = 4

    def __post_init__(self) -> None:
        if not self.allowed_skills:
            raise ValueError("at least one Agent-callable skill is required")
        if self.max_calls_per_turn <= 0:
            raise ValueError("max_calls_per_turn must be positive")

    def partition(
        self, calls: tuple[PlannedToolCall, ...]
    ) -> tuple[tuple[PlannedToolCall, ...], tuple[PlannedToolCall, ...]]:
        accepted: list[PlannedToolCall] = []
        rejected: list[PlannedToolCall] = []
        for index, call in enumerate(calls):
            unsafe_name = any(
                token in call.skill.lower()
                for token in ("joint", "torque", "motor", "low_cmd", "dds")
            )
            if (
                index >= self.max_calls_per_turn
                or call.skill not in self.allowed_skills
                or unsafe_name
            ):
                rejected.append(call)
            else:
                accepted.append(call)
        return tuple(accepted), tuple(rejected)


class VoiceCommandPipeline:
    def __init__(
        self,
        *,
        asr: ASRAdapter,
        planner: ToolPlannerAdapter,
        policy: AgentToolPolicy,
    ) -> None:
        self.asr = asr
        self.planner = planner
        self.policy = policy

    async def handle_audio(self, audio: bytes) -> VoicePlan:
        if not audio:
            raise ValueError("audio payload cannot be empty")
        transcript = (await self.asr.transcribe(audio)).strip()
        return await self.handle_transcript(transcript)

    async def handle_transcript(self, transcript: str) -> VoicePlan:
        normalized = transcript.strip()
        if not normalized:
            raise ValueError("ASR transcript cannot be empty")
        proposed = await self.planner.plan(normalized)
        accepted, rejected = self.policy.partition(proposed)
        return VoicePlan(
            transcript=normalized,
            calls=accepted,
            rejected_calls=rejected,
            planner=type(self.planner).__name__,
        )


class ScriptedASR:
    """Simulation adapter whose transcript is fixed by the test scenario."""

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    async def transcribe(self, audio: bytes) -> str:
        del audio
        return self.transcript


class RuleBasedToolPlanner:
    """Offline deterministic baseline for voice/Agent integration tests."""

    async def plan(self, transcript: str) -> tuple[PlannedToolCall, ...]:
        lowered = transcript.lower()
        if any(token in lowered for token in ("继续", "恢复", "resume")):
            return (
                PlannedToolCall(
                    "resume_interrupted_task", {"goal": transcript}
                ),
            )
        if any(token in lowered for token in ("代码", "程序", "python", "code")):
            return (
                PlannedToolCall("prepare_computer", {}),
                PlannedToolCall("ask_code_agent", {"prompt": transcript}),
                PlannedToolCall(
                    "verify_software_task", {"acceptance": "independent_test"}
                ),
            )
        if any(token in lowered for token in ("终端", "terminal")):
            return (PlannedToolCall("prepare_computer", {}),)
        return (PlannedToolCall("execute_verified_goal", {"goal": transcript}),)


class OpenAICompatibleToolPlanner:
    """Adapter for a local llama.cpp/vLLM/Ollama OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        tools: Mapping[str, Mapping[str, Any]],
        timeout_seconds: float = 20.0,
    ) -> None:
        if not endpoint.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("local planner endpoint must bind to loopback")
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.tools = dict(tools)
        self.timeout_seconds = timeout_seconds

    async def plan(self, transcript: str) -> tuple[PlannedToolCall, ...]:
        # Intentionally synchronous inside this thin optional adapter. Production
        # deployments should wrap it in a worker thread or an async HTTP client.
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Select only provided finite-horizon robot skills. "
                        "Never emit joint, torque, motor, or DDS commands."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(schema.get("description", "")),
                        "parameters": schema.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    },
                }
                for name, schema in self.tools.items()
            ],
            "tool_choice": "auto",
        }
        request = Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
        try:
            raw_calls = result["choices"][0]["message"].get("tool_calls", [])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("local LLM returned an invalid chat completion") from exc
        calls: list[PlannedToolCall] = []
        for raw in raw_calls:
            function = raw["function"]
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must decode to an object")
            calls.append(PlannedToolCall(str(function["name"]), arguments))
        return tuple(calls)

