"""HTTP API импорта выписок."""

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from backend.schemas import CategorizationRequest, CategorizationResult, ImportResult, RecurringResult, Transaction
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
    request.state.workspace.transactions = tuple(result.transactions)
    request.state.workspace.categorizations = {}
    request.state.workspace.balance_info = result.balance_info
    request.state.workspace.last_forecast = None
    request.state.workspace.last_gaps = None
    return result


@router.get("", response_model=list[Transaction])
def get_transactions(request: Request) -> tuple[Transaction, ...]:
    return request.state.workspace.transactions


@router.post("/categorize", response_model=CategorizationResult)
async def categorize_stored_transactions(
    request: Request, selection: CategorizationRequest | None = None,
) -> CategorizationResult:
    transactions = request.state.workspace.transactions
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
    request.state.workspace.categorizations.update({item.transaction_id: item for item in result.categorizations})
    return result


@router.get("/recurring", response_model=RecurringResult)
async def get_recurring_operations(request: Request) -> RecurringResult:
    # Снимок текущей выписки; новые платежи или категории не создаются.
    return detect_recurring_transactions(request.state.workspace.transactions, request.state.workspace.categorizations)
