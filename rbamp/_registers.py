"""
AUTO-GENERATED from libs/spec/registers.yaml — DO NOT EDIT

Regenerate with:  python tools/lib_codegen/codegen.py

Any divergence between this file and libs/spec/registers.yaml is a bug.
tools/lib_codegen/parity_check.py enforces consistency across all 5 libs.
"""


RBAMP_REG_SCHEMA_CRC32 = 0x53BC3606
RBAMP_PROTOCOL_VERSION = (1, 0)  # 1.2

# ---- Register addresses ----
REG_STATUS                     = 0x00  # bit0=READY, bit1=ERROR
REG_COMMAND                    = 0x01  # Write CMD_* opcode (see commands.yaml)
REG_ERROR                      = 0x02  # 0x00=OK; 0xFA..0xFF error classes (see errors.yaml)
REG_VERSION                    = 0x03  # Firmware version: 0x01 = v1.0
REG_MODE                       = 0x04  # 0=production, 1=develop (mirrors g_mode_develop pin)
REG_CT_MODEL                   = 0x05  # SCT-013 SKU 0=unset/1=-005/2=-010/3=-030/4=-050/5=-100. Save via CMD_SAVE_GAINS.
REG_V03_PHASE_SAMPLES          = 0x06  # ADC sample advance of U vs I for cross-product. Range 0..30. Save via CMD_SAVE_GAINS.
REG_V03_PERIOD_VALID           = 0x07  # After CMD_LATCH_PERIOD: bit0=1 if snapshot fresh, 0 if race. Master MUST check before reading period block.
REG_LUT_VALID_MASK             = 0x08  # bit n = 1 if slot n (0..3) has valid LUT
REG_LUT_QUERY_SLOT             = 0x09  # Write 0..3 to latch metadata into 0x0A-0x0F
REG_LUT_VIEW_TIER              = 0x0A  # Tier of queried slot: 0=BASIC, 1=STANDARD
REG_LUT_VIEW_POINTS_LOG2       = 0x0B  # 8 (256 pts) or 9 (512 pts)
REG_LUT_VIEW_INL_MAX_L         = 0x0C  # uint16 LE LSB — measured INL_max
REG_LUT_VIEW_INL_MAX_H         = 0x0D  # uint16 LE MSB
REG_LUT_VIEW_DNL_MAX_L         = 0x0E  # uint16 LE LSB — measured DNL_max
REG_LUT_VIEW_DNL_MAX_H         = 0x0F  # uint16 LE MSB
REG_DIM0_LEVEL                 = 0x10  # Brightness 0..100
REG_DIM0_CURVE                 = 0x11  # 0=LINEAR, 1=RMS, 2=LOG
REG_DIM0_FADE_TIME             = 0x18  # Fade time, 0 = off
REG_AC_FREQ                    = 0x20  # Detected mains frequency: 50 or 60
REG_AC_PERIOD_L                = 0x21  # Half-period us LSB (uint16 LE with AC_PERIOD_H)
REG_AC_PERIOD_H                = 0x22  # Half-period us MSB
REG_CALIBRATION                = 0x23  # 0=in progress, 1=done
REG_TOPOLOGY                   = 0x24  # v1.1+: 1=SINGLE, 2=SPLIT_PHASE, 3=THREE_PHASE. v1.0 firmware returns 0x00 (unmapped) — library falls back to constructor hint.
REG_SENSOR_CLASS               = 0x25  # v1.2+: 0=UNSET, 1=SCT-013, 2=WIRED_CT, 3=BUILTIN_CT. Required before REG_CT_MODEL write (else ERR_PARAM). Class change resets CT_MODEL=0. Save via CMD_SAVE_GAINS.
REG_V03_PHASE_FRACT            = 0x26  # v1.2+: Q8 fractional sample shift (0..255 = 0..0.996 sample) combined with V03_PHASE_SAMPLES for sub-sample phase comp. Save via CMD_SAVE_GAINS. Calibrated on inductive bench fixture only.
REG_I2C_ADDRESS                = 0x30  # Slave address (0x08..0x77). Gate behind MODE==develop; save via CMD_SAVE_GAINS; reset to apply.
REG_TEMP_T_WARN                = 0x36  # Warning threshold
REG_TEMP_T_DERATE              = 0x37  # Derate threshold
REG_TEMP_T_CRIT                = 0x38  # Critical threshold
REG_TEMP_T_SHUTDOWN            = 0x39  # Shutdown threshold
REG_TEMP_HYST                  = 0x3A  # Hysteresis
REG_TEMP_CONFIG                = 0x3B  # bit0=enable, bit1=Celsius (0=Fahrenheit)
REG_TEMP_CURRENT               = 0x40  # Temperature with +50 offset
REG_TEMP_STATE                 = 0x41  # 0..5 = NORMAL..FAULT
REG_TEMP_MAX_LEVEL             = 0x42  # Max allowed power %
REG_TEMP_FLAGS                 = 0x43  # Status flags
REG_TEMP_PEAK                  = 0x44  # Peak temperature (+50)
REG_TEMP_RATE                  = 0x45  # Rate of change (+128)
REG_FAN_SPEED                  = 0x50  # Current PWM %
REG_FAN_TARGET                 = 0x51  # Manual target PWM %
REG_FAN_MODE                   = 0x52  # 0=OFF, 1=AUTO, 2=MANUAL, 3=HYBRID, 4=FULL
REG_FAN_STATUS                 = 0x53  # Status flags
REG_CS_CONFIG                  = 0x54  # bit0 = CH0 enable (backward compat)
REG_CS_INTERVAL_L              = 0x55  # No-op (kept for compat)
REG_CS_INTERVAL_H              = 0x56  # No-op (kept for compat)
REG_CS0_SENSOR_TYPE            = 0x57  # ACS712 sensitivity (e.g. 66 = ACS712-30A)
REG_ACC_SEL                    = 0x58  # Select accumulator 0..7 for register window
REG_COMMIT                     = 0x59  # Write N (0..7): commit accumulator N, clear PA2
REG_CS0_MODE                   = 0x5A  # 0=current (ACS712), 1=voltage (ZMPT107)
REG_CS0_NOISE_FLOOR            = 0x5B  # Quadrature subtraction noise floor
REG_CS_PERIOD_BUFS_2           = 0x5C  # No-op (kept for compat)
REG_CS_PERIOD_BUFS_3           = 0x5D  # No-op (kept for compat)
REG_CS_PERIOD_BUFS_L           = 0x5E  # No-op (kept for compat)
REG_CS_PERIOD_BUFS_H           = 0x5F  # No-op (kept for compat)
REG_CS0_STATUS                 = 0x60  # bit0=valid, bit1=rt_mode, bit7:5=acc_n. Reading latches snapshot.
REG_CS0_RMS_L                  = 0x61  # uint16 LE LSB — RMS current (mA)
REG_CS0_RMS_H                  = 0x62  # uint16 LE MSB
REG_CS0_PEAK_L                 = 0x63  # uint16 LE LSB — Peak current (mA)
REG_CS0_PEAK_H                 = 0x64  # uint16 LE MSB
REG_CS0_DIR                    = 0x65  # DC bias direction: +1 / 0 / -1
REG_CS0_PERIOD_IDX             = 0x66  # Monotonic period counter (wraps 0..255)
REG_CS0_DUR_0                  = 0x67  # uint32 LE byte 0 — Duration (ms)
REG_CS0_DUR_1                  = 0x68  # uint32 LE byte 1
REG_CS0_DUR_2                  = 0x69  # uint32 LE byte 2
REG_CS0_DUR_3                  = 0x6A  # uint32 LE byte 3 (MSB)
REG_CS0_SMPL_0                 = 0x6B  # uint32 LE byte 0 — Sample count
REG_CS0_SMPL_1                 = 0x6C  # uint32 LE byte 1
REG_CS0_SMPL_2                 = 0x6D  # uint32 LE byte 2
REG_CS0_SMPL_3                 = 0x6E  # uint32 LE byte 3 (MSB)
REG_CS0_MIN_L                  = 0x6F  # uint16 LE LSB — Min ADC value
REG_CS0_MIN_H                  = 0x70  # uint16 LE MSB
REG_CS0_MAX_L                  = 0x71  # uint16 LE LSB — Max ADC value
REG_CS0_MAX_H                  = 0x72  # uint16 LE MSB
REG_CS0_DC_L                   = 0x73  # int16 LE LSB — DC offset (signed ADC units)
REG_CS0_DC_H                   = 0x74  # int16 LE MSB
REG_CS0_CREST_L                = 0x75  # uint16 LE LSB — Crest factor x100 (141=pure sine)
REG_CS0_CREST_H                = 0x76  # uint16 LE MSB
REG_CS0_RESERVED               = 0x77  # Reserved (future THD)
REG_VS_STATUS                  = 0x78  # bit7=NO_HW (no voltage hardware), bit0=data_ready
REG_VS_RMS_L                   = 0x79  # uint16 LE LSB — voltage RMS (0.1 V units)
REG_VS_RMS_H                   = 0x7A  # uint16 LE MSB
REG_VS_PEAK_L                  = 0x7B  # uint16 LE LSB — peak voltage
REG_VS_PEAK_H                  = 0x7C  # uint16 LE MSB
REG_VS_RATIO                   = 0x7D  # Voltage transformer ratio (1..255)
REG_CHARGE_Q_0                 = 0x7E  # uint32 LE byte 0 (LSB) — Q in 0.1 mA·h
REG_CHARGE_Q_1                 = 0x7F  # uint32 LE byte 1
REG_CHARGE_Q_2                 = 0x80  # uint32 LE byte 2
REG_CHARGE_Q_3                 = 0x81  # uint32 LE byte 3 (MSB)
REG_CHARGE_N_0                 = 0x82  # uint32 LE byte 0 — period count (200 ms each)
REG_CHARGE_N_1                 = 0x83  # uint32 LE byte 1
REG_CHARGE_N_2                 = 0x84  # uint32 LE byte 2
REG_CHARGE_N_3                 = 0x85  # uint32 LE byte 3 (MSB)
REG_V03_U_RMS                  = 0x86  # Voltage RMS
REG_V03_U_PEAK                 = 0x8A  # Voltage peak
REG_V03_I0_RMS                 = 0x8E  # Current CH0 RMS
REG_V03_I1_RMS                 = 0x92  # Current CH1 RMS (UI2/UI3 only)
REG_V03_I2_RMS                 = 0x96  # Current CH2 RMS (UI3 only)
REG_V03_I0_PEAK                = 0x9A  # Current CH0 peak
REG_V03_I1_PEAK                = 0x9E  # Current CH1 peak (UI2/UI3 only)
REG_V03_I2_PEAK                = 0xA2  # Current CH2 peak (UI3 only)
REG_V03_P0_REAL                = 0xA6  # Real power CH0 (signed)
REG_V03_P1_REAL                = 0xAA  # Real power CH1 (UI2/UI3 only)
REG_V03_P2_REAL                = 0xAE  # Real power CH2 (UI3 only)
REG_V03_PF0                    = 0xB2  # Power factor CH0 (-1..+1)
REG_V03_PF1                    = 0xB6  # Power factor CH1
REG_V03_PF2                    = 0xBA  # Power factor CH2
REG_V03_PERIOD_COMMIT_CNT      = 0xBE  # Diagnostic — # of RT commits this period
REG_V03_PERIOD_AVG_P_F1        = 0xC2  # Avg real power CH1 (UI2/UI3 only). Variant-detect: NACK here -> SINGLE.
REG_V03_PERIOD_AVG_P_F2        = 0xC6  # Avg real power CH2 (UI3 only). Variant-detect: NACK here -> SPLIT_PHASE.
REG_V03_PERIOD_MS_B0           = 0xCA  # Period duration (chip's view)
REG_V03_STATUS                 = 0xCE  # bit0=valid (cleared on read), bit1..7=reserved
REG_V03_RESERVED               = 0xCF  # Reserved
REG_CAL_CH_SEL                 = 0xD0  # 0=U, 1=I0, 2=I1, 3=I2
REG_CAL_SAMPLES_N              = 0xD1  # Samples to average (max 100)
REG_CAL_MEAN_L                 = 0xD2  # uint16 LE LSB — raw ADC mean
REG_CAL_MEAN_H                 = 0xD3  # uint16 LE MSB
REG_CAL_STDDEV_L               = 0xD4  # uint16 LE LSB — raw ADC stddev (noise floor)
REG_CAL_STDDEV_H               = 0xD5  # uint16 LE MSB
REG_CAL_MIN_L                  = 0xD6  # uint16 LE LSB — raw ADC min
REG_CAL_MIN_H                  = 0xD7  # uint16 LE MSB
REG_CAL_MAX_L                  = 0xD8  # uint16 LE LSB — raw ADC max
REG_CAL_MAX_H                  = 0xD9  # uint16 LE MSB
REG_CAL_STATE                  = 0xDA  # 0=idle, 1=armed, 2=sampling, 3=ready, 4=writing, 5=done, 6=error
REG_CAL_ERROR                  = 0xDB  # Calibration error code (CAL_ERR_*)
REG_V03_PERIOD_AVG_P_F0        = 0xDC  # PRODUCTION energy primitive — avg P_real CH0 over latched period
REG_V03_PERIOD_MAX_P_F0        = 0xE0  # Peak P_real CH0 during latched period
REG_V03_PERIOD_LATCH_MS        = 0xEC  # Chip's view of dt between LATCHes (diagnostic — master must time its own wall-clock)
REG_V03_U_NOISE_FLOOR_L        = 0xE4  # uint16 LE LSB — voltage noise floor (ADC counts)
REG_V03_U_NOISE_FLOOR_H        = 0xE5  # uint16 LE MSB
REG_V03_I0_NOISE_FLOOR_L       = 0xE6  # uint16 LE LSB — I0 noise floor
REG_V03_I0_NOISE_FLOOR_H       = 0xE7  # uint16 LE MSB
REG_V03_I1_NOISE_FLOOR_L       = 0xE8  # uint16 LE LSB — I1 noise floor
REG_V03_I1_NOISE_FLOOR_H       = 0xE9  # uint16 LE MSB
REG_V03_I2_NOISE_FLOOR_L       = 0xEA  # uint16 LE LSB — I2 noise floor
REG_V03_I2_NOISE_FLOOR_H       = 0xEB  # uint16 LE MSB
REG_V03_U_GAIN                 = 0xF0  # Voltage channel gain
REG_V03_I0_GAIN                = 0xF4  # Current I0 gain
REG_V03_I1_GAIN                = 0xF8  # Current I1 gain
REG_V03_I2_GAIN                = 0xFC  # Current I2 gain

