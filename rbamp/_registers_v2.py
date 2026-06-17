"""
AUTO-GENERATED from libs/spec/registers_v2.yaml — DO NOT EDIT

Schema v2 (v1.3 wire contract). Regenerate:
    python tools/lib_codegen/codegen_v2.py

Production-build registers only; the factory cal block (build: cal) is
intentionally absent — factory tooling reads the YAML directly.
"""


RBAMP_REG_SCHEMA_CRC32_V2 = 0x5FB3E9F3
RBAMP_PROTOCOL_VERSION_V2 = (1, 3)  # 1.3

# ---- Register addresses + sizes ----
REG_STATUS                   = 0x00  # bit0=READY, bit1=ERROR, bit2=EVENTS_PENDING (v1.3: mirror of EVENT_FLAGS!=0)
REG_COMMAND                  = 0x01  # Write CMD_* opcode (commands.yaml)
REG_ERROR                    = 0x02  # 0x00=OK; 0xFA..0xFF error classes; ERR_CLONE added v1.3. Clear via CMD_CLEAR_ERROR (v1.3)
REG_VERSION                  = 0x03  # 0x01=v1.0 .. 0x04=v1.3
REG_MODE                     = 0x04  # 0=production, 1=develop (PB5 strap at boot)
REG_CT_MODEL                 = 0x05  # SCT-013 SKU 0=unset/1=-005/2=-010/3=-030/4=-050/5=-100/6=-020/7=-060 (v1.3). Direct write applies preset to ch
REG_V03_PHASE_SAMPLES        = 0x06  # U-vs-I sample advance, 0..30. Develop-gated write (v1.3). Save via CMD_SAVE_GAINS.
REG_V03_PERIOD_VALID         = 0x07  # Set by CMD_LATCH_PERIOD: 1=fresh snapshot, 0=empty accumulator (race). NOT cleared-on-read. Failed latch does 
REG_LUT_VALID_MASK           = 0x08  # bit n = slot n has valid LUT
REG_LUT_QUERY_SLOT           = 0x09  # Select slot 0..3 → metadata latched into 0x0A-0x0F
REG_LUT_VIEW_TIER            = 0x0A  # 0=BASIC, 1=STANDARD
REG_LUT_VIEW_POINTS_LOG2     = 0x0B  # 8 or 9
REG_LUT_VIEW_INL_MAX         = 0x0C  # Measured INL_max
REG_LUT_VIEW_INL_MAX_SIZE    = 2
REG_LUT_VIEW_DNL_MAX         = 0x0E  # Measured DNL_max
REG_LUT_VIEW_DNL_MAX_SIZE    = 2
REG_ADC_MEAN_U               = 0x10  # Raw ADC mean of U channel (~2048 centered). DC-offset cal: gate |mean-2048|<tol at 0A
REG_ADC_MEAN_U_SIZE          = 2
REG_ADC_MEAN_I0              = 0x12  # Raw ADC mean of I0 channel
REG_ADC_MEAN_I0_SIZE         = 2
REG_ADC_MEAN_I1              = 0x14  # Raw ADC mean of I1 (UI2/UI3/I2/I3)
REG_ADC_MEAN_I1_SIZE         = 2
REG_ADC_MEAN_I2              = 0x16  # Raw ADC mean of I2 (UI3/I3)
REG_ADC_MEAN_I2_SIZE         = 2
REG_CAPTURE_STATUS           = 0x18  # v1.3 raw-capture diag (major-carry glitch): bit0=ready. Arm via CMD_CAPTURE_RAW
REG_CAPTURE_PAGE             = 0x19  # Page 0..7 — latches 32 raw I0 samples into CAPTURE_WINDOW
REG_CAPTURE_WINDOW           = 0x1A  # 32×u16 LE raw pre-LUT I0 codes of selected page. Burst-read 64 bytes. 8 pages × 32 = 256 samples ~1.3 mains pe
REG_CAPTURE_WINDOW_SIZE      = 64
REG_AC_FREQ                  = 0x20  # 50 or 60
REG_AC_PERIOD                = 0x21  # Mains half-period
REG_AC_PERIOD_SIZE           = 2
REG_CALIBRATION              = 0x23  # Legacy calibration status byte
REG_TOPOLOGY                 = 0x24  # 1=SINGLE, 2=SPLIT_PHASE, 3=THREE_PHASE (=V03_N_I)
REG_SENSOR_CLASS             = 0x25  # 0=UNSET, 1=SCT_013, 2=WIRED_CT, 3=BUILTIN_CT. Class change resets CT_MODEL=0.
REG_V03_PHASE_FRACT          = 0x26  # Sub-sample phase shift Q8. Develop-gated write (v1.3). Save via CMD_SAVE_GAINS.
REG_FLEET_CONFIG             = 0x27  # bit0=GC_ENABLE (General-Call latch reception; effective after reset - ENGC not toggled live). bits1-7 reserved
REG_GROUP_ID                 = 0x28  # GC latch group filter. 0 = respond to all-call only. GC frame group byte must match or be 0x00
REG_DIGEST_CONFIG            = 0x29  # Digest window composition bitmask (see digest_mask_bits). Bits unsupported by variant → ERR_PARAM. 0 = digest 
REG_EVENT_FLAGS              = 0x2A  # Sticky event bits, write-1-to-clear (see event_bits). DRDY held solid LOW while (EVENT_FLAGS & EVENT_MASK) != 
REG_EVENT_MASK               = 0x2B  # Which EVENT_FLAGS bits assert DRDY solid LOW (alarm class). 0 = line never held
REG_THRESH_I_HI              = 0x2C  # Current threshold → EVENT_FLAGS.THRESH_I. 0xFFFF = disabled. Applies to max(I_rms[ch])
REG_THRESH_I_HI_SIZE         = 2
REG_THRESH_P_HI              = 0x2E  # Power threshold → EVENT_FLAGS.THRESH_P. 0xFFFF = disabled. Applies to sum(P[ch])
REG_THRESH_P_HI_SIZE         = 2
REG_I2C_ADDRESS              = 0x30  # v1.3 two-phase: write candidate (0x08..0x77) -> RAM only (reads return staged value); arm ADDR_COMMIT_MAGIC th
REG_ADDR_COMMIT_MAGIC        = 0x31  # Write 0xA5 to arm CMD_COMMIT_ADDR; consumed (cleared) on commit attempt. Write-only - reads return 0x00
REG_UPTIME_S                 = 0x46  # Seconds since boot
REG_UPTIME_S_SIZE            = 4
REG_RESET_CAUSE              = 0x4A  # Last reset reason flags from RCC_CSR: bit0=PIN, bit1=POR/BOR, bit2=SW, bit3=IWDG, bit4=WWDG, bit5=LPWR
REG_I2C_ERR_COUNT            = 0x4B  # Accumulated bus errors (BERR+OVR) since boot, saturating
REG_I2C_ERR_COUNT_SIZE       = 2
REG_I2C_REINIT_COUNT         = 0x4D  # I2C peripheral BUSY-recovery reinit count, saturating
REG_ZC_OFFSET                = 0x4E  # Time from last GC-latch STOP edge to next voltage zero-cross. U-variants only (CAPABILITY bit); I-variants rea
REG_ZC_OFFSET_SIZE           = 2
REG_CT_MODEL_CH0             = 0x51  # v1.3 D-1.3: CT model actually APPLIED to channel 0 (0=unset). Mixed-CT modules: per-channel assignment persist
REG_CT_MODEL_CH1             = 0x52  # Model applied to channel 1
REG_CT_MODEL_CH2             = 0x53  # Model applied to channel 2
REG_PRODUCT_ID               = 0x54  # Product family: 0x01=rbAmp sensor, 0x02=rbDimmer (own map!). Master MUST read before interpreting family-speci
REG_HW_VARIANT               = 0x55  # BUILD_VARIANT: 1=UI1, 2=UI2, 3=UI3, 4=I1, 5=I2, 6=I3
REG_FW_TIER                  = 0x56  # bits0-1: 0=BASIC,1=STANDARD,2=PRO; bit2=bidirectional; bit3=LUT-calibrated
REG_CAPABILITY               = 0x57  # Feature bitmap (see capability_bits). Libraries branch on bits, never on VERSION heuristics
REG_CAPABILITY_SIZE          = 2
REG_GC_TICK                  = 0x59  # Master tick from last accepted GC-latch frame; 0xFFFF = never received. Fleet-wide window numbering + per-modu
REG_GC_TICK_SIZE             = 2
REG_UID                      = 0x5C  # 96-bit chip UID (3×u32 LE from UID_BASE). One burst read. Used by: address arbitration, seal verification, sti
REG_UID_SIZE                 = 12
REG_LABEL                    = 0x68  # User location label, ASCII zero-padded ('boiler'). Empty = unset → replacement-detection signal
REG_LABEL_SIZE               = 8
REG_DIGEST                   = 0x70  # Compact poll window, one burst read. Layout: [STATUS_MIRROR u8][SEQ u8] then fields in canonical order, only m
REG_DIGEST_SIZE              = 22
REG_V03_U_RMS                = 0x86  # 0.0 on I-variants
REG_V03_U_RMS_SIZE           = 4
REG_V03_U_PEAK               = 0x8A  # 
REG_V03_U_PEAK_SIZE          = 4
REG_V03_I0_RMS               = 0x8E  # 
REG_V03_I0_RMS_SIZE          = 4
REG_V03_I1_RMS               = 0x92  # 0.0 if variant lacks ch1
REG_V03_I1_RMS_SIZE          = 4
REG_V03_I2_RMS               = 0x96  # 0.0 if variant lacks ch2
REG_V03_I2_RMS_SIZE          = 4
REG_V03_I0_PEAK              = 0x9A  # 
REG_V03_I0_PEAK_SIZE         = 4
REG_V03_I1_PEAK              = 0x9E  # 
REG_V03_I1_PEAK_SIZE         = 4
REG_V03_I2_PEAK              = 0xA2  # 
REG_V03_I2_PEAK_SIZE         = 4
REG_V03_P0_REAL              = 0xA6  # 0.0 on I-variants (no power calc)
REG_V03_P0_REAL_SIZE         = 4
REG_V03_P1_REAL              = 0xAA  # 
REG_V03_P1_REAL_SIZE         = 4
REG_V03_P2_REAL              = 0xAE  # 
REG_V03_P2_REAL_SIZE         = 4
REG_V03_PF0                  = 0xB2  # -1..+1
REG_V03_PF0_SIZE             = 4
REG_V03_PF1                  = 0xB6  # 
REG_V03_PF1_SIZE             = 4
REG_V03_PF2                  = 0xBA  # 
REG_V03_PF2_SIZE             = 4
REG_V03_PERIOD_COMMIT_CNT    = 0xBE  # RT commits within current period (diagnostic)
REG_V03_PERIOD_COMMIT_CNT_SIZE = 4
REG_V03_PERIOD_AVG_P_CH1     = 0xC2  # Latched avg P ch1 (UI2/UI3)
REG_V03_PERIOD_AVG_P_CH1_SIZE = 4
REG_V03_PERIOD_AVG_P_CH2     = 0xC6  # Latched avg P ch2 (UI3)
REG_V03_PERIOD_AVG_P_CH2_SIZE = 4
REG_V03_PERIOD_MS            = 0xCA  # Current period duration
REG_V03_PERIOD_MS_SIZE       = 4
REG_V03_STATUS               = 0xCE  # bit0=valid (RT commit result). NOT cleared-on-read. Libraries use STATUS 0x00 for ready-wait
REG_V03_RESERVED_CF          = 0xCF  # Reserved, reads 0x00
REG_V03_Q0_REAC              = 0xD0  # Reactive power ch0 (IEEE 1459 quadrature)
REG_V03_Q0_REAC_SIZE         = 4
REG_V03_Q1_REAC              = 0xD4  # 
REG_V03_Q1_REAC_SIZE         = 4
REG_V03_Q2_REAC              = 0xD8  # 
REG_V03_Q2_REAC_SIZE         = 4
REG_V03_PERIOD_AVG_P         = 0xDC  # PRODUCTION energy primitive: latched avg P ch0, >=0 (BASIC unidirectional clamp)
REG_V03_PERIOD_AVG_P_SIZE    = 4
REG_V03_PERIOD_MAX_P         = 0xE0  # Latched max P ch0 this period
REG_V03_PERIOD_MAX_P_SIZE    = 4
REG_V03_U_NOISE_FLOOR        = 0xE4  # Develop-gated write (v1.3)
REG_V03_U_NOISE_FLOOR_SIZE   = 2
REG_V03_I0_NOISE_FLOOR       = 0xE6  # Develop-gated write (v1.3)
REG_V03_I0_NOISE_FLOOR_SIZE  = 2
REG_V03_I1_NOISE_FLOOR       = 0xE8  # Develop-gated write (v1.3)
REG_V03_I1_NOISE_FLOOR_SIZE  = 2
REG_V03_I2_NOISE_FLOOR       = 0xEA  # Develop-gated write (v1.3)
REG_V03_I2_NOISE_FLOOR_SIZE  = 2
REG_V03_PERIOD_LATCH_MS      = 0xEC  # Chip-side dt between last two latches. Master fallback after its own restart
REG_V03_PERIOD_LATCH_MS_SIZE = 4
REG_V03_U_GAIN               = 0xF0  # Develop-gated write (v1.3). Save via CMD_SAVE_GAINS
REG_V03_U_GAIN_SIZE          = 4
REG_V03_I0_GAIN              = 0xF4  # Develop-gated write (v1.3)
REG_V03_I0_GAIN_SIZE         = 4
REG_V03_I1_GAIN              = 0xF8  # Develop-gated write (v1.3)
REG_V03_I1_GAIN_SIZE         = 4
REG_V03_I2_GAIN              = 0xFC  # Develop-gated write (v1.3)
REG_V03_I2_GAIN_SIZE         = 4

