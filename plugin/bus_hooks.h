/* wrappers of the driver callbacks and of the bus calls (sceUsbbd*, sceUsbAcc*) */
#ifndef PSYCHEA_BUS_HOOKS_H
#define PSYCHEA_BUS_HOOKS_H

static int ctl_common(int s, int a0, int a1, int a2)
{
	L_begin();
	L_time(); L_s("CTRL    "); L_s(safe_name(slot[s].name));
	L_s(" a0="); L_x((u32)a0, 8); L_s(" a1="); L_x((u32)a1, 8);
	L_s(" req: "); L_hex((u8 *)a2, 8); L_c('\n');
	L_busstate();
	L_end();
	return ((int (*)(int, int, int))slot[s].o_ctl)(a0, a1, a2);
}

static int att_common(int s, int a0, int a1, int a2)
{
	int r;
	L_begin();
	L_time(); L_s("ATTACH  "); L_s(safe_name(slot[s].name));
	L_s(" speed="); L_d(a0); L_s(" a1="); L_x((u32)a1, 8); L_s(" a2="); L_x((u32)a2, 8); L_c('\n');
	L_busstate();
	L_end();
	r = ((int (*)(int, int, int))slot[s].o_att)(a0, a1, a2);
	L_begin(); L_time(); L_s("ATTACH  "); L_s(safe_name(slot[s].name)); L_s(" -> "); L_x((u32)r, 8); L_c('\n');
	if (slot[s].d) { L_s("    descriptors after attach:\n"); dump_config(slot[s].d, 0); dump_config(slot[s].d, 1); }
	L_end();
	return r;
}

static int det_common(int s, int a0, int a1, int a2)
{
	L_begin();
	L_time(); L_s("DETACH  "); L_s(safe_name(slot[s].name));
	L_s(" a0="); L_x((u32)a0, 8); L_c('\n');
	L_busstate();
	L_end();
	return ((int (*)(int, int, int))slot[s].o_det)(a0, a1, a2);
}

#define SLOTFN(n) \
	static int ctl##n(int a, int b, int c) { return ctl_common(n, a, b, c); } \
	static int att##n(int a, int b, int c) { return att_common(n, a, b, c); } \
	static int det##n(int a, int b, int c) { return det_common(n, a, b, c); }
SLOTFN(0) SLOTFN(1) SLOTFN(2) SLOTFN(3)

static void *const ctl_fns[NSLOT] = { ctl0, ctl1, ctl2, ctl3 };
static void *const att_fns[NSLOT] = { att0, att1, att2, att3 };
static void *const det_fns[NSLOT] = { det0, det1, det2, det3 };

static void on_register(struct drv *d)
{
	u32 i;
	if (!ptr_ok_w(d, sizeof(*d))) return;
	if (p_ci_contains(safe_name(d->name), "stor")) return;      /* mass storage: hands off */
	if (ptr_ok(d->endp, 12) && d->endpoints > 0 && d->endpoints <= MAX_ENDPOINTS) {
		int w = key_slot(watch_key, &nwatch, NWATCH, (unsigned int)d->endp);
		if (w >= 0) {
			int k, dir;
			watch[w].base = (u32)d->endp;
			watch[w].end = (u32)d->endp + 12 * (u32)d->endpoints;
			watch[w].name = d->name;
			for (dir = 0; dir < 2; dir++)                       /* a module loaded again starts with fresh counters */
				for (k = 0; k < 8; k++) {
					seen[dir][w * 8 + k] = dseen[dir][w * 8 + k] = 0;
					tally[dir][w * 8 + k].n = tally[dir][w * 8 + k].bytes = tally[dir][w * 8 + k].errs = 0;
				}
		}
	}
	L_begin();
	L_time(); L_s("REGISTER driver \""); L_s(safe_name(d->name)); L_s("\" endpoints=");
	L_d(d->endpoints);
	L_s(" recvctl="); L_x((u32)d->recvctl, 8);
	L_s(" attach="); L_x((u32)d->attach, 8);
	L_s(" detach="); L_x((u32)d->detach, 8);
	L_s(" start="); L_x((u32)d->start_func, 8);
	L_s(" stop="); L_x((u32)d->stop_func, 8); L_c('\n');
	if (ptr_ok_w(d->endp, 12 * 4) && d->endpoints > 0 && d->endpoints <= MAX_ENDPOINTS) {
		for (i = 0; i < (u32)d->endpoints; i++) {
			int *e = (int *)d->endp + 3 * i;
			L_s("    ep["); L_d((int)i); L_s("] endpnum="); L_d(e[0]); L_c('\n');
		}
	}
	dump_config(d, 0);
	dump_config(d, 1);
	if (ptr_ok(d->str, 4)) { L_s("    string desc: "); L_hex((u8 *)d->str, 16); L_c('\n'); }
	L_end();

	/* wrap the callbacks; a driver registered again keeps its row, and a field that already holds the wrapper is left alone */
	{
		int s = key_slot(slot_key, &nslot, NSLOT, (unsigned int)d);
		if (s >= 0) {
			slot[s].name = d->name;
			slot[s].d = d;
			if (d->recvctl != (void *)ctl_fns[s]) { slot[s].o_ctl = d->recvctl; if (d->recvctl) d->recvctl = (void *)ctl_fns[s]; }
			if (d->attach  != (void *)att_fns[s]) { slot[s].o_att = d->attach;  if (d->attach)  d->attach  = (void *)att_fns[s]; }
			if (d->detach  != (void *)det_fns[s]) { slot[s].o_det = d->detach;  if (d->detach)  d->detach  = (void *)det_fns[s]; }
		}
	}
}

