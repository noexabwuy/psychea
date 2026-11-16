/* settings from ms0:/seplugins/psychea.ini ("key=value" per line). Pure code, tested on the PC in test_cfg.c. */
#ifndef PSYCHEA_CFG_H
#define PSYCHEA_CFG_H

struct cfg {
	int xmb_camera;        /* GetAuthStat of the XMB camera app returns 0 */
	int accessory_power;   /* the accessory power flag accessor returns 1 */
	int dump_modules;      /* dump sceUSB_Driver / camera_plugin to ms0:/dump/ */
	int short_log;         /* log only registration, attach/detach, patches, STAT and failures */
	int log_camera_calls;  /* log every sceUsbCam call with its parameter structure */
	int photos_1280x960;   /* the app's SetupStill/SetupStillEx resolution becomes 1280x960 */
};

static void cfg_defaults(struct cfg *c)
{
	c->xmb_camera = 1; c->accessory_power = 1; c->dump_modules = 0; c->short_log = 0; c->log_camera_calls = 0;
	c->photos_1280x960 = 0;
}

static int cfg_key(const char *k, int kn, const char *name)
{
	int i = 0;
	while (i < kn && name[i] && k[i] == name[i]) i++;
	return i == kn && !name[i];
}

/* One line "key=0|1": spaces and tabs ignored, '#' or ';' starts a comment; unknown keys and other values are ignored. */
static void cfg_line(struct cfg *c, const char *s, int n)
{
	int ks, ke, v;
	while (n > 0 && (*s == ' ' || *s == '\t')) { s++; n--; }
	if (n <= 0 || *s == '#' || *s == ';') return;
	for (ke = 0; ke < n && s[ke] != '='; ke++) ;
	if (ke >= n) return;
	ks = ke;
	while (ks > 0 && (s[ks - 1] == ' ' || s[ks - 1] == '\t')) ks--;
	v = ke + 1;
	while (v < n && (s[v] == ' ' || s[v] == '\t')) v++;
	if (v >= n || (s[v] != '0' && s[v] != '1')) return;
	{
		int val = s[v] - '0', e = v + 1;
		while (e < n && (s[e] == ' ' || s[e] == '\t' || s[e] == '\r')) e++;
		if (e < n && s[e] != '#' && s[e] != ';') return;
		if (cfg_key(s, ks, "xmb_camera")) c->xmb_camera = val;
		else if (cfg_key(s, ks, "accessory_power")) c->accessory_power = val;
		else if (cfg_key(s, ks, "dump_modules")) c->dump_modules = val;
		else if (cfg_key(s, ks, "short_log")) c->short_log = val;
		else if (cfg_key(s, ks, "log_camera_calls")) c->log_camera_calls = val;
		else if (cfg_key(s, ks, "photos_1280x960")) c->photos_1280x960 = val;
	}
}

/* truncated = the buffer was full and the file may go on, so the last line may be cut ("dump_modules=1" of "dump_modules=10"):
 * only lines ending with '\n' count. Returns the length to parse. */
static int cfg_usable_len(const char *buf, int n, int truncated)
{
	if (!truncated) return n;
	while (n > 0 && buf[n - 1] != '\n') n--;
	return n;
}

/* lines end with \n, \r\n or \r; a UTF-8 BOM at the start is skipped */
static void cfg_parse(struct cfg *c, const char *buf, int len)
{
	int i = 0, b = 0;
	if (len >= 3 && (unsigned char)buf[0] == 0xEF && (unsigned char)buf[1] == 0xBB && (unsigned char)buf[2] == 0xBF) b = 3;
	for (i = b; i <= len; i++) {
		if (i == len || buf[i] == '\n' || buf[i] == '\r') { cfg_line(c, buf + b, i - b); b = i + 1; }
	}
}
#endif
