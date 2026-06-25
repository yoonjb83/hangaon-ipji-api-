"""
경량 자보(교통사고) 수요 분석 (DB 없이 accidents.json 사용)
- 시작 시 accidents.json 을 메모리에 로드 (전국 사고다발지역 약 2,400곳)
- 하버사인 거리로 반경 내 다발지역의 발생건수·사상자를 거리감쇠 가중 합산
- 데이터: 도로교통공단 '지자체별 교통사고 다발지역(TOP3)' 2019~2023 합산
- clinics_data.py 와 동일한 구조(메모리 로드 + bbox 선거름 + 하버사인)
"""
import os
import json
import math

_DATA = None
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accidents.json")


def _load():
    global _DATA
    if _DATA is not None:
        return
    try:
        with open(_PATH, encoding="utf-8") as f:
            obj = json.load(f)
    except FileNotFoundError:
        # 파일이 없어도 서버는 떠야 하므로 빈 데이터로 처리 (auto 점수 0)
        _DATA = []
        return
    rows = obj if isinstance(obj, list) else obj.get("spots", [])
    data = []
    for r in rows:
        try:
            lat = float(r["lat"])
            lng = float(r["lng"])
            occ = int(r.get("occ") or 0)
            caslt = int(r.get("caslt") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        data.append((lat, lng, occ, caslt))
    _DATA = data


def count_loaded():
    _load()
    return len(_DATA)


# clinics_data.py 와 동일한 거리계산
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


def analyze_accidents(lat: float, lng: float, radius_m: int):
    """반경 내 사고다발지역을 집계.
    auto_index = Σ (발생건수 × 거리감쇠)  → 가까운 큰 다발지점일수록 크게 반영."""
    _load()
    lat_lo, lat_hi, lng_lo, lng_hi = _bbox(lat, lng, radius_m)

    spot_count = 0
    acc_total = 0       # 발생건수 합
    caslt_total = 0     # 사상자 합
    nearest = None
    weighted = 0.0      # 거리감쇠 가중 발생건수 (자보 원지표)

    for plat, plng, occ, caslt in _DATA:
        # 1차: 사각형으로 빠르게 거르기
        if not (lat_lo <= plat <= lat_hi and lng_lo <= plng <= lng_hi):
            continue
        # 2차: 정확한 원형 거리
        dist = _haversine_m(lat, lng, plat, plng)
        if dist > radius_m:
            continue
        spot_count += 1
        acc_total += occ
        caslt_total += caslt
        if nearest is None or dist < nearest:
            nearest = dist
        w = 1.0 - (dist / radius_m)   # 중심 1.0 → 경계 0.0
        weighted += w * occ

    return {
        "spot_count": spot_count,
        "acc_total": acc_total,
        "caslt_total": caslt_total,
        "nearest_spot_m": round(nearest) if nearest is not None else None,
        "auto_index": round(weighted, 1),
    }
