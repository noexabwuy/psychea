#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cfg.h"
#include "stillbig.h"
#include "safe.h"
static int fails;
#define CHECK(x) do { if (!(x)) { printf("FAIL line %d: %s\n", __LINE__, #x); fails++; } } while (0)
static struct cfg P(const char *t) { struct cfg c; cfg_defaults(&c); cfg_parse(&c, t, (int)strlen(t)); return c; }
int main(void)
{
	struct cfg c = P("");
	CHECK(c.xmb_camera == 1 && c.accessory_power == 1 && c.dump_modules == 0 && c.short_log == 0 && c.log_camera_calls == 0 && c.photos_1280x960 == 0);
	c = P("xmb_camera=0\ndump_modules=1\nshort_log=1\naccessory_power=0\n");
	CHECK(!c.xmb_camera && c.dump_modules && c.short_log && !c.accessory_power);
	c = P("  xmb_camera = 0 \r\n\tdump_modules\t=\t1\r\n");           /* spaces, tabs, CRLF */
	CHECK(!c.xmb_camera && c.dump_modules);
	c = P("# xmb_camera=0\n; dump_modules=1\nxmb_camera=0 # off\n");        /* comments */
	CHECK(!c.xmb_camera && !c.dump_modules);
	c = P("xmb_camera=2\nshort_log=yes\ndump_modules=\nfoo=1\nxmbpas=0\nxmb_camerax=0\n=1\n"); /* garbage: defaults stay */
	CHECK(c.xmb_camera == 1 && !c.short_log && !c.dump_modules);
	c = P("short_log=1");                                         /* no trailing newline */
	CHECK(c.short_log);
	c = P("dump_modules=11\nshort_log=1x\n");                             /* trailing junk rejects the line */
	CHECK(!c.dump_modules && !c.short_log);
	c = P("dump_modules=1\ndump_modules=0\n");                                /* last wins */
	CHECK(!c.dump_modules);
	c = P("log_camera_calls=1\n");
	CHECK(c.log_camera_calls && c.xmb_camera == 1 && !c.dump_modules);
	c = P("log_camera_calls=1\nlog_camera_calls=0\ncamcall=1\nlog_camera_callsx=1\n"); /* last wins; near-miss keys are ignored */
	CHECK(!c.log_camera_calls);
	c = P("photos_1280x960=1\nphotos_1280x960x=1\nstill=1\n");
	CHECK(c.photos_1280x960 && !c.log_camera_calls);
	c = P("photos_1280x960=1\nphotos_1280x960=0\nphotos_1280x960s=1\n");
	CHECK(!c.photos_1280x960);
	/* ini written by Windows editors and read in pieces */
	c = P("\xEF\xBB\xBFxmb_camera=0\r\ndump_modules=1\r\n");          /* UTF-8 BOM (Notepad "UTF-8"): the first key still counts */
	CHECK(!c.xmb_camera && c.dump_modules);
	c = P("dump_modules=1\rshort_log=1\r");                              /* old Mac line ends */
	CHECK(c.dump_modules && c.short_log);
	c = P("\xEF\xBB\xBF");                                    /* BOM only */
	CHECK(c.xmb_camera == 1 && !c.dump_modules);
	c = P("xmb_camera=0\xEF\xBB\xBF\ndump_modules=1\n");                /* a BOM in the middle is just junk: that line is ignored */
	CHECK(c.xmb_camera == 1 && c.dump_modules);
	CHECK(cfg_usable_len("dump_modules=1\nqui", 18, 0) == 18);          /* the whole file was read: every line counts */
	CHECK(cfg_usable_len("dump_modules=1\nqui", 18, 1) == 15);           /* the buffer was full: the cut last line is dropped */
	CHECK(cfg_usable_len("dump_modules=1\nshort_log=1\n", 27, 1) == 27);    /* ... unless it ended exactly on a line end */
	CHECK(cfg_usable_len("dump_modules=10 # x", 19, 1) == 0);           /* one long line, cut: nothing usable */
	CHECK(cfg_usable_len("x", 0, 1) == 0);
	{	/* the cut line "dump_modules=1" of "dump_modules=10" must not turn dump_modules on */
		static const char t[] = "short_log=1\ndump_modules=1";          /* the file went on: "dump_modules=10 # x" */
		int n = cfg_usable_len(t, sizeof t - 1, 1);
		c = P("");
		cfg_parse(&c, t, n);
		CHECK(c.short_log && !c.dump_modules);
	}
	{	/* unknown keys change nothing */
		struct cfg d;
		cfg_defaults(&d);
		c = P("probe=1\nprobestill=1\nprobestop=1\n");
		CHECK(memcmp(&c, &d, sizeof c) == 0);
	}
	/* photos_1280x960=1: the app's own SetupStill/SetupStillEx call gets 1280x960 (plain enum 6, Ex enum 8) */
	CHECK(stillbig_word(1) == 2 && stillbig_word(0) == 1);   /* index of the resolution word in the parameter structure */
	CHECK(stillbig_res(1, 6) == 8 && stillbig_res(1, 5) == 8 && stillbig_res(1, 0) == 8);
	CHECK(stillbig_res(0, 7) == 6 && stillbig_res(0, 4) == 6);
	CHECK(stillbig_res(1, 9) == -1 && stillbig_res(1, -1) == -1 && stillbig_res(0, 99) == -1);   /* unknown value: leave it */
	/* addr_ok / addr_ok_w: what a foreign pointer must satisfy before psychea reads it */
	CHECK(!addr_ok(0, 4) && !addr_ok(0x08000000 - 4, 4) && addr_ok(0x08000000, 4) && addr_ok(0x0bfffffc, 4));
	CHECK(!addr_ok(0x0bfffffd, 4) && addr_ok(0x0bfffffd, 3));              /* end of RAM */
	CHECK(addr_ok(0x88000000, 16) && addr_ok(0x48800000, 16) && addr_ok(0xA8000000u, 16));   /* kernel, uncached, mirrors */
	CHECK(!addr_ok(0xC8000000u, 16) && !addr_ok(0x04000000, 16) && !addr_ok(0x00010000, 4)); /* seg 3, VRAM, scratchpad */
	CHECK(!addr_ok(0x08800000, 0xFFFFFFFFu) && !addr_ok(0x08800000, 0xF8000000u));          /* length overflow */
	CHECK(addr_ok_w(0x08800000, 16) && addr_ok_w(0x88800004, 4));
	CHECK(!addr_ok_w(0x08800001, 4) && !addr_ok_w(0x08800002, 4) && !addr_ok_w(0x88800003, 4));   /* odd address: no word access */
	CHECK(!addr_ok_w(0, 4) && !addr_ok_w(0xC8000000u, 4));
	/* time_due: the microsecond counter wraps every 71.6 minutes */
	CHECK(time_due(1000, 1000) && time_due(1001, 1000) && !time_due(999, 1000));
	CHECK(!time_due(0xFFFFFF00u, 0xFFFFFF00u + 300000u));       /* due = 300 ms later, wrapped to a small number */
	CHECK(!time_due(10, 300010u) && time_due(300010u, 300010u));
	CHECK(!time_due(0xFFFFF000u, 0x00000500u));                 /* due just after the wrap, now just before it: not yet */
	CHECK(time_due(0x00000600u, 0x00000500u) && time_due(0x00000500u, 0xFFFFF000u));   /* now just after the wrap, due before it: due */
	{	/* wrap-safe: a due time set before the wrap is not due 100 ms later */
		uint32_t next = 0xFFFFFF00u + 300000u;                  /* set before the wrap */
		uint32_t now = 0xFFFFFF00u + 100000u;                   /* 100 ms later, wrapped: still waiting */
		CHECK(!time_due(now, next));
		now = 0xFFFFFF00u + 300000u;
		CHECK(time_due(now, next));
	}
	/* timestamps: seconds.milliseconds across the wraps of the 32-bit counter */
	{
		struct clk k = {0, 0};
		unsigned int s, m;
		uint64_t i;
		clk_advance(&k, 4000000000u); clk_advance(&k, 4294967000u);
		CHECK(k.epoch == 0);
		clk_advance(&k, 5000);                                  /* wrapped */
		CHECK(k.epoch == 1);
		clk_split(&k, 5000, &s, &m);
		CHECK(s == 4294 && m == 972);                         /* 2^32 + 5000 us = 4294972296 us = 4294.972 s */
		clk_advance(&k, 5000);                                  /* the same reading again is no wrap */
		CHECK(k.epoch == 1);
		k.epoch = 0;
		clk_split(&k, 123456789u, &s, &m);
		CHECK(s == 123 && m == 456);                            /* before the first wrap: plain seconds and milliseconds */
		srand(7);
		for (i = 0; i < 20000; i++) {
			uint32_t lo = ((uint32_t)rand() << 16) ^ (uint32_t)rand();
			uint32_t ep = (uint32_t)rand() % 4000;
			uint64_t us = ((uint64_t)ep << 32) + lo;
			struct clk kk;
			kk.last = 0; kk.epoch = ep;
			clk_split(&kk, lo, &s, &m);
			CHECK(s == (uint32_t)(us / 1000000u) && m == (uint32_t)((us % 1000000u) / 1000u));
		}
	}
	{	/* fmt_d: the digits, and no overflow for INT_MIN */
		char o[12];
		CHECK(fmt_d(o, 0) == 1 && o[0] == '0');
		CHECK(fmt_d(o, -1) == 2 && o[0] == '-' && o[1] == '1');
		CHECK(fmt_d(o, 1234567890) == 10 && memcmp(o, "1234567890", 10) == 0);
		CHECK(fmt_d(o, 2147483647) == 10 && memcmp(o, "2147483647", 10) == 0);
		CHECK(fmt_d(o, -2147483647 - 1) == 11 && memcmp(o, "-2147483648", 11) == 0);
		CHECK(fmt_d(o, 10) == 2 && o[0] == '1' && o[1] == '0');
	}
	/* stillbig: the resolution word must lie inside the structure the caller says it passed */
	CHECK(stillbig_size_ok(0, 24) && stillbig_size_ok(0, 20) && stillbig_size_ok(0, 8));     /* SetupStill: word 1 -> 8 bytes */
	CHECK(!stillbig_size_ok(0, 7) && !stillbig_size_ok(0, 0) && !stillbig_size_ok(0, -4));
	CHECK(stillbig_size_ok(1, 60) && stillbig_size_ok(1, 12));                                /* SetupStillEx: word 2 -> 12 bytes */
	CHECK(!stillbig_size_ok(1, 11) && !stillbig_size_ok(1, 8) && !stillbig_size_ok(1, 0) && !stillbig_size_ok(1, -60));
	CHECK(!stillbig_size_ok(0, 0x1000) && !stillbig_size_ok(1, 0x7FFFFFFF));                  /* a garbage size word */
	if (fails) return 1;
	puts("cfg ok"); return 0;
}