# ---- Command opcodes / settle ----
CMD_NOP                      = 0x00
CMD_RESET                    = 0x01
CMD_RECALIBRATE              = 0x02
CMD_SWITCH_UART              = 0x03
CMD_CAL_BEGIN                = 0x20
CMD_CAL_SAMPLE               = 0x21
CMD_CAL_LUT_WRITE            = 0x22
CMD_CAL_LUT_COMMIT           = 0x23
CMD_CAL_LUT_ABORT            = 0x24
CMD_CAL_END                  = 0x25
CMD_SAVE_GAINS               = 0x26
CMD_LATCH_PERIOD             = 0x27
CMD_SET_CT_MODEL_CH0         = 0x28
CMD_SET_CT_MODEL_CH1         = 0x29
CMD_SET_CT_MODEL_CH2         = 0x2A
CMD_COMMIT_ADDR              = 0x30
CMD_CLEAR_ERROR              = 0x31
CMD_SAVE_USER_CONFIG         = 0x32
CMD_SEAL                     = 0x33
CMD_UID_ARBITRATE            = 0x34
CMD_UID_PRESENT              = 0x35
CMD_UID_MUTE_RESET           = 0x36
CMD_ENTER_BOOTLOADER         = 0x37
CMD_CAPTURE_RAW              = 0x38
CMD_FACTORY_RESET            = 0xAA

