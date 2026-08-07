# sdk/c/Makefile
CC      ?= gcc
CFLAGS  ?= -std=c11 -Wall -Wextra -O2 -Iinclude
LDFLAGS ?= -lpaho-mqtt3as -lcurl -lssl -lcrypto -lcjson

SRCS    = src/iot_client.c src/provisioning.c src/ota.c
OBJS    = $(SRCS:.c=.o)

LIB     = libiot_platform.a

$(LIB): $(OBJS)
	ar rcs $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

test_compile: tests/test_compile.c $(LIB)
	$(CC) $(CFLAGS) $< -L. -liot_platform $(LDFLAGS) -o $@

test: test_compile
	./test_compile

clean:
	rm -f $(OBJS) $(LIB) test_compile

.PHONY: test clean
