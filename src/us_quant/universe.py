from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import csv
import io
import json
from pathlib import Path
import tempfile
from time import sleep
from typing import Callable, Iterable
from urllib.request import Request, urlopen
from urllib.parse import urlencode


NASDAQ_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
)
OTHER_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
)
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSION_URL = (
    "https://data.sec.gov/submissions/CIK{cik:010d}.json"
)
NASDAQ_STOCK_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks?"
    "{query}"
)
DEFAULT_USER_AGENT = (
    "USQuant/0.2 local research application "
    "(contact: local-user@example.invalid)"
)


class UniverseRefreshCancelled(RuntimeError):
    """Raised when a user cancels a potentially long official-universe refresh."""

US_STATE_CODES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
        "DC",
    }
)

_NON_COMMON_NAME_MARKERS = (
    " Warrant",
    " Warrants",
    " Right",
    " Rights",
    " Unit",
    " Units",
    " Preferred",
    " Preference",
    " Debt",
    " Notes due",
    " Bond",
)


@dataclass(frozen=True, slots=True)
class UniverseRecord:
    symbol: str
    name: str
    exchange: str
    security_type: str
    sector: str = "未分类"
    leader_tier: int = 0
    country_status: str = "待核验"
    country_evidence_level: str = "unknown"
    country_code: str = ""
    business_country_code: str = ""
    incorporation_country_name: str = ""
    business_country_name: str = ""
    cik: int | None = None
    sic: str = ""
    sic_description: str = ""
    sec_source_rank: int | None = None
    eligible_for_research: bool = False
    eligible_for_trading: bool = False
    exclusion_reason: str = "中概排除证据尚未核验"
    source: str = "Nasdaq Trader"


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    generated_at: datetime
    source_timestamps: dict[str, str]
    records: tuple[UniverseRecord, ...]

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.records),
            "stocks": sum(
                row.security_type == "STK" for row in self.records
            ),
            "etfs": sum(
                row.security_type == "ETF" for row in self.records
            ),
            "verified_us": sum(
                row.country_status.startswith("美国")
                for row in self.records
            ),
            "china_excluded": sum(
                row.country_status == "中概排除"
                for row in self.records
            ),
            "research_eligible": sum(
                row.eligible_for_research for row in self.records
            ),
            "trade_eligible": sum(
                row.eligible_for_trading for row in self.records
            ),
            "excluded": sum(
                not row.eligible_for_research
                for row in self.records
            ),
        }


@dataclass(frozen=True, slots=True)
class LeaderSeed:
    symbol: str
    security_type: str
    sector: str
    leader_tier: int
    country_status: str
    note: str


