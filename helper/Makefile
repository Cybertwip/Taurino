# helper/Makefile — Build the taurino-hid-helper native binary.
#
# Usage:
#   make                       Build the helper
#   make sign                  Ad-hoc sign with HID entitlement
#   make sign IDENTITY="Developer ID Application: ..."
#   TAURINO_CODESIGN_IDENTITY="Developer ID ..." make sign
#   make clean
#
CC        = clang
CFLAGS    = -Wall -Wextra -O2
FRAMEWORKS = -framework CoreFoundation -framework IOKit

ENTITLEMENTS = ../packaging/taurino-hid.entitlements

# Inherit TAURINO_CODESIGN_IDENTITY from the environment, fall back to ad-hoc.
IDENTITY    ?= $(or $(TAURINO_CODESIGN_IDENTITY),-)

TARGET = taurino-hid-helper
SOURCE = taurino_hid_helper.c

.PHONY: all sign clean

all: $(TARGET)

$(TARGET): $(SOURCE)
	$(CC) $(CFLAGS) -o $@ $< $(FRAMEWORKS)

sign: $(TARGET)
	codesign --force --sign "$(IDENTITY)" \
		--entitlements $(ENTITLEMENTS) \
		$(TARGET)

clean:
	rm -f $(TARGET)
