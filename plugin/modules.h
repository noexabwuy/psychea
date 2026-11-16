/* module-start handler and the pass over the modules that are loaded already */
#ifndef PSYCHEA_MODULES_H
#define PSYCHEA_MODULES_H

static STMOD_HANDLER prev_handler;

/* L_busstate needs the bus driver module whether dumps are on or not */
static void note_usb_module(SceModule *m)
{
	if (m && ptr_ok_w(m, sizeof(SceModule)) && p_streq(m->modname, "sceUSB_Driver")) usb_uid = m->modid;
}

static int on_module_start(SceModule *mod)
{
	if (mod && ptr_ok_w(mod, 64)) {
		L_begin(); L_time(); L_s("module start: "); L_s(mod->modname); L_c('\n'); L_end();
		note_usb_module(mod);
		queue_dump(mod);
		patch_module(mod);
		if (p_streq(mod->modname, "sceUSBCam_Driver")) cam_gen++;
	}
	return prev_handler ? prev_handler(mod) : 0;
}

static void patch_loaded_modules(void)
{
	SceUID ids[MAX_MODS];
	u32 cnt = 0;
	u32 i;
	if (sceKernelGetModuleIdListForKernel(ids, sizeof(ids), &cnt, 0) < 0) return;
	if (cnt > MAX_MODS) cnt = MAX_MODS;
	for (i = 0; i < cnt; i++) {
		SceModule *m = sceKernelFindModuleByUID(ids[i]);
		note_usb_module(m);
		queue_dump(m);
		patch_module(m);
	}
}

#endif
