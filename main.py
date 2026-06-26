"""
한가온 입지 진단 API (클라우드 경량판 · DB 없음)
- 경쟁분석: clinics.json 메모리 로드 (PostGIS 대체)
- 인구분석: SGIS
- 자보분석: accidents.json 메모리 로드 (도로교통공단 사고다발지역)
- 지오코딩: 카카오 (메모리 캐시)

로컬 실행:  uvicorn main:app --reload
클라우드:   uvicorn main:app --host 0.0.0.0 --port $PORT
"""
import os
import math
import datetime as dt

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

import scoring
import sgis
import clinics_data
import traffic

load_dotenv()

KAKAO_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")

app = FastAPI(title="한가온 입지 진단 API (cloud)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

DEFAULT_RADIUS = {"clinic": 800, "inpatient": 1200, "hospital": 2000}

# 유동인구(상권 활성도) 반경 — 즉시 상권 기준 고정 500m
FLOW_RADIUS = 500

# 지오코딩 결과 메모리 캐시 (DB 대체)
_geo_cache = {}


async def geocode(address: str):
    if address in _geo_cache:
        return _geo_cache[address]
    if not KAKAO_KEY:
        raise HTTPException(500, "KAKAO_REST_API_KEY 미설정")
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
        r = await client.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            params={"query": address}, headers=headers,
        )
        r.raise_for_status()
        docs = r.json().get("documents", [])
        if not docs:
            r2 = await client.get(
                "https://dapi.kakao.com/v2/local/search/keyword.json",
                params={"query": address}, headers=headers,
            )
            r2.raise_for_status()
            docs = r2.json().get("documents", [])
    if not docs:
        raise HTTPException(404, f"주소를 찾을 수 없습니다: {address}")
    lng, lat = float(docs[0]["x"]), float(docs[0]["y"])
    result = {"lat": lat, "lng": lng}
    _geo_cache[address] = result
    return result


async def region_from_coord(lat: float, lng: float):
    if not KAKAO_KEY:
        return None, None, None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json",
                params={"x": lng, "y": lat},
                headers={"Authorization": f"KakaoAK {KAKAO_KEY}"},
            )
        r.raise_for_status()
        docs = r.json().get("documents", [])
        doc = next((d for d in docs if d.get("region_type") == "H"), docs[0] if docs else None)
        if not doc:
            return None, None, None
        return (doc.get("region_1depth_name"), doc.get("region_2depth_name"),
                doc.get("region_3depth_name"))
    except Exception:
        return None, None, None


async def nearest_subway(lat: float, lng: float):
    """카카오 카테고리 검색(SW8=지하철역)으로 가장 가까운 역과 거리(m)를 구함."""
    if not KAKAO_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://dapi.kakao.com/v2/local/search/category.json",
                params={"category_group_code": "SW8", "x": lng, "y": lat,
                        "radius": 20000, "sort": "distance", "size": 1},
                headers={"Authorization": f"KakaoAK {KAKAO_KEY}"},
            )
        r.raise_for_status()
        docs = r.json().get("documents", [])
        if not docs:
            return None
        d = docs[0]
        return {"name": d.get("place_name"), "dist_m": int(d.get("distance") or 0)}
    except Exception:
        return None


async def market_density(lat: float, lng: float, radius: int = 500):
    """소상공인 상가정보 API로 반경 내 상가 수(상권 활성도)를 구함."""
    if not DATA_GO_KR_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                "http://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius",
                params={"serviceKey": DATA_GO_KR_KEY, "cx": lng, "cy": lat,
                        "radius": radius, "type": "json", "numOfRows": "1", "pageNo": "1"},
            )
        r.raise_for_status()
        tc = r.json().get("body", {}).get("totalCount")
        if tc is None:
            return None
        return {"store_count": int(tc), "radius_m": radius}
    except Exception:
        return None


class DiagnoseReq(BaseModel):
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    inst: str = "clinic"
    ptype: str = "pain"
    radius_m: int | None = None


