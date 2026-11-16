/* writer thread: log file, statistics, config file */
#ifndef PSYCHEA_WRITER_H
#define PSYCHEA_WRITER_H

static u32 total_written;

/* one line per second for the traffic that is not logged packet by packet */
static void emit_stats(void)
{
	int d, e;
	L_begin();
	for (d = 0; d < 2; d++) {
		for (e = 0; e < NEP; e++) {
			if (!tally[d][e].n && !tally[d][e].errs) continue;
			L_time(); L_s("STAT    "); L_s(d ? "recv" : "send"); L_s(" "); L_s(safe_name(watch[e / 8].name));
			L_s(" ep["); L_d(e % 8); L_s("]");
			L_s(" packets="); L_d((int)tally[d][e].n); L_s(" bytes="); L_d((int)tally[d][e].bytes);
			L_s(" errors="); L_d((int)tally[d][e].errs); L_c('\n');
			tally[d][e].n = tally[d][e].bytes = tally[d][e].errs = 0;
		}
	}
	L_end();
}

static void rotate_log(void)
{
	SceIoStat st;
	if (sceIoGetstat(LOG_PATH, &st) >= 0 && (u32)st.st_size > ROTATE_AT) {
		sceIoRemove(LOG_OLD);
		sceIoRename(LOG_PATH, LOG_OLD);
	}
}

static int writer_thread(SceSize args, void *argp)
{
	u32 tick = 0, next_slow = sceKernelGetSystemTimeLow();
	int rotated = 0;
	(void)args; (void)argp;
	for (;;) {
		u32 n, d, now;
		int s;
		sceKernelDelayThread(xmb_uid >= 0 ? POLL_FAST_US : POLL_SLOW_US);
		xmb_try_patch();
		clk_poll();
		now = sceKernelGetSystemTimeLow();      /* microseconds, wraps after 71.6 min: compare with time_due only */
		if (!time_due(now, next_slow)) continue;
		next_slow = now + POLL_SLOW_US;
		cam_try_hook();
		if (++tick % STAT_EVERY == 0) emit_stats();
		process_dumps();
		s = sceKernelCpuSuspendIntr();
		n = lhead;
		d = dropped;
		{ u32 i; for (i = 0; i < n; i++) wbuf[i] = lbuf[i]; }
		lhead = 0;
		dropped = 0;
		sceKernelCpuResumeIntr(s);
		if (n && !rotated) { rotate_log(); rotated = 1; }
		if (n && total_written < LOG_MAX) {
			SceUID fd = sceIoOpen(LOG_PATH, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_APPEND, 0777);
			if (fd >= 0) {
				sceIoWrite(fd, wbuf, n);
				if (d) {
					static const char m[] = "!! log characters dropped (RAM buffer full): ";
					if (wbuf[n - 1] != '\n') sceIoWrite(fd, "\n", 1);      /* the cut line gets its end, the message its own line */
					char num[12];
					int k = 11;
					u32 v = d;
					num[k] = '\n';
					do { num[--k] = '0' + v % 10; v /= 10; } while (v && k > 0);
					sceIoWrite(fd, m, sizeof(m) - 1);
					sceIoWrite(fd, num + k, 12 - k);
				}
				if (total_written + n >= LOG_MAX) {              /* the last write of this boot: say so once */
					static const char lm[] = "!! log limit of this boot reached, further lines are discarded\n";
					sceIoWrite(fd, lm, sizeof(lm) - 1);
				}
				sceIoClose(fd);
				total_written += n;
			}
		}
	}
	return 0;
}

#define CFG_BUF 2048                         /* a longer ini keeps only its complete first lines */

static void load_config(void)
{
	static char buf[CFG_BUF];
	SceUID fd;
	int n = 0, r = 1;
	cfg_defaults(&cfg);
	fd = sceIoOpen(CFG_PATH, PSP_O_RDONLY, 0);
	if (fd < 0) return;
	while (n < CFG_BUF && (r = sceIoRead(fd, buf + n, CFG_BUF - n)) > 0) n += r;     /* a read may return less than asked */
	sceIoClose(fd);
	if (n > 0) cfg_parse(&cfg, buf, cfg_usable_len(buf, n, r != 0));                 /* r == 0: end of file reached */
}

#endif
