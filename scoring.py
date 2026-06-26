"""
점수 산출 엔진 v2
- 5축: 거주수요(demand) · 유동인구(flow) · 경쟁우위(comp) · 자보(auto) · 특화적합(fit)
- 거주수요 = 행정동 기반 반경 거주인구(주축). 역거리 접근성 폐기.
- 경쟁 = 종별 맞춤(한방병원은 한방병원 수, 한의원은 한의원 수) + 현실 기준 완만 감점.
- 9단계 등급(A+~D).
"""

AXES = ["demand", "flow", "comp", "auto", "fit"]

# 진료 특화별 가중치 (demand, flow, comp, auto, fit)
TYPES = {
    "pain":  {"demand": 1.3, "flow": 0.7, "comp": 1.0, "auto": 0.4, "fit": 0.7},
    "diet":  {"demand": 1.0, "flow": 1.3, "comp": 1.0, "auto": 0.3, "fit": 0.9},
    "child": {"demand": 1.3, "flow": 0.7, "comp": 1.0, "auto": 0.3, "fit": 1.0},
    "auto":  {"demand": 1.1, "flow": 0.6, "comp": 1.0, "auto": 1.1, "fit": 0.7},
}

# 기관 종별 보정 (한의원 / 입원실 한의원 / 한방병원)
INST = {
    "clinic":    {"demand": 1.0, "flow": 1.1, "comp": 1.0, "auto": 0.5, "fit": 1.0},
    "inpatient": {"demand": 1.2, "flow": 0.8, "comp": 1.2, "auto": 0.7, "fit": 1.0},
    "hospital":  {"demand": 1.3, "flow": 0.6, "comp": 1.1, "auto": 0.5, "fit": 1.0},
}

# 거주수요 만점/하한 기준(종별 적정 인구). 한방병원 2km≈10만 기준 반영.
DEMAND_LO = {"clinic": 8000,  "inpatient": 12000, "hospital": 22000}
DEMAND_HI = {"clinic": 30000, "inpatient": 38000, "hospital": 85000}

# 자보 원지표(auto_index) 만점기준 (v2에서 완화: 260 → 120)
AUTO_INDEX_FULL = 120.0
# 유동인구(상가 수) 만점기준
FLOW_INDEX_FULL = 2200.0


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def normalize(value, lo, hi, invert=False):
    if value is None:
        return None
    if hi == lo:
        return 50.0
    s = (value - lo) / (hi - lo) * 100.0
    if invert:
        s = 100.0 - s
    return clamp(s)


# ── 축 서브점수 ─────────────────────────────
def demand_score(catchment_pop, inst):
    """거주수요: 내원 가능 반경 내 거주인구(행정동 기반). 종별 적정 인구로 정규화."""
    if catchment_pop is None:
        return None
    return normalize(catchment_pop, DEMAND_LO.get(inst, 8000), DEMAND_HI.get(inst, 60000))


def comp_score(inst, clinic_cnt, hospital_cnt):
    """경쟁우위: 종별 직접 경쟁만, 현실 기준으로 완만하게(바닥 점수 보장)."""
    cc = clinic_cnt or 0
    hc = hospital_cnt or 0
    if inst == "clinic":
        return clamp(100 - cc * 1.0, 40, 100)     # 한의원 수 기준(완만, 바닥40)
    return clamp(100 - hc * 10, 55, 100)          # 한방병원 수 기준(10만당 2~3 정상, 바닥55)


def flow_score(store_count):
    """유동인구(상권 활성도): 반경 500m 내 상가 수 → 0~100."""
    return normalize(store_count, 0, FLOW_INDEX_FULL)


def auto_score(auto_index):
    """자보수요: 반경 내 (발생건수 × 거리감쇠) 합산 → 0~100."""
    return normalize(auto_index, 0, AUTO_INDEX_FULL)


def fit_score_from_sgis(ptype, s):
    """진료 특화별 타깃 인구 적합도 (행정동 SGIS 연령·부양비 기반).
    ※ ptype=='auto'는 main에서 자보수요(auto_index)로 직접 계산한다."""
    if not s:
        return None
    avg_age = s.get("avg_age")
    old = s.get("oldage_suprt_per")   # 노년부양비
    juv = s.get("juv_suprt_per")      # 유년부양비
    if ptype == "pain":
        return normalize(old, 12, 35)
    if ptype == "diet":
        return normalize(avg_age, 36, 48, invert=True)
    if ptype == "child":
        return normalize(juv, 12, 28)
    return None


# ── 가중 합산 ─────────────────────────────
def total_score(axes: dict, inst: str, ptype: str):
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
    """9단계 등급."""
    if score is None:
        return None
    table = [(88, "A+", "최우수"), (80, "A", "우수"), (73, "A-", "우수"),
             (66, "B+", "양호"), (60, "B", "양호"), (54, "B-", "양호"),
             (47, "C+", "주의"), (40, "C", "주의")]
    for th, g, txt in table:
        if score >= th:
            return {"g": g, "txt": txt}
    return {"g": "D", "txt": "미흡"}