@app.post("/geocode")
async def geocode_ep(body: dict):
    addr = body.get("address")
    if not addr:
        raise HTTPException(400, "address 필요")
    return await geocode(addr)


@app.post("/diagnose")
async def diagnose(req: DiagnoseReq):
    if req.inst not in scoring.WEIGHTS:
        raise HTTPException(400, "inst 값 오류")

    if req.lat is not None and req.lng is not None:
        coord = {"lat": req.lat, "lng": req.lng}
    elif req.address:
        coord = await geocode(req.address)
    else:
        raise HTTPException(400, "address 또는 lat/lng 필요")

    radius = req.radius_m or DEFAULT_RADIUS[req.inst]

    # 경쟁분석 (clinics.json)
    comp = clinics_data.analyze_competition(coord["lat"], coord["lng"], radius)

    # 접근성 (카카오: 최근접 지하철역)
    transit = await nearest_subway(coord["lat"], coord["lng"])

    # 유동인구 (소상공인: 반경 내 상가 수)
    market = await market_density(coord["lat"], coord["lng"], FLOW_RADIUS)

    # 인구분석 (SGIS · 행정동 단위)
    pop_data = None
    region = {"sido": None, "sigungu": None, "dong": None}
    try:
        sido, sigungu, dong = await region_from_coord(coord["lat"], coord["lng"])
        region = {"sido": sido, "sigungu": sigungu, "dong": dong}
        if sigungu:
            pop_data = sgis.get_population_for_dong(sido, sigungu, dong)
    except Exception as e:
        print(f"[SGIS] 실패: {e}")
        pop_data = None

    # 반경 내 거주인구 = max(행정동 인구, 밀도 × 반경면적) — 도심·시골 모두 보정
    catchment = None
    if pop_data and pop_data.get("ppltn_dnsty") and pop_data.get("tot_ppltn"):
        area = math.pi * (radius / 1000.0) ** 2
        catchment = max(pop_data["tot_ppltn"], pop_data["ppltn_dnsty"] * area)

    # 자보분석: 시군구 연간 교통사고 발생건수 (자보 환자 풀)
    annual_acc = traffic.annual_accidents(region.get("sido"), region.get("sigungu"))
    acc_region = " ".join(x for x in [region.get("sido"), region.get("sigungu")] if x)

    # 입지 4축 (종합점수)
    axes = {
        "demand": scoring.demand_score(catchment, req.inst),
        "flow":   scoring.flow_score(market["store_count"]) if market else None,
        "comp":   scoring.comp_score(req.inst, comp.get("clinic_cnt"), comp.get("hospital_cnt")),
        "auto":   scoring.auto_score(annual_acc),
    }
    score, used = scoring.total_score(axes, req.inst)

    # 진료특화 4종 적합도 (참고 그래프용, 종합점수 미반영)
    fit = scoring.fit_scores(pop_data, annual_acc)

    return {
        "address": req.address,
        "coord": coord,
        "region": region,
        "radius_m": radius,
        "inst": req.inst,
        "ptype": req.ptype,
        "raw": comp,
        "accident": {
            "region": acc_region,
            "annual_acc": annual_acc,
            "year": traffic.YEAR,
            "source": traffic.SOURCE,
        },
        "transit": transit,
        "market": market,
        "population": pop_data,
        "catchment_pop": round(catchment) if catchment else None,
        "axes": axes,
        "axes_used": used,
        "fit_scores": fit,
        "score": score,
        "grade": scoring.grade(score),
        "data_generated_at": clinics_data.generated_at(),
        "note": "v3.1: 자보=시군구 연간 교통사고 발생건수 기반.",
        "generated_at": dt.datetime.now().isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok",
            "clinics_loaded": clinics_data.count_loaded(),
            "sgg_traffic_loaded": len(traffic.DATA),
            "data_generated_at": clinics_data.generated_at()}


@app.get("/")
def root():
    return {"service": "한가온 입지 진단 API", "status": "running"}