SETTLE_MS_NOP                = 0
SETTLE_MS_RESET              = 300
SETTLE_MS_RECALIBRATE        = 200
SETTLE_MS_SWITCH_UART        = 50
SETTLE_MS_CAL_BEGIN          = 10
SETTLE_MS_CAL_SAMPLE         = 50
SETTLE_MS_CAL_LUT_WRITE      = 5
SETTLE_MS_CAL_LUT_COMMIT     = 700
SETTLE_MS_CAL_LUT_ABORT      = 5
SETTLE_MS_CAL_END            = 50
SETTLE_MS_SAVE_GAINS         = 700
SETTLE_MS_LATCH_PERIOD       = 50
SETTLE_MS_SET_CT_MODEL_CH0   = 5
SETTLE_MS_SET_CT_MODEL_CH1   = 5
SETTLE_MS_SET_CT_MODEL_CH2   = 5
SETTLE_MS_COMMIT_ADDR        = 700
SETTLE_MS_CLEAR_ERROR        = 0
SETTLE_MS_SAVE_USER_CONFIG   = 700
SETTLE_MS_SEAL               = 700
SETTLE_MS_UID_ARBITRATE      = 5
SETTLE_MS_UID_PRESENT        = 10
SETTLE_MS_UID_MUTE_RESET     = 10
SETTLE_MS_ENTER_BOOTLOADER   = 100
SETTLE_MS_CAPTURE_RAW        = 80
SETTLE_MS_FACTORY_RESET      = 1500

