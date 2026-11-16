/* pure helpers (no PSP calls), tested on the PC in test_cfg.c */
#ifndef PSYCHEA_SAFE_H
#define PSYCHEA_SAFE_H

/* PSP memory map: RAM is 0x08000000..0x0c000000 (a PSP-1000 has 32 MB, below this end), mirrored in three segments
 * selected by the top bits of an address. */
#define PSP_RAM_START    0x08000000u
#define PSP_RAM_END      0x0c000000u
#define PSP_ADDR_MASK    0x1fffffffu    /* the physical part */
#define PSP_SEG_MASK     0xc0000000u
#define PSP_SEG_UNCACHED 0x40000000u    /* 0x40000000 = the uncached mirror */
#define PSP_SEG_KERNEL   0x80000000u    /* kernel mirror: the segment of a j target in an import stub */

/* Is [v, v+len) inside RAM (any mirror)? An overflow of v+len is rejected. */
static int addr_ok(unsigned int v, unsigned int len)
{
	unsigned int a = v & PSP_ADDR_MASK;
	unsigned int seg = v & PSP_SEG_MASK;
	unsigned int e = a + len;
	if (!v) return 0;
	if (seg != 0 && seg != PSP_SEG_KERNEL && seg != PSP_SEG_UNCACHED) return 0;
	return a >= PSP_RAM_START && e <= PSP_RAM_END && e >= a;
}

/* The same, and v is 4-aligned: required before a word access (an unaligned one freezes the console). */
static int addr_ok_w(unsigned int v, unsigned int len)
{
	return !(v & 3) && addr_ok(v, len);
}

/* Has `due` been reached? Both are the 32-bit microsecond counter (wraps every 71.6 min); the difference is taken
 * modulo 2^32, so this is right across a wrap while due is less than 35 minutes ahead. */
static int time_due(unsigned int now, unsigned int due)
{
	return (int)(now - due) >= 0;
}

/* epoch counts the wraps of the 32-bit counter, so the log timestamp never falls back to 0 */
struct clk {
	unsigned int last;
	unsigned int epoch;
};

/* call with every reading, in time order, at least once per wrap period */
static void clk_advance(struct clk *c, unsigned int lo)
{
	if (lo < c->last) c->epoch++;
	c->last = lo;
}

/* seconds and milliseconds of epoch * 2^32 + lo microseconds, in 32-bit arithmetic (2^32 us = 4294 s 967296 us) */
static void clk_split(const struct clk *c, unsigned int lo, unsigned int *sec, unsigned int *ms)
{
	unsigned int e_us = c->epoch * 967296u;            /* fits for epoch < 4440 (more than 300 days) */
	unsigned int s = c->epoch * 4294u + lo / 1000000u + e_us / 1000000u;
	unsigned int r = lo % 1000000u + e_us % 1000000u;
	if (r >= 1000000u) { r -= 1000000u; s++; }
	*sec = s;
	*ms = r / 1000u;
}

/* decimal text of v into out[12] (no NUL); returns the length */
static int fmt_d(char *out, int v)
{
	char t[12];
	int n = 0, k = 0;
	unsigned int u;
	if (v < 0) { out[k++] = '-'; u = 0u - (unsigned int)v; } else u = (unsigned int)v;
	if (!u) t[n++] = '0';
	while (u) { t[n++] = '0' + (u % 10); u /= 10; }
	while (n) out[k++] = t[--n];
	return k;
}
#endif
