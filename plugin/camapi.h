/* hooks of the camera driver API (sceUsbCam*) */
#ifndef PSYCHEA_CAMAPI_H
#define PSYCHEA_CAMAPI_H

/* The camera driver's own API (sceUsbCam*) is called through syscalls, so no stub can be patched:
 * sctrlHENPatchSyscall redirects each export. kind: 0 plain, 1 a0 = parameter structure (first word: its size),
 * 2 a0 = out pointer to an int. Hooked only with log_camera_calls or photos_1280x960. Polled getters are logged
 * for the first 20 calls only. */

#define CAMHOOKS(X) \
	X( 0, 0x17F7B2FB, "sceUsbCamSetupVideo",             1) \
	X( 1, 0xCFE9E999, "sceUsbCamSetupVideoEx",           1) \
	X( 2, 0x574A8C3F, "sceUsbCamStartVideo",             0) \
	X( 3, 0x6CF32CB9, "sceUsbCamStopVideo",              0) \
	X( 4, 0x03ED7A82, "sceUsbCamSetupMic",               1) \
	X( 5, 0x2E930264, "sceUsbCamSetupMicEx",             1) \
	X( 6, 0x82A64030, "sceUsbCamStartMic",               0) \
	X( 7, 0x5145868A, "sceUsbCamStopMic",                0) \
	X( 8, 0x08AEE98A, "sceUsbCamSetMicGain",             0) \
	X( 9, 0x3F0CF289, "sceUsbCamSetupStill",             1) \
	X(10, 0x0A41A298, "sceUsbCamSetupStillEx",           1) \
	X(11, 0x61BE5CAC, "sceUsbCamStillInputBlocking",     0) \
	X(12, 0xFB0A6C5D, "sceUsbCamStillInput",             0) \
	X(13, 0x7563AFA1, "sceUsbCamStillWaitInputEnd",      0) \
	X(14, 0xA720937C, "sceUsbCamStillCancelInput",       0) \
	X(15, 0xE5959C36, "sceUsbCamStillGetInputLength",    0) \
	X(16, 0x951BEDF5, "sceUsbCamSetReverseMode",         0) \
	X(17, 0xD5279339, "sceUsbCamGetReverseMode",         2) \
	X(18, 0xF93C4669, "sceUsbCamAutoImageReverseSW",     0) \
	X(19, 0x11A1F128, "sceUsbCamGetAutoImageReverseState", 0) \
	X(20, 0x4C34F553, "sceUsbCamGetLensDirection",       0) \
	X(21, 0xD293A100, "sceUsbCamRegisterLensRotationCallback",   0) \
	X(22, 0x41EE8797, "sceUsbCamUnregisterLensRotationCallback", 0) \
	X(23, 0x6E205974, "sceUsbCamSetSaturation",          0) \
	X(24, 0x383E9FA8, "sceUsbCamGetSaturation",          2) \
	X(25, 0x4F3D84D5, "sceUsbCamSetBrightness",          0) \
	X(26, 0x70F522C5, "sceUsbCamGetBrightness",          2) \
	X(27, 0x09C26C7E, "sceUsbCamSetContrast",            0) \
	X(28, 0xA063A957, "sceUsbCamGetContrast",            2) \
	X(29, 0x622F83CC, "sceUsbCamSetSharpness",           0) \
	X(30, 0xFDB68C23, "sceUsbCamGetSharpness",           2) \
	X(31, 0xD4876173, "sceUsbCamSetImageEffectMode",     0) \
	X(32, 0x994471E0, "sceUsbCamGetImageEffectMode",     2) \
	X(33, 0x1D686870, "sceUsbCamSetEvLevel",             0) \
	X(34, 0x2BCD50C0, "sceUsbCamGetEvLevel",             2) \
	X(35, 0xC484901F, "sceUsbCamSetZoom",                0) \
	X(36, 0x9E8AAF8D, "sceUsbCamGetZoom",                2) \
	X(37, 0x6784E6A8, "sceUsbCamSetAntiFlicker",         0) \
	X(38, 0xAA7D94BA, "sceUsbCamGetAntiFlicker",         2) \
	X(39, 0x00631D06, "sceUsbCam_00631D06",              0) \
	X(40, 0xD865997B, "sceUsbCam_D865997B",              0) \
	X(41, 0x1E958148, "sceUsbCam_1E958148",              0)

#define NCAM 42
enum { CAM_SETUP_STILL = 9, CAM_SETUP_STILL_EX = 10 };       /* the rows of CAMHOOKS that photos_1280x960 changes */

static const u32 cam_nid[NCAM] = {
#define X(i, n, s, k) n,
	CAMHOOKS(X)
#undef X
};
static const char *const cam_name[NCAM] = {
#define X(i, n, s, k) s,
	CAMHOOKS(X)
#undef X
};
static const u8 cam_kind[NCAM] = {
#define X(i, n, s, k) k,
	CAMHOOKS(X)
#undef X
};
static const unsigned char cam_idx[] = {
#define X(i, n, s, k) i,
	CAMHOOKS(X)
#undef X
};
_Static_assert(sizeof(cam_idx) == NCAM, "CAMHOOKS has not NCAM entries");