def refresh_official_universe(
    *,
    cache_root: str | Path = "data/reference",
    leader_seed_path: str | Path = "configs/sector_leaders.csv",
    china_denylist_path: str | Path = (
        "configs/china_concept_denylist.csv"
    ),
    user_agent: str = DEFAULT_USER_AGENT,
    should_stop: Callable[[], bool] | None = None,
    save_snapshot: bool = True,
) -> UniverseSnapshot:
    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    sources = {
        "nasdaqlisted": (
            NASDAQ_LISTED_URL,
            root / "nasdaqlisted.txt",
        ),
        "otherlisted": (
            OTHER_LISTED_URL,
            root / "otherlisted.txt",
        ),
        "sec_tickers": (
            SEC_TICKERS_URL,
            root / "company_tickers_exchange.json",
        ),
        "nasdaq_china": (
            _nasdaq_screener_url("China"),
            root / "nasdaq_country_china.json",
        ),
        "nasdaq_hong_kong": (
            _nasdaq_screener_url("Hong Kong"),
            root / "nasdaq_country_hong_kong.json",
        ),
        "nasdaq_macau": (
            _nasdaq_screener_url("Macau"),
            root / "nasdaq_country_macau.json",
        ),
    }
    source_timestamps: dict[str, str] = {}
    contents: dict[str, bytes] = {}
    for source_name, (url, path) in sources.items():
        _raise_if_cancelled(should_stop)
        try:
            content = _download(
                url,
                user_agent=user_agent,
                timeout_seconds=8,
                max_attempts=1,
            )
            _raise_if_cancelled(should_stop)
            _atomic_write(path, content)
            source_state = datetime.now(timezone.utc).isoformat()
        except OSError:
            if not source_name.startswith("nasdaq_") or not path.exists():
                raise
            content = path.read_bytes()
            source_state = (
                "cached:"
                + datetime.fromtimestamp(
                    path.stat().st_mtime,
                    timezone.utc,
                ).isoformat()
            )
        contents[source_name] = content
        source_timestamps[source_name] = source_state

    seeds = load_leader_seeds(leader_seed_path)
    china_symbols = load_china_concept_denylist(
        china_denylist_path
    )
    for source_name in (
        "nasdaq_china",
        "nasdaq_hong_kong",
        "nasdaq_macau",
    ):
        china_symbols.update(
            _parse_nasdaq_country_screener(contents[source_name])
        )
    sec_map = _parse_sec_tickers(contents["sec_tickers"])
    rows = _parse_nasdaq_listed(contents["nasdaqlisted"])
    rows.extend(_parse_other_listed(contents["otherlisted"]))

    unique: dict[str, UniverseRecord] = {}
    for row in rows:
        _raise_if_cancelled(should_stop)
        sec = sec_map.get(row.symbol)
        if sec is not None:
            row = replace(
                row,
                cik=sec["cik"],
                sec_source_rank=sec["rank"],
            )
        seed = seeds.get(row.symbol)
        if seed is not None:
            row = _apply_seed(row, seed)
        if row.symbol in china_symbols:
            row = _exclude_china_concept(
                row,
                reason=(
                    "命中 Nasdaq Country=China/Hong Kong/Macau "
                    "或本地中概拒绝清单"
                ),
            )
        elif row.security_type == "STK" and row.leader_tier == 0:
            row = replace(
                row,
                leader_tier=3,
                country_status="未命中中概清单（仅研究）",
                country_evidence_level="denylist_only",
                eligible_for_research=True,
                eligible_for_trading=False,
                exclusion_reason=(
                    "广域非中概研究样本；不是龙头/优质二线交易池"
                ),
                source=f"{row.source} + Nasdaq Country 筛选",
            )
        unique.setdefault(row.symbol, row)

    snapshot = UniverseSnapshot(
        generated_at=datetime.now(timezone.utc),
        source_timestamps=source_timestamps,
        records=tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    item.leader_tier == 0,
                    item.leader_tier or 99,
                    item.sec_source_rank or 10**9,
                    item.symbol,
                ),
            )
        ),
    )
    _raise_if_cancelled(should_stop)
    if save_snapshot:
        save_universe_snapshot(snapshot, root / "universe.json")
    return snapshot


