"""
Short-circuit currents in the low-voltage installation, IEC 60909-0:2026.

The method of the equivalent voltage source at the fault location (5.2),
with the impedances of the network feeder (6.7), network transformers with
their correction factor K_T (6.3.3), synchronous generators with K_G
(6.8.2), cables (6.4) and power-electronic sources as current sources
(5.2.3, 6.10.2, 6.11.5). Complex arithmetic throughout; impedances in ohms
referred to the LV side.

Maximum currents (7.1.2): c_max, correction factors, conductor resistance at
20 C, all sources in service. They set breaking capacity, making capacity
(peak current) and the conductor's adiabatic withstand.

Minimum currents (7.1.3): c_min, correction factors = 1, conductor
resistance at the end-of-fault temperature (Formula 50), wind and PV
contributions neglected. They decide whether the protective device
disconnects in time (IEC 60364-4-41).

Single line-to-earth current (TN systems), used for the disconnection check:

    I"k1 = sqrt(3) c Un / |Z(1) + Z(2) + Z(0)|

with Z(2) = Z(1) and the circuit's zero-sequence impedance Z(0)L = R_L +
3 R_PE for a separate protective conductor sized to IEC 60364-5-54 Table
54.2. The supply's zero-sequence impedance is taken equal to its positive-
sequence impedance (a Dyn transformer, Z(0)T ~ Z_T), which is the usual
assumption when the network data give only a three-phase fault level.

What this module does not do: meshed networks, unbalanced loading, motor
contributions, DC components. Converter contributions use a current
multiple of rated current (`k_converter`); the standard requires the
manufacturer's figure, so the default is stated with the result.
"""

from __future__ import annotations

import cmath
import math

from . import cables as cab_mod

SQRT3 = math.sqrt(3.0)

# Table 1 - voltage factor c. LV systems with +6 % tolerance (e.g. 400 V
# renamed from 380 V) use 1.05; +10 % systems 1.10.
C_FACTOR = {
    "lv6": {"max": 1.05, "min": 0.95},
    "lv10": {"max": 1.10, "min": 0.90},
    "hv": {"max": 1.10, "min": 1.00},
}

# Conductor temperature at the end of the fault for minimum currents,
# Formula (50): R = [1 + 0.004 (theta_e - 20)] R20. 145 C gives the
# 1.5 x R20 commonly used for LV disconnection checks.
THETA_END_C = 145.0
ALPHA = 0.004

# Making capacity Icm = n x Icu, IEC 60947-2 Table 2 (n against Icu kA).
def making_factor(icu_ka):
    if icu_ka <= 1.5:
        return 1.41
    if icu_ka <= 3:
        return 1.42
    if icu_ka <= 4.5:
        return 1.47
    if icu_ka <= 6:
        return 1.5
    if icu_ka <= 10:
        return 1.7
    if icu_ka <= 20:
        return 2.0
    if icu_ka <= 50:
        return 2.1
    return 2.2


def c_factors(un_v, tolerance="lv6"):
    if un_v > 1000:
        return C_FACTOR["hv"]
    return C_FACTOR.get(tolerance, C_FACTOR["lv6"])


def network_feeder(un_v, ik_ka, r_over_x=0.1, c=1.05):
    """Formula (23)/(24): Z_Q from the three-phase fault level at Q."""
    if not ik_ka or ik_ka <= 0:
        return None
    z = c * un_v / (SQRT3 * ik_ka * 1000.0)
    x = z / math.sqrt(1 + r_over_x ** 2)
    return complex(r_over_x * x, x)


def transformer(sr_kva, ur_lv_v, uk_pct=6.0, ur_pct=1.0, c_max=1.05,
                corrected=True):
    """
    Formulas (4)-(7) and (16)-(17), referred to the LV side.
    K_T = 0.95 c_max / (1 + 0.6 x_T).
    """
    if sr_kva <= 0:
        return None
    sr = sr_kva * 1000.0
    z = uk_pct / 100.0 * ur_lv_v ** 2 / sr
    r = ur_pct / 100.0 * ur_lv_v ** 2 / sr
    x = math.sqrt(max(0.0, z * z - r * r))
    zt = complex(r, x)
    if corrected:
        xt = x / (ur_lv_v ** 2 / sr)
        kt = 0.95 * c_max / (1.0 + 0.6 * xt)
        zt *= kt
    return zt


