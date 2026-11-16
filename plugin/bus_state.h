/* hook table of the bus and accessory libraries, the driver and request tables, descriptor dump */
#ifndef PSYCHEA_BUS_STATE_H
#define PSYCHEA_BUS_STATE_H

#define HOOKS(X) \
	X( 0, "sceUsbBus_driver",   0xB1644BE7, "sceUsbbdRegister")     \
	X( 1, "sceUsbBus_driver",   0xC1E2A540, "sceUsbbdUnregister")   \
	X( 2, "sceUsbBus_driver",   0x951A24CC, "sceUsbbdClearFIFO")    \
	X( 3, "sceUsbBus_driver",   0xE65441C1, "sceUsbbdStall")        \
	X( 4, "sceUsbBus_driver",   0x23E51D8F, "sceUsbbdReqSend")      \
	X( 5, "sceUsbBus_driver",   0x913EC15D, "sceUsbbdReqRecv")      \
	X( 6, "sceUsbBus_driver",   0xCC57EC9D, "sceUsbbdReqCancel")    \
	X( 7, "sceUsbBus_driver",   0xC5E53685, "sceUsbbdReqCancelAll") \
	X( 8, "sceUsbBus_driver",   0xC21645A4, "sceUsbGetState")       \
	X( 9, "sceUsbBus_driver",   0x48CCE3C1, "bus_48CCE3C1")         \
	X(10, "sceUsbBus_driver",   0x7B87815D, "bus_7B87815D")         \
	X(11, "sceUsbBus_driver",   0x8A3EB5D2, "bus_8A3EB5D2")         \
	X(12, "sceUsbBus_driver",   0x90B82F55, "bus_90B82F55")         \
	X(13, "sceUsbBus_driver",   0xFBA2072B, "bus_FBA2072B")         \
	X(14, "sceUsbBus_driver",   0xEDA8A020, "bus_EDA8A020")         \
	X(15, "sceUsbAcc_internal", 0x0CD7D4AA, "sceUsbAccGetInfo")     \
	X(16, "sceUsbAcc_internal", 0x18B04C82, "sceUsbAccUnregisterType") \
	X(17, "sceUsbAcc_internal", 0x2A100C1F, "sceUsbAccIntrInReq")   \
	X(18, "sceUsbAcc_internal", 0x2E251404, "sceUsbAccRegisterType") \
	X(19, "sceUsbAcc_internal", 0x79A1C743, "sceUsbAccGetAuthStat")

enum {
	H_REGISTER = 0, H_UNREGISTER, H_CLEARFIFO, H_STALL, H_REQSEND, H_REQRECV,
	H_REQCANCEL, H_REQCANCELALL, H_GETSTATE, H_BUS48CC,
	H_BUS8A3E = 11, H_GETINFO = 15, H_AUTHSTAT = 19,
	NHOOKS = 20
};

static const char *const hook_lib[NHOOKS] = {
#define X(i, l, n, s) l,
	HOOKS(X)
#undef X
};
static const u32 hook_nid[NHOOKS] = {
#define X(i, l, n, s) n,
	HOOKS(X)
#undef X
};
static const char *const hook_name[NHOOKS] = {
#define X(i, l, n, s) s,
	HOOKS(X)
#undef X
};

/* a wrong entry count would leave a NULL hook */
static const unsigned char hook_idx[] = {
#define X(i, l, n, s) i,
	HOOKS(X)
#undef X
};
_Static_assert(sizeof(hook_idx) == NHOOKS, "HOOKS has not NHOOKS entries");
_Static_assert(sizeof(hook_idx) == 20, "H_AUTHSTAT/H_BUS8A3E/the case labels in spy() assume 20 hooks");

static u32 orig_fn[NHOOKS];
static u32 hook_cnt[NHOOKS];
static int polled_last[NHOOKS];
static u32 polled_changes[NHOOKS];

typedef int (*fn8_t)(int, int, int, int, int, int, int, int);

struct req {
	void *endp; void *data; int size; int unkc; void *func;
	int recvsize; int retcode; int unk1c; void *arg; void *link;
};