def enrich_us_profiles(
    snapshot: UniverseSnapshot,
    *,
    cache_root: str | Path = "data/reference/sec_profiles",
    max_new_profiles: int = 250,
    request_interval_seconds: float = 0.12,
    user_agent: str = DEFAULT_USER_AGENT,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> UniverseSnapshot:
    if max_new_profiles < 0:
        raise ValueError("max_new_profiles cannot be negative")
    cache = Path(cache_root)
    cache.mkdir(parents=True, exist_ok=True)
    candidates = [
        row
        for row in snapshot.records
        if row.security_type == "STK" and row.cik is not None
    ]
    candidates.sort(
        key=lambda row: (
            row.leader_tier == 0,
            row.leader_tier or 99,
            row.sec_source_rank or 10**9,
            row.symbol,
        )
    )
    updated: dict[str, UniverseRecord] = {
        row.symbol: row for row in snapshot.records
    }
    downloaded = 0
    total = min(len(candidates), max_new_profiles)
    for index, row in enumerate(candidates):
        _raise_if_cancelled(should_stop)
        profile_path = cache / f"CIK{row.cik:010d}.json"
        if profile_path.exists():
            payload = json.loads(profile_path.read_text(encoding="utf-8"))
        else:
            if downloaded >= max_new_profiles:
                continue
            try:
                payload_bytes = _download(
                    SEC_SUBMISSION_URL.format(cik=row.cik),
                    user_agent=user_agent,
                    timeout_seconds=8,
                    max_attempts=1,
                )
                _raise_if_cancelled(should_stop)
            except OSError as error:
                if progress is not None:
                    progress(
                        min(index + 1, total),
                        total,
                        f"{row.symbol} 暂时失败: {error}",
                    )
                continue
            _atomic_write(profile_path, payload_bytes)
            payload = json.loads(payload_bytes)
            downloaded += 1
            if request_interval_seconds:
                sleep(request_interval_seconds)
                _raise_if_cancelled(should_stop)
        updated[row.symbol] = _apply_sec_profile(row, payload)
        if progress is not None:
            progress(
                min(index + 1, total),
                total,
                row.symbol,
            )

    enriched = UniverseSnapshot(
        generated_at=datetime.now(timezone.utc),
        source_timestamps=snapshot.source_timestamps,
        records=tuple(updated[row.symbol] for row in snapshot.records),
    )
    _raise_if_cancelled(should_stop)
    reference_root = cache.parent
    save_universe_snapshot(enriched, reference_root / "universe.json")
    return enriched


def load_universe_snapshot(
    path: str | Path = "data/reference/universe.json",
) -> UniverseSnapshot:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return UniverseSnapshot(
        generated_at=datetime.fromisoformat(payload["generated_at"]),
        source_timestamps=dict(payload["source_timestamps"]),
        records=tuple(
            replace(
                UniverseRecord(**row),
                sector=normalize_sector(row.get("sector", "未分类")),
            )
            for row in payload["records"]
        ),
    )


def save_universe_snapshot(
    snapshot: UniverseSnapshot,
    path: str | Path,
) -> Path:
    target = Path(path)
    payload = {
        "generated_at": snapshot.generated_at.isoformat(),
        "source_timestamps": snapshot.source_timestamps,
        "summary": snapshot.summary(),
        "records": [asdict(row) for row in snapshot.records],
    }
    _atomic_write(
        target,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
    )
    return target


def load_leader_seeds(
    path: str | Path,
) -> dict[str, LeaderSeed]:
    with Path(path).open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)
        return {
            row["symbol"].strip().upper(): LeaderSeed(
                symbol=row["symbol"].strip().upper(),
                security_type=row["security_type"].strip().upper(),
                sector=row["sector"].strip(),
                leader_tier=int(row["leader_tier"]),
                country_status=row["country_status"].strip(),
                note=row.get("note", "").strip(),
            )
            for row in reader
            if row.get("symbol", "").strip()
        }


def load_china_concept_denylist(
    path: str | Path,
) -> set[str]:
    target = Path(path)
    if not target.exists():
        return set()
    with target.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return {
            row["symbol"].strip().upper()
            for row in reader
            if row.get("symbol", "").strip()
        }


def prioritized_research_symbols(
    snapshot: UniverseSnapshot,
    *,
    limit: int | None = 250,
) -> tuple[str, ...]:
    if limit is not None and limit <= 0:
        return ()
    eligible = [
        row
        for row in snapshot.records
        if row.eligible_for_research
    ]
    eligible.sort(
        key=lambda row: (
            row.leader_tier == 0,
            row.leader_tier or 99,
            row.sec_source_rank or 10**9,
            row.symbol,
        )
    )
    selected = eligible if limit is None else eligible[:limit]
    return tuple(row.symbol for row in selected)


