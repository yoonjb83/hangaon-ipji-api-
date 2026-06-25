"""
SGIS(통계지리정보서비스) 인구 데이터 연동
- 토큰 발급(만료 시 자동 갱신)
- 전국 시군구 이름 → SGIS 코드 매핑 (addr/stage 로 1회 구축 후 캐시)
- 시군구 단위 인구/고령/유년/가구/종사자 조회
※ 시군구 단위라 배후인구 절대값은 다소 거칠지만, 고령·유년 '비율' 지표는 그대로 의미가 있다.
"""
import os
import re
import time
import threading
import httpx
from dotenv import load_dotenv

load_dotenv()


def _clean_key(v: str) -> str:
    # 영문자·숫자만 남김 (BOM, 공백, 따옴표, 줄바꿈 등 모두 제거)
    return re.sub(r"[^A-Za-z0-9]", "", v or "")


KEY = _clean_key(os.environ.get("SGIS_CONSUMER_KEY", ""))
SECRET = _clean_key(os.environ.get("SGIS_CONSUMER_SECRET", ""))
BASE = "https://sgisapi.mods.go.kr/OpenAPI3"
POP_YEAR = os.environ.get("SGIS_POP_YEAR", "2023")

_lock = threading.Lock()
_token = {"value": None, "exp": 0}
_sigungu = {}   # {"서울특별시|강남구": "11230", ...}  및  {"강남구": "11230"}


def _get_token():
    with _lock:
        if _token["value"] and time.time() < _token["exp"] - 60:
            return _token["value"]
        if not KEY or not SECRET:
            raise RuntimeError("SGIS 키 미설정(.env)")
        print(f"[SGIS] key_len={len(KEY)} secret_len={len(SECRET)}")
        r = httpx.get(f"{BASE}/auth/authentication.json",
                      params={"consumer_key": KEY, "consumer_secret": SECRET},
                      timeout=30, follow_redirects=True)
        r.raise_for_status()
        res = (r.json().get("result") or {})
        tok = res.get("accessToken")
        if not tok:
            raise RuntimeError(f"SGIS 토큰 발급 실패: {r.json().get('errMsg')}")
        # accessTimeout(ms) 활용, 없으면 30분
        try:
            exp = int(res.get("accessTimeout", "0")) / 1000
        except (TypeError, ValueError):
            exp = 0
        _token["value"] = tok
        _token["exp"] = exp if exp > time.time() else time.time() + 1800
        return tok


def _build_sigungu_map():
    """전국 시도→시군구를 돌며 이름→코드 매핑 구축 (최초 1회)."""
    if _sigungu:
        return
    tok = _get_token()
    # 시도 목록
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
                _sigungu.setdefault(nm, cd)   # 이름만으로도 조회 가능(동명 시군구는 시도 우선)
        time.sleep(0.02)


def resolve_sigungu_code(sido_nm: str, sigungu_nm: str):
    """카카오가 준 시도·시군구 이름 → SGIS 코드."""
    _build_sigungu_map()
    if sido_nm and sigungu_nm and f"{sido_nm}|{sigungu_nm}" in _sigungu:
        return _sigungu[f"{sido_nm}|{sigungu_nm}"]
    return _sigungu.get(sigungu_nm)


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
        "tot_ppltn": num(row.get("tot_ppltn")),       # 총인구
        "tot_family": num(row.get("tot_family")),      # 총가구
        "employee_cnt": num(row.get("employee_cnt")),  # 종사자(직장인구)
        "avg_age": num(row.get("avg_age")),            # 평균연령
        "oldage_suprt_per": num(row.get("oldage_suprt_per")),  # 노년부양비
        "juv_suprt_per": num(row.get("juv_suprt_per")),        # 유년부양비
        "ppltn_dnsty": num(row.get("ppltn_dnsty")),    # 인구밀도
    }


def get_population_for_region(sido_nm: str, sigungu_nm: str):
    """카카오 지역명으로 인구 데이터를 한 번에 가져오기."""
    code = resolve_sigungu_code(sido_nm, sigungu_nm)
    if not code:
        return None
    pop = get_population_by_code(code)
    if pop:
        pop["adm_cd"] = code
    return pop
