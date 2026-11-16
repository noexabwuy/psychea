/* string and pointer helpers */
#ifndef PSYCHEA_UTIL_H
#define PSYCHEA_UTIL_H

static u32 p_strlen(const char *s) { u32 n = 0; while (s[n]) n++; return n; }

static int p_streq(const char *a, const char *b)
{
	while (*a && *a == *b) { a++; b++; }
	return *a == *b;
}

static int p_ci_contains(const char *h, const char *n)
{
	u32 hl = p_strlen(h), nl = p_strlen(n), i, j;
	for (i = 0; i + nl <= hl; i++) {
		for (j = 0; j < nl; j++) {
			char a = h[i + j], b = n[j];
			if (a >= 'A' && a <= 'Z') a += 32;
			if (b >= 'A' && b <= 'Z') b += 32;
			if (a != b) break;
		}
		if (j == nl) return 1;
	}
	return 0;
}

/* ptr_ok: [p, p+len) is RAM. ptr_ok_w: also 4-aligned (a word access at an odd address freezes the console). */
static int ptr_ok(const void *p, u32 len)   { return addr_ok((u32)p, len); }
static int ptr_ok_w(const void *p, u32 len) { return addr_ok_w((u32)p, len); }

static const char *safe_name(const char *s)
{
	u32 i;
	if (!ptr_ok(s, 1)) return "(null)";
	for (i = 0; i < 40; i++) {
		if (!ptr_ok(s + i, 1)) return "(bad)";
		if (s[i] == 0) return s;
	}
	return "(long)";
}

#endif