# ---- Device / library error codes ----
DEV_ERR_OK                   = 0x00
DEV_ERR_CLONE                = 0xF9
DEV_ERR_LUT_BAD              = 0xFA
DEV_ERR_FLASH_PARAMS_BAD     = 0xFB
DEV_ERR_NOT_READY            = 0xFC
DEV_ERR_SENSOR_OVERFLOW      = 0xFD
DEV_ERR_PARAM                = 0xFE
DEV_ERR_UNHANDLED            = 0xFF
LIB_OK                       = 0
LIB_ERR_IO                   = -1
LIB_ERR_NACK                 = -2
LIB_ERR_TIMEOUT              = -3
LIB_ERR_NOT_READY            = -4
LIB_ERR_STALE                = -5
LIB_ERR_PARAM                = -6
LIB_ERR_MODE                 = -7
LIB_ERR_CHECKSUM             = -8
LIB_ERR_VERSION              = -9
LIB_ERR_NOT_IMPLEMENTED      = -10
LIB_ERR_NON_PHYSICAL         = -11

# ---- Bit tables ----
CAP_EXT_ADDRESSING         = 1 << 0
CAP_GC_LATCH               = 1 << 1
CAP_GC_GROUP_FILTER        = 1 << 2
CAP_DIGEST                 = 1 << 3
CAP_EVENTS                 = 1 << 4
CAP_UID_ARBITRATION        = 1 << 5
CAP_SEAL                   = 1 << 6
CAP_TWO_PHASE_ADDR         = 1 << 7
CAP_ZC_PHASE_OFFSET        = 1 << 8
CAP_SAVE_USER_CONFIG       = 1 << 9
CAP_CLEAR_ERROR            = 1 << 10
CAP_IAP                    = 1 << 11

