/*
 * taurino-hid-helper: Native helper for Taurino virtual HID gamepad.
 *
 * Creates an IOKit virtual HID gamepad device and accepts HID reports
 * from the Taurino Python bridge via a Unix domain socket.
 *
 * This binary must be codesigned with:
 *   com.apple.developer.hid.virtual.device
 * See packaging/taurino-hid.entitlements
 *
 * Protocol (per client connection):
 *   1. Helper accepts connection
 *   2. Helper creates virtual HID device
 *   3. Helper sends 1-byte status: 0x01 = ready, 0x00 = failed
 *   4. Client streams 14-byte HID reports
 *   5. Helper forwards each report to IOKit
 *   6. On disconnect, helper destroys device and waits for next client
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <errno.h>

#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOReturn.h>

/*
 * IOHIDUserDevice is a private IOKit API — the symbols live in the IOKit
 * framework but Apple does not ship a public header.  We forward-declare
 * the handful of functions we need so the helper compiles against the
 * standard SDK and links against IOKit normally.
 */
typedef struct __IOHIDUserDevice *IOHIDUserDeviceRef;

extern IOHIDUserDeviceRef IOHIDUserDeviceCreate(
    CFAllocatorRef allocator, CFDictionaryRef properties);
extern IOReturn IOHIDUserDeviceHandleReport(
    IOHIDUserDeviceRef device, uint8_t *report, CFIndex reportLength);
extern void IOHIDUserDeviceScheduleWithRunLoop(
    IOHIDUserDeviceRef device, CFRunLoopRef runLoop, CFStringRef mode);
extern void IOHIDUserDeviceUnscheduleFromRunLoop(
    IOHIDUserDeviceRef device, CFRunLoopRef runLoop, CFStringRef mode);

#define SOCKET_PATH "/tmp/taurino-hid.sock"
#define REPORT_SIZE 14
#define BACKLOG     1

static volatile sig_atomic_t g_running = 1;
static int g_listen_fd = -1;
static CFRunLoopRef g_runloop = NULL;
static pthread_mutex_t g_rl_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_rl_cond = PTHREAD_COND_INITIALIZER;

/*
 * HID Report Descriptor — Xbox-style gamepad.
 * Must match GAMEPAD_HID_DESCRIPTOR in taurino/protocol.py exactly.
 *
 * Layout (14 bytes total):
 *   - 16 buttons  (2 bytes)
 *   - 2 triggers  (2 × 16-bit, 0–1023)
 *   - Left stick   (2 × 16-bit signed)
 *   - Right stick  (2 × 16-bit signed)
 */
static const uint8_t kGamepadDescriptor[] = {
    0x05, 0x01,        /* Usage Page (Generic Desktop)        */
    0x09, 0x05,        /* Usage (Game Pad)                    */
    0xA1, 0x01,        /* Collection (Application)            */
    0xA1, 0x00,        /*   Collection (Physical)             */
    /* 16 buttons */
    0x05, 0x09,        /*     Usage Page (Button)             */
    0x19, 0x01,        /*     Usage Minimum (1)               */
    0x29, 0x10,        /*     Usage Maximum (16)              */
    0x15, 0x00,        /*     Logical Minimum (0)             */
    0x25, 0x01,        /*     Logical Maximum (1)             */
    0x75, 0x01,        /*     Report Size (1)                 */
    0x95, 0x10,        /*     Report Count (16)               */
    0x81, 0x02,        /*     Input (Data, Var, Abs)          */
    /* 2 triggers (0–1023) */
    0x05, 0x02,        /*     Usage Page (Simulation Controls)*/
    0x09, 0xC5,        /*     Usage (Brake)  → Left Trigger   */
    0x09, 0xC4,        /*     Usage (Accelerator) → Right Tr. */
    0x15, 0x00,        /*     Logical Minimum (0)             */
    0x26, 0xFF, 0x03,  /*     Logical Maximum (1023)          */
    0x75, 0x10,        /*     Report Size (16)                */
    0x95, 0x02,        /*     Report Count (2)                */
    0x81, 0x02,        /*     Input (Data, Var, Abs)          */
    /* Left stick (X, Y) */
    0x05, 0x01,        /*     Usage Page (Generic Desktop)    */
    0x09, 0x30,        /*     Usage (X)                       */
    0x09, 0x31,        /*     Usage (Y)                       */
    0x16, 0x00, 0x80,  /*     Logical Minimum (-32768)        */
    0x26, 0xFF, 0x7F,  /*     Logical Maximum (32767)         */
    0x75, 0x10,        /*     Report Size (16)                */
    0x95, 0x02,        /*     Report Count (2)                */
    0x81, 0x02,        /*     Input (Data, Var, Abs)          */
    /* Right stick (Rx, Ry) */
    0x09, 0x33,        /*     Usage (Rx)                      */
    0x09, 0x34,        /*     Usage (Ry)                      */
    0x16, 0x00, 0x80,  /*     Logical Minimum (-32768)        */
    0x26, 0xFF, 0x7F,  /*     Logical Maximum (32767)         */
    0x75, 0x10,        /*     Report Size (16)                */
    0x95, 0x02,        /*     Report Count (2)                */
    0x81, 0x02,        /*     Input (Data, Var, Abs)          */
    0xC0,              /*   End Collection                    */
    0xC0,              /* End Collection                      */
};

