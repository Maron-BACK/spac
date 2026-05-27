"""
스팩 트래커 데이터 수집 파이프라인
"""
from __future__ import annotations
import re
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

import db
import calc

log = logging.getLogger("pipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# =========================================================================
# 1) KRX 상장 스팩 동기화
# =========================================================================

def sync_krx() -> dict:
    try:
        import FinanceDataReader as fdr
    except ImportError:
        return {"ok": False, "msg": "FinanceDataReader 미설치"}

    df = fdr.StockListing("KRX")
    spac_df = df[df["Name"].str.contains("스팩", na=False)].copy()
    log.info(f"  스팩 {len(spac_df)}개 발견")

    today_str = date.today().isoformat()
    found_tickers = set()
    added = 0
    updated = 0

    existing = {s["ticker"]: s for s in db.get_all_spacs(include_delisted=True)}

    for _, row in spac_df.iterrows():
        ticker = str(row["Code"]).zfill(6)
        name = str(row["Name"]).strip()
        found_tickers.add(ticker)

        cur_px = None
        try:
            px_df = fdr.DataReader(ticker, start=(date.today() - timedelta(days=10)))
            if len(px_df) > 0:
                cur_px = float(px_df["Close"].iloc[-1])
        except Exception:
            pass

        payload = {
            "ticker": ticker,
            "name": name,
            "current_price": cur_px,
            "last_synced": datetime.now().isoformat(timespec="seconds"),
        }
        if ticker not in existing:
            payload["offering_price"] = 2000.0
            added += 1
        else:
            updated += 1

        db.upsert_spac(payload)
        db.clear_delisted_if_relisted(ticker)

    delisted = 0
    for s in db.get_all_spacs(include_delisted=False):
        if s["ticker"] not in found_tickers:
            db.mark_delisted(s["ticker"], today_str)
            delisted += 1

    msg = f"신규 {added}건, 갱신 {updated}건, 폐지마킹 {delisted}건"
    return {"ok": True, "msg": msg, "added": added, "updated": updated, "delisted": delisted}


# =========================================================================
# 2) KSFC (한국증권금융) 12개월 거치금 금리
# =========================================================================

KSFC_URL = "https://www.ksfc.co.kr:4443/product/rate/deposit.do"
KSFC_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def fetch_ksfc_rate() -> dict:
    """기업인수목적회사예수금 12개월 거치식 금리 추출"""
    try:
        resp = requests.get(KSFC_URL, headers=KSFC_HEADERS, timeout=15, verify=True)
        resp.raise_for_status()
    except Exception as e:
        return {"ok": False, "msg": f"KSFC 페이지 접근 실패: {e}"}

    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    rate = None
    note = ""

    target_keywords = ["기업인수목적회사", "기업 인수목적회사", "기업인수목적"]
    for tbl in soup.find_all("table"):
        tbl_text = tbl.get_text(" ", strip=True)
        if not any(k in tbl_text for k in target_keywords):
            continue
        for tr in tbl.find_all("tr"):
            row_text = tr.get_text(" ", strip=True)
            if "12개월" in row_text or "12 개월" in row_text:
                rates_in_row = re.findall(r"(\d+\.\d+)\s*%", row_text)
                if not rates_in_row:
                    rates_in_row = re.findall(r"(\d+\.\d+)", row_text)
                for candidate in rates_in_row:
                    val = float(candidate)
                    if 0.5 < val < 10:
                        rate = val
                        note = "기업인수목적회사 12개월 거치식"
                        break
            if rate is not None:
                break
        if rate is not None:
            break

    if rate is None:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"12\s*개월[^0-9%]{0,40}(\d+\.\d+)\s*%", text)
        if m:
            val = float(m.group(1))
            if 0.5 < val < 10:
                rate = val
                note = "전체 텍스트 추출"

    if rate is None:
        return {"ok": False, "msg": "KSFC 페이지에서 12개월 금리를 찾지 못함."}

    today_str = date.today().isoformat()
    db.upsert_ksfc_rate(today_str, rate)
    return {"ok": True, "msg": f"KSFC 12개월: {rate}% ({note})", "rate": rate, "date": today_str}


# =========================================================================
# 3) DART 보강
# =========================================================================

