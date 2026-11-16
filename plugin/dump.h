/* module dumps for offline disassembly (dump_modules=1) */
#ifndef PSYCHEA_DUMP_H
#define PSYCHEA_DUMP_H

/* Module dumps to ms0:/dump/<name>_s<N>_<ADDR>.bin, once per boot: sceUSB_Driver and camera_plugin*.
 * The start handler only notes the module; the writer thread dumps it later. */
static struct { char name[32]; SceUID uid; u32 due_ms; } dq[NDUMPQ];
static volatile int dq_head, dq_tail;
static int dumped_usb, dumped_cam;

static int dump_wanted(const char *name)
{
	if (!cfg.dump_modules) return 0;
	return (p_streq(name, "sceUSB_Driver") && !dumped_usb) || (p_ci_contains(name, "camera_plugin") && !dumped_cam);
}

/* only name and id are noted: the segment table of a starting module may not be filled in yet */
static void queue_dump(SceModule *m)
{
	int i, h = dq_head;
	if (!m || !ptr_ok_w(m, sizeof(SceModule)) || h >= NDUMPQ || !dump_wanted(m->modname)) return;
	if (p_streq(m->modname, "sceUSB_Driver")) dumped_usb = 1; else dumped_cam = 1;
	for (i = 0; i < 31 && m->modname[i]; i++) dq[h].name[i] = m->modname[i];
	dq[h].name[i] = 0;
	dq[h].uid = m->modid;
	dq[h].due_ms = sceKernelGetSystemTimeLow() / 1000 + 1000;
	dq_head = h + 1;
}

static char *cat_s(char *d, const char *s) { while (*s) *d++ = *s++; *d = 0; return d; }

static void dump_skip(const char *name, int seg, u32 a, u32 sz, const char *why)
{
	L_begin(); L_time(); L_s("DUMP    skip "); L_s(name); L_s(" seg"); L_d(seg); L_s(" addr="); L_x(a, 8);
	L_s(" size="); L_x(sz, 8); L_s(" : "); L_s(why); L_c('\n'); L_end();
}

static void dump_module(int j)
{
	static u32 total;
	static int made_dir;
	SceModule *m = sceKernelFindModuleByUID(dq[j].uid);
	int i, n;
	if (!m || !ptr_ok_w(m, sizeof(SceModule))) { dump_skip(dq[j].name, -1, 0, 0, "module not found"); return; }
	n = m->nsegment > 4 ? 4 : m->nsegment;
	L_begin(); L_time(); L_s("DUMP    "); L_s(dq[j].name); L_s(" segments="); L_d(m->nsegment); L_c('\n'); L_end();
	for (i = 0; i < n; i++) {
		char path[128], *p;
		u32 a = m->segmentaddr[i], sz = m->segmentsize[i], k;
		SceUID fd;
		int w;
		if (!sz) { dump_skip(dq[j].name, i, a, sz, "empty"); continue; }
		if (sz > DUMP_SEG_MAX) { dump_skip(dq[j].name, i, a, sz, "segment too big"); continue; }
		if (total + sz > DUMP_TOTAL_MAX) { dump_skip(dq[j].name, i, a, sz, "dump budget used up"); continue; }
		if (!ptr_ok((void *)a, sz)) { dump_skip(dq[j].name, i, a, sz, "address not readable"); continue; }
		if (!made_dir) { sceIoMkdir(DUMP_DIR, 0777); made_dir = 1; }
		p = cat_s(path, DUMP_DIR "/"); p = cat_s(p, dq[j].name);
		p = cat_s(p, "_s"); *p++ = '0' + i; *p++ = '_';
		for (k = 0; k < 8; k++) { u32 d = (a >> (28 - 4 * k)) & 15; *p++ = d < 10 ? '0' + d : 'A' + d - 10; }
		p = cat_s(p, ".bin");
		fd = sceIoOpen(path, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
		if (fd < 0) { dump_skip(dq[j].name, i, a, sz, "cannot create file"); continue; }
		w = sceIoWrite(fd, (void *)a, sz);
		sceIoClose(fd);
		total += sz;
		L_begin(); L_time(); L_s("DUMP    "); L_s(path); L_s(" size="); L_d((int)sz); L_s(" written="); L_d(w); L_c('\n'); L_end();
	}
}

static void process_dumps(void)
{
	while (dq_tail < dq_head) {
		int j = dq_tail;
		if ((int)(sceKernelGetSystemTimeLow() / 1000 - dq[j].due_ms) < 0) return;
		dq_tail = j + 1;
		dump_module(j);
	}
}

#endif