# ---- Command opcodes ----
CMD_NOP                        = 0x00
CMD_RESET                      = 0x01
CMD_RECALIBRATE                = 0x02
CMD_SWITCH_UART                = 0x03
CMD_CHARGE_RESET               = 0x05
CMD_CAL_BEGIN                  = 0x20
CMD_CAL_SAMPLE                 = 0x21
CMD_CAL_LUT_WRITE              = 0x22
CMD_CAL_LUT_COMMIT             = 0x23
CMD_CAL_LUT_ABORT              = 0x24
CMD_CAL_END                    = 0x25
CMD_SAVE_GAINS                 = 0x26
CMD_LATCH_PERIOD               = 0x27
CMD_SET_CT_MODEL_CH0           = 0x28
CMD_SET_CT_MODEL_CH1           = 0x29
CMD_SET_CT_MODEL_CH2           = 0x2A
CMD_FACTORY_RESET              = 0xAA

# ---- Command settle times (ms) ----
SETTLE_MS_NOP                    = 0
SETTLE_MS_RESET                  = 100
SETTLE_MS_RECALIBRATE            = 200
SETTLE_MS_SWITCH_UART            = 50
SETTLE_MS_CHARGE_RESET           = 5
SETTLE_MS_CAL_BEGIN              = 10
SETTLE_MS_CAL_SAMPLE             = 50
SETTLE_MS_CAL_LUT_WRITE          = 5
SETTLE_MS_CAL_LUT_COMMIT         = 700
SETTLE_MS_CAL_LUT_ABORT          = 5
SETTLE_MS_CAL_END                = 50
SETTLE_MS_SAVE_GAINS             = 700
SETTLE_MS_LATCH_PERIOD           = 50
SETTLE_MS_SET_CT_MODEL_CH0       = 5
SETTLE_MS_SET_CT_MODEL_CH1       = 5
SETTLE_MS_SET_CT_MODEL_CH2       = 5
SETTLE_MS_FACTORY_RESET          = 1500

