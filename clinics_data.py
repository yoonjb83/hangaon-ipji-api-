"""
경량 경쟁분석 (DB 없이 clinics.json 사용)
- 시작 시 clinics.json 을 메모리에 로드
- 하버사인 거리로 반경 내 한방기관 카운트 (PostGIS 대체)
- 데이터가 작아(약 0.5MB / 1.5만건) 매 요청 전수 계산해도 즉시 응답
"""
import os
import json
import math
import datetime as dt

_DATA = None
_GENERATED_AT = None

# clinics.json 위치 (이 파일과 같은 폴더)
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clinics.json")


def _load():
    global _DATA, _GENERATED_AT
    if _DATA is not None:
        return
    with open(_PATH, encoding="utf-8") as f:
        obj = json.load(f)
    _GENERATED_AT = obj.get("generated_at")
    # 각 항목을 (lat, lng, kind, estb_date|None) 로 보관
    data = []
    for lat, lng, kind, estb in obj["clinics"]:
        d = None
        if estb:
            try:
                d = dt.date.fromisoformat(estb)
            except ValueError:
                d = None
        data.append((lat, lng, kind, d))
    _DATA = data


def count_loaded():
    _load()
    return len(_DATA)


def generated_at():
    _load()
    return _GENERATED_AT


# 위도 1도 ≈ 111,320m. 경도는 위도에 따라 cos 보정.
def _bbox(lat, lng, radius_m):
    dlat = radius_m / 111320.0
    dlng = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def _haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def analyze_competition(lat: float, lng: float, radius_m: int):
    _load()
    lat_lo, lat_hi, lng_lo, lng_hi = _bbox(lat, lng, radius_m)
    three_years_ago = dt.date.today() - dt.timedelta(days=365 * 3)

    total = clinic_cnt = hospital_cnt = opened_3y = 0
    nearest = None
    for plat, plng, kind, estb in _DATA:
        # 1차: 사각형으로 빠르게 거르기
        if not (lat_lo <= plat <= lat_hi and lng_lo <= plng <= lng_hi):
            continue
        # 2차: 정확한 원형 거리
        dist = _haversine_m(lat, lng, plat, plng)
        if dist > radius_m:
            continue
        total += 1
        if kind == 1:
            hospital_cnt += 1
        else:
            clinic_cnt += 1
        if estb and estb > three_years_ago:
            opened_3y += 1
        if nearest is None or dist < nearest:
            nearest = dist

    turnover_ratio = round(opened_3y / total, 2) if total else None
    return {
        "competitors": total,
        "clinic_cnt": clinic_cnt,
        "hospital_cnt": hospital_cnt,
        "opened_last_3y": opened_3y,
        "turnover_ratio": turnover_ratio,
        "nearest_competitor_m": round(nearest) if nearest is not None else None,
    }