def _apply_seed(
    row: UniverseRecord,
    seed: LeaderSeed,
) -> UniverseRecord:
    approved_country = seed.country_status in {
        "美国",
        "明确非中国",
    }
    research_ok = approved_country and seed.leader_tier in {1, 2}
    return replace(
        row,
        security_type=seed.security_type,
        sector=seed.sector,
        leader_tier=seed.leader_tier,
        country_status=(
            "美国注册"
            if seed.country_status == "美国"
            else seed.country_status
        ),
        country_evidence_level="manual_verified_non_china",
        eligible_for_research=research_ok,
        eligible_for_trading=research_ok,
        exclusion_reason=(
            ""
            if research_ok
            else seed.note or "未进入核心或优质二线池"
        ),
        source=f"{row.source} + 人工复核种子",
    )


def _apply_sec_profile(
    row: UniverseRecord,
    payload: dict,
) -> UniverseRecord:
    incorporation = str(
        payload.get("stateOfIncorporation") or ""
    ).upper()
    business_country = str(
        (
            payload.get("addresses", {})
            .get("business", {})
            .get("stateOrCountry")
        )
        or ""
    ).upper()
    incorporation_name = str(
        payload.get("stateOfIncorporationDescription") or ""
    ).strip()
    business_country_name = str(
        (
            payload.get("addresses", {})
            .get("business", {})
            .get("stateOrCountryDescription")
        )
        or ""
    ).strip()
    recent_forms = set(
        (
            payload.get("filings", {})
            .get("recent", {})
            .get("form", [])
        )
        or []
    )
    sic = str(payload.get("sic") or "")
    sic_description = str(payload.get("sicDescription") or "")
    sector = (
        row.sector
        if row.sector != "未分类"
        else sector_from_sic(sic)
    )
    if _has_china_evidence(
        incorporation,
        business_country,
        incorporation_name,
        business_country_name,
    ):
        return _exclude_china_concept(
            replace(
                row,
                sector=sector,
                country_code=incorporation,
                business_country_code=business_country,
                incorporation_country_name=incorporation_name,
                business_country_name=business_country_name,
                sic=sic,
                sic_description=sic_description,
                source=f"{row.source} + SEC EDGAR",
            ),
            reason=(
                "SEC 注册地或主营地址出现中国大陆/香港/澳门证据"
            ),
        )
    if (
        row.leader_tier in {1, 2}
        and row.eligible_for_research
        and row.country_status == "明确非中国"
    ):
        return replace(
            row,
            sector=sector,
            country_code=incorporation,
            business_country_code=business_country,
            incorporation_country_name=incorporation_name,
            business_country_name=business_country_name,
            sic=sic,
            sic_description=sic_description,
            exclusion_reason="",
            source=f"{row.source} + SEC EDGAR",
            country_evidence_level="verified_non_china",
        )
    incorporation_is_verified = (
        incorporation in US_STATE_CODES
        or (
            not incorporation
            and row.country_status == "美国注册"
            and row.leader_tier in {1, 2}
        )
    )
    domestic_filing_is_verified = (
        "10-K" in recent_forms
        or (
            row.country_status == "美国注册"
            and row.leader_tier in {1, 2}
            and incorporation in US_STATE_CODES
            and business_country in US_STATE_CODES
            and "20-F" not in recent_forms
        )
    )
    if (
        incorporation_is_verified
        and business_country in US_STATE_CODES
        and domestic_filing_is_verified
        and "20-F" not in recent_forms
    ):
        leader_tier = row.leader_tier or 3
        research_ok = leader_tier in {1, 2, 3}
        trade_ok = leader_tier in {1, 2}
        return replace(
            row,
            sector=sector,
            leader_tier=leader_tier,
            country_status=(
                "美国主营地址+国内10-K（注册地字段空）"
                if not incorporation
                else "美国注册+美国主营地址"
            ),
            country_evidence_level="verified_non_china",
            country_code=incorporation,
            business_country_code=business_country,
            incorporation_country_name=incorporation_name,
            business_country_name=business_country_name,
            sic=sic,
            sic_description=sic_description,
            eligible_for_research=research_ok,
            eligible_for_trading=trade_ok,
            exclusion_reason=(
                "" if trade_ok else "广域研究样本；尚未进入龙头/二线交易池"
            ),
            source=f"{row.source} + SEC EDGAR",
        )
    leader_tier = row.leader_tier or 3
    trade_ok = (
        leader_tier in {1, 2}
        and row.eligible_for_trading
    )
    evidence = (
        business_country_name
        or incorporation_name
        or business_country
        or incorporation
        or "Nasdaq 非中概筛选"
    )
    return replace(
        row,
        sector=sector,
        leader_tier=leader_tier,
        country_status=f"非中概证据：{evidence}",
        country_evidence_level="verified_non_china",
        country_code=incorporation,
        business_country_code=business_country,
        incorporation_country_name=incorporation_name,
        business_country_name=business_country_name,
        sic=sic,
        sic_description=sic_description,
        eligible_for_research=True,
        eligible_for_trading=trade_ok,
        exclusion_reason=(
            ""
            if trade_ok
            else "广域非中概研究样本；不是龙头/优质二线交易池"
        ),
        source=f"{row.source} + SEC EDGAR",
    )


