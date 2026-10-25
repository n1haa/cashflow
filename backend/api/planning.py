"""Manual future events and data-grounded assistant."""

import json
import math
import os
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException, Request

from backend.schemas import ChatRequest, ChatResponse, PlannedEvent, PlannedEventInput
from backend.services.recurring_service import detect_recurring_transactions

router = APIRouter(prefix="/api", tags=["planning"])


@router.get("/events", response_model=list[PlannedEvent])
def list_events(request: Request):
    return request.state.workspace.planned_events


@router.post("/events", response_model=PlannedEvent, status_code=201)
def add_event(payload: PlannedEventInput, request: Request):
    event = PlannedEvent(id=request.state.workspace.next_event_id, **payload.model_dump())
    request.state.workspace.next_event_id += 1
    request.state.workspace.planned_events.append(event)
    return event


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, request: Request):
    events = request.state.workspace.planned_events
    kept = [event for event in events if event.id != event_id]
    if len(kept) == len(events):
        raise HTTPException(status_code=404, detail="Будущее событие не найдено")
    request.state.workspace.planned_events = kept


def _context(request: Request) -> tuple[list[str], list[str]]:
    transactions = request.state.workspace.transactions
    events = request.state.workspace.planned_events
    sources: list[str] = []
    facts: list[str] = []
    for tx in transactions:
        category = request.state.workspace.categorizations.get(tx.id)
        label = category.category_name if category else "категория не рассчитана"
        item = f"Операция #{tx.id}: {tx.date} | {tx.direction} | {tx.amount} ₽ | {tx.counterparty} | {label} | {tx.description}"
        facts.append(item)
        sources.append(f"Операция #{tx.id} от {tx.date}: {tx.description}, {tx.amount} ₽")
    for event in events:
        facts.append(f"Событие #{event.id}: {event.date} | {event.direction} | {event.amount} ₽ | {event.category} | {event.description}")
        sources.append(f"Ручное событие #{event.id} на {event.date}: {event.description}, {event.amount} ₽")
    recurring = detect_recurring_transactions(transactions, request.state.workspace.categorizations)
    for group in recurring.groups:
        facts.append(f"Регулярная операция: {group.description} | {group.direction} | {group.category_name} | {group.frequency} | средняя сумма {group.average_amount} ₽ | последняя дата {group.last_date} | следующая дата {group.next_expected_date} | статус {group.status} | операции {', '.join(map(str, group.transaction_ids))}")
        sources.append(f"Регулярная группа {group.group_id}: {group.description}, операции {', '.join(map(str, group.transaction_ids))}")
    forecast = request.state.workspace.last_forecast
    if forecast:
        facts.append(f"Сводка прогноза: остаток на {forecast.as_of} — {forecast.summary.opening_balance} ₽; ожидаемый остаток на {forecast.end_date} — {forecast.summary.closing_balance} ₽.")
        sources.append(f"Итог прогноза на {forecast.end_date}: {forecast.summary.closing_balance} ₽")
        for day in forecast.daily:
            if day.balance is not None:
                facts.append(f"День прогноза {day.date}: остаток {day.balance} ₽; поступления {day.income} ₽; расходы {day.expense} ₽")
                sources.append(f"День прогноза {day.date}: остаток {day.balance} ₽, поступления {day.income} ₽, расходы {day.expense} ₽")
    gaps = request.state.workspace.last_gaps
    if gaps:
        for gap in gaps.gaps:
            facts.append(f"Риск кассового разрыва {gap.start_date}: максимум дефицита {gap.maximum_deficit} ₽, минимум остатка {gap.minimum_balance} ₽")
            sources.append(f"Анализ разрыва от {gap.start_date}: максимальный дефицит {gap.maximum_deficit} ₽")
    return facts, sources


def _openrouter_request(path: str, payload: dict):
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    allow_financial_data = os.getenv("OPENROUTER_ALLOW_FINANCIAL_DATA", "false").strip().casefold() == "true"
    if not key or not allow_financial_data:
        return None
    body = json.dumps(payload).encode()
    req = UrlRequest(f"https://openrouter.ai/api/v1/{path}", body,
                     {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                      "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
                      "X-OpenRouter-Title": os.getenv("OPENROUTER_SITE_NAME", "CashFlow AI")}, method="POST")
    try:
        with urlopen(req, timeout=25) as response:
            return json.loads(response.read())
    except Exception:
        return None


