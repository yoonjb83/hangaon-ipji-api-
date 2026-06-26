"""
시군구별 연간 교통사고 발생건수 (도로교통공단, 2024 기준)
- accidents_sgg.json 메모리 로드
- 자보 수요 = 그 지역(시군구) 실제 교통사고 발생 규모 (자보 환자 풀)
- 카카오 주소(시도/시군구) → 발생건수 매칭. 통합시·세종 등 특수표기 처리.
"""
import json, os

_BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_BASE, "accidents_sgg.json"), encoding="utf-8") as f:
    _RAW = json.load(f)

DATA = _RAW["data"]
YEAR = _RAW.get("year")
SOURCE = _RAW.get("source", "도로교통공단 시군구별 교통사고 통계")

# 카카오 시도명(풀네임) → CSV 시도명(약칭)
_SIDO = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원",
    "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "제주도": "제주",
}


def _norm_sido(s):
    if not s:
        return s
    if s in _SIDO:
        return _SIDO[s]
    # 폴백: 접미사 제거
    for suf in ("특별자치도", "특별자치시", "특별시", "광역시"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def annual_accidents(sido_raw, sigungu_raw):
    """시도·시군구 → 연간 교통사고 발생건수. 매칭 실패 시 None."""
    sd = _norm_sido(sido_raw)
    gu = (sigungu_raw or "").strip()
    if sd == "세종":
        return DATA.get("세종|세종시")
    cand = [gu]
    if " " in gu:                       # "용인시 기흥구" → "용인시"
        first = gu.split()[0]
        cand += [first, first + "(통합)"]   # 창원시(통합) 대응
    for c in cand:
        v = DATA.get(f"{sd}|{c}")
        if v is not None:
            return v
    return None