def synchronous_generator(sr_kva, ur_v, un_v, xd_pp=0.15, cos_phi=0.8,
                          c_max=1.05, corrected=True):
    """
    Formulas (26)-(27), (31)-(32). R_G = 0.15 X"d for generators of
    1 kV and below (IEC 60909-0 6.8.1).
    """
    if sr_kva <= 0:
        return None
    sr = sr_kva * 1000.0
    xd = xd_pp * ur_v ** 2 / sr
    z = complex(0.15 * xd, xd)
    if corrected:
        sin_phi = math.sqrt(max(0.0, 1 - cos_phi ** 2))
        kg = (un_v / ur_v) * c_max / (1.0 + xd_pp * sin_phi)
        z *= kg
    return z


def cable(csa_mm2, length_m, material="copper", parallel_runs=1,
          x_per_m=0.00008, theta_c=20.0):
    """Positive-sequence impedance of one circuit, all runs in parallel."""
    if not csa_mm2 or not length_m:
        return complex(0, 0)
    rho = cab_mod.RESISTIVITY.get(material, 0.01724)
    r20 = rho * length_m / (csa_mm2 * max(1, parallel_runs))
    r = r20 * (1.0 + ALPHA * (theta_c - 20.0))
    return complex(r, x_per_m * length_m / max(1, parallel_runs))


def pe_csa(phase_csa):
    """IEC 60364-5-54 Table 54.2 (PE of the same material as the line)."""
    if phase_csa <= 16:
        return phase_csa
    if phase_csa <= 35:
        return 16.0
    return phase_csa / 2.0


def kappa(z):
    """Formula (60): kappa = 1.02 + 0.98 exp(-3 R/X)."""
    if z.imag <= 0:
        return 1.02
    return 1.02 + 0.98 * math.exp(-3.0 * z.real / z.imag)


def _parallel(zs):
    zs = [z for z in zs if z is not None and abs(z) > 0]
    if not zs:
        return None
    y = sum(1.0 / z for z in zs)
    return 1.0 / y


