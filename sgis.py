"""
SGIS(통계지리정보서비스) 인구 데이터 연동
- 토큰 발급(만료 시 자동 갱신)
- 전국 시군구 이름 → SGIS 코드 매핑 (addr/stage 로 1회 구축 후 캐시)
- 시군구/행정동 단위 인구/고령/유년/가구/종사자 조회
※ v2: 행정동(읍면동) 단위 인구·밀도 지원 (배후 거주인구 정밀화).
"""
import os
import re
import time
import threading
import httpx
from dotenv import load_dotenv

load_dotenv()


def _clean_key(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", v or "")


KEY = _clean_key(os.environ.get("SGIS_CONSUMER_KEY", ""))
SECRET = _clean_key(os.environ.get("SGIS_CONSUMER_SECRET", ""))
BASE = "https://sgisapi.mods.go.kr/OpenAPI3"
POP_YEAR = os.environ.get("SGIS_POP_YEAR", "2023")

_lock = threading.Lock()
_token = {"value": None, "exp": 0}
_sigungu = {}   # {"서울특별시|강남구": "11230", ...}  및  {"강남구": "11230"}
_dong = {}      # {시군구코드: {읍면동명: 읍면동코드}}


def _get_token():
    with _lock:
        if _token["value"] and time.time() < _token["exp"] - 60:
            return _token["value"]
        if not KEY or not SECRET:
            raise RuntimeError("SGIS 키 미설정(.env)")
        r = httpx.get(f"{BASE}/auth/authentication.json",
                      params={"consumer_key": KEY, "consumer_secret": SECRET},
                      timeout=30, follow_redirects=True)
        r.raise_for_status()
        res = (r.json().get("result") or {})
        tok = res.get("accessToken")
        if not tok:
            raise RuntimeError(f"SGIS 토큰 발급 실패: {r.json().get('errMsg')}")
        try:
            exp = int(res.get("accessTimeout", "0")) / 1000
        except (TypeError, ValueError):
            exp = 0
        _token["value"] = tok
        _token["exp"] = exp if exp > time.time() else time.time() + 1800
        return tok


def _build_sigungu_map():
    if _sigungu:
        return
    tok = _get_token()
    sido = httpx.get(f"{BASE}/addr/stage.json", params={"accessToken": tok},
                     timeout=30, follow_redirects=True).json().get("result", [])
    for s in sido:
        sido_cd = s.get("cd")
        sido_nm = s.get("addr_name", "")
        try:
            sgg = httpx.get(f"{BASE}/addr/stage.json",
                            params={"accessToken": tok, "cd": sido_cd},
                            timeout=30, follow_redirects=True).json().get("result", [])
        except Exception:
            continue
        for g in sgg:
            nm = g.get("addr_name")
            cd = g.get("cd")
            if nm and cd:
                _sigungu[f"{sido_nm}|{nm}"] = cd
                _sigungu.setdefault(nm, cd)
        time.sleep(0.02)


def resolve_sigungu_code(sido_nm: str, sigungu_nm: str):
    _build_sigungu_map()
    if sido_nm and sigungu_nm and f"{sido_nm}|{sigungu_nm}" in _sigungu:
        return _sigungu[f"{sido_nm}|{sigungu_nm}"]
    return _sigungu.get(sigungu_nm)


def _dong_map(sgg_code: str):
    """시군구 코드 → {읍면동명: 읍면동코드} (캐시)."""
    if sgg_code in _dong:
        return _dong[sgg_code]
    tok = _get_token()
    try:
        res = httpx.get(f"{BASE}/addr/stage.json",
                        params={"accessToken": tok, "cd": sgg_code},
                        timeout=30, follow_redirects=True).json().get("result", [])
    except Exception:
        res = []
    m = {g["addr_name"]: g["cd"] for g in res if g.get("addr_name") and g.get("cd")}
    _dong[sgg_code] = m
    return m


def resolve_dong_code(sgg_code: str, dong_nm: str):
    if not (sgg_code and dong_nm):
        return None
    dm = _dong_map(sgg_code)
    if dong_nm in dm:
        return dm[dong_nm]
    key = dong_nm.replace(" ", "")
    for nm, cd in dm.items():
        if nm.replace(" ", "") == key:
            return cd
    return None


def get_population_by_code(adm_cd: str, year: str = None):
    tok = _get_token()
    r = httpx.get(f"{BASE}/stats/population.json",
                  params={"accessToken": tok, "year": year or POP_YEAR,
                          "adm_cd": adm_cd, "low_search": "0"},
                  timeout=30, follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    if data.get("errCd") != 0 or not data.get("result"):
        return None
    row = data["result"][0]

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "adm_nm": row.get("adm_nm"),
        "tot_ppltn": num(row.get("tot_ppltn")),
        "tot_family": num(row.get("tot_family")),
        "employee_cnt": num(row.get("employee_cnt")),
        "avg_age": num(row.get("avg_age")),
        "oldage_suprt_per": num(row.get("oldage_suprt_per")),
        "juv_suprt_per": num(row.get("juv_suprt_per")),
        "ppltn_dnsty": num(row.get("ppltn_dnsty")),
    }


def get_population_for_region(sido_nm: str, sigungu_nm: str):
    """시군구 단위 (폴백용)."""
    code = resolve_sigungu_code(sido_nm, sigungu_nm)
    if not code:
        return None
    pop = get_population_by_code(code)
    if pop:
        pop["adm_cd"] = code
        pop["level"] = "sigungu"
    return pop


def get_population_for_dong(sido_nm: str, sigungu_nm: str, dong_nm: str):
    """행정동 단위 인구. 행정동 매칭 실패 시 시군구로 폴백."""
    sgg_code = resolve_sigungu_code(sido_nm, sigungu_nm)
    if not sgg_code:
        return None
    dcode = resolve_dong_code(sgg_code, dong_nm)
    code = dcode or sgg_code
    pop = get_population_by_code(code)
    if pop:
        pop["adm_cd"] = code
        pop["level"] = "dong" if dcode else "sigungu"
        pop["dong_nm"] = dong_nm if dcode else None
    return pop
