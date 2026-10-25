"""HTTP API импорта выписок."""

from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.schemas import (
    CategorizationRequest, CategorizationResult, ImportResult, RecurringRule,
    RecurringRuleRequest, RecurringResult, Transaction,
)
from backend.services.category_service import categorize_transactions
from backend.services.file_service import MAX_FILE_SIZE, load_transactions_file, validate_file_metadata
from backend.services.transaction_service import normalize_transactions
from backend.services.recurring_service import detect_recurring_transactions

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _process(content: bytes, filename: str) -> ImportResult:
    return normalize_transactions(load_transactions_file(content, filename))


@router.post("/upload", response_model=ImportResult)
async def upload_transactions(request: Request, file: UploadFile = File(...)) -> ImportResult:
    try:
        filename = file.filename or ""
        validate_file_metadata(filename, file.size if file.size is not None else 1)
        content = await file.read(MAX_FILE_SIZE + 1)
        validate_file_metadata(filename, len(content))
        result = await run_in_threadpool(_process, content, filename)
    except ValueError as error:
        status_code = 413 if "размер" in str(error).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(error)) from None
    finally:
        await file.close()
    # Заменяем данные только после успешной проверки всей выписки.
    request.app.state.transactions = tuple(result.transactions)
    request.app.state.categorizations = {}
    request.app.state.balance_info = result.balance_info
    request.app.state.recurring_rules = {
        key: rule for key, rule in request.app.state.recurring_rules.items()
        if rule.source_group_id is None
    }
    return result


@router.get("", response_model=list[Transaction])
def get_transactions(request: Request) -> tuple[Transaction, ...]:
    return request.app.state.transactions


@router.post("/categorize", response_model=CategorizationResult)
async def categorize_stored_transactions(
    request: Request, selection: CategorizationRequest | None = None,
) -> CategorizationResult:
    transactions = request.app.state.transactions
    if selection is not None and selection.transaction_ids is not None:
        ids = selection.transaction_ids
        if len(ids) != len(set(ids)):
            raise HTTPException(status_code=400, detail="Идентификаторы операций не должны повторяться")
        available = {item.id for item in transactions}
        if set(ids) - available:
            raise HTTPException(status_code=404, detail="Указанные операции отсутствуют в загруженной выписке")
        selected = set(ids)
        transactions = tuple(item for item in transactions if item.id in selected)
    result = categorize_transactions(transactions)
    request.app.state.categorizations.update({item.transaction_id: item for item in result.categorizations})
    return result


@router.get("/recurring", response_model=RecurringResult)
async def get_recurring_operations(request: Request) -> RecurringResult:
    # Снимок текущей выписки; новые платежи или категории не создаются.
    return detect_recurring_transactions(request.app.state.transactions, request.app.state.categorizations)


def _validate_source_group(request: Request, source_group_id: str | None, exclude_rule_id: str | None = None) -> None:
    if source_group_id is None:
        return
    groups = detect_recurring_transactions(request.app.state.transactions, request.app.state.categorizations).groups
    if source_group_id not in {group.group_id for group in groups}:
        raise HTTPException(status_code=404, detail="Группа регулярных операций не найдена")
    if any(rule.source_group_id == source_group_id and key != exclude_rule_id
           for key, rule in request.app.state.recurring_rules.items()):
        raise HTTPException(status_code=409, detail="Для этой группы уже настроено пользовательское правило")


@router.get("/recurring-rules", response_model=list[RecurringRule])
def get_recurring_rules(request: Request) -> list[RecurringRule]:
    return list(request.app.state.recurring_rules.values())


@router.post("/recurring-rules", response_model=RecurringRule, status_code=201)
def create_recurring_rule(rule: RecurringRuleRequest, request: Request) -> RecurringRule:
    _validate_source_group(request, rule.source_group_id)
    result = RecurringRule(id=uuid4().hex, **rule.model_dump())
    request.app.state.recurring_rules[result.id] = result
    return result


@router.put("/recurring-rules/{rule_id}", response_model=RecurringRule)
def update_recurring_rule(rule_id: str, rule: RecurringRuleRequest, request: Request) -> RecurringRule:
    if rule_id not in request.app.state.recurring_rules:
        raise HTTPException(status_code=404, detail="Пользовательское правило не найдено")
    _validate_source_group(request, rule.source_group_id, exclude_rule_id=rule_id)
    result = RecurringRule(id=rule_id, **rule.model_dump())
    request.app.state.recurring_rules[rule_id] = result
    return result


@router.delete("/recurring-rules/{rule_id}", status_code=204)
def delete_recurring_rule(rule_id: str, request: Request) -> Response:
    if request.app.state.recurring_rules.pop(rule_id, None) is None:
        raise HTTPException(status_code=404, detail="Пользовательское правило не найдено")
    return Response(status_code=204)