DIGEST_I_RMS                  = 1 << 0
DIGEST_U_RMS                  = 1 << 1
DIGEST_P_REAL                 = 1 << 2
DIGEST_PF                     = 1 << 3

EVENT_PERIOD_READY           = 1 << 0
EVENT_THRESH_I               = 1 << 1
EVENT_THRESH_P               = 1 << 2
EVENT_ERROR                  = 1 << 3
EVENT_CONFIG_CHANGED         = 1 << 4
EVENT_RESET_OCCURRED         = 1 << 5

# ---- Full register table (runtime introspection) ----
REGISTERS = (
    dict(name='STATUS', addr=0x00, size=1, type='u8', endian=None, access='r', group='control', persistence='ram'),
    dict(name='COMMAND', addr=0x01, size=1, type='u8', endian=None, access='w', group='control', persistence='ram'),
    dict(name='ERROR', addr=0x02, size=1, type='u8', endian=None, access='r', group='control', persistence='ram'),
    dict(name='VERSION', addr=0x03, size=1, type='u8', endian=None, access='r', group='control', persistence='rom'),
    dict(name='MODE', addr=0x04, size=1, type='u8', endian=None, access='r', group='control', persistence='ram'),
    dict(name='CT_MODEL', addr=0x05, size=1, type='u8', endian=None, access='rw', group='control', persistence='user_config'),
    dict(name='V03_PHASE_SAMPLES', addr=0x06, size=1, type='u8', endian=None, access='rw', group='control', persistence='flash'),
    dict(name='V03_PERIOD_VALID', addr=0x07, size=1, type='u8', endian=None, access='r', group='control', persistence='ram'),
    dict(name='LUT_VALID_MASK', addr=0x08, size=1, type='u8', endian=None, access='r', group='lut_debug', persistence='ram'),
    dict(name='LUT_QUERY_SLOT', addr=0x09, size=1, type='u8', endian=None, access='rw', group='lut_debug', persistence='ram'),
    dict(name='LUT_VIEW_TIER', addr=0x0A, size=1, type='u8', endian=None, access='r', group='lut_debug', persistence='ram'),
    dict(name='LUT_VIEW_POINTS_LOG2', addr=0x0B, size=1, type='u8', endian=None, access='r', group='lut_debug', persistence='ram'),
    dict(name='LUT_VIEW_INL_MAX', addr=0x0C, size=2, type='u16', endian='le', access='r', group='lut_debug', persistence='ram'),
    dict(name='LUT_VIEW_DNL_MAX', addr=0x0E, size=2, type='u16', endian='le', access='r', group='lut_debug', persistence='ram'),
    dict(name='ADC_MEAN_U', addr=0x10, size=2, type='u16', endian='le', access='r', group='diag', persistence='ram'),
    dict(name='ADC_MEAN_I0', addr=0x12, size=2, type='u16', endian='le', access='r', group='diag', persistence='ram'),
    dict(name='ADC_MEAN_I1', addr=0x14, size=2, type='u16', endian='le', access='r', group='diag', persistence='ram'),
    dict(name='ADC_MEAN_I2', addr=0x16, size=2, type='u16', endian='le', access='r', group='diag', persistence='ram'),
    dict(name='CAPTURE_STATUS', addr=0x18, size=1, type='u8', endian=None, access='r', group='diag', persistence='ram'),
    dict(name='CAPTURE_PAGE', addr=0x19, size=1, type='u8', endian=None, access='rw', group='diag', persistence='ram'),
    dict(name='CAPTURE_WINDOW', addr=0x1A, size=64, type='bytes', endian=None, access='r', group='diag', persistence='ram'),
    dict(name='AC_FREQ', addr=0x20, size=1, type='u8', endian=None, access='r', group='system', persistence='ram'),
    dict(name='AC_PERIOD', addr=0x21, size=2, type='u16', endian='le', access='r', group='system', persistence='ram'),
    dict(name='CALIBRATION', addr=0x23, size=1, type='u8', endian=None, access='r', group='system', persistence='ram'),
    dict(name='TOPOLOGY', addr=0x24, size=1, type='u8', endian=None, access='r', group='system', persistence='rom'),
    dict(name='SENSOR_CLASS', addr=0x25, size=1, type='u8', endian=None, access='rw', group='system', persistence='user_config'),
    dict(name='V03_PHASE_FRACT', addr=0x26, size=1, type='u8', endian=None, access='rw', group='system', persistence='flash'),
    dict(name='FLEET_CONFIG', addr=0x27, size=1, type='u8', endian=None, access='rw', group='fleet', persistence='user_config'),
    dict(name='GROUP_ID', addr=0x28, size=1, type='u8', endian=None, access='rw', group='fleet', persistence='user_config'),
    dict(name='DIGEST_CONFIG', addr=0x29, size=1, type='u8', endian=None, access='rw', group='fleet', persistence='ram'),
    dict(name='EVENT_FLAGS', addr=0x2A, size=1, type='u8', endian=None, access='w1c', group='fleet', persistence='ram'),
    dict(name='EVENT_MASK', addr=0x2B, size=1, type='u8', endian=None, access='rw', group='fleet', persistence='ram'),
    dict(name='THRESH_I_HI', addr=0x2C, size=2, type='u16', endian='le', access='rw', group='fleet', persistence='ram'),
    dict(name='THRESH_P_HI', addr=0x2E, size=2, type='u16', endian='le', access='rw', group='fleet', persistence='ram'),
    dict(name='I2C_ADDRESS', addr=0x30, size=1, type='u8', endian=None, access='rw', group='system', persistence='user_config'),
    dict(name='ADDR_COMMIT_MAGIC', addr=0x31, size=1, type='u8', endian=None, access='w', group='system', persistence='ram'),
    dict(name='UPTIME_S', addr=0x46, size=4, type='u32', endian='le', access='r', group='health', persistence='ram'),
    dict(name='RESET_CAUSE', addr=0x4A, size=1, type='u8', endian=None, access='r', group='health', persistence='ram'),
    dict(name='I2C_ERR_COUNT', addr=0x4B, size=2, type='u16', endian='le', access='r', group='health', persistence='ram'),
    dict(name='I2C_REINIT_COUNT', addr=0x4D, size=1, type='u8', endian=None, access='r', group='health', persistence='ram'),
    dict(name='ZC_OFFSET', addr=0x4E, size=2, type='u16', endian='le', access='r', group='health', persistence='ram'),
    dict(name='CT_MODEL_CH0', addr=0x51, size=1, type='u8', endian=None, access='r', group='identity', persistence='user_config'),
    dict(name='CT_MODEL_CH1', addr=0x52, size=1, type='u8', endian=None, access='r', group='identity', persistence='user_config'),
    dict(name='CT_MODEL_CH2', addr=0x53, size=1, type='u8', endian=None, access='r', group='identity', persistence='user_config'),
    dict(name='PRODUCT_ID', addr=0x54, size=1, type='u8', endian=None, access='r', group='identity', persistence='rom'),
    dict(name='HW_VARIANT', addr=0x55, size=1, type='u8', endian=None, access='r', group='identity', persistence='rom'),
    dict(name='FW_TIER', addr=0x56, size=1, type='u8', endian=None, access='r', group='identity', persistence='rom'),
    dict(name='CAPABILITY', addr=0x57, size=2, type='u16', endian='le', access='r', group='identity', persistence='rom'),
    dict(name='GC_TICK', addr=0x59, size=2, type='u16', endian='le', access='r', group='fleet', persistence='ram'),
    dict(name='UID', addr=0x5C, size=12, type='bytes', endian=None, access='r', group='identity', persistence='rom'),
    dict(name='LABEL', addr=0x68, size=8, type='bytes', endian=None, access='rw', group='identity', persistence='user_config'),
    dict(name='DIGEST', addr=0x70, size=22, type='bytes', endian=None, access='r', group='digest', persistence='ram'),
    dict(name='V03_U_RMS', addr=0x86, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_U_PEAK', addr=0x8A, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_I0_RMS', addr=0x8E, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_I1_RMS', addr=0x92, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_I2_RMS', addr=0x96, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_I0_PEAK', addr=0x9A, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_I1_PEAK', addr=0x9E, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_I2_PEAK', addr=0xA2, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_P0_REAL', addr=0xA6, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_P1_REAL', addr=0xAA, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_P2_REAL', addr=0xAE, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_PF0', addr=0xB2, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_PF1', addr=0xB6, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_PF2', addr=0xBA, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_PERIOD_COMMIT_CNT', addr=0xBE, size=4, type='u32', endian='le', access='r', group='v03_period', persistence='ram'),
    dict(name='V03_PERIOD_AVG_P_CH1', addr=0xC2, size=4, type='float32', endian='le', access='r', group='v03_period', persistence='ram'),
    dict(name='V03_PERIOD_AVG_P_CH2', addr=0xC6, size=4, type='float32', endian='le', access='r', group='v03_period', persistence='ram'),
    dict(name='V03_PERIOD_MS', addr=0xCA, size=4, type='u32', endian='le', access='r', group='v03_period', persistence='ram'),
    dict(name='V03_STATUS', addr=0xCE, size=1, type='u8', endian=None, access='r', group='v03_period', persistence='ram'),
    dict(name='V03_RESERVED_CF', addr=0xCF, size=1, type='u8', endian=None, access='r', group='v03_period', persistence='ram'),
    dict(name='V03_Q0_REAC', addr=0xD0, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_Q1_REAC', addr=0xD4, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_Q2_REAC', addr=0xD8, size=4, type='float32', endian='le', access='r', group='v03_rt', persistence='ram'),
    dict(name='V03_PERIOD_AVG_P', addr=0xDC, size=4, type='float32', endian='le', access='r', group='v03_period', persistence='ram'),
    dict(name='V03_PERIOD_MAX_P', addr=0xE0, size=4, type='float32', endian='le', access='r', group='v03_period', persistence='ram'),
    dict(name='V03_U_NOISE_FLOOR', addr=0xE4, size=2, type='u16', endian='le', access='rw', group='factory_cal', persistence='flash'),
    dict(name='V03_I0_NOISE_FLOOR', addr=0xE6, size=2, type='u16', endian='le', access='rw', group='factory_cal', persistence='flash'),
    dict(name='V03_I1_NOISE_FLOOR', addr=0xE8, size=2, type='u16', endian='le', access='rw', group='factory_cal', persistence='flash'),
    dict(name='V03_I2_NOISE_FLOOR', addr=0xEA, size=2, type='u16', endian='le', access='rw', group='factory_cal', persistence='flash'),
    dict(name='V03_PERIOD_LATCH_MS', addr=0xEC, size=4, type='u32', endian='le', access='r', group='v03_period', persistence='ram'),
    dict(name='V03_U_GAIN', addr=0xF0, size=4, type='float32', endian='le', access='rw', group='factory_cal', persistence='flash'),
    dict(name='V03_I0_GAIN', addr=0xF4, size=4, type='float32', endian='le', access='rw', group='factory_cal', persistence='flash'),
    dict(name='V03_I1_GAIN', addr=0xF8, size=4, type='float32', endian='le', access='rw', group='factory_cal', persistence='flash'),
    dict(name='V03_I2_GAIN', addr=0xFC, size=4, type='float32', endian='le', access='rw', group='factory_cal', persistence='flash'),
)

