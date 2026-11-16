/* RAM log (safe in interrupt context) and the timestamp clock */
#ifndef PSYCHEA_LOG_H
#define PSYCHEA_LOG_H

static char lbuf[LB_SZ];
static char wbuf[LB_SZ];
static volatile u32 lhead;
static u32 dropped;
static int intr_state;

static void L_begin(void) { intr_state = sceKernelCpuSuspendIntr(); }
static void L_end(void)   { sceKernelCpuResumeIntr(intr_state); }

static void L_c(char c)
{
	if (lhead < LB_SZ) lbuf[lhead++] = c; else dropped++;
}

static void L_s(const char *s) { while (*s) L_c(*s++); }

static void L_x(u32 v, int digits)
{
	int i;
	for (i = digits - 1; i >= 0; i--) {
		u32 d = (v >> (4 * i)) & 15;
		L_c(d < 10 ? '0' + d : 'A' + d - 10);
	}
}

static void L_d(int v)
{
	char t[12];
	int i, n = fmt_d(t, v);
	for (i = 0; i < n; i++) L_c(t[i]);
}

static void L_hex(const u8 *p, int n)
{
	int i;
	if (!ptr_ok(p, n)) { L_s("(bad ptr)"); return; }
	for (i = 0; i < n; i++) { if (i) L_c(' '); L_x(p[i], 2); }
}

/* The 32-bit microsecond counter wraps every 71.6 min; tclk counts the wraps. Touched only with interrupts off
 * and read once per tick by the writer thread (clk_poll), so no wrap is missed. */
static struct clk tclk;

static void L_time(void)
{
	unsigned int lo = sceKernelGetSystemTimeLow(), sec, ms;
	clk_advance(&tclk, lo);
	clk_split(&tclk, lo, &sec, &ms);
	L_c('['); L_d((int)sec); L_c('.');
	L_c('0' + ms / 100); L_c('0' + (ms / 10) % 10); L_c('0' + ms % 10);
	L_s("] ");
}

static void clk_poll(void)
{
	L_begin();
	clk_advance(&tclk, sceKernelGetSystemTimeLow());
	L_end();
}

#endif
