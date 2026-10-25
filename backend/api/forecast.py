from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas import CashGapAnalysis, ForecastEvent, ForecastResult
from backend.services.cash_gap_service import analyze_cash_gaps
from backend.services.forecast_service import forecast_cash_flow

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("", response_model=ForecastResult)
async def get_forecast(
    request: Request, as_of: date | None = None,
    opening_balance: Decimal | None = Query(default=None, allow_inf_nan=False),
) -> ForecastResult:
    try:
        result = forecast_cash_flow(request.state.workspace.transactions, as_of=as_of, opening_balance=opening_balance,
                                  categorizations=request.state.workspace.categorizations, balance_info=request.state.workspace.balance_info)
        manual = [event for event in request.state.workspace.planned_events if result.start_date <= event.date <= result.end_date]
        if manual:
            amounts = {day.date: {"income": day.income, "expense": day.expense,
                                  "regular_income": day.regular_income, "regular_expense": day.regular_expense}
                       for day in result.daily}
            events = list(result.scheduled_events)
            for event in manual:
                amounts[event.date][event.direction] += event.amount
                amounts[event.date][f"regular_{event.direction}"] += event.amount
                events.append(ForecastEvent(group_id=f"manual-{event.id}", date=event.date,
                               category=event.category, direction=event.direction, amount=event.amount))
            balance = result.summary.opening_balance
            cumulative = Decimal("0.00")
            daily = []
            for day in result.daily:
                values = amounts[day.date]
                net = values["income"] - values["expense"]
                cumulative += net
                balance_value = balance + cumulative if balance is not None else None
                daily.append(day.model_copy(update={"income": values["income"], "expense": values["expense"],
                    "net_cash_flow": net, "cumulative_cash_flow": cumulative, "balance": balance_value,
                    "regular_income": values["regular_income"], "regular_expense": values["regular_expense"]}))
            total_income = sum((d.income for d in daily), Decimal("0.00"))
            total_expense = sum((d.expense for d in daily), Decimal("0.00"))
            result = result.model_copy(update={"daily": daily, "scheduled_events": events,
                "summary": result.summary.model_copy(update={"total_income": total_income,
                    "total_expense": total_expense, "net_cash_flow": total_income-total_expense,
                    "closing_balance": balance+cumulative if balance is not None else None})})
        request.state.workspace.last_forecast = result
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.get("/cash-gaps", response_model=CashGapAnalysis)
async def get_cash_gaps(request: Request, forecast: ForecastResult = Depends(get_forecast)) -> CashGapAnalysis:
    result = analyze_cash_gaps(forecast)
    request.state.workspace.last_gaps = result
    return result