struct drv {
	const char *name; int endpoints; void *endp; void *intp;
	void *devp_hi; void *confp_hi; void *devp; void *confp; void *str;
	void *recvctl; void *func28; void *attach; void *detach; int unk34;
	void *start_func; void *stop_func; void *link;
};

#define NSLOT 4
static struct { const char *name; void *o_ctl, *o_att, *o_det; struct drv *d; } slot[NSLOT];
static unsigned int slot_key[NSLOT];      /* the driver structure address of each row: a driver registered again finds its row */
static int nslot;

#define NPEND 64
static struct { struct req *r; void *orig; int dir; } pend[NPEND];

/* Mass-storage drivers are not watched: their traffic passes through unlogged. */
#define NWATCH 6
static struct { u32 base, end; const char *name; } watch[NWATCH];
static unsigned int watch_key[NWATCH];    /* the endpoint array address of each row */
static int nwatch;

/* endpoint slot = watched driver * 8 + endpoint index; -1 = not watched */
#define NEP (NWATCH * 8)
static u32 seen[2][NEP];                  /* queued requests so far     [dir][slot], dir 1 = recv */
static u32 dseen[2][NEP];                 /* completed requests so far */
static struct { u32 n, bytes, errs; } tally[2][NEP];   /* counted but not logged line by line */

static int ep_of(struct req *q)
{
	int i;
	if (!ptr_ok_w(q, sizeof(*q)) || !ptr_ok(q->endp, 4)) return -1;
	for (i = 0; i < nwatch; i++)
		if ((u32)q->endp >= watch[i].base && (u32)q->endp < watch[i].end)
			return i * 8 + (int)(((u32)q->endp - watch[i].base) / 12) % 8;
	return -1;
}

static int ep_watched(void *ep)
{
	int i;
	for (i = 0; i < nwatch; i++)
		if ((u32)ep >= watch[i].base && (u32)ep < watch[i].end) return 1;
	return 0;
}

static void dump_config(struct drv *d, int hs)
{
	u32 *c = (u32 *)(hs ? d->confp_hi : d->confp);
	u8 *dev = (u8 *)(hs ? d->devp_hi : d->devp);
	u8 *cd;
	u32 *infs;
	u8 *ep;
	u32 i, n;

	L_s("    "); L_s(hs ? "HS" : "FS"); L_s(" device desc: ");
	L_hex(dev, 18); L_c('\n');
	if (!ptr_ok_w(c, 16)) return;
	cd = (u8 *)c[0];
	L_s("    "); L_s(hs ? "HS" : "FS"); L_s(" config desc: "); L_hex(cd, 9); L_c('\n');
	infs = (u32 *)c[1];
	if (ptr_ok_w(infs, 12)) {
		n = infs[2];
		if (n > 2) n = 2;
		for (i = 0; i < n; i++) {
			L_s("      interface["); L_d((int)i); L_s("]: "); L_hex((u8 *)infs[i], 9); L_c('\n');
		}
	}
	ep = (u8 *)c[3];
	if (ptr_ok(ep, 16)) {
		for (i = 0; i < 8; i++) {
			u8 *e = ep + 16 * i;
			if (!ptr_ok(e, 7) || e[0] != 7 || e[1] != 5) break;
			L_s("      endpoint: "); L_hex(e, 7); L_c('\n');
		}
	}
}

/* The bus state words of sceUSB_Driver (BUSSTATE_OFFSET in its data): sceUsbGetState() returns their OR.
 * Logged with every ATTACH/CTRL/DETACH. */
static SceUID usb_uid = -1;
static void L_busstate(void)
{
	SceModule *m;
	u32 *w;
	if (usb_uid < 0) return;
	m = sceKernelFindModuleByUID(usb_uid);
	if (!m || !ptr_ok_w(m, sizeof(SceModule)) || !m->segmentaddr[0]) return;
	w = (u32 *)(m->segmentaddr[0] + BUSSTATE_OFFSET);
	if (!ptr_ok_w(w, 12)) return;
	L_s("    busstate: "); L_x(w[0], 8); L_c(' '); L_x(w[1], 8); L_c(' '); L_x(w[2], 8); L_c('\n');
}

#endif
