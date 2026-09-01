"""Strict parser for JMA prefectural forecast documents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
try:
    TOKYO = ZoneInfo("Asia/Tokyo")
except ZoneInfoNotFoundError:
    # Minimal Windows Python installations may omit the IANA database. Japan has
    # used UTC+09:00 without DST throughout the forecast horizon handled here.
    TOKYO = timezone(timedelta(hours=9), "Asia/Tokyo")

WEATHER_DAILY = "weather_daily"
PRECIPITATION_PROBABILITY = "precipitation_probability"
TEMPERATURE_DAILY = "temperature_daily"


class ForecastDataError(ValueError):
    """A response is valid JSON but not a supported, safe forecast structure."""


@dataclass(frozen=True)
class ForecastDocument:
    document_type: str
    issued_at: str
    sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ForecastRecord:
    target_code: str
    target_kind: str
    target_start: str
    target_end: str
    forecast_type: str
    weather_code: str | None = None
    weather_text: str | None = None
    precipitation_probability: int | None = None
    high_temperature: float | None = None
    low_temperature: float | None = None


def decode_forecast_json(raw: bytes) -> list[Any]:
    if not raw:
        raise ForecastDataError("空のHTTPレスポンスです")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ForecastDataError(f"予報JSONをdecodeできません: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise ForecastDataError("予報JSONのルートは空でない配列である必要があります")
    return value


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_iso(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ForecastDataError(f"{label}はISO 8601文字列である必要があります")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ForecastDataError(f"{label}が不正です: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastDataError(f"{label}にUTC offsetがありません: {value!r}")
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _local_datetime(value: object, label: str) -> datetime:
    normalized = _utc_iso(value, label)  # type: ignore[arg-type]
    return datetime.fromisoformat(normalized).astimezone(TOKYO)


def _daily_period(value: object, label: str) -> tuple[str, str]:
    local_value = _local_datetime(value, label)
    start = datetime.combine(local_value.date(), time.min, TOKYO)
    end = start + timedelta(days=1)
    return (
        start.astimezone(UTC).isoformat(timespec="seconds"),
        end.astimezone(UTC).isoformat(timespec="seconds"),
    )


def _require_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ForecastDataError(f"{label}はobjectである必要があります")
    return value


def _require_list(container: dict[str, Any], key: str, label: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise ForecastDataError(f"{label}.{key}は配列である必要があります")
    return value


def _series_area_keys(series: object) -> set[str]:
    obj = _require_dict(series, "timeSeries要素")
    result: set[str] = set()
    for area in _require_list(obj, "areas", "timeSeries要素"):
        result.update(_require_dict(area, "areas要素").keys())
    return result


def identify_documents(decoded: list[Any]) -> list[ForecastDocument]:
    documents: list[ForecastDocument] = []
    seen_types: set[str] = set()
    for index, raw_document in enumerate(decoded):
        document = _require_dict(raw_document, f"文書[{index}]")
        series = _require_list(document, "timeSeries", f"文書[{index}]")
        if not series:
            raise ForecastDataError(f"文書[{index}].timeSeriesが空です")
        key_sets = [_series_area_keys(item) for item in series]
        has_short_weather = any({"weatherCodes", "weathers"} <= keys for keys in key_sets)
        has_short_pop = any("pops" in keys and "weathers" not in keys for keys in key_sets)
        has_short_temp = any("temps" in keys for keys in key_sets)
        has_weekly_weather = any(
            {"weatherCodes", "pops"} <= keys and "weathers" not in keys
            for keys in key_sets
        )
        has_weekly_temp = any({"tempsMin", "tempsMax"} <= keys for keys in key_sets)
        short_term = has_short_weather and has_short_pop and has_short_temp
        weekly = has_weekly_weather and has_weekly_temp
        if short_term == weekly:
            raise ForecastDataError(
                f"文書[{index}]を短期予報または週間予報として一意に識別できません"
            )
        document_type = "short_term" if short_term else "weekly"
        if document_type in seen_types:
            raise ForecastDataError(f"{document_type}文書が重複しています")
        seen_types.add(document_type)
        if "publishingOffice" not in document:
            raise ForecastDataError(f"文書[{index}].publishingOfficeがありません")
        issued_at = _utc_iso(document.get("reportDatetime"), "reportDatetime")
        documents.append(
            ForecastDocument(
                document_type=document_type,
                issued_at=issued_at,
                sha256=canonical_sha256(document),
                payload=document,
            )
        )
    return documents


def _matching_series(document: dict[str, Any], required: set[str], label: str) -> dict[str, Any]:
    matches = []
    for series in _require_list(document, "timeSeries", "文書"):
        if required <= _series_area_keys(series):
            matches.append(_require_dict(series, "timeSeries要素"))
    if len(matches) != 1:
        raise ForecastDataError(f"{label}のtimeSeriesは1件必要です（実際: {len(matches)}件）")
    return matches[0]


def _area_code(area_record: dict[str, Any], label: str) -> str:
    area = _require_dict(area_record.get("area"), f"{label}.area")
    code = area.get("code")
    if not isinstance(code, str) or not code:
        raise ForecastDataError(f"{label}.area.codeが不正です")
    return code


def _aligned_values(
    area: dict[str, Any], key: str, count: int, label: str
) -> list[Any]:
    values = _require_list(area, key, label)
    if len(values) != count:
        raise ForecastDataError(
            f"{label}.{key}とtimeDefinesの配列長が一致しません: {len(values)} != {count}"
        )
    return values


def _optional_probability(value: object, label: str) -> int | None:
    if value == "" or value is None:
        return None
    if isinstance(value, bool):
        raise ForecastDataError(f"{label}が数値ではありません")
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ForecastDataError(f"{label}が数値ではありません: {value!r}") from exc
    if str(result) != str(value).strip() or not 0 <= result <= 100:
        raise ForecastDataError(f"{label}は0から100の整数である必要があります: {value!r}")
    return result


def _optional_temperature(value: object, label: str) -> float | None:
    if value == "" or value is None:
        return None
    if isinstance(value, bool):
        raise ForecastDataError(f"{label}が数値ではありません")
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ForecastDataError(f"{label}が数値ではありません: {value!r}") from exc


def _validate_code(code: str, known: set[str], kind: str) -> None:
    if code not in known:
        raise ForecastDataError(f"未知の{kind}コードです: {code}")


def _iter_areas(series: dict[str, Any], label: str) -> Iterable[tuple[int, dict[str, Any]]]:
    for index, value in enumerate(_require_list(series, "areas", label)):
        yield index, _require_dict(value, f"{label}.areas[{index}]")


def _parse_short(
    document: dict[str, Any], known_areas: set[str], known_stations: set[str]
) -> list[ForecastRecord]:
    records: list[ForecastRecord] = []
    weather_series = _matching_series(document, {"weatherCodes", "weathers"}, "短期天気")
    times = _require_list(weather_series, "timeDefines", "短期天気")
    for area_index, area in _iter_areas(weather_series, "短期天気"):
        code = _area_code(area, f"短期天気.areas[{area_index}]")
        _validate_code(code, known_areas, "予報区域")
        weather_codes = _aligned_values(area, "weatherCodes", len(times), "短期天気")
        weathers = _aligned_values(area, "weathers", len(times), "短期天気")
        for index, time_define in enumerate(times):
            start, end = _daily_period(time_define, f"短期天気.timeDefines[{index}]")
            if not isinstance(weather_codes[index], str) or not isinstance(weathers[index], str):
                raise ForecastDataError("短期天気のweatherCodes/weathersは文字列である必要があります")
            records.append(
                ForecastRecord(code, "area", start, end, WEATHER_DAILY,
                               weather_code=weather_codes[index], weather_text=weathers[index])
            )

    pop_series = _matching_series(document, {"pops"}, "短期降水確率")
    pop_times = _require_list(pop_series, "timeDefines", "短期降水確率")
    for area_index, area in _iter_areas(pop_series, "短期降水確率"):
        code = _area_code(area, f"短期降水確率.areas[{area_index}]")
        _validate_code(code, known_areas, "予報区域")
        pops = _aligned_values(area, "pops", len(pop_times), "短期降水確率")
        for index, time_define in enumerate(pop_times):
            local_start = _local_datetime(time_define, f"短期降水確率.timeDefines[{index}]")
            utc_start = local_start.astimezone(UTC)
            records.append(
                ForecastRecord(
                    code, "area", utc_start.isoformat(timespec="seconds"),
                    (utc_start + timedelta(hours=6)).isoformat(timespec="seconds"),
                    PRECIPITATION_PROBABILITY,
                    precipitation_probability=_optional_probability(
                        pops[index], f"短期降水確率.pops[{index}]"
                    ),
                )
            )

    temp_series = _matching_series(document, {"temps"}, "短期気温")
    temp_times = _require_list(temp_series, "timeDefines", "短期気温")
    for area_index, area in _iter_areas(temp_series, "短期気温"):
        code = _area_code(area, f"短期気温.areas[{area_index}]")
        _validate_code(code, known_stations, "観測地点")
        temps = _aligned_values(area, "temps", len(temp_times), "短期気温")
        by_day: dict[object, dict[str, float | None]] = {}
        for index, time_define in enumerate(temp_times):
            local_value = _local_datetime(time_define, f"短期気温.timeDefines[{index}]")
            # JMA uses 00:00 for the daily minimum and 09:00 for the daily maximum.
            # Classification is by the timestamp, never by array position.
            if local_value.time() == time(0, 0):
                key = "low"
            elif local_value.time() == time(9, 0):
                key = "high"
            else:
                raise ForecastDataError(
                    f"短期気温の安全に解釈できない時刻です: {time_define!r}"
                )
            daily = by_day.setdefault(local_value.date(), {})
            if key in daily:
                raise ForecastDataError(f"短期気温の{local_value.date()} {key}が重複しています")
            daily[key] = _optional_temperature(temps[index], f"短期気温.temps[{index}]")
        for day, values in by_day.items():
            start, end = _daily_period(f"{day.isoformat()}T00:00:00+09:00", "短期気温日")
            records.append(
                ForecastRecord(
                    code, "station", start, end, TEMPERATURE_DAILY,
                    high_temperature=values.get("high"), low_temperature=values.get("low")
                )
            )
    return records


def _parse_weekly(
    document: dict[str, Any], known_areas: set[str], known_stations: set[str]
) -> list[ForecastRecord]:
    records: list[ForecastRecord] = []
    weather_series = _matching_series(document, {"weatherCodes", "pops"}, "週間天気")
    times = _require_list(weather_series, "timeDefines", "週間天気")
    for area_index, area in _iter_areas(weather_series, "週間天気"):
        code = _area_code(area, f"週間天気.areas[{area_index}]")
        _validate_code(code, known_areas, "予報区域")
        weather_codes = _aligned_values(area, "weatherCodes", len(times), "週間天気")
        pops = _aligned_values(area, "pops", len(times), "週間天気")
        for index, time_define in enumerate(times):
            start, end = _daily_period(time_define, f"週間天気.timeDefines[{index}]")
            weather_code = weather_codes[index]
            if not isinstance(weather_code, str):
                raise ForecastDataError("週間天気.weatherCodesは文字列である必要があります")
            records.append(ForecastRecord(code, "area", start, end, WEATHER_DAILY,
                                          weather_code=weather_code))
            records.append(
                ForecastRecord(
                    code, "area", start, end, PRECIPITATION_PROBABILITY,
                    precipitation_probability=_optional_probability(
                        pops[index], f"週間天気.pops[{index}]"
                    )
                )
            )

    temp_series = _matching_series(document, {"tempsMin", "tempsMax"}, "週間気温")
    temp_times = _require_list(temp_series, "timeDefines", "週間気温")
    for area_index, area in _iter_areas(temp_series, "週間気温"):
        code = _area_code(area, f"週間気温.areas[{area_index}]")
        _validate_code(code, known_stations, "観測地点")
        mins = _aligned_values(area, "tempsMin", len(temp_times), "週間気温")
        maxes = _aligned_values(area, "tempsMax", len(temp_times), "週間気温")
        for index, time_define in enumerate(temp_times):
            start, end = _daily_period(time_define, f"週間気温.timeDefines[{index}]")
            records.append(
                ForecastRecord(
                    code, "station", start, end, TEMPERATURE_DAILY,
                    high_temperature=_optional_temperature(maxes[index], f"週間気温.tempsMax[{index}]"),
                    low_temperature=_optional_temperature(mins[index], f"週間気温.tempsMin[{index}]")
                )
            )
    return records


def parse_document(
    document: ForecastDocument,
    known_areas: set[str],
    known_stations: set[str],
) -> list[ForecastRecord]:
    if document.document_type == "short_term":
        return _parse_short(document.payload, known_areas, known_stations)
    if document.document_type == "weekly":
        return _parse_weekly(document.payload, known_areas, known_stations)
    raise ForecastDataError(f"未知の予報文書種別です: {document.document_type}")
