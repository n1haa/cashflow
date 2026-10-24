from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas import CashGapAnalysis, ForecastResult
from backend.services.cash_gap_service import analyze_cash_gaps
from backend.services.forecast_service import forecast_cash_flow

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("", response_model=ForecastResult)
async def get_forecast(
    request: Request, as_of: date | None = None,
    opening_balance: Decimal | None = Query(default=None, allow_inf_nan=False),
) -> ForecastResult:
    try:
        return forecast_cash_flow(request.app.state.transactions, as_of=as_of, opening_balance=opening_balance,
                                  categorizations=request.app.state.categorizations, balance_info=request.app.state.balance_info)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.get("/cash-gaps", response_model=CashGapAnalysis)
async def get_cash_gaps(forecast: ForecastResult = Depends(get_forecast)) -> CashGapAnalysis:
    return analyze_cash_gaps(forecast)