# ---- Device error codes ----
DEV_ERR_OK                     = 0x00
DEV_ERR_LUT_BAD                = 0xFA
DEV_ERR_FLASH_PARAMS_BAD       = 0xFB
DEV_ERR_NOT_READY              = 0xFC
DEV_ERR_SENSOR_OVERFLOW        = 0xFD
DEV_ERR_PARAM                  = 0xFE
DEV_ERR_UNHANDLED              = 0xFF

# ---- Library error codes ----
OK                               =   0
ERR_IO                           =  -1
ERR_NACK                         =  -2
ERR_TIMEOUT                      =  -3
ERR_NOT_READY                    =  -4
ERR_STALE                        =  -5
ERR_PARAM                        =  -6
ERR_MODE                         =  -7
ERR_CHECKSUM                     =  -8
ERR_VERSION                      =  -9
ERR_NOT_IMPLEMENTED              = -10
ERR_NON_PHYSICAL                 = -11

# ---- Convenience: full register table ----
REGISTERS = (
    dict(name='STATUS', addr=0x00, size=1, type='u8', endian=None, access='r', group='control'),
    dict(name='COMMAND', addr=0x01, size=1, type='u8', endian=None, access='w', group='control'),
    dict(name='ERROR', addr=0x02, size=1, type='u8', endian=None, access='r', group='control'),
    dict(name='VERSION', addr=0x03, size=1, type='u8', endian=None, access='r', group='control'),
    dict(name='MODE', addr=0x04, size=1, type='u8', endian=None, access='r', group='control'),
    dict(name='CT_MODEL', addr=0x05, size=1, type='u8', endian=None, access='rw', group='control'),
    dict(name='V03_PHASE_SAMPLES', addr=0x06, size=1, type='u8', endian=None, access='rw', group='control'),
    dict(name='V03_PERIOD_VALID', addr=0x07, size=1, type='u8', endian=None, access='r', group='control'),
    dict(name='LUT_VALID_MASK', addr=0x08, size=1, type='u8', endian=None, access='r', group='lut_debug'),
    dict(name='LUT_QUERY_SLOT', addr=0x09, size=1, type='u8', endian=None, access='rw', group='lut_debug'),
    dict(name='LUT_VIEW_TIER', addr=0x0A, size=1, type='u8', endian=None, access='r', group='lut_debug'),
    dict(name='LUT_VIEW_POINTS_LOG2', addr=0x0B, size=1, type='u8', endian=None, access='r', group='lut_debug'),
    dict(name='LUT_VIEW_INL_MAX_L', addr=0x0C, size=1, type='u8', endian=None, access='r', group='lut_debug'),
    dict(name='LUT_VIEW_INL_MAX_H', addr=0x0D, size=1, type='u8', endian=None, access='r', group='lut_debug'),
    dict(name='LUT_VIEW_DNL_MAX_L', addr=0x0E, size=1, type='u8', endian=None, access='r', group='lut_debug'),
    dict(name='LUT_VIEW_DNL_MAX_H', addr=0x0F, size=1, type='u8', endian=None, access='r', group='lut_debug'),
    dict(name='DIM0_LEVEL', addr=0x10, size=1, type='u8', endian=None, access='rw', group='dimmer'),
    dict(name='DIM0_CURVE', addr=0x11, size=1, type='u8', endian=None, access='rw', group='dimmer'),
    dict(name='DIM0_FADE_TIME', addr=0x18, size=1, type='u8', endian=None, access='rw', group='dimmer'),
    dict(name='AC_FREQ', addr=0x20, size=1, type='u8', endian=None, access='r', group='system'),
    dict(name='AC_PERIOD_L', addr=0x21, size=1, type='u8', endian=None, access='r', group='system'),
    dict(name='AC_PERIOD_H', addr=0x22, size=1, type='u8', endian=None, access='r', group='system'),
    dict(name='CALIBRATION', addr=0x23, size=1, type='u8', endian=None, access='r', group='system'),
    dict(name='TOPOLOGY', addr=0x24, size=1, type='u8', endian=None, access='r', group='system'),
    dict(name='SENSOR_CLASS', addr=0x25, size=1, type='u8', endian=None, access='rw', group='system'),
    dict(name='V03_PHASE_FRACT', addr=0x26, size=1, type='u8', endian=None, access='rw', group='system'),
    dict(name='I2C_ADDRESS', addr=0x30, size=1, type='u8', endian=None, access='rw', group='i2c'),
    dict(name='TEMP_T_WARN', addr=0x36, size=1, type='u8', endian=None, access='rw', group='thermal_cfg'),
    dict(name='TEMP_T_DERATE', addr=0x37, size=1, type='u8', endian=None, access='rw', group='thermal_cfg'),
    dict(name='TEMP_T_CRIT', addr=0x38, size=1, type='u8', endian=None, access='rw', group='thermal_cfg'),
    dict(name='TEMP_T_SHUTDOWN', addr=0x39, size=1, type='u8', endian=None, access='rw', group='thermal_cfg'),
    dict(name='TEMP_HYST', addr=0x3A, size=1, type='u8', endian=None, access='rw', group='thermal_cfg'),
    dict(name='TEMP_CONFIG', addr=0x3B, size=1, type='u8', endian=None, access='rw', group='thermal_cfg'),
    dict(name='TEMP_CURRENT', addr=0x40, size=1, type='u8', endian=None, access='r', group='thermal'),
    dict(name='TEMP_STATE', addr=0x41, size=1, type='u8', endian=None, access='r', group='thermal'),
    dict(name='TEMP_MAX_LEVEL', addr=0x42, size=1, type='u8', endian=None, access='r', group='thermal'),
    dict(name='TEMP_FLAGS', addr=0x43, size=1, type='u8', endian=None, access='r', group='thermal'),
    dict(name='TEMP_PEAK', addr=0x44, size=1, type='u8', endian=None, access='r', group='thermal'),
    dict(name='TEMP_RATE', addr=0x45, size=1, type='u8', endian=None, access='r', group='thermal'),
    dict(name='FAN_SPEED', addr=0x50, size=1, type='u8', endian=None, access='r', group='fan'),
    dict(name='FAN_TARGET', addr=0x51, size=1, type='u8', endian=None, access='rw', group='fan'),
    dict(name='FAN_MODE', addr=0x52, size=1, type='u8', endian=None, access='rw', group='fan'),
    dict(name='FAN_STATUS', addr=0x53, size=1, type='u8', endian=None, access='r', group='fan'),
    dict(name='CS_CONFIG', addr=0x54, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS_INTERVAL_L', addr=0x55, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS_INTERVAL_H', addr=0x56, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS0_SENSOR_TYPE', addr=0x57, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='ACC_SEL', addr=0x58, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='COMMIT', addr=0x59, size=1, type='u8', endian=None, access='w', group='cs_cfg'),
    dict(name='CS0_MODE', addr=0x5A, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS0_NOISE_FLOOR', addr=0x5B, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS_PERIOD_BUFS_2', addr=0x5C, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS_PERIOD_BUFS_3', addr=0x5D, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS_PERIOD_BUFS_L', addr=0x5E, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS_PERIOD_BUFS_H', addr=0x5F, size=1, type='u8', endian=None, access='rw', group='cs_cfg'),
    dict(name='CS0_STATUS', addr=0x60, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_RMS_L', addr=0x61, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_RMS_H', addr=0x62, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_PEAK_L', addr=0x63, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_PEAK_H', addr=0x64, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_DIR', addr=0x65, size=1, type='i8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_PERIOD_IDX', addr=0x66, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_DUR_0', addr=0x67, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_DUR_1', addr=0x68, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_DUR_2', addr=0x69, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_DUR_3', addr=0x6A, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_SMPL_0', addr=0x6B, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_SMPL_1', addr=0x6C, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_SMPL_2', addr=0x6D, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_SMPL_3', addr=0x6E, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_MIN_L', addr=0x6F, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_MIN_H', addr=0x70, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_MAX_L', addr=0x71, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_MAX_H', addr=0x72, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_DC_L', addr=0x73, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_DC_H', addr=0x74, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_CREST_L', addr=0x75, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_CREST_H', addr=0x76, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='CS0_RESERVED', addr=0x77, size=1, type='u8', endian=None, access='r', group='cs_snapshot'),
    dict(name='VS_STATUS', addr=0x78, size=1, type='u8', endian=None, access='r', group='vs'),
    dict(name='VS_RMS_L', addr=0x79, size=1, type='u8', endian=None, access='r', group='vs'),
    dict(name='VS_RMS_H', addr=0x7A, size=1, type='u8', endian=None, access='r', group='vs'),
    dict(name='VS_PEAK_L', addr=0x7B, size=1, type='u8', endian=None, access='r', group='vs'),
    dict(name='VS_PEAK_H', addr=0x7C, size=1, type='u8', endian=None, access='r', group='vs'),
    dict(name='VS_RATIO', addr=0x7D, size=1, type='u8', endian=None, access='rw', group='vs'),
    dict(name='CHARGE_Q_0', addr=0x7E, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='CHARGE_Q_1', addr=0x7F, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='CHARGE_Q_2', addr=0x80, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='CHARGE_Q_3', addr=0x81, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='CHARGE_N_0', addr=0x82, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='CHARGE_N_1', addr=0x83, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='CHARGE_N_2', addr=0x84, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='CHARGE_N_3', addr=0x85, size=1, type='u8', endian=None, access='rw', group='charge'),
    dict(name='V03_U_RMS', addr=0x86, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_U_PEAK', addr=0x8A, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_I0_RMS', addr=0x8E, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_I1_RMS', addr=0x92, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_I2_RMS', addr=0x96, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_I0_PEAK', addr=0x9A, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_I1_PEAK', addr=0x9E, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_I2_PEAK', addr=0xA2, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_P0_REAL', addr=0xA6, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_P1_REAL', addr=0xAA, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_P2_REAL', addr=0xAE, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_PF0', addr=0xB2, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_PF1', addr=0xB6, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_PF2', addr=0xBA, size=4, type='float32', endian='le', access='r', group='rt'),
    dict(name='V03_PERIOD_COMMIT_CNT', addr=0xBE, size=4, type='u32', endian='le', access='r', group='period'),
    dict(name='V03_PERIOD_AVG_P_F1', addr=0xC2, size=4, type='float32', endian='le', access='r', group='period'),
    dict(name='V03_PERIOD_AVG_P_F2', addr=0xC6, size=4, type='float32', endian='le', access='r', group='period'),
    dict(name='V03_PERIOD_MS_B0', addr=0xCA, size=4, type='u32', endian='le', access='r', group='period'),
    dict(name='V03_STATUS', addr=0xCE, size=1, type='u8', endian=None, access='r', group='rt'),
    dict(name='V03_RESERVED', addr=0xCF, size=1, type='u8', endian=None, access='r', group='rt'),
    dict(name='CAL_CH_SEL', addr=0xD0, size=1, type='u8', endian=None, access='rw', group='cal'),
    dict(name='CAL_SAMPLES_N', addr=0xD1, size=1, type='u8', endian=None, access='rw', group='cal'),
    dict(name='CAL_MEAN_L', addr=0xD2, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_MEAN_H', addr=0xD3, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_STDDEV_L', addr=0xD4, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_STDDEV_H', addr=0xD5, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_MIN_L', addr=0xD6, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_MIN_H', addr=0xD7, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_MAX_L', addr=0xD8, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_MAX_H', addr=0xD9, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_STATE', addr=0xDA, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='CAL_ERROR', addr=0xDB, size=1, type='u8', endian=None, access='r', group='cal'),
    dict(name='V03_PERIOD_AVG_P_F0', addr=0xDC, size=4, type='float32', endian='le', access='r', group='period'),
    dict(name='V03_PERIOD_MAX_P_F0', addr=0xE0, size=4, type='float32', endian='le', access='r', group='period'),
    dict(name='V03_PERIOD_LATCH_MS', addr=0xEC, size=4, type='u32', endian='le', access='r', group='period'),
    dict(name='V03_U_NOISE_FLOOR_L', addr=0xE4, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_U_NOISE_FLOOR_H', addr=0xE5, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_I0_NOISE_FLOOR_L', addr=0xE6, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_I0_NOISE_FLOOR_H', addr=0xE7, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_I1_NOISE_FLOOR_L', addr=0xE8, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_I1_NOISE_FLOOR_H', addr=0xE9, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_I2_NOISE_FLOOR_L', addr=0xEA, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_I2_NOISE_FLOOR_H', addr=0xEB, size=1, type='u8', endian=None, access='rw', group='gain'),
    dict(name='V03_U_GAIN', addr=0xF0, size=4, type='float32', endian='le', access='rw', group='gain'),
    dict(name='V03_I0_GAIN', addr=0xF4, size=4, type='float32', endian='le', access='rw', group='gain'),
    dict(name='V03_I1_GAIN', addr=0xF8, size=4, type='float32', endian='le', access='rw', group='gain'),
    dict(name='V03_I2_GAIN', addr=0xFC, size=4, type='float32', endian='le', access='rw', group='gain'),
)

# ---- Convenience: command table ----
COMMANDS = (
    dict(name='NOP', opcode=0x00, settle_ms=0),
    dict(name='RESET', opcode=0x01, settle_ms=100),
    dict(name='RECALIBRATE', opcode=0x02, settle_ms=200),
    dict(name='SWITCH_UART', opcode=0x03, settle_ms=50),
    dict(name='CHARGE_RESET', opcode=0x05, settle_ms=5),
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
    dict(name='FACTORY_RESET', opcode=0xAA, settle_ms=1500),
)
