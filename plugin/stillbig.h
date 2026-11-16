/* photos_1280x960=1: the resolution in the app's own sceUsbCamSetupStill (enum 5 = 1024x768, 6 = 1280x960) or
 * SetupStillEx (Ex enum 7 = 1024x768, 8 = 1280x960) call becomes 1280x960 before it runs. Pure code, tested on the PC
 * in test_cfg.c. stillbig_res returns the new value, or -1 to leave the call alone (not a valid enum). */
#ifndef PSYCHEA_STILLBIG_H
#define PSYCHEA_STILLBIG_H

#define STILL_RES_WORD        1    /* index of the resolution word in the parameter structure of SetupStill */
#define STILL_EX_RES_WORD     2    /* the same for SetupStillEx */
#define STILL_RES_1280X960    6    /* the plain enum's 1280x960 */
#define STILL_EX_RES_1280X960 8    /* the Ex enum's 1280x960 */
#define STILL_ENUM_MAX        8    /* the highest value of either enum; anything above is not a resolution */
#define STILL_STRUCT_MAX      256  /* a size word above this is garbage */
#define WORD_BYTES            4

static int stillbig_word(int ex)
{
	return ex ? STILL_EX_RES_WORD : STILL_RES_WORD;
}

static int stillbig_res(int ex, int current)
{
	if (current < 0 || current > STILL_ENUM_MAX) return -1;
	return ex ? STILL_EX_RES_1280X960 : STILL_RES_1280X960;
}

/* size: the first word of the parameter structure. The resolution word must lie inside it, or the write would hit
 * neighbouring memory; above 256 is garbage (the real structures are 24 and 60 bytes). */
static int stillbig_size_ok(int ex, int size)
{
	return size >= WORD_BYTES * (stillbig_word(ex) + 1) && size <= STILL_STRUCT_MAX;
}
#endif