class Busbar:
    """
    The LV main busbar and the sources feeding it.

    `voltage_sources` are impedances behind the equivalent source (network
    through its transformer, generators); `current_sources` are converter
    contributions in amperes (RMS, positive sequence) with a flag saying
    whether they are PV/wind (neglected for minimum currents, 7.1.3 d).
    """

    def __init__(self, un_v, tolerance="lv6"):
        self.un = float(un_v)
        self.c = c_factors(self.un, tolerance)
        self.vmax = []      # (name, Z corrected)
        self.vmin = []      # (name, Z uncorrected)
        self.isrc = []      # (name, amps, is_pv_or_wind)
        self.notes = []

    def add_network(self, ik_ka, r_over_x=0.1, via_transformer=None,
                    hv_v=None, cable_z=None):
        """
        Network feeder. With `via_transformer` = (kVA, uk%, ur%) the fault
        level is at the HV side and is referred through the transformer.
        """
        if via_transformer:
            kva, uk, ur = via_transformer
            zq_hv = network_feeder(hv_v, ik_ka, r_over_x, C_FACTOR["hv"]["max"])
            ratio = (self.un / hv_v) ** 2
            zq = zq_hv * ratio if zq_hv is not None else complex(0, 0)
            ztk = transformer(kva, self.un, uk, ur, self.c["max"], True)
            zt = transformer(kva, self.un, uk, ur, self.c["max"], False)
            self.vmax.append(("network via transformer", zq + ztk))
            # The same Z_Q for both: the minimum current then follows from
            # c_min (7.1.3 a). A separate minimum network fault level, when
            # the operator gives one, would be lower still.
            self.vmin.append(("network via transformer", zq + zt))
            self.notes.append(
                f"network {ik_ka:g} kA at {hv_v / 1000:g} kV through "
                f"{kva:,.0f} kVA, uk {uk:g}% (K_T applied)")
        else:
            zq = network_feeder(self.un, ik_ka, r_over_x, self.c["max"])
            if zq is None:
                return
            zc = cable_z or complex(0, 0)
            self.vmax.append(("network", zq + zc))
            self.vmin.append(("network", zq + zc))
            self.notes.append(f"network {ik_ka:g} kA at the PCC")

    def add_generator(self, kva_each, n, xd_pp=0.15, cos_phi=0.8):
        for _ in range(int(n)):
            self.vmax.append(("generator", synchronous_generator(
                kva_each, self.un, self.un, xd_pp, cos_phi, self.c["max"], True)))
            self.vmin.append(("generator", synchronous_generator(
                kva_each, self.un, self.un, xd_pp, cos_phi, self.c["max"], False)))
        if n:
            self.notes.append(f"{int(n)} generator(s) {kva_each:,.0f} kVA, "
                              f"x\"d = {xd_pp:g} (K_G applied)")

    def add_converter(self, name, rated_kva, k=1.2, renewable=True):
        if rated_kva <= 0:
            return
        i = k * rated_kva * 1000.0 / (SQRT3 * self.un)
        self.isrc.append((name, i, renewable))
        self.notes.append(f"{name} {rated_kva:,.0f} kVA as a {k:g} pu current source")

    # ------------------------------------------------------------ results

    def z_source(self, which="max"):
        lst = self.vmax if which == "max" else self.vmin
        return _parallel([z for _n, z in lst])

    def fault(self, z_line=complex(0, 0), z_line_min=None, z_pe_min=None):
        """
        Three-phase maximum and minimum, and line-to-earth minimum, at the
        busbar (z_line = 0) or at the end of a circuit.
        """
        cmax, cmin = self.c["max"], self.c["min"]
        zs_max = self.z_source("max")
        zs_min = self.z_source("min")

        # Converter currents reduced by the share flowing through the line:
        # sources at the busbar feed a remote fault through the same line,
        # so their current reaches it undiminished (current sources).
        i_conv_max = sum(i for _n, i, _r in self.isrc)
        i_conv_min = sum(i for _n, i, r in self.isrc if not r)

        ik_max = 0.0
        z_tot = None
        if zs_max is not None:
            z_tot = zs_max + z_line
            ik_max = abs(cmax * self.un / (SQRT3 * z_tot))
        ik_max += i_conv_max

        zlm = z_line_min if z_line_min is not None else z_line
        ik_min = 0.0
        ik1_min = 0.0
        if zs_min is not None:
            ik_min = abs(cmin * self.un / (SQRT3 * (zs_min + zlm)))
            z0_line = zlm + 3 * (z_pe_min if z_pe_min is not None else zlm)
            z1 = zs_min + zlm
            z0 = zs_min + z0_line
            ik1_min = abs(SQRT3 * cmin * self.un / (2 * z1 + z0))
        ik_min += i_conv_min
        # A current-limited converter delivers about the same current to a
        # line-to-earth fault as to a three-phase one.
        ik1_min += i_conv_min

        k = kappa(z_tot) if z_tot is not None else 1.02
        ip = k * math.sqrt(2) * (ik_max - i_conv_max) + math.sqrt(2) * i_conv_max
        return {
            "ik3_max_a": ik_max,
            "ik3_min_a": ik_min,
            "ik1_min_a": ik1_min,
            "ip_a": ip,
            "kappa": k,
            "r_over_x": (z_tot.real / z_tot.imag) if z_tot is not None and z_tot.imag else None,
        }

    def at_circuit_end(self, csa_mm2, length_m, material="copper",
                       parallel_runs=1):
        z20 = cable(csa_mm2, length_m, material, parallel_runs, theta_c=20.0)
        zhot = cable(csa_mm2, length_m, material, parallel_runs,
                     theta_c=THETA_END_C)
        pe = cable(pe_csa(csa_mm2), length_m, material, parallel_runs,
                   theta_c=THETA_END_C)
        return self.fault(z20, zhot, pe)


# ------------------------------------------------------- disconnection

# IEC 60364-4-41 Table 41.1 (TN, 120 V < U0 <= 230 V): final circuits up to
# 63 A with socket-outlets and 32 A fixed: 0.4 s; distribution and other
# circuits: 5 s (411.3.2.3).
def disconnection_time_s(rating_a, final_circuit):
    return 0.4 if final_circuit and rating_a <= 63 else 5.0


def device_operates(device, rating_a, curve, fault_a, t_required_s):
    """
    Whether the device clears `fault_a` within `t_required_s`.

    Circuit breakers are credited only with their instantaneous release
    (upper limit of the band, e.g. 10 In for curve C), which operates well
    inside 0.1 s; gG fuses with the conventional 5 s / 0.4 s points of
    IEC 60269 approximated as 6 In / 10 In. Anything else is reported as
    not verified rather than assumed to pass.
    """
    if fault_a <= 0:
        return False
    if device in ("mcb", "mccb"):
        hi = {"B": 5, "C": 10, "D": 20}.get(curve or "C", 10)
        return fault_a >= hi * rating_a
    if device == "acb":
        return fault_a >= 10 * rating_a
    if device.startswith("fuse"):
        mult = 10 if t_required_s <= 0.4 else 6
        return fault_a >= mult * rating_a
    return False
