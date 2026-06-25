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
import datetime as dt

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

import scoring
import sgis
import clinics_data
import accidents_data

load_dotenv()

KAKAO_KEY = os.environ.get("KAKAO_REST_API_KEY", "")

app = FastAPI(title="한가온 입지 진단 API (cloud)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

DEFAULT_RADIUS = {"clinic": 500, "inpatient": 1500, "hospital": 2000}

# 자보 전용 반경 (자보 환자는 더 넓은 곳에서 유입 → 경쟁 반경보다 크게)
AUTO_RADIUS = {"clinic": 1500, "inpatient": 2500, "hospital": 3000}

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
        return None, None
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
            return None, None
        return doc.get("region_1depth_name"), doc.get("region_2depth_name")
    except Exception:
        return None, None


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
    if req.inst not in scoring.INST or req.ptype not in scoring.TYPES:
        raise HTTPException(400, "inst/ptype 값 오류")

    if req.lat is not None and req.lng is not None:
        coord = {"lat": req.lat, "lng": req.lng}
    elif req.address:
        coord = await geocode(req.address)
    else:
        raise HTTPException(400, "address 또는 lat/lng 필요")

    radius = req.radius_m or DEFAULT_RADIUS[req.inst]

    # 경쟁분석 (clinics.json)
    comp = clinics_data.analyze_competition(coord["lat"], coord["lng"], radius)

    # 자보분석 (accidents.json) — 자보 전용 반경 사용
    auto_radius = AUTO_RADIUS[req.inst]
    acc = accidents_data.analyze_accidents(coord["lat"], coord["lng"], auto_radius)

    # 인구분석 (SGIS)
    pop_data = None
    region = {"sido": None, "sigungu": None}
    try:
        sido, sigungu = await region_from_coord(coord["lat"], coord["lng"])
        region = {"sido": sido, "sigungu": sigungu}
        if sigungu:
            pop_data = sgis.get_population_for_region(sido, sigungu)
    except Exception as e:
        print(f"[SGIS] 실패: {e}")
        pop_data = None

    axes = {
        "comp": scoring.comp_score(comp["competitors"]),
        "pop":  scoring.pop_score_from_sgis(pop_data),
        "fit":  scoring.fit_score_from_sgis(req.ptype, pop_data),
        "auto": scoring.auto_score(acc["auto_index"]),
        "flow":   None,
        "access": None,
    }
    score, used = scoring.total_score(axes, req.inst, req.ptype)

    return {
        "address": req.address,
        "coord": coord,
        "region": region,
        "radius_m": radius,
        "auto_radius_m": auto_radius,
        "inst": req.inst,
        "ptype": req.ptype,
        "raw": comp,
        "accident": acc,
        "population": pop_data,
        "axes": axes,
        "axes_used": used,
        "score": score,
        "grade": scoring.grade(score),
        "data_generated_at": clinics_data.generated_at(),
        "note": "경량판: 경쟁분석 + 인구분석 + 자보분석 실데이터.",
        "generated_at": dt.datetime.now().isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok",
            "clinics_loaded": clinics_data.count_loaded(),
            "accidents_loaded": accidents_data.count_loaded(),
            "data_generated_at": clinics_data.generated_at()}


@app.get("/")
def root():
    return {"service": "한가온 입지 진단 API", "status": "running"}
