"""
청산 가치 / 수익률 계산 로직

가정:
- 청산예상일: 납입기일 + 2년 320일 (= 1050일)
- 수익률 계산은 보수적: 1년차·2년차 100%, 3년차는 6개월(0.5년)만 반영
- 단리 누적: 청산금 = 공모가 × (1 + r1 + r2 + r3*0.5)
"""
from datetime import date, datetime, timedelta
from typing import Optional


ASSUMED_LIQUIDATION_DAYS = 2 * 365 + 320  # = 1050일
WATCH_LIST_MONTHS_BEFORE = 6


def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    if isinstance(s, date):
        return s
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(str(s)[:10], fmt).date()
        except ValueError:
            continue
    return None


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def estimated_liquidation_date(payment_date) -> Optional[date]:
    pd_ = _parse_date(payment_date)
    if not pd_:
        return None
    return pd_ + timedelta(days=ASSUMED_LIQUIDATION_DAYS)


def days_until_liquidation(payment_date, today: Optional[date] = None) -> Optional[int]:
    today = today or date.today()
    liq = estimated_liquidation_date(payment_date)
    if not liq:
        return None
    return (liq - today).days


def is_in_watchlist_window(payment_date, today: Optional[date] = None) -> bool:
    today = today or date.today()
    liq = estimated_liquidation_date(payment_date)
    if not liq:
        return False
    watch_start = add_months(liq, -WATCH_LIST_MONTHS_BEFORE)
    return watch_start <= today <= liq


def liquidation_value(offering_price, rate_y1, rate_y2, rate_y3,
                     payment_date=None, today=None) -> Optional[float]:
    """
    예상 청산금액(주당). 이율은 % 단위.
    1년차·2년차는 100%, 3년차는 6개월(0.5년)만 반영 (보수적).
    """
    if not offering_price:
        return None
    r1 = (rate_y1 or 0) / 100.0
    r2 = (rate_y2 or 0) / 100.0
    r3 = (rate_y3 or 0) / 100.0
    third_year_fraction = 0.5
    total_interest = r1 + r2 + r3 * third_year_fraction
    return offering_price * (1 + total_interest)


def annualized_return(current_price, liquidation_val, days_left) -> Optional[float]:
    if not current_price or current_price <= 0:
        return None
    if not liquidation_val or liquidation_val <= 0:
        return None
    if not days_left or days_left <= 0:
        return None
    total_return = liquidation_val / current_price
    years = days_left / 365.25
    if years <= 0:
        return None
    return (total_return ** (1.0 / years) - 1) * 100.0


def total_return(current_price, liquidation_val) -> Optional[float]:
    if not current_price or current_price <= 0 or not liquidation_val:
        return None
    return (liquidation_val / current_price - 1) * 100.0


def compute_row(spac: dict, today: Optional[date] = None) -> dict:
    today = today or date.today()
    out = dict(spac)

    liq_date = estimated_liquidation_date(spac.get("payment_date"))
    days_left = days_until_liquidation(spac.get("payment_date"), today)
    liq_val = liquidation_value(
        spac.get("offering_price"),
        spac.get("rate_y1"),
        spac.get("rate_y2"),
        spac.get("rate_y3"),
        spac.get("payment_date"),
        today,
    )
    cur_px = spac.get("current_price")
    ann = annualized_return(cur_px, liq_val, days_left) if days_left else None
    tot = total_return(cur_px, liq_val) if cur_px else None

    out["estimated_liquidation_date"] = liq_date.isoformat() if liq_date else None
    out["days_until_liquidation"] = days_left
    out["liquidation_value"] = round(liq_val, 1) if liq_val else None
    out["total_return_pct"] = round(tot, 2) if tot is not None else None
    out["annualized_return_pct"] = round(ann, 2) if ann is not None else None
    out["watchlist_window"] = is_in_watchlist_window(spac.get("payment_date"), today)
    return out
