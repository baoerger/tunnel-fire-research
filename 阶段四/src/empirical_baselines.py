"""经文献核验的传统隧道顶棚最大气体温升基线。

这里只实现符号、系数和单位已经由原论文 DOI 与开放全文转录交叉核对的
Kurioka (2003) 和 Li--Lei--Ingason (2011) 公式。两式都只预测最大顶棚
气体温升，不生成纵向温度剖面，也不预测峰值位置。

公共接口使用项目单位 MW、m/s、m、J/(kg K)；函数内部在 Li 公式的
单位特定系数处显式转换为原式使用的 kW 与 kJ/(kg K)。
"""
from __future__ import annotations

import math


DEFAULT_RHO0_KG_M3 = 1.2
DEFAULT_CP_J_KG_K = 1005.0
DEFAULT_T0_K = 293.15
DEFAULT_G_M_S2 = 9.81

KURIOKA_2003_DOI = "10.1016/S0379-7112(02)00089-9"
LI_2011_DOI = "10.1016/j.firesaf.2011.02.002"
OPEN_TRANSCRIPTION_DOI = "10.3390/fire8010009"


def _positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值: {value!r}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} 必须为有限正数")
    return number


def _air_properties(rho0_kg_m3, cp_J_kg_K, T0_K, g_m_s2):
    rho0 = _positive(rho0_kg_m3, "rho0_kg_m3")
    cp = _positive(cp_J_kg_K, "cp_J_kg_K")
    T0 = _positive(T0_K, "T0_K")
    g = _positive(g_m_s2, "g_m_s2")
    return rho0, cp, T0, g


def kurioka_2003_max_temperature_rise(
    Q_MW,
    U_m_s,
    H_eff_m,
    *,
    rho0_kg_m3=DEFAULT_RHO0_KG_M3,
    cp_J_kg_K=DEFAULT_CP_J_KG_K,
    T0_K=DEFAULT_T0_K,
    g_m_s2=DEFAULT_G_M_S2,
):
    """返回 Kurioka et al. (2003) 最大顶棚气体温升及中间无量纲量。

    原式含 ``Fr = U^2/(g H_eff)``，因此 U=0 是公式奇点而非可外推工况；
    无风比较应使用明确包含无风分支的其他基线（如 Li 2011 低风分支）。
    """
    Q_MW = _positive(Q_MW, "Q_MW")
    U = _positive(U_m_s, "U_m_s")
    H_eff = _positive(H_eff_m, "H_eff_m")
    rho0, cp, T0, g = _air_properties(rho0_kg_m3, cp_J_kg_K, T0_K, g_m_s2)

    # W 与 J/(kg K) 成对使用；数值等价于原文的 kW 与 kJ/(kg K)。
    Q_star = Q_MW * 1.0e6 / (rho0 * cp * T0 * math.sqrt(g) * H_eff ** 2.5)
    froude = U * U / (g * H_eff)
    driving = Q_star ** (2.0 / 3.0) / froude ** (1.0 / 3.0)
    if driving < 1.35:
        gamma, epsilon = 1.77, 6.0 / 5.0
        branch = "driving_parameter_lt_1.35"
    else:
        gamma, epsilon = 2.54, 0.0
        branch = "driving_parameter_ge_1.35"
    delta_T = T0 * gamma * driving ** epsilon
    return {
        "formula_id": "kurioka_2003_max_ceiling_gas_temperature",
        "temperature_type": "ceiling_gas_temperature",
        "delta_T_max_K": delta_T,
        "Q_star": Q_star,
        "Fr": froude,
        "driving_parameter": driving,
        "gamma": gamma,
        "epsilon": epsilon,
        "branch": branch,
    }


def li_2011_max_temperature_rise(
    Q_MW,
    U_m_s,
    D_f_m,
    H_eff_m,
    *,
    chi_r=0.30,
    rho0_kg_m3=DEFAULT_RHO0_KG_M3,
    cp_J_kg_K=DEFAULT_CP_J_KG_K,
    T0_K=DEFAULT_T0_K,
    g_m_s2=DEFAULT_G_M_S2,
):
    """返回 Li, Lei & Ingason (2011) 最大顶棚气体温升。

    原式的 ``r`` 是火源半径，本项目由等效直径 ``D_f`` 转为 ``r=D_f/2``；
    ``Q`` 是总 HRR，特征羽流速度中的 ``Q_c=(1-chi_r)Q`` 是对流 HRR。
    原式数值系数要求 Q 用 kW、cp 用 kJ/(kg K)，此处显式转换。
    """
    Q_MW = _positive(Q_MW, "Q_MW")
    try:
        U = float(U_m_s)
        chi_r = float(chi_r)
    except (TypeError, ValueError) as exc:
        raise ValueError("U_m_s/chi_r 不是有效数值") from exc
    if not math.isfinite(U) or U < 0:
        raise ValueError("U_m_s 必须为有限非负数")
    if not math.isfinite(chi_r) or not 0 <= chi_r < 1:
        raise ValueError("chi_r 必须满足 0<=chi_r<1")
    D_f = _positive(D_f_m, "D_f_m")
    H_eff = _positive(H_eff_m, "H_eff_m")
    rho0, cp_J, T0, g = _air_properties(rho0_kg_m3, cp_J_kg_K, T0_K, g_m_s2)

    Q_kW = Q_MW * 1.0e3
    Q_c_kW = (1.0 - chi_r) * Q_kW
    cp_kJ = cp_J / 1.0e3
    radius = D_f / 2.0
    w_star = (Q_c_kW * g / (radius * rho0 * cp_kJ * T0)) ** (1.0 / 3.0)
    u_prime = U / w_star
    if u_prime > 0.19:
        delta_T = Q_kW / (U * radius ** (1.0 / 3.0) * H_eff ** (5.0 / 3.0))
        branch = "u_prime_gt_0.19"
    else:
        delta_T = 17.5 * Q_kW ** (2.0 / 3.0) / H_eff ** (5.0 / 3.0)
        branch = "u_prime_le_0.19"
    return {
        "formula_id": "li_2011_max_ceiling_gas_temperature",
        "temperature_type": "ceiling_gas_temperature",
        "delta_T_max_K": delta_T,
        "u_prime": u_prime,
        "w_star_m_s": w_star,
        "Q_total_kW": Q_kW,
        "Q_convective_kW": Q_c_kW,
        "fire_radius_m": radius,
        "branch": branch,
    }
