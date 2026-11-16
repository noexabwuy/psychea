/* SPDX-License-Identifier: GPL-3.0-or-later
 * psychea - kernel plugin for ARK-4 (PSP 6.61): lets a PC act as the PSP Go!Cam camera (the PC side is the psychea
 * program) and logs the traffic between the PSP USB drivers and the bus driver to ms0:/psychea.log.
 *
 * Settings: ms0:/seplugins/psychea.ini, keys and defaults in psychea.ini.sample.
 *
 * The import stubs of the modules that link against the bus libraries (J target; nop) are patched, so the system code
 * stays untouched; later modules are caught through sctrlHENSetStartModuleHandler. The wrappers do no file I/O (they
 * can run in interrupt context): lines go to a RAM buffer that a thread writes out every 300 ms. High-rate endpoints
 * are logged line by line for the first requests only, then as one STAT line per second.
 */
#include <pspkernel.h>
#include <pspiofilemgr.h>
#include <pspthreadman.h>
#include <psploadcore.h>
#include <pspintrman.h>
#include <pspsdk.h>
#include <systemctrl.h>
#include "cfg.h"
#include "stillbig.h"
#include "safe.h"
#include "slots.h"

PSP_MODULE_INFO("psychea", PSP_MODULE_KERNEL, 0, 1);
PSP_NO_CREATE_MAIN_THREAD();

#include "defs.h"
#include "util.h"
#include "log.h"
#include "bus_state.h"
#include "bus_hooks.h"
#include "camapi.h"
#include "patch.h"
#include "dump.h"
#include "modules.h"
#include "writer.h"

/* Nothing is installed until what can fail has worked: config, writer thread (created, not started), start line,
 * start-module handler, patch the loaded modules, start the thread. Lines before the thread runs wait in the RAM buffer. */
int module_start(SceSize args, void *argp)
{
	SceUID th;
	(void)args; (void)argp;

	load_config();
	th = sceKernelCreateThread("psychea", writer_thread, WRITER_PRIORITY, WRITER_STACK, 0, NULL);
	if (th < 0) return th;
	L_begin(); L_time(); L_s("psychea " VERSION " start: xmb_camera="); L_d(cfg.xmb_camera); L_s(" accessory_power="); L_d(cfg.accessory_power);
	L_s(" short_log="); L_d(cfg.short_log); L_s(" dump_modules="); L_d(cfg.dump_modules);
	L_s(" log_camera_calls="); L_d(cfg.log_camera_calls); L_s(" photos_1280x960="); L_d(cfg.photos_1280x960);
	L_c('\n'); L_end();
	prev_handler = sctrlHENSetStartModuleHandler(on_module_start);
	patch_loaded_modules();

	if (sceKernelStartThread(th, 0, NULL) < 0) {          /* the patches stay, the log stays in RAM (and is dropped when full) */
		sceKernelDeleteThread(th);
		L_begin(); L_time(); L_s("psychea: the writer thread did not start, no log file\n"); L_end();
	}
	return 0;
}

/* The patches cannot be undone (import stubs, syscall entries, the start-module handler, driver structures, two
 * overwritten system functions, the writer thread), so a stop is refused (SCE_KERNEL_STOP_FAIL). */
int module_stop(SceSize args, void *argp)
{
	(void)args; (void)argp;
	L_begin(); L_time(); L_s("psychea: module_stop refused, patches and hooks cannot be undone\n"); L_end();
	return 1;
}