/* ------------------------------------------------------------------ */
/*  Signal handling                                                    */
/* ------------------------------------------------------------------ */

static void signal_handler(int sig) {
    (void)sig;
    g_running = 0;
    if (g_listen_fd >= 0) {
        shutdown(g_listen_fd, SHUT_RDWR);
    }
}

/* ------------------------------------------------------------------ */
/*  RunLoop thread — keeps IOKit device registration alive             */
/* ------------------------------------------------------------------ */

static void *runloop_thread(void *arg) {
    (void)arg;

    /* Dummy source prevents CFRunLoopRun from returning immediately. */
    CFRunLoopSourceContext ctx;
    memset(&ctx, 0, sizeof(ctx));
    CFRunLoopSourceRef dummy =
        CFRunLoopSourceCreate(kCFAllocatorDefault, 0, &ctx);
    CFRunLoopAddSource(CFRunLoopGetCurrent(), dummy, kCFRunLoopDefaultMode);
    CFRelease(dummy);

    pthread_mutex_lock(&g_rl_mutex);
    g_runloop = CFRunLoopGetCurrent();
    pthread_cond_signal(&g_rl_cond);
    pthread_mutex_unlock(&g_rl_mutex);

    CFRunLoopRun();
    return NULL;
}

/* ------------------------------------------------------------------ */
/*  Virtual HID device creation                                        */
/* ------------------------------------------------------------------ */

static IOHIDUserDeviceRef create_virtual_device(void) {
    CFMutableDictionaryRef props = CFDictionaryCreateMutable(
        kCFAllocatorDefault, 0,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    if (!props) return NULL;

    int vid = 0x0E6F;
    int pid = 0xCAFE;
    CFNumberRef cfVid =
        CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &vid);
    CFNumberRef cfPid =
        CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &pid);
    CFDataRef cfDesc = CFDataCreate(
        kCFAllocatorDefault, kGamepadDescriptor, sizeof(kGamepadDescriptor));

    CFDictionarySetValue(props, CFSTR("VendorID"),          cfVid);
    CFDictionarySetValue(props, CFSTR("ProductID"),         cfPid);
    CFDictionarySetValue(props, CFSTR("Product"),
                         CFSTR("Taurino Virtual Gamepad"));
    CFDictionarySetValue(props, CFSTR("Manufacturer"),
                         CFSTR("Taurino"));
    CFDictionarySetValue(props, CFSTR("Transport"),
                         CFSTR("Virtual"));
    CFDictionarySetValue(props, CFSTR("ReportDescriptor"),  cfDesc);

    IOHIDUserDeviceRef device =
        IOHIDUserDeviceCreate(kCFAllocatorDefault, props);

    CFRelease(cfDesc);
    CFRelease(cfPid);
    CFRelease(cfVid);
    CFRelease(props);
    return device;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/* Read exactly n bytes; returns 0 on success, -1 on error/EOF. */
