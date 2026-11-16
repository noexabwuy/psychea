/* constants, kernel imports and the parsed settings */
#ifndef PSYCHEA_DEFS_H
#define PSYCHEA_DEFS_H

#define MIPS_JR_RA         0x03E00008u
#define MIPS_LI_V0_0       0x24020000u
#define MIPS_LI_V0_1       0x24020001u
#define MIPS_J             0x08000000u   /* j: opcode 2 in the top 6 bits */
#define MIPS_OP_J          2
#define MIPS_J_TARGET      0x03ffffffu   /* the 26-bit word index of a j */
#define MIPS_FUNCT_MASK    0x3F
#define MIPS_FUNCT_SYSCALL 0x0C          /* the delay slot of a syscall stub: "syscall n" */
#define NID_GETAUTHSTAT    0x79A1C743u   /* sceUsbAccGetAuthStat */
#define NID_ACC_POWER      0x48CCE3C1u   /* the accessory power flag getter ("G4C") */
#define BUSSTATE_OFFSET    0x98E8        /* three bus state words in the data of sceUSB_Driver */
#define STUB_BYTES         8             /* one import stub: J target + delay slot */
#define NID_BYTES          4
#define STUB_HDR_BYTES     20            /* the part of an import header that is read */
#define MAX_IMPORTS        256           /* a library with more imports is not believed */
#define MAX_STUB_LIBS      96            /* import libraries walked per module */
#define MAX_ENDPOINTS      8
#define MAX_SHOWN_BYTES    128           /* bytes of a request or of a returned structure shown in the log */
#define XMB_PATCH_WINDOW_US (20 * 1000 * 1000)   /* how long the XMB stub patch is retried after camera_plugin starts */
#define POLL_FAST_US       (10 * 1000)   /* writer pass while the XMB patch is pending */
#define POLL_SLOW_US       (300 * 1000)  /* writer pass otherwise, and the spacing of the slow jobs */
#define STAT_EVERY         3             /* slow passes per STAT line (about 1 s) */
#define WRITER_PRIORITY    0x30
#define WRITER_STACK       0x4000

#define CFG_PATH   "ms0:/seplugins/psychea.ini"
#define VERSION    "v0.1"
#define LOG_PATH   "ms0:/psychea.log"
#define LOG_OLD    "ms0:/psychea.old"
#define LOG_MAX    (4 * 1024 * 1024)      /* per boot */
#define ROTATE_AT  (8 * 1024 * 1024)      /* the log of older boots moves to .old at this size */
#define WARMUP_RECV 12                    /* full log lines per endpoint, then STAT only */
#define WARMUP_SEND 200
#define LB_SZ      (96 * 1024)
#define MAX_MODS   192
#define DUMP_DIR   "ms0:/dump"
#define DUMP_SEG_MAX   0x100000          /* one segment */
#define DUMP_TOTAL_MAX (3 * 1024 * 1024) /* per boot */
#define NDUMPQ     8

extern int sceKernelGetModuleIdListForKernel(SceUID *ids, u32 size, u32 *count, u32 user_only);
extern SceModule *sceKernelFindModuleByUID(SceUID id);

static struct cfg cfg;

#endif