COMMANDS = (
    dict(name='NOP', opcode=0x00, settle_ms=0),
    dict(name='RESET', opcode=0x01, settle_ms=300),
    dict(name='RECALIBRATE', opcode=0x02, settle_ms=200),
    dict(name='SWITCH_UART', opcode=0x03, settle_ms=50),
    dict(name='CAL_BEGIN', opcode=0x20, settle_ms=10),
    dict(name='CAL_SAMPLE', opcode=0x21, settle_ms=50),
    dict(name='CAL_LUT_WRITE', opcode=0x22, settle_ms=5),
    dict(name='CAL_LUT_COMMIT', opcode=0x23, settle_ms=700),
    dict(name='CAL_LUT_ABORT', opcode=0x24, settle_ms=5),
    dict(name='CAL_END', opcode=0x25, settle_ms=50),
    dict(name='SAVE_GAINS', opcode=0x26, settle_ms=700),
    dict(name='LATCH_PERIOD', opcode=0x27, settle_ms=50),
    dict(name='SET_CT_MODEL_CH0', opcode=0x28, settle_ms=5),
    dict(name='SET_CT_MODEL_CH1', opcode=0x29, settle_ms=5),
    dict(name='SET_CT_MODEL_CH2', opcode=0x2A, settle_ms=5),
    dict(name='COMMIT_ADDR', opcode=0x30, settle_ms=700),
    dict(name='CLEAR_ERROR', opcode=0x31, settle_ms=0),
    dict(name='SAVE_USER_CONFIG', opcode=0x32, settle_ms=700),
    dict(name='SEAL', opcode=0x33, settle_ms=700),
    dict(name='UID_ARBITRATE', opcode=0x34, settle_ms=5),
    dict(name='UID_PRESENT', opcode=0x35, settle_ms=10),
    dict(name='UID_MUTE_RESET', opcode=0x36, settle_ms=10),
    dict(name='ENTER_BOOTLOADER', opcode=0x37, settle_ms=100),
    dict(name='CAPTURE_RAW', opcode=0x38, settle_ms=80),
    dict(name='FACTORY_RESET', opcode=0xAA, settle_ms=1500),
)

EXTENDED_SPACE = (
    dict(range='0x0100-0x011F', purpose='Bidirectional: PERIOD_AVG_P_NEG[3] f32, E_NEG accumulators (decision 5.3: F4 tiers only)'),
    dict(range='0x0120-0x01FF', purpose='Channels 3..7 (UI5/UI7): RT float block mirroring 0x86 layout'),
    dict(range='0x0200-0x02FF', purpose='IAP/bootloader control block (F4)'),
    dict(range='0x0300-0xFFFF', purpose='reserved'),
)