def _strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(" ", strip=True)


def find_payment_date(dart, search_key, ticker):
    """증권발행실적보고서에서 납입기일 추출."""
    try:
        filings = dart.list(search_key, start="2018-01-01", end=date.today().isoformat(), kind="C")
    except Exception:
        try:
            filings = dart.list(search_key, start="2018-01-01")
        except Exception as e:
            return None, f"DART 검색 실패: {e}"

    if filings is None or len(filings) == 0:
        return None, "DART에서 공시 못 찾음"

    candidates = []
    for _, f in filings.iterrows():
        rname = str(f.get("report_nm", ""))
        if "증권발행실적" in rname:
            candidates.append(f)

    if not candidates:
        return None, "증권발행실적보고서 없음"

    candidates.sort(key=lambda x: str(x.get("rcept_dt", "")))
    for f in candidates:
        try:
            doc = dart.document(f["rcept_no"])
            text = _strip_html(doc)
        except Exception:
            continue

        for m in re.finditer(
            r"납입\s*기일[^0-9]{0,80}(\d{4})\s*[년.\-\/]\s*(\d{1,2})\s*[월.\-\/]\s*(\d{1,2})",
            text
        ):
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            if 2015 <= int(y) <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y}-{mo:02d}-{d:02d}", f"증권발행실적({f.get('rcept_dt')})"

    return None, f"증권발행실적보고서 {len(candidates)}건 있으나 패턴 매칭 실패"


RATE_CHANGE_KEYWORDS = [
    "기업인수목적회사의 예치ㆍ신탁계약 내용변경",
    "기업인수목적회사의 예치·신탁계약 내용변경",
    "기업인수목적회사의 예치/신탁계약 내용변경",
    "예치ㆍ신탁계약 내용변경",
    "예치·신탁계약 내용변경",
    "예치ㆍ신탁계약",
    "예치·신탁계약",
]


def _matches_rate_change_report(report_nm):
    return any(kw in report_nm for kw in RATE_CHANGE_KEYWORDS)


def find_rate_changes(dart, search_key):
    """예치/신탁계약 변경공시에서 이율 변경 이력 추출."""
    try:
        filings = dart.list(search_key, start="2018-01-01", end=date.today().isoformat())
    except Exception as e:
        return [], f"DART 검색 실패: {e}"

    if filings is None or len(filings) == 0:
        return [], "공시 없음"

    candidates = []
    for _, f in filings.iterrows():
        rname = str(f.get("report_nm", ""))
        if _matches_rate_change_report(rname):
            candidates.append(f)

    if not candidates:
        return [], "예치/신탁계약 변경공시 없음"

    changes = []
    for f in candidates:
        rcept_no = f["rcept_no"]
        rcept_dt = str(f.get("rcept_dt", ""))
        rname = str(f.get("report_nm", ""))

        try:
            doc = dart.document(rcept_no)
            text = _strip_html(doc)
        except Exception:
            continue

        rate_before = rate_after = None
        m = re.search(
            r"변경\s*전\s*[:：]?\s*(\d+\.\d+)\s*%[^%]{0,40}변경\s*후\s*[:：]?\s*(\d+\.\d+)\s*%",
            text
        )
        if m:
            rate_before = float(m.group(1))
            rate_after = float(m.group(2))
        else:
            m_b = re.search(r"변경\s*전\s*[:：(]\s*(\d+\.\d+)\s*%", text)
            m_a = re.search(r"변경\s*후\s*[:：(]\s*(\d+\.\d+)\s*%", text)
            if m_b and m_a:
                rate_before = float(m_b.group(1))
                rate_after = float(m_a.group(1))

        if rate_before is None or rate_after is None:
            continue

        change_date = None
        for m_d in re.finditer(
            r"변경\s*일자?\s*[:：]?\s*(\d{4})\s*[년.\-\/]\s*(\d{1,2})\s*[월.\-\/]\s*(\d{1,2})",
            text
        ):
            y, mo, d = m_d.group(1), int(m_d.group(2)), int(m_d.group(3))
            if 2015 <= int(y) <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                change_date = f"{y}-{mo:02d}-{d:02d}"
                break

        if not change_date and len(rcept_dt) == 8:
            change_date = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"

        if change_date:
            changes.append({
                "date": change_date,
                "before": rate_before,
                "after": rate_after,
                "rcept_no": rcept_no,
                "report_nm": rname,
            })

    changes.sort(key=lambda x: x["date"])
    if not changes:
        return [], f"변경공시 {len(candidates)}건 있으나 이율 패턴 매칭 실패"
    return changes, f"변경공시 {len(changes)}건 파싱 성공"