static u32 cam_orig[NCAM];
static u32 cam_cnt[NCAM];
static volatile u32 cam_gen = 1;      /* raised by the module-start handler each time sceUSBCam_Driver starts */
static u32 cam_hooked_gen;            /* the generation the hooks were installed for (writer thread only) */

static int cam_spy(int idx, int a0, int a1, int a2, int a3, int a4, int a5, int a6, int a7)
{
	u32 c = ++cam_cnt[idx];
	int show = cfg.log_camera_calls && (c <= 20 || (c & 255) == 0);
	int r;
	if (show) {
		L_begin();
		L_time(); L_s("CAMAPI  "); L_s(cam_name[idx]); L_s("(");
		L_x((u32)a0, 8); L_s(", "); L_x((u32)a1, 8); L_s(", "); L_x((u32)a2, 8); L_s(", "); L_x((u32)a3, 8);
		L_s(") call#"); L_d((int)c); L_c('\n');
		if (cam_kind[idx] == 1 && ptr_ok((void *)a0, 4)) {
			int n = ptr_ok_w((void *)a0, 4) ? *(int *)a0 : 0;     /* a0 comes from the app: no word read at an odd address */
			if (n < 4 || n > 128) n = 48;                 /* the size word is not trustworthy: dump a default */
			L_s("    param["); L_d(n); L_s("]: "); L_hex((u8 *)a0, n); L_c('\n');
		}
		L_end();
	}
	if (cfg.photos_1280x960 && (idx == CAM_SETUP_STILL || idx == CAM_SETUP_STILL_EX) && ptr_ok_w((void *)a0, 16)) {
		int *p = (int *)a0;
		int w = stillbig_word(idx == CAM_SETUP_STILL_EX), nv;
		if (!stillbig_size_ok(idx == CAM_SETUP_STILL_EX, p[0])) {         /* the size word says the structure ends before the resolution word */
			if (c <= 5) { L_begin(); L_time(); L_s("STILLBIG "); L_s(cam_name[idx]); L_s(": structure size "); L_d(p[0]); L_s(", left alone\n"); L_end(); }
			nv = -1;
		} else nv = stillbig_res(idx == CAM_SETUP_STILL_EX, p[w]);
		if (nv >= 0) {
			L_begin(); L_time(); L_s("STILLBIG "); L_s(cam_name[idx]); L_s(": resolution "); L_d(p[w]); L_s(" -> "); L_d(nv); L_c('\n'); L_end();
			p[w] = nv;
		}
	}
	r = ((fn8_t)cam_orig[idx])(a0, a1, a2, a3, a4, a5, a6, a7);
	if (show) {
		L_begin();
		if (cam_kind[idx] == 2 && ptr_ok_w((void *)a0, 4)) { L_s("    out: "); L_d(*(int *)a0); L_c('\n'); }
		L_time(); L_s("  ret   "); L_s(cam_name[idx]); L_s(" = 0x"); L_x((u32)r, 8); L_c('\n');
		L_end();
	}
	return r;
}

#define X(i, n, s, k) \
	static int cw##i(int a0, int a1, int a2, int a3, int a4, int a5, int a6, int a7) \
	{ return cam_spy(i, a0, a1, a2, a3, a4, a5, a6, a7); }
CAMHOOKS(X)
#undef X

static void *const cam_fn[NCAM] = {
#define X(i, n, s, k) (void *)cw##i,
	CAMHOOKS(X)
#undef X
};

/* Run by the writer thread every 300 ms: the driver exists only once the first camera user has loaded it. Each start
 * of sceUSBCam_Driver (cam_gen) has fresh syscall entries, so the hooks are installed again. */
static void cam_try_hook(void)
{
	int i, n = 0;
	u32 gen = cam_gen;
	if (!(cfg.log_camera_calls || cfg.photos_1280x960) || cam_hooked_gen == gen) return;
	if (!sctrlHENFindFunction("sceUSBCam_Driver", "sceUsbCam", cam_nid[0])) return;       /* not loaded yet */
	for (i = 0; i < NCAM; i++) {
		cam_cnt[i] = 0;
		cam_orig[i] = sctrlHENFindFunction("sceUSBCam_Driver", "sceUsbCam", cam_nid[i]);
		if (!cam_orig[i]) continue;
		sctrlHENPatchSyscall((void *)cam_orig[i], cam_fn[i]);
		n++;
	}
	cam_hooked_gen = gen;                     /* a start that came while installing leaves gen different: tried again */
	L_begin(); L_time(); L_s("camera hooks: hooked "); L_d(n); L_s(" of "); L_d(NCAM); L_s(" sceUsbCam exports\n"); L_end();
}

#endif
