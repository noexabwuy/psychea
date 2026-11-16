/* import stub patches: the XMB camera check, the accessory power flag, the bus hooks */
#ifndef PSYCHEA_PATCH_H
#define PSYCHEA_PATCH_H

typedef struct { u32 libname; u8 ver[2]; u16 attr; u8 len; u8 vcnt; u16 fcnt; u32 nids; u32 stubs; u32 vstubs; } stub_t;

/* The XMB camera app asks sceUsbAccGetAuthStat once, about 1 ms after the connect, and gives up on a non-zero result
 * (0x80243701). The import stub of that module becomes "return 0". The module-start notice comes before the loader has
 * resolved the stubs, so the writer thread retries every 10 ms until the stub looks like "jr ra; syscall n". */
static volatile SceUID xmb_uid = -1;     /* written by the module-start handler, read by the writer thread */
static volatile u32 xmb_deadline;        /* microseconds (32-bit counter, compared with time_due) */

static void xmb_try_patch(void)
{
	SceModule *m;
	u8 *q, *qend;
	int g = 0;
	if (xmb_uid < 0) return;
	if (time_due(sceKernelGetSystemTimeLow(), xmb_deadline)) {
		L_begin(); L_time(); L_s("XMBPASS gave up: stub never became a syscall stub\n"); L_end();
		xmb_uid = -1; return;
	}
	m = sceKernelFindModuleByUID(xmb_uid);
	if (!m || !ptr_ok_w(m, sizeof(SceModule))) return;
	q = (u8 *)m->stub_top; qend = q + m->stub_size;
	while (ptr_ok_w(q, STUB_HDR_BYTES) && q < qend && g++ < MAX_STUB_LIBS) {
		stub_t *t = (stub_t *)q;
		u32 i;
		if (t->len == 0) break;
		if (p_streq(safe_name((const char *)t->libname), "sceUsbAcc") && t->fcnt < MAX_IMPORTS
		    && ptr_ok_w((void *)t->nids, NID_BYTES * t->fcnt) && ptr_ok_w((void *)t->stubs, STUB_BYTES * t->fcnt)) {
			u32 *nids = (u32 *)t->nids;
			volatile u32 *st = (volatile u32 *)t->stubs;
			for (i = 0; i < t->fcnt; i++) {
				if (nids[i] != NID_GETAUTHSTAT) continue;
				if (st[2 * i] == MIPS_JR_RA && (st[2 * i + 1] & MIPS_FUNCT_MASK) == MIPS_FUNCT_SYSCALL) {
					st[2 * i + 1] = MIPS_LI_V0_0;
					sceKernelDcacheWritebackInvalidateAll();
					sceKernelIcacheInvalidateAll();
					L_begin(); L_time(); L_s("XMBPASS GetAuthStat stub at 0x"); L_x((u32)&st[2 * i], 8); L_s(" now returns 0\n"); L_end();
					xmb_uid = -1;
				} else if (st[2 * i] == MIPS_JR_RA && st[2 * i + 1] == MIPS_LI_V0_0) {
					xmb_uid = -1;
				}
				return;
			}
		}
		q += t->len * 4;
	}
}

static int patch_module(SceModule *m)
{
	u8 *p, *end;
	int guard = 0, patched = 0;

	if (!m || !ptr_ok_w(m, sizeof(SceModule))) return 0;
	if (p_streq(m->modname, "psychea")) return 0;
	if (!p_ci_contains(m->modname, "cam") && !p_ci_contains(m->modname, "acc")
	    && !p_ci_contains(m->modname, "mic")) return 0;
	p = (u8 *)m->stub_top;
	end = p + m->stub_size;
	if (!ptr_ok(p, 4)) return 0;

	while (p < end && guard++ < MAX_STUB_LIBS) {
		stub_t *s = (stub_t *)p;
		const char *ln;
		u32 i;
		int h;
		if (!ptr_ok_w(s, STUB_HDR_BYTES) || s->len == 0) break;
		ln = safe_name((const char *)s->libname);
		if ((p_streq(ln, "sceUsbBus_driver") || p_streq(ln, "sceUsbAcc_internal"))
		    && s->fcnt < MAX_IMPORTS && ptr_ok_w((void *)s->nids, NID_BYTES * s->fcnt)
		    && ptr_ok_w((void *)s->stubs, STUB_BYTES * s->fcnt)) {
			u32 *nids = (u32 *)s->nids;
			u32 *st = (u32 *)s->stubs;
			for (i = 0; i < s->fcnt; i++) {
				for (h = 0; h < NHOOKS; h++) {
					if (nids[i] == hook_nid[h] && p_streq(ln, hook_lib[h])) {
						u32 ins = st[2 * i];
						u32 cur;
						if ((ins >> 26) != MIPS_OP_J) break;
						cur = ((ins & MIPS_J_TARGET) << 2) | PSP_SEG_KERNEL;
						if (cur == (u32)hook_fn[h]) break;
						if (!orig_fn[h]) orig_fn[h] = cur;
						if (cur != orig_fn[h]) break;
						st[2 * i] = MIPS_J | (((u32)hook_fn[h] >> 2) & MIPS_J_TARGET);
						patched++;
						break;
					}
				}
			}
		}
		p += s->len * 4;
	}
	if (cfg.xmb_camera && p_ci_contains(m->modname, "camera_plugin")) { xmb_deadline = sceKernelGetSystemTimeLow() + XMB_PATCH_WINDOW_US; xmb_uid = m->modid; xmb_try_patch(); }   /* deadline first: the writer thread reads uid, then deadline */
	{	/* the accessory power flag accessor (NID 48CCE3C1) returns 1: the firmware clears the flag on every bus reset. Once. */
		static int g4c_patched;
		int h2;
		for (h2 = 0; h2 < NHOOKS && !g4c_patched && cfg.accessory_power; h2++) {
			if (hook_nid[h2] == NID_ACC_POWER && orig_fn[h2]) {
				volatile u32 *fn = (volatile u32 *)orig_fn[h2];
				if (ptr_ok_w((void *)fn, 8)) {
					fn[0] = MIPS_JR_RA;
					fn[1] = MIPS_LI_V0_1;
					sceKernelDcacheWritebackInvalidateAll();
					sceKernelIcacheInvalidateAll();
					g4c_patched = 1;
					patched++;
					L_begin(); L_time(); L_s("patched 48CCE3C1 to return 1 at 0x"); L_x(orig_fn[h2], 8); L_c('\n'); L_end();
				}
			}
		}
	}
	if (patched) {
		sceKernelDcacheWritebackInvalidateAll();
		sceKernelIcacheInvalidateAll();
		L_begin();
		L_time(); L_s("patched "); L_d(patched); L_s(" import stubs in module "); L_s(m->modname); L_c('\n');
		L_end();
	}
	return patched;
}

#endif