def _retrieve(request: Request, question: str, facts: list[str]) -> list[str]:
    """Use Nemotron vectors for top-k retrieval; fall back to local lexical matching."""
    if not facts:
        return []
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    allow_financial_data = os.getenv("OPENROUTER_ALLOW_FINANCIAL_DATA", "false").strip().casefold() == "true"
    model = os.getenv("OPENROUTER_EMBEDDING_MODEL", "nvidia/nemotron-3.5-embed-1b:free")
    cache = request.state.workspace.embedding_cache
    query_vector = None
    if key and allow_financial_data:
        missing = [fact for fact in facts if fact not in cache]
        payload_texts = [question, *missing]
        result = _openrouter_request("embeddings", {"model": model, "input": payload_texts, "encoding_format": "float"})
        if result and isinstance(result.get("data"), list) and len(result["data"]) == len(payload_texts):
            try:
                query_vector = result["data"][0]["embedding"]
                for fact, item in zip(missing, result["data"][1:]):
                    cache[fact] = item["embedding"]
            except (KeyError, TypeError):
                query_vector = None
    if query_vector:
        def cosine(vector):
            dot = sum(a * b for a, b in zip(query_vector, vector))
            norm = math.sqrt(sum(a*a for a in query_vector)) * math.sqrt(sum(b*b for b in vector))
            return dot / norm if norm else 0
        ranked = sorted(((cosine(cache[fact]), index, fact) for index, fact in enumerate(facts) if fact in cache), reverse=True)
        if ranked:
            return [fact for _, _, fact in ranked[:12]]
    words = [word.casefold().strip("?.,!;:") for word in question.split() if len(word) > 3]
    ranked = [(sum(word in fact.casefold() for word in words), -index, fact) for index, fact in enumerate(facts)]
    return [fact for score, _, fact in sorted(ranked, reverse=True)[:12] if score > 0]


def _llm_answer(message: str, facts: list[str]) -> str | None:
    """Optional OpenRouter chat model; embedding model alone cannot generate answers."""
    model = os.getenv("OPENROUTER_CHAT_MODEL", "").strip()
    allow_financial_data = os.getenv("OPENROUTER_ALLOW_FINANCIAL_DATA", "false").strip().casefold() == "true"
    if not model or not allow_financial_data or not os.getenv("OPENROUTER_API_KEY", "").strip():
        return None
    data = _openrouter_request("chat/completions", {"model": model, "temperature": 0, "messages": [
        {"role": "system", "content": "Ты ассистент CashFlow. Отвечай только на основании переданного контекста. Не придумывай данные и советы. Если данных не хватает, прямо скажи об этом. Ссылайся на номера операций и события."},
        {"role": "user", "content": f"ДАННЫЕ:\n{chr(10).join(facts)}\n\nВОПРОС: {message}"},
    ]})
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (TypeError, KeyError, IndexError, AttributeError):
        return None


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request):
    facts, sources = _context(request)
    question = payload.message.casefold()
    if not facts:
        answer = "Пока нет загруженных операций или будущих событий. Загрузите выписку или добавьте событие, чтобы я мог ответить по вашим данным."
        return ChatResponse(answer=answer, sources=[])
    chosen = _retrieve(request, payload.message, facts) or facts[:12]
    if any(word in question for word in ("аренд", "зарплат", "регуляр", "платеж")):
        chosen = [fact for fact in _retrieve(request, payload.message, facts) if any(word in fact.casefold() for word in ("аренд", "зарплат", "rent", "salary", "регулярная операция"))]
        if not chosen:
            chosen = [fact for fact in facts if "регулярная операция:" in fact.casefold()]
    elif any(word in question for word in ("разрыв", "нехват", "остаток", "прогноз")):
        forecast = request.state.workspace.last_forecast
        gaps = request.state.workspace.last_gaps
        if forecast:
            chosen = [f"Прогноз на {forecast.as_of}: остаток на начало {forecast.summary.opening_balance}; ожидаемый остаток на {forecast.end_date}: {forecast.summary.closing_balance} ₽."]
            chosen.extend(f"День {d.date}: остаток {d.balance} ₽, поступления {d.income} ₽, расходы {d.expense} ₽" for d in forecast.daily if d.balance is not None and d.balance < 0)
            if gaps and gaps.gaps:
                chosen.extend(f"Кассовый разрыв: {g.start_date}, максимум дефицита {g.maximum_deficit} ₽, минимум {g.minimum_balance} ₽" for g in gaps.gaps)
        else:
            chosen = ["Прогноз еще не рассчитан."]
    elif any(word in question for word in ("поступлен", "выруч", "крупн")):
        forecast = request.state.workspace.last_forecast
        if forecast:
            chosen = [f"{day.date}: ожидаемые поступления {day.income} ₽, прогнозный остаток {day.balance} ₽" for day in forecast.daily if day.income > 0]
        else:
            chosen = [fact for fact in facts if "income" in fact.casefold() or "поступлен" in fact.casefold()]
    answer = _llm_answer(payload.message, chosen)
    mode = "llm" if answer else ("nemotron_rag" if request.state.workspace.embedding_cache else "grounded_local")
    if answer is None:
        relevant = [fact for fact in chosen if any(token in fact.casefold() for token in question.split() if len(token) > 3)]
        if relevant:
            answer = "Нашел в ваших данных:\n" + "\n".join(f"• {line}" for line in relevant[:12])
        elif chosen and chosen != facts:
            answer = "По рассчитанным данным:\n" + "\n".join(f"• {line}" for line in chosen[:12])
        else:
            answer = "Не нашел достаточно данных, чтобы надежно ответить на этот вопрос. Уточните контрагента, категорию или период."
    matched_ids = {token for token in answer.replace("#", " ").split() if token.isdigit()}
    refs = [source for source in sources if any(f"#{identifier}" in source for identifier in matched_ids)]
    if not refs and chosen and chosen != facts:
        refs = [source for source in sources if "прогноза" in source.casefold() or "разрыва" in source.casefold()]
    if not refs:
        refs = sources[:5]
    return ChatResponse(answer=answer, sources=refs[:12], mode=mode)
