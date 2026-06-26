"""
점수 산출 엔진 v3
- 종합점수 = 입지 4축: 거주수요(demand)·유동인구(flow)·경쟁우위(comp)·자보(auto)
- 진료특화는 '선택'이 아니라 '결과' → 4개 특화 적합도(통증/다이어트/소아/자보)를 항상 계산해 함께 제공(종합점수엔 미반영).
- 거주수요 = 행정동 기반 반경 거주인구(주축). 경쟁 = 종별 맞춤·현실 기준 완만 감점.
- 9단계 등급(A+~D).
"""

AXES = ["demand", "flow", "comp", "auto"]

# 종합점수 가중치 (종별). 진료특화 가중치는 제거(특화는 별도 적합도로만 제공).
WEIGHTS = {
    "clinic":    {"demand": 1.3, "flow": 1.1, "comp": 1.0, "auto": 0.5},
    "inpatient": {"demand": 1.4, "flow": 0.7, "comp": 1.3, "auto": 0.8},
    "hospital":  {"demand": 1.6, "flow": 0.6, "comp": 1.3, "auto": 0.4},
}

# 진료특화 4종(적합도 그래프용 라벨)
SPECIALTIES = ["pain", "diet", "child", "auto"]

import math

DEMAND_LO = {"clinic": 8000,  "inpatient": 12000, "hospital": 22000}
DEMAND_HI = {"clinic": 30000, "inpatient": 38000, "hospital": 85000}
# 유동인구(상가 수) 만점기준
FLOW_INDEX_FULL = 2200.0
# 자보: 시군구 연간 교통사고 발생건수 로그곡선 (바닥 35점 보장)
AUTO_FLOOR = 35.0
AUTO_LOG_A = -70.0
AUTO_LOG_B = 47.0


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


# ── 입지 4축 ─────────────────────────────
def demand_score(catchment_pop, inst):
    if catchment_pop is None:
        return None
    return normalize(catchment_pop, DEMAND_LO.get(inst, 8000), DEMAND_HI.get(inst, 60000))


def comp_score(inst, clinic_cnt, hospital_cnt):
    cc = clinic_cnt or 0
    hc = hospital_cnt or 0
    if inst == "clinic":
        return clamp(100 - cc * 1.0, 40, 100)
    return clamp(100 - hc * 10, 55, 100)


def flow_score(store_count):
    return normalize(store_count, 0, FLOW_INDEX_FULL)


def auto_score(annual_acc):
    """자보 점수 = 시군구 연간 교통사고 발생건수(로그 보정). 0/None이면 바닥점 35."""
    if not annual_acc or annual_acc <= 0:
        return AUTO_FLOOR
    return clamp(AUTO_LOG_A + AUTO_LOG_B * math.log10(annual_acc), AUTO_FLOOR, 100)


# ── 진료특화 4종 적합도 (종합점수 미반영) ─────────────────────────────
def fit_scores(pop, annual_acc, female_ratio=None):
    """통증·재활 / 다이어트·미용 / 소아·성장 / 자보 적합도.
    다이어트 = 20~50대 젊은층(평균연령) + 여성비율 결합."""
    out = {"pain": None, "diet": None, "child": None, "auto": auto_score(annual_acc)}
    if pop:
        out["pain"] = normalize(pop.get("oldage_suprt_per"), 12, 35)   # 고령 ↑
        out["child"] = normalize(pop.get("juv_suprt_per"), 12, 28)     # 영유아 ↑
        young = normalize(pop.get("avg_age"), 36, 48, invert=True)     # 젊을수록 ↑(20~50대 대리)
        if young is not None:
            if female_ratio is not None:
                female = normalize(female_ratio, 46, 53)               # 여성비율 46~53% → 0~100
                out["diet"] = round(0.7 * young + 0.3 * female)        # 젊음 70% + 여성 30%
            else:
                out["diet"] = round(young)
    return out


# ── 가중 합산 ─────────────────────────────
def total_score(axes: dict, inst: str):
    w = WEIGHTS.get(inst, WEIGHTS["clinic"])
    num = den = 0.0
    used = []
    for a in AXES:
        s = axes.get(a)
        if s is None:
            continue
        num += w[a] * s
        den += w[a]
        used.append(a)
    if den == 0:
        return None, used
    return round(num / den), used


def grade(score):
    if score is None:
        return None
    table = [(88, "A+", "최우수"), (80, "A", "우수"), (73, "A-", "우수"),
             (66, "B+", "양호"), (60, "B", "양호"), (54, "B-", "양호"),
             (47, "C+", "주의"), (40, "C", "주의")]
    for th, g, txt in table:
        if score >= th:
            return {"g": g, "txt": txt}
    return {"g": "D", "txt": "미흡"}