def sector_from_sic(sic: str) -> str:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "未分类"
    if 100 <= code <= 999:
        return "原材料"
    if 1000 <= code <= 1499:
        return "能源" if 1300 <= code <= 1399 else "原材料"
    if 1500 <= code <= 1799:
        return "工业"
    if 2000 <= code <= 2399:
        return "日常消费"
    if 2400 <= code <= 3999:
        if 2830 <= code <= 2839 or 3840 <= code <= 3859:
            return "医疗保健"
        if 3570 <= code <= 3699:
            return "信息技术"
        return "工业"
    if 4000 <= code <= 4899:
        if 4800 <= code <= 4899:
            return "通信服务"
        return "工业"
    if 4900 <= code <= 4999:
        return "公用事业"
    if 5000 <= code <= 5999:
        return "消费"
    if 6000 <= code <= 6799:
        if 6500 <= code <= 6599:
            return "房地产"
        return "金融"
    if 7000 <= code <= 8999:
        if 8000 <= code <= 8099:
            return "医疗保健"
        if 7370 <= code <= 7379:
            return "信息技术"
        return "通信服务"
    return "未分类"


def normalize_sector(value: str) -> str:
    aliases = {
        "能源/原材料": "能源",
        "工业/可选消费": "工业",
        "通信/服务": "通信服务",
        "消费": "可选消费",
    }
    cleaned = str(value or "未分类").strip()
    return aliases.get(cleaned, cleaned)


def _exclude_china_concept(
    row: UniverseRecord,
    *,
    reason: str,
) -> UniverseRecord:
    return replace(
        row,
        country_status="中概排除",
        country_evidence_level="china_evidence",
        eligible_for_research=False,
        eligible_for_trading=False,
        exclusion_reason=reason,
        source=f"{row.source} + 中概排除门",
    )


def _has_china_evidence(*values: str) -> bool:
    normalized = " | ".join(values).upper()
    if any(
        marker in normalized
        for marker in (
            "CHINA",
            "HONG KONG",
            "MACAU",
            "PEOPLE'S REPUBLIC",
        )
    ):
        return True
    codes = {
        value.strip().upper()
        for value in values
        if len(value.strip()) <= 3
    }
    return bool(codes & {"E9", "K3"})


def _nasdaq_screener_url(country: str) -> str:
    query = urlencode(
        {
            "tableonly": "true",
            "limit": 500,
            "offset": 0,
            "country": country,
        }
    )
    return NASDAQ_STOCK_SCREENER_URL.format(query=query)


def _parse_nasdaq_country_screener(
    content: bytes,
) -> set[str]:
    payload = json.loads(content)
    rows = (
        (payload.get("data") or {})
        .get("table", {})
        .get("rows", [])
    )
    return {
        str(row.get("symbol") or "").strip().upper()
        for row in rows
        if str(row.get("symbol") or "").strip()
    }


