"""Разбор логов упавшего сервиса.

Ценность алерта не в том, что он сообщает о падении, а в том, что вместе с
ним приходит причина. Читать сто строк лога с телефона в час ночи —
занятие, ради которого watchdog и писался.

Схема ответа валидируется API, а не парсингом текста, поэтому карточка
алерта собирается из полей, а не из свободного абзаца.
"""

from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"

SYSTEM = """\
Ты разбираешь логи упавшего сервиса и отвечаешь дежурному инженеру.

Правила:
1. Опирайся только на предоставленные логи. Не додумывай причину, которой в них нет.
2. Если логи не содержат внятной причины, так и скажи: confidence = "low",
   а в cause опиши, чего именно не хватает для вывода.
3. fix — конкретное первое действие, а не рассуждение. «Поднять лимит памяти
   сервиса», а не «стоит изучить потребление ресурсов».
4. Отвечай по-русски и коротко: это сообщение читают с телефона.
"""


class Triage(BaseModel):
    cause: str = Field(description="Вероятная причина падения, одно-два предложения")
    evidence: str = Field(description="Строка лога, на которой основан вывод")
    fix: str = Field(description="Первое конкретное действие")
    confidence: str = Field(description="high, medium или low")
    transient: bool = Field(
        description="True, если похоже на разовый сбой, который пройдёт сам"
    )


def analyze(
    target: str,
    summary: str,
    logs: list[str],
    *,
    client: anthropic.Anthropic | None = None,
    max_lines: int = 200,
) -> Triage | None:
    """Разобрать логи. Вернуть None, если разбирать нечего или модель недоступна.

    Падение триажа не должно ронять алерт: сообщение о том, что сервис лежит,
    важнее объяснения, почему он лежит.
    """
    if not logs:
        return None

    client = client or anthropic.Anthropic()
    tail = logs[-max_lines:]

    prompt = (
        f"Сервис: {target}\n"
        f"Что увидел мониторинг: {summary}\n\n"
        f"Последние {len(tail)} строк лога:\n\n" + "\n".join(tail)
    )

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_format=Triage,
        )
    except anthropic.APIError as exc:
        log.warning("Триаж недоступен для %s: %s", target, exc)
        return None

    if response.stop_reason == "refusal":
        log.warning("Модель отказалась разбирать логи %s", target)
        return None

    return response.parsed_output


def format_triage(triage: Triage) -> str:
    marker = {"high": "", "medium": " (уверенность средняя)", "low": " (уверенность низкая)"}
    tail = marker.get(triage.confidence, "")
    lines = [f"*Причина:*{tail} {triage.cause}", f"*Что делать:* {triage.fix}"]
    if triage.evidence:
        lines.append(f"```\n{triage.evidence[:300]}\n```")
    if triage.transient:
        lines.append("_Похоже на разовый сбой._")
    return "\n".join(lines)
