"""
점수 산출 엔진
- 프론트(index.html)의 TYPES(진료특화 가중치) / INST(종별 보정)를 그대로 이관해
  프론트·백엔드 점수 로직을 일치시킨다.
- 일부 축이 아직 실데이터가 없으면(None), 해당 축을 제외하고 가중 재정규화한다.
"""

AXES = ["pop", "flow", "comp", "auto", "access", "fit"]

# 진료 특화별 가중치 (pop, flow, comp, auto, access, fit)
TYPES = {
    "pain":  {"pop": 1.0, "flow": 0.6, "comp": 0.8, "auto": 1.0, "access": 0.9, "fit": 1.3},
    "diet":  {"pop": 0.6, "flow": 1.3, "comp": 0.7, "auto": 0.3, "access": 1.0, "fit": 1.3},
    "child": {"pop": 1.3, "flow": 0.6, "comp": 1.0, "auto": 0.3, "access": 0.8, "fit": 1.3},
    "auto":  {"pop": 0.8, "flow": 0.5, "comp": 0.6, "auto": 1.4, "access": 1.1, "fit": 1.0},
}

# 기관 종별 보정 (한의원 / 입원실 한의원 / 한방병원)
INST = {
    "clinic":    {"pop": 1.0,  "flow": 1.2,  "comp": 1.2,  "auto": 0.9,  "access": 1.0,  "fit": 1.0},
    "inpatient": {"pop": 1.05, "flow": 0.85, "comp": 1.05, "auto": 1.45, "access": 1.2,  "fit": 1.0},
    "hospital":  {"pop": 1.25, "flow": 0.6,  "comp": 0.75, "auto": 1.4,  "access": 1.4,  "fit": 1.0},
}


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def normalize(value, lo, hi, invert=False):
    """min-max 정규화 → 0~100. invert=True면 작을수록 높은 점수.
    ※ 운영 단계에선 전국 분포의 percentile 테이블로 교체 권장."""
    if value is None:
        return None
    if hi == lo:
        return 50.0
    s = (value - lo) / (hi - lo) * 100.0
    if invert:
        s = 100.0 - s
    return clamp(s)


# ── 원본지표 → 축 서브점수 ─────────────────────────────
def comp_score(competitor_count: int) -> float:
    """경쟁우위: 반경 내 동종 수가 적을수록 높음 (기준 0~20곳)."""
    return normalize(competitor_count, 0, 20, invert=True)


def pop_score(backing_pop):
    """배후인구: 반경 내 인구 (기준 5천~6만). SGIS 연동(P2) 후 값 주입."""
    return normalize(backing_pop, 5000, 60000)


def flow_score(floating_index):
    """유동인구: 상권 활성도 지수 0~100 (소상공인, P2)."""
    return normalize(floating_index, 0, 100)


def auto_score(accident_grade_value):
    """자보수요: 반경 내 사고다발 가중치 (도로교통공단, P3)."""
    return normalize(accident_grade_value, 0, 100)


def access_score(nearest_station_m):
    """접근성: 최근접 역 거리 (0m=만점, 1200m=0점)."""
    return normalize(nearest_station_m, 0, 1200, invert=True)


def fit_score(value):
    """특화적합: 타깃 인구 매칭 0~100 (P2에서 종별·특화별 산출)."""
    return normalize(value, 0, 100)


# ── SGIS 인구 데이터 기반 (P2) ─────────────────────────────
def pop_score_from_sgis(s):
    """배후인구: 시군구 총인구 (기준 8만~60만)."""
    if not s:
        return None
    return normalize(s.get("tot_ppltn"), 80000, 600000)


def fit_score_from_sgis(ptype, s):
    """진료 특화별 타깃 인구 적합도 (SGIS 연령·부양비 기반)."""
    if not s:
        return None
    avg_age = s.get("avg_age")
    old = s.get("oldage_suprt_per")   # 노년부양비 ↑ = 고령 많음
    juv = s.get("juv_suprt_per")      # 유년부양비 ↑ = 아이 많음
    if ptype == "pain":      # 통증·추나: 고령 많을수록 ↑
        return normalize(old, 12, 35)
    if ptype == "diet":      # 다이어트·미용: 젊은 동네일수록 ↑
        return normalize(avg_age, 36, 48, invert=True)
    if ptype == "child":     # 소아·성장: 아이 많을수록 ↑
        return normalize(juv, 12, 28)
    if ptype == "auto":      # 자보: 인구 규모 비례(사고 잠재)
        return normalize(s.get("tot_ppltn"), 100000, 600000)
    return None


# ── 가중 합산 ─────────────────────────────
def total_score(axes: dict, inst: str, ptype: str):
    """있는 축만으로 가중 합산(없는 축 제외 후 재정규화)."""
    w_type = TYPES[ptype]
    w_inst = INST[inst]
    num = den = 0.0
    used = []
    for a in AXES:
        s = axes.get(a)
        if s is None:
            continue
        w = w_type[a] * w_inst[a]
        num += w * s
        den += w
        used.append(a)
    if den == 0:
        return None, used
    return round(num / den), used


def grade(score):
    if score is None:
        return None
    if score >= 80:
        return {"g": "A", "txt": "추천"}
    if score >= 66:
        return {"g": "B", "txt": "양호"}
    return {"g": "C", "txt": "주의"}