static int req_done(int a0, int a1, int a2, int a3)
{
	struct req *r = (struct req *)a0;
	void *orig = 0;
	int dir = 0, i;

	L_begin();
	for (i = 0; i < NPEND; i++) {
		if (pend[i].r == r) { orig = pend[i].orig; dir = pend[i].dir; break; }
	}
	if (ptr_ok_w(r, sizeof(*r))) {
		int e = ep_of(r);
		if (e >= 0 && r->retcode != 0) tally[dir][e].errs++;
		if (e >= 0 && r->retcode == 0 && dseen[dir][e]++ >= (dir ? WARMUP_RECV : WARMUP_SEND)) {
			tally[dir][e].n++;                      /* routine completion: counted, summed up in STAT */
			if (r->recvsize > 0) tally[dir][e].bytes += (u32)r->recvsize;
			L_end();
			return orig ? ((int (*)(int, int, int, int))orig)(a0, a1, a2, a3) : 0;
		}
		if (cfg.short_log && r->retcode == 0) { L_end(); return orig ? ((int (*)(int, int, int, int))orig)(a0, a1, a2, a3) : 0; }
		L_time(); L_s(dir ? "RECVDONE" : "SENDDONE");
		L_s(" req="); L_x((u32)r, 8);
		L_s(" ret="); L_x((u32)r->retcode, 8);
		L_s(" size="); L_d(r->recvsize);
		if (dir && r->retcode == 0 && r->recvsize > 0 && ptr_ok(r->data, 4)) {
			int n = r->recvsize > MAX_SHOWN_BYTES ? MAX_SHOWN_BYTES : r->recvsize;
			L_s(" data: "); L_hex((u8 *)r->data, n);
		}
		L_c('\n');
	}
	L_end();
	if (orig) return ((int (*)(int, int, int, int))orig)(a0, a1, a2, a3);
	return 0;
}

/* the driver goes away: its rows are free for the next registration (a reloaded module may land on other addresses) */
static void on_unregister(struct drv *d)
{
	int s, w;
	if (!ptr_ok_w(d, sizeof(*d))) return;
	s = key_free(slot_key, nslot, (unsigned int)d);
	if (s >= 0) slot[s].d = 0;
	w = key_free(watch_key, nwatch, (unsigned int)d->endp);
	if (w >= 0) watch[w].base = watch[w].end = 0;
}

/* the request -> original callback mapping lives as long as the request (drivers re-queue the same object) */
static void track_req(struct req *r, int dir)
{
	int i, freei = -1;
	if (!ptr_ok_w(r, sizeof(*r))) return;
	for (i = 0; i < NPEND; i++) {
		if (pend[i].r == r) {
			if (r->func && r->func != (void *)req_done) {
				pend[i].orig = r->func;
				r->func = (void *)req_done;
			}
			pend[i].dir = dir;
			return;
		}
		if (!pend[i].r && freei < 0) freei = i;
	}
	if (freei >= 0 && r->func && r->func != (void *)req_done) {
		pend[freei].r = r; pend[freei].orig = r->func; pend[freei].dir = dir;
		r->func = (void *)req_done;
	}
}