static int read_exact(int fd, void *buf, size_t n) {
    size_t total = 0;
    while (total < n) {
        ssize_t r = read(fd, (uint8_t *)buf + total, n - total);
        if (r <= 0) return -1;
        total += (size_t)r;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/*  Main loop                                                          */
/* ------------------------------------------------------------------ */

int main(void) {
    /* Install signal handlers. */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = signal_handler;
    sigaction(SIGINT,  &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Start the RunLoop thread (IOKit needs an active RunLoop). */
    pthread_t rl_tid;
    if (pthread_create(&rl_tid, NULL, runloop_thread, NULL) != 0) {
        perror("[taurino-hid] pthread_create");
        return 1;
    }
    pthread_mutex_lock(&g_rl_mutex);
    while (!g_runloop)
        pthread_cond_wait(&g_rl_cond, &g_rl_mutex);
    pthread_mutex_unlock(&g_rl_mutex);

    /* Remove stale socket file. */
    unlink(SOCKET_PATH);

    /* Create the listening Unix-domain socket. */
    g_listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (g_listen_fd < 0) {
        perror("[taurino-hid] socket");
        return 1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strlcpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path));

    if (bind(g_listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("[taurino-hid] bind");
        close(g_listen_fd);
        return 1;
    }
    /* Owner-only access to the socket. */
    chmod(SOCKET_PATH, 0600);

    if (listen(g_listen_fd, BACKLOG) < 0) {
        perror("[taurino-hid] listen");
        close(g_listen_fd);
        unlink(SOCKET_PATH);
        return 1;
    }

    fprintf(stderr, "[taurino-hid] Listening on %s\n", SOCKET_PATH);

    /* Accept loop — one client at a time. */
    while (g_running) {
        int client_fd = accept(g_listen_fd, NULL, NULL);
        if (client_fd < 0) {
            if (!g_running) break;
            if (errno == EINTR) continue;
            perror("[taurino-hid] accept");
            continue;
        }

        fprintf(stderr, "[taurino-hid] Client connected\n");

        /* Create (or fail to create) a virtual HID device. */
        IOHIDUserDeviceRef device = create_virtual_device();
        uint8_t status;

        if (device) {
            IOHIDUserDeviceScheduleWithRunLoop(
                device, g_runloop, kCFRunLoopDefaultMode);
            CFRunLoopWakeUp(g_runloop);
            usleep(100000);   /* 100 ms — let IOKit register the device */
            status = 0x01;    /* ready */
            fprintf(stderr, "[taurino-hid] Virtual gamepad created\n");
        } else {
            status = 0x00;    /* creation failed */
            fprintf(stderr,
                    "[taurino-hid] FAILED to create virtual gamepad "
                    "(missing entitlement?)\n");
        }

        /* Tell the client whether the device is ready. */
        if (write(client_fd, &status, 1) != 1) {
            if (device) CFRelease(device);
            close(client_fd);
            continue;
        }

        if (!device) {
            close(client_fd);
            continue;
        }

        /* Forward 14-byte HID reports from client → IOKit. */
        uint8_t report[REPORT_SIZE];
        while (g_running) {
            if (read_exact(client_fd, report, REPORT_SIZE) < 0)
                break;
            IOReturn ret =
                IOHIDUserDeviceHandleReport(device, report, REPORT_SIZE);
            if (ret != kIOReturnSuccess) {
                fprintf(stderr,
                        "[taurino-hid] HandleReport failed: 0x%x\n", ret);
            }
        }

        fprintf(stderr, "[taurino-hid] Client disconnected\n");

        IOHIDUserDeviceUnscheduleFromRunLoop(
            device, g_runloop, kCFRunLoopDefaultMode);
        CFRunLoopWakeUp(g_runloop);
        CFRelease(device);
        close(client_fd);
    }

    /* Cleanup. */
    close(g_listen_fd);
    unlink(SOCKET_PATH);
    CFRunLoopStop(g_runloop);
    pthread_join(rl_tid, NULL);
    fprintf(stderr, "[taurino-hid] Stopped\n");
    return 0;
}
