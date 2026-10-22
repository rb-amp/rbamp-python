# SPDX-License-Identifier: MIT
"""
SCT-013 rating-switch correction (BASIC firmware <= 1.4.19).

Canon: ``libs/spec/SCT_RATING_SWITCH.md``. Every BASIC SCT batch is
calibrated on SCT-013-030 (model 3). When the user switches a channel to the
sensor they actually own (model X != 3), fw <= 1.4.19 cannot re-scale the
factory gain: after a restart the module keeps the 030 gain but shows X, so
current/power read at the 030 scale. The library can read the gain the module
applies (user-mode read; the gain write is factory-gated and never issued by
the library) and correct the reading relative to it.

This module is pure (no I/O): :func:`rating_correction` takes the shown model
and the applied gain and returns the scale factor + a verdict. The I/O (read
model + gain, the one-shot CMD_RESET on the same-boot state, applying the
scale) lives in :class:`rbamp.RbAmp`. Plain functions + module constants only
(MicroPython-safe: no dataclass / enum / typing).
"""

import struct

# Sensor classes (mirror REG_SENSOR_CLASS 0x25) — correction is class-1 only.
SENSOR_CLASS_SCT_013 = 1

CT_SCT013_030 = 3                      # the calibration model (c = 1, no scale)

# preset gain per SCT-013 model code, from firmware ``ct_presets.c`` (op-amp
# revision). These are the RAW table gains the correction is computed against;
# they are NOT in the generated ``_sensor_models`` registry (that holds names
# + limits only). Flagged to root to fold into ``sensor_models.yaml`` codegen
# so all libraries share one source.
PRESET_G = {
    1: 0.1058,    # SCT-013-005
    2: 0.2116,    # SCT-013-010
    6: 0.4233,    # SCT-013-020
    3: 0.6349,    # SCT-013-030 (calibration model)
    4: 1.0582,    # SCT-013-050
    7: 1.2698,    # SCT-013-060
    5: 2.1622,    # SCT-013-100
}

# rating correction c(X): bench residual of the preset vs truth. Only the
# -100 preset reads high (+4.0 % bench / +3.9 % / hub +3.7..5.2 %); the rest
# are within +-1 % so c = 1. Default 1.0 for any model not listed.
RATING_C = {
    5: 0.962,     # SCT-013-100
}

PRESET_CAL = PRESET_G[CT_SCT013_030]   # 0.6349 — the gain the batch is cal'd at
SANITY_TOL = 0.05                      # |G_a / preset(3) - 1| must be <= 5 %

# verdict actions returned alongside the scale
RATING_UNITY       = "unity"        # s = 1: model 3, unknown model, or n/a
RATING_OK          = "ok"           # normal flash state, scale applied
RATING_NEEDS_RESET = "needs_reset"  # same-boot raw preset: CMD_RESET once, re-read
RATING_WARN_UNCAL  = "warn_uncal"   # still raw preset after reset: uncalibrated (k=1)
RATING_WARN_GAIN   = "warn_gain"    # factory gain not ~preset(3): do not guess, s = 1


def preset_gain(model):
    """Raw preset gain for an SCT model code, or ``None`` if unknown."""
    return PRESET_G.get(model)


def rating_c(model):
    """Rating correction c(X) for an SCT model code (1.0 if none)."""
    return RATING_C.get(model, 1.0)


def _f32_eq(a, b):
    """True if two values are bit-equal as IEEE-754 float32 (the device width)."""
    return struct.pack("<f", a) == struct.pack("<f", b)


def rating_correction(model, g_applied, post_reset=False):
    """Scale factor for a SCT-013 channel on fw <= 1.4.19.

    Pure policy — mirrors ``SCT_RATING_SWITCH.md`` §Algorithm step 2/3. The
    caller has already established this is a class-1 SCT channel on firmware
    that does NOT self-correct (fw < 1.4.20).

    Args:
        model (int): CT model code the module currently shows.
        g_applied (float): the gain the module is actually applying (read from
            0xF4/0xF8/0xFC or window field 5; never written).
        post_reset (bool): True on the re-read after a same-boot CMD_RESET.

    Returns:
        (scale, action, detail): ``scale`` to multiply I/P/energy by;
        ``action`` one of the ``RATING_*`` constants; ``detail`` a short string
        for logging. ``action == RATING_NEEDS_RESET`` means the caller should
        issue one CMD_RESET and call again with ``post_reset=True``.
    """
    preset = PRESET_G.get(model)
    if model == CT_SCT013_030 or preset is None:
        # 030 is the calibration model (c = 1, k cancels -> s = 1); an unknown
        # code cannot be corrected (leave the reading untouched).
        return 1.0, RATING_UNITY, "model {} needs no correction".format(model)

    c = RATING_C.get(model, 1.0)

    # Same-boot state: the module applies the raw X preset (a model write in
    # this boot), so the per-unit trim k from the 030 cal is unknown.
    if _f32_eq(g_applied, preset):
        if not post_reset:
            return 1.0, RATING_NEEDS_RESET, \
                "gain == preset({}) raw — CMD_RESET to recover factory gain".format(model)
        # Still the raw preset after a reset -> treat as uncalibrated, k = 1.
        # s = preset(X) * c(X) * k / G_a, with k = 1 and G_a = preset(X) -> c(X).
        return c, RATING_WARN_UNCAL, \
            "uncalibrated: gain still preset({}) after reset, k=1 -> s=c".format(model)

    # Flash state: sanity-check the factory gain is ~preset(3) (within 5 %).
    if abs(g_applied / PRESET_CAL - 1.0) > SANITY_TOL:
        return 1.0, RATING_WARN_GAIN, \
            "unexpected factory gain {:.4f} vs preset(3) {:.4f} -> s=1".format(
                g_applied, PRESET_CAL)

    # Normal flash state. k = G_a / preset(3); the applied scale is
    #   s = preset(X) * c(X) * k / G_a = preset(X) * c(X) / preset(3)
    # (G_a and k cancel). G_a is used only for the sanity check above.
    s = preset * c / PRESET_CAL
    return s, RATING_OK, "k={:.5f}".format(g_applied / PRESET_CAL)