static int spy(int idx, int a0, int a1, int a2, int a3, int a4, int a5, int a6, int a7)
{
	int r;
	u32 c = ++hook_cnt[idx];

	/* drivers we do not watch: pass through */
	if ((idx == H_REQSEND || idx == H_REQRECV || idx == H_REQCANCEL || idx == H_CLEARFIFO || idx == H_STALL)
	    && !ep_watched((idx == H_REQSEND || idx == H_REQRECV || idx == H_REQCANCEL) ? (ptr_ok_w((void *)a0, 4) ? *(void **)a0 : 0) : (void *)a0))
		return ((fn8_t)orig_fn[idx])(a0, a1, a2, a3, a4, a5, a6, a7);

	/* polled status calls: log the first calls, every 1024th and a few changes */
	if (idx == H_GETSTATE || idx == H_AUTHSTAT || idx == H_BUS8A3E) {
		int pr = ((fn8_t)orig_fn[idx])(a0, a1, a2, a3, a4, a5, a6, a7);
		int changed = pr != polled_last[idx] && polled_changes[idx] < 20;
		if (changed) polled_changes[idx]++;
		polled_last[idx] = pr;
		if (c <= 6 || (c & 1023) == 0 || changed) {
			L_begin();
			L_time(); L_s("POLL    "); L_s(hook_name[idx]); L_s("(");
			L_x((u32)a0, 8); L_s(", "); L_x((u32)a1, 8); L_s(") = 0x"); L_x((u32)pr, 8);
			L_s(" call#"); L_d((int)c); if (changed) L_s(" (result changed)");
			L_c('\n');
			L_end();
		}
		return pr;
	}

	if (idx == H_REQSEND || idx == H_REQRECV) {
		int dir = idx == H_REQRECV, e = ep_of((struct req *)a0);
		if (e >= 0 && seen[dir][e]++ >= (u32)(dir ? WARMUP_RECV : WARMUP_SEND)) {
			L_begin(); track_req((struct req *)a0, dir); L_end();      /* routine traffic: counted at completion */
			return ((fn8_t)orig_fn[idx])(a0, a1, a2, a3, a4, a5, a6, a7);
		}
	}

	if (cfg.short_log && idx != H_REGISTER && idx != H_UNREGISTER) {
		if (idx == H_REQSEND || idx == H_REQRECV) { L_begin(); track_req((struct req *)a0, idx == H_REQRECV); L_end(); }
		return ((fn8_t)orig_fn[idx])(a0, a1, a2, a3, a4, a5, a6, a7);
	}

	L_begin();
	L_time(); L_s("CALL    "); L_s(hook_name[idx]);
	L_s("("); L_x((u32)a0, 8); L_s(", "); L_x((u32)a1, 8); L_s(", ");
	L_x((u32)a2, 8); L_s(", "); L_x((u32)a3, 8); L_s(")\n");
	switch (idx) {
	case H_REQSEND:
	case H_REQRECV: {
		struct req *q = (struct req *)a0;
		if (ptr_ok_w(q, sizeof(*q))) {
			L_s("    endp="); L_x((u32)q->endp, 8);
			if (ptr_ok_w(q->endp, 4)) { L_s(" (epnum "); L_d(*(int *)q->endp); L_c(')'); }
			L_s(" size="); L_d(q->size); L_s(" func="); L_x((u32)q->func, 8);
			if (idx == H_REQSEND && ptr_ok(q->data, 4) && q->size > 0) {
				int n = q->size > MAX_SHOWN_BYTES ? MAX_SHOWN_BYTES : q->size;
				L_s(" data: "); L_hex((u8 *)q->data, n);
			}
			L_c('\n');
		}
		break;
	}
	case H_BUS48CC ... H_AUTHSTAT: {       /* the bus_* and sceUsbAcc* calls: show the pointers they are given */
		const int args[4] = { a0, a1, a2, a3 };
		int i;
		for (i = 0; i < 4; i++) {
			if (ptr_ok((void *)args[i], 16) && (u32)args[i] > PSP_RAM_START) {
				L_s("    arg"); L_d(i); L_s(" -> "); L_hex((u8 *)args[i], 16); L_c('\n');
			}
		}
		break;
	}
	default: break;
	}
	L_end();

	if (idx == H_REGISTER) on_register((struct drv *)a0);   /* takes L_begin itself */
	if (idx == H_UNREGISTER) on_unregister((struct drv *)a0);
	if (idx == H_REQSEND)  { L_begin(); track_req((struct req *)a0, 0); L_end(); }
	if (idx == H_REQRECV)  { L_begin(); track_req((struct req *)a0, 1); L_end(); }

	r = ((fn8_t)orig_fn[idx])(a0, a1, a2, a3, a4, a5, a6, a7);

	L_begin();
	if (idx == H_GETINFO && ptr_ok((void *)a0, 4) && a1 > 0 && a1 <= MAX_SHOWN_BYTES) {
		L_s("    GetInfo out: "); L_hex((u8 *)a0, a1); L_c('\n');
	}
	L_time(); L_s("  ret   "); L_s(hook_name[idx]); L_s(" = 0x"); L_x((u32)r, 8); L_c('\n');
	L_end();
	return r;
}

#define X(i, l, n, s) \
	static int w##i(int a0, int a1, int a2, int a3, int a4, int a5, int a6, int a7) \
	{ return spy(i, a0, a1, a2, a3, a4, a5, a6, a7); }
HOOKS(X)
#undef X

static void *const hook_fn[NHOOKS] = {
#define X(i, l, n, s) (void *)w##i,
	HOOKS(X)
#undef X
};

#endif
