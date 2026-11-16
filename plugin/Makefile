TARGET = psychea
OBJS = main.o

BUILD_PRX = 1
USE_KERNEL_LIBS = 1
PSP_FW_VERSION = 661

CFLAGS = -O2 -G0 -Wall -fno-pic -fno-builtin
CXXFLAGS = $(CFLAGS) -fno-exceptions -fno-rtti
ASFLAGS = $(CFLAGS)

LIBS = -lpspsystemctrl_kernel

PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build_prx.mak
