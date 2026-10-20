# Suika: English-only app and NumPlay module.
CC = arm-none-eabi-gcc
NWLINK ?= npx --yes -- nwlink@1.0.0
SRC = src/main.c src/physics.c src/save.c src/render.c
HEADERS = $(wildcard src/*.h) ../common/epsilon_app.h ../common/epsilon_files.h
OBJECTS = $(patsubst src/%.c,output/%.o,$(SRC))
CFLAGS = -std=c11 $(shell $(NWLINK) eadk-cflags-device) -Os -Wall -Wextra -Werror
CFLAGS += -fno-math-errno -fsingle-precision-constant -ffunction-sections -fdata-sections -fstack-usage
CFLAGS += -fno-tree-loop-distribute-patterns -flto -fno-fat-lto-objects -fvisibility=internal
LINK = -nostartfiles --specs=nano.specs -Wl,--relocatable,--gc-sections,-e,main -flinker-output=nolto-rel

.PHONY: build check module sim-module test clean
build: output/suika.nwa
output/%.o: src/%.c $(HEADERS) | output
	$(CC) $(CFLAGS) -c $< -o $@
output/icon.o: src/icon.png | output
	$(NWLINK) png-icon-o $< $@
output/suika.nwa: $(OBJECTS) output/icon.o
	$(CC) $(CFLAGS) $(LINK) -Wl,-u,eadk_app_name,-u,eadk_app_icon,-u,eadk_api_level $^ -lm -o $@
	arm-none-eabi-strip --strip-unneeded $@
module: output/module.o
output/module.o: $(OBJECTS)
	$(CC) $(CFLAGS) $(LINK) $^ -lm -o $@
check: build
	$(NWLINK) nwa-bin output/suika.nwa output/suika.bin --ram-length 107674
output:
	mkdir -p $@
test: | output
	$(HOST_CC) -std=c11 -O2 -Wall -Wextra -Werror -Isrc tests/test.c src/physics.c src/save.c src/render.c -lm -o output/test
	cd output && ./test
HOST_CC ?= cc
sim-module: | output
	mkdir -p output/sim
	cc -std=c11 -O2 -fPIC $(shell $(NWLINK) eadk-cflags-simulator) -Dmain=np_suika_main -c src/main.c -o output/sim/main.o
	cc -std=c11 -O2 -fPIC -c src/physics.c -o output/sim/physics.o
	cc -std=c11 -O2 -fPIC -c src/save.c -o output/sim/save.o
	cc -std=c11 -O2 -fPIC -c src/render.c -o output/sim/render.o
	ld -r output/sim/*.o -exported_symbol _np_suika_main -o output/sim-module.o
clean:
	rm -rf output