def assign_rates_by_year(payment_date, rate_changes, ksfc_rate):
    """납입기일 + 변경공시들 → 1/2/3년차 이율 매핑."""
    out = {"rate_y1": None, "rate_y2": None, "rate_y3": None,
           "rate_source": None, "rate_note": None}

    if not payment_date:
        if ksfc_rate is not None:
            out.update({"rate_y1": ksfc_rate, "rate_y2": ksfc_rate, "rate_y3": ksfc_rate,
                        "rate_source": "KSFC",
                        "rate_note": f"KSFC {ksfc_rate}% (납입기일 없음)"})
        return out

    pd_ = calc._parse_date(payment_date)
    if not pd_:
        return out

    year_starts = [pd_, pd_ + timedelta(days=365), pd_ + timedelta(days=730)]
    year_keys = ["rate_y1", "rate_y2", "rate_y3"]

    if not rate_changes:
        if ksfc_rate is not None:
            for k in year_keys:
                out[k] = ksfc_rate
            out["rate_source"] = "KSFC"
            out["rate_note"] = f"KSFC {ksfc_rate}% (DART 변경공시 없음)"
        return out

    def rate_for_year_idx(year_idx):
        """
        year_idx: 0=1년차, 1=2년차, 2=3년차
        변경일자가 속한 연차부터 '변경 후' 이율 적용.
        예: 변경일자가 2년차 중간에 있으면, 2년차 전체가 변경 후 이율.
        """
        current = rate_changes[0]["before"]
        for ch in rate_changes:
            ch_d = calc._parse_date(ch["date"])
            if not ch_d:
                continue
            ch_year = max(0, (ch_d - pd_).days // 365)
            if ch_year <= year_idx:
                current = ch["after"]
        return current

    for i, k in enumerate(year_keys):
        out[k] = rate_for_year_idx(i)

    out["rate_source"] = "DART"
    summary = ", ".join(
        f"{ch['date']}: {ch['before']}→{ch['after']}%" for ch in rate_changes
    )
    out["rate_note"] = f"DART 변경공시 {len(rate_changes)}건 ({summary})"
    return out


def enrich_with_dart(api_key, only_missing=True):
    if not api_key:
        return {"ok": False, "msg": "API 키가 없습니다."}

    try:
        import OpenDartReader
    except ImportError:
        return {"ok": False, "msg": "OpenDartReader 미설치"}

    try:
        dart = OpenDartReader(api_key)
    except Exception as e:
        return {"ok": False, "msg": f"OpenDartReader 초기화 실패: {e}"}

    _, ksfc_rate = db.get_latest_ksfc_rate()

    spacs = db.get_all_spacs(include_delisted=False)
    processed = 0
    paydate_ok = 0
    rate_dart_ok = 0
    rate_ksfc_ok = 0
    details = []

    for s in spacs:
        if s.get("manual_override"):
            continue
        if only_missing and s.get("payment_date") and s.get("rate_y1") is not None:
            continue

        ticker = s["ticker"]
        name = s["name"]
        processed += 1
        detail = {"ticker": ticker, "name": name, "steps": []}

        corp_code = None
        try:
            corp_code = dart.find_corp_code(name)
        except Exception:
            pass
        search_key = corp_code or name
        detail["steps"].append(f"corp_code={corp_code or '없음(이름검색)'}")

        payment_date = s.get("payment_date")
        if not payment_date:
            try:
                payment_date, msg = find_payment_date(dart, search_key, ticker)
                detail["steps"].append(f"납입기일: {msg}")
                if payment_date:
                    paydate_ok += 1
            except Exception as e:
                detail["steps"].append(f"납입기일 오류: {e}")

        try:
            rate_changes, msg = find_rate_changes(dart, search_key)
            detail["steps"].append(f"이율공시: {msg}")
        except Exception as e:
            rate_changes = []
            detail["steps"].append(f"이율공시 오류: {e}")

        rate_data = assign_rates_by_year(payment_date, rate_changes, ksfc_rate)
        if rate_data["rate_source"] == "DART":
            rate_dart_ok += 1
        elif rate_data["rate_source"] == "KSFC":
            rate_ksfc_ok += 1

        payload = {"ticker": ticker, "offering_price": s.get("offering_price") or 2000.0}
        if payment_date:
            payload["payment_date"] = payment_date
        payload.update(rate_data)
        db.upsert_spac(payload)

        details.append(detail)

    msg = (f"처리 {processed}건 | 납입기일 추출 {paydate_ok}건 | "
           f"이율 DART {rate_dart_ok}건 | 이율 KSFC fallback {rate_ksfc_ok}건")
    return {"ok": True, "msg": msg,
            "processed": processed, "paydate_ok": paydate_ok,
            "rate_dart_ok": rate_dart_ok, "rate_ksfc_ok": rate_ksfc_ok,
            "details": details[:20]}


def auto_delist_overdue():
    """
    청산예상일(납입기일 + 1050일)이 이미 지난 종목을 자동으로 폐지 마킹.
    KRX 목록에 남아있어도 보통 거래정지/실질상폐 상태인 경우가 많아서
    활성 종목 리스트에서 제외시키는 게 더 안전함.
    수동 보정(manual_override=1)된 종목은 건드리지 않음.
    """
    today = date.today()
    today_str = today.isoformat()

    delisted_names = []
    for s in db.get_all_spacs(include_delisted=False):
        if s.get("manual_override"):
            continue
        pd_str = s.get("payment_date")
        if not pd_str:
            continue
        liq_date = calc.estimated_liquidation_date(pd_str)
        if liq_date and liq_date < today:
            db.mark_delisted(s["ticker"], today_str)
            delisted_names.append(f"{s['ticker']} {s['name']}(청산예상 {liq_date})")

    msg = f"청산예상일 경과 자동 폐지 {len(delisted_names)}건"
    if delisted_names:
        sample = ", ".join(delisted_names[:3])
        msg += f" - 예: {sample}"
        if len(delisted_names) > 3:
            msg += f" 외 {len(delisted_names) - 3}건"
    return {"ok": True, "msg": msg, "delisted": delisted_names}


def fill_missing_rates_from_ksfc():
    _, rate = db.get_latest_ksfc_rate()
    if rate is None:
        return {"ok": False, "msg": "KSFC 금리 없음"}

    spacs = db.get_all_spacs(include_delisted=False)
    filled = 0
    for s in spacs:
        if s.get("manual_override"):
            continue
        if s.get("rate_y1") is not None:
            continue
        db.upsert_spac({
            "ticker": s["ticker"],
            "rate_y1": rate, "rate_y2": rate, "rate_y3": rate,
            "rate_source": "KSFC",
            "rate_note": f"KSFC {rate}% (잔여 fallback)",
        })
        filled += 1

    return {"ok": True, "msg": f"KSFC 잔여 fallback {filled}건"}


def refresh_all(dart_api_key=None):
    db.init_db()
    results = []

    r = sync_krx()
    results.append(("KRX 종목 동기화", r))

    r = fetch_ksfc_rate()
    results.append(("KSFC 금리 조회", r))

    if dart_api_key:
        r = enrich_with_dart(dart_api_key, only_missing=False)
        results.append(("DART 공시 보강", r))
    else:
        results.append(("DART 공시 보강", {"ok": False, "msg": "API 키 미입력"}))

    r = fill_missing_rates_from_ksfc()
    results.append(("KSFC 잔여 fallback", r))

    # 마지막: 청산예상일 경과 종목 자동 폐지 (납입기일 정보가 다 채워진 뒤에)
    r = auto_delist_overdue()
    results.append(("청산예상일 경과 자동 폐지", r))

    return results


if __name__ == "__main__":
    db.init_db()
    for label, res in refresh_all():
        print(f"[{label}] {res.get('msg','-')}")