def _parse_sec_tickers(content: bytes) -> dict[str, dict[str, int]]:
    payload = json.loads(content)
    fields = list(payload["fields"])
    ticker_index = fields.index("ticker")
    cik_index = fields.index("cik")
    return {
        str(row[ticker_index]).upper(): {
            "cik": int(row[cik_index]),
            "rank": rank,
        }
        for rank, row in enumerate(payload["data"], start=1)
    }


def _parse_nasdaq_listed(content: bytes) -> list[UniverseRecord]:
    reader = csv.DictReader(
        io.StringIO(content.decode("utf-8-sig")),
        delimiter="|",
    )
    rows: list[UniverseRecord] = []
    for raw in reader:
        symbol = (raw.get("Symbol") or "").strip().upper()
        name = (raw.get("Security Name") or "").strip()
        if (
            not symbol
            or symbol.startswith("FILE CREATION TIME")
            or raw.get("Test Issue") == "Y"
            or raw.get("Financial Status") not in {"", "N"}
        ):
            continue
        security_type = "ETF" if raw.get("ETF") == "Y" else "STK"
        if security_type == "STK" and not _looks_like_common_stock(
            symbol, name
        ):
            continue
        rows.append(
            UniverseRecord(
                symbol=symbol,
                name=name,
                exchange="NASDAQ",
                security_type=security_type,
            )
        )
    return rows


def _parse_other_listed(content: bytes) -> list[UniverseRecord]:
    reader = csv.DictReader(
        io.StringIO(content.decode("utf-8-sig")),
        delimiter="|",
    )
    exchange_names = {
        "A": "NYSE American",
        "N": "NYSE",
        "P": "NYSE Arca",
        "Z": "Cboe",
        "V": "IEX",
    }
    rows: list[UniverseRecord] = []
    for raw in reader:
        symbol = (raw.get("ACT Symbol") or "").strip().upper()
        name = (raw.get("Security Name") or "").strip()
        if (
            not symbol
            or symbol.startswith("FILE CREATION TIME")
            or raw.get("Test Issue") == "Y"
        ):
            continue
        security_type = "ETF" if raw.get("ETF") == "Y" else "STK"
        if security_type == "STK" and not _looks_like_common_stock(
            symbol, name
        ):
            continue
        rows.append(
            UniverseRecord(
                symbol=symbol,
                name=name,
                exchange=exchange_names.get(
                    (raw.get("Exchange") or "").strip(),
                    (raw.get("Exchange") or "").strip(),
                ),
                security_type=security_type,
            )
        )
    return rows


def _looks_like_common_stock(symbol: str, name: str) -> bool:
    if not symbol.replace(".", "").replace("-", "").isalnum():
        return False
    return not any(marker.lower() in name.lower() for marker in _NON_COMMON_NAME_MARKERS)


def _raise_if_cancelled(
    should_stop: Callable[[], bool] | None,
) -> None:
    if should_stop is not None and should_stop():
        raise UniverseRefreshCancelled("已取消官方标的刷新")


def _download(
    url: str,
    *,
    user_agent: str,
    timeout_seconds: float = 15,
    max_attempts: int = 3,
) -> bytes:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    last_error: OSError | None = None
    for attempt in range(max_attempts):
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "identity",
        }
        if "api.nasdaq.com" in url:
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138 Safari/537.36"
                    ),
                    "Origin": "https://www.nasdaq.com",
                    "Referer": (
                        "https://www.nasdaq.com/market-activity/"
                        "stocks/screener"
                    ),
                }
            )
        request = Request(
            url,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except OSError as error:
            last_error = error
            if attempt < max_attempts - 1:
                sleep(0.75 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def count_by(
    records: Iterable[UniverseRecord],
    attribute: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in records:
        key = str(getattr(row, attribute))
        counts[key] = counts.get(key, 0) + 1
    return counts
