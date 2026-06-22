/*
 * l3-netkeys.c [FIX1-INCLUDE]
 *
 * NetKeys / AmigaKVM over UDP/IP via bsdsocket.library.
 *
 * This is the Wi-Fi-friendly Layer-3 transport variant of the latest
 * l2-netkeys receiver. The AKVM packet payload is intentionally unchanged:
 *
 *   byte 0-3:  "AKVM"
 *   byte 4:    version = 1
 *   byte 5:    type
 *   byte 6-7:  seq, big endian
 *   byte 8+:   payload
 *
 * Preserved from the L2 receiver:
 *   - repeated key-down autorepeat handling
 *   - MOUSE2 combined move/button-state protocol
 *   - all-keys-up cleanup on exit
 *   - remote keyboard reset fallback
 *   - current qualifier tracking for keys and mouse buttons
 *
 * Build example:
 *   m68k-amigaos-gcc -noixemul -Os -s -o l3-netkeys l3-netkeys.c -lamiga
 *
 * Note:
 *   Do not include <arpa/inet.h> after <proto/socket.h>/<proto/bsdsocket.h>
 *   with some m68k-amigaos NDKs: bsdsocket inline macros can collide with
 *   inet_* prototypes. This receiver only needs htons(), sockaddr_in and
 *   INADDR_ANY, so <netinet/in.h> is enough.
 *
 * Run on Amiga after Miami/Roadshow is online:
 *   l3-netkeys
 *   l3-netkeys 6800
 *
 * Exit:
 *   Ctrl-C releases all keys/buttons and exits.
 */

#include <exec/types.h>
#include <exec/io.h>
#include <exec/ports.h>
#include <exec/libraries.h>
#include <dos/dos.h>
#include <devices/input.h>
#include <devices/inputevent.h>

#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/socket.h>

#include <sys/types.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <netinet/in.h>
#include <errno.h>

#ifndef EAGAIN
#define EAGAIN EWOULDBLOCK
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct Library *SocketBase = NULL;

#define DEFAULT_PORT 6800
#define RX_BUFSIZE 1600

#define RAW_LSHIFT  0x60
#define RAW_RSHIFT  0x61
#define RAW_CAPS    0x62
#define RAW_CONTROL 0x63
#define RAW_LALT    0x64
#define RAW_RALT    0x65
#define RAW_LAMIGA  0x66
#define RAW_RAMIGA  0x67

#ifndef IEQUALIFIER_LCOMMAND
#define IEQUALIFIER_LCOMMAND 0x0040
#endif

#ifndef IEQUALIFIER_RCOMMAND
#define IEQUALIFIER_RCOMMAND 0x0080
#endif

#ifndef IECODE_NOBUTTON
#define IECODE_NOBUTTON 0x00FF
#endif

#ifndef IECODE_LBUTTON
#define IECODE_LBUTTON 0x0068
#endif

#ifndef IECODE_RBUTTON
#define IECODE_RBUTTON 0x0069
#endif

#ifndef IECODE_MBUTTON
#define IECODE_MBUTTON 0x006A
#endif

#ifndef IEQUALIFIER_LEFTBUTTON
#define IEQUALIFIER_LEFTBUTTON 0x4000
#endif

#ifndef IEQUALIFIER_RBUTTON
#define IEQUALIFIER_RBUTTON 0x2000
#endif

#ifndef IEQUALIFIER_MIDBUTTON
#define IEQUALIFIER_MIDBUTTON 0x1000
#endif

#ifndef IEQUALIFIER_RELATIVEMOUSE
#define IEQUALIFIER_RELATIVEMOUSE 0x8000
#endif

#define PKT_MAGIC0 'A'
#define PKT_MAGIC1 'K'
#define PKT_MAGIC2 'V'
#define PKT_MAGIC3 'M'
#define PKT_VERSION 1

#define PKT_KEY       1
#define PKT_MOUSE     2
#define PKT_BUTTON    3
#define PKT_ALLUP     4
#define PKT_HEARTBEAT 5
#define PKT_RESET     6
#define PKT_MOUSE2    7

static struct MsgPort *inputPort = NULL;
static struct IOStdReq *inputIO = NULL;
static int inputOpen = 0;

static unsigned char key_down[128];
static UBYTE mouse_buttons = 0;
static UWORD last_seq = 0;
static int have_seq = 0;
static ULONG packets_seen = 0;
static ULONG packets_handled = 0;

static int udp_socket = -1;
static UBYTE rxbuf[RX_BUFSIZE];

/* ---------------- input.device side ---------------- */

static int open_input_device(void)
{
    inputPort = CreateMsgPort();
    if (!inputPort) {
        Printf("CreateMsgPort input failed\n");
        return 0;
    }

    inputIO = (struct IOStdReq *)CreateIORequest(inputPort, sizeof(struct IOStdReq));
    if (!inputIO) {
        Printf("CreateIORequest input failed\n");
        DeleteMsgPort(inputPort);
        inputPort = NULL;
        return 0;
    }

    if (OpenDevice("input.device", 0, (struct IORequest *)inputIO, 0) != 0) {
        Printf("OpenDevice input.device failed\n");
        DeleteIORequest((struct IORequest *)inputIO);
        DeleteMsgPort(inputPort);
        inputIO = NULL;
        inputPort = NULL;
        return 0;
    }

    inputOpen = 1;
    return 1;
}

static void close_input_device(void)
{
    if (inputOpen) {
        CloseDevice((struct IORequest *)inputIO);
        inputOpen = 0;
    }

    if (inputIO) {
        DeleteIORequest((struct IORequest *)inputIO);
        inputIO = NULL;
    }

    if (inputPort) {
        DeleteMsgPort(inputPort);
        inputPort = NULL;
    }
}

static UWORD current_qualifier(void)
{
    UWORD q = 0;

    if (key_down[RAW_LSHIFT])  q |= IEQUALIFIER_LSHIFT;
    if (key_down[RAW_RSHIFT])  q |= IEQUALIFIER_RSHIFT;
    if (key_down[RAW_CONTROL]) q |= IEQUALIFIER_CONTROL;
    if (key_down[RAW_LALT])    q |= IEQUALIFIER_LALT;
    if (key_down[RAW_RALT])    q |= IEQUALIFIER_RALT;
    if (key_down[RAW_LAMIGA])  q |= IEQUALIFIER_LCOMMAND;
    if (key_down[RAW_RAMIGA])  q |= IEQUALIFIER_RCOMMAND;

    return q;
}

static void send_rawkey_q(UWORD rawcode, int keyup, UWORD qualifier)
{
    struct InputEvent ie;

    memset(&ie, 0, sizeof(ie));

    ie.ie_NextEvent = NULL;
    ie.ie_Class = IECLASS_RAWKEY;
    ie.ie_SubClass = 0;
    ie.ie_Code = keyup ? (rawcode | IECODE_UP_PREFIX) : (rawcode & ~IECODE_UP_PREFIX);
    ie.ie_Qualifier = qualifier;

    inputIO->io_Command = IND_WRITEEVENT;
    inputIO->io_Data = (APTR)&ie;
    inputIO->io_Length = sizeof(ie);

    DoIO((struct IORequest *)inputIO);
}

static UWORD current_mouse_qualifier(UBYTE buttons)
{
    UWORD q = current_qualifier() | IEQUALIFIER_RELATIVEMOUSE;

    if (buttons & 1) q |= IEQUALIFIER_LEFTBUTTON;
    if (buttons & 2) q |= IEQUALIFIER_RBUTTON;
    if (buttons & 4) q |= IEQUALIFIER_MIDBUTTON;

    return q;
}

static void send_mouse_event_q(UWORD code, WORD dx, WORD dy, UBYTE buttons)
{
    struct InputEvent ie;

    memset(&ie, 0, sizeof(ie));

    ie.ie_NextEvent = NULL;
    ie.ie_Class = IECLASS_RAWMOUSE;
    ie.ie_SubClass = 0;
    ie.ie_Code = code;
    ie.ie_Qualifier = current_mouse_qualifier(buttons);
    ie.ie_X = dx;
    ie.ie_Y = dy;

    inputIO->io_Command = IND_WRITEEVENT;
    inputIO->io_Data = (APTR)&ie;
    inputIO->io_Length = sizeof(ie);

    DoIO((struct IORequest *)inputIO);
}

static void send_mouse_move(WORD dx, WORD dy)
{
    if (dx == 0 && dy == 0)
        return;

    send_mouse_event_q(IECODE_NOBUTTON, dx, dy, mouse_buttons);
}

static void handle_mouse2(WORD dx, WORD dy, UBYTE buttons)
{
    UBYTE old_buttons = mouse_buttons;
    UBYTE new_buttons = buttons & 0x07;
    UBYTE changed = old_buttons ^ new_buttons;

    if (changed & 1) {
        if (new_buttons & 1)
            send_mouse_event_q(IECODE_LBUTTON, 0, 0, new_buttons);
        else
            send_mouse_event_q(IECODE_LBUTTON | IECODE_UP_PREFIX, 0, 0, new_buttons);
    }

    if (changed & 2) {
        if (new_buttons & 2)
            send_mouse_event_q(IECODE_RBUTTON, 0, 0, new_buttons);
        else
            send_mouse_event_q(IECODE_RBUTTON | IECODE_UP_PREFIX, 0, 0, new_buttons);
    }

    if (changed & 4) {
        if (new_buttons & 4)
            send_mouse_event_q(IECODE_MBUTTON, 0, 0, new_buttons);
        else
            send_mouse_event_q(IECODE_MBUTTON | IECODE_UP_PREFIX, 0, 0, new_buttons);
    }

    mouse_buttons = new_buttons;

    if (dx || dy)
        send_mouse_event_q(IECODE_NOBUTTON, dx, dy, mouse_buttons);
}

static void send_mouse_button(int button, int down)
{
    UBYTE buttons = mouse_buttons;

    switch (button) {
        case 1:
            if (down) buttons |= 1; else buttons &= ~1;
            break;
        case 2:
            if (down) buttons |= 2; else buttons &= ~2;
            break;
        case 3:
            if (down) buttons |= 4; else buttons &= ~4;
            break;
        default:
            return;
    }

    handle_mouse2(0, 0, buttons);
}

static void handle_key(UWORD rawcode, int down)
{
    UWORD q;

    if (rawcode >= 128)
        return;

    if (down) {
        /*
         * Real key-down: remember the key.
         * Autorepeat key-down: key is already remembered, but still inject
         * another RAWKEY down event. Older versions returned here and therefore
         * forced the Python side to fake repeat with a very short up/down pulse.
         */
        if (!key_down[rawcode])
            key_down[rawcode] = 1;

        q = current_qualifier();
        send_rawkey_q(rawcode, 0, q);
    } else {
        if (!key_down[rawcode])
            return;
        key_down[rawcode] = 0;
        q = current_qualifier();
        send_rawkey_q(rawcode, 1, q);
    }
}

static void all_keys_up(void)
{
    int raw;

    for (raw = 0; raw < 128; raw++) {
        if (key_down[raw]) {
            key_down[raw] = 0;
            send_rawkey_q((UWORD)raw, 1, 0);
        }
    }

    if (mouse_buttons) {
        handle_mouse2(0, 0, 0);
    }
}

static void send_keyboard_reset_chord(void)
{
    UWORD q_ctrl;
    UWORD q_ctrl_la;
    UWORD q_ctrl_la_ra;

    q_ctrl = IEQUALIFIER_CONTROL;
    q_ctrl_la = IEQUALIFIER_CONTROL | IEQUALIFIER_LCOMMAND;
    q_ctrl_la_ra = IEQUALIFIER_CONTROL | IEQUALIFIER_LCOMMAND | IEQUALIFIER_RCOMMAND;

    send_rawkey_q(RAW_CONTROL, 0, q_ctrl);
    send_rawkey_q(RAW_LAMIGA, 0, q_ctrl_la);
    send_rawkey_q(RAW_RAMIGA, 0, q_ctrl_la_ra);

    Delay(5);

    send_rawkey_q(RAW_RAMIGA, 1, q_ctrl_la);
    send_rawkey_q(RAW_LAMIGA, 1, q_ctrl);
    send_rawkey_q(RAW_CONTROL, 1, 0);
}

static void trigger_remote_reset(void)
{
    Printf("Remote reset requested.\n");
    all_keys_up();
    send_keyboard_reset_chord();
    Delay(10);
    Printf("Falling back to ColdReboot().\n");
    ColdReboot();
}

/* ---------------- packet decode ---------------- */

static WORD read_s16(unsigned char *p)
{
    UWORD u = ((UWORD)p[0] << 8) | (UWORD)p[1];
    return (WORD)u;
}

static UWORD read_u16(unsigned char *p)
{
    return ((UWORD)p[0] << 8) | (UWORD)p[1];
}

static void handle_packet(unsigned char *buf, int len)
{
    UBYTE type;
    UWORD seq;

    if (len < 8)
        return;

    if (buf[0] != PKT_MAGIC0 || buf[1] != PKT_MAGIC1 ||
        buf[2] != PKT_MAGIC2 || buf[3] != PKT_MAGIC3)
        return;

    if (buf[4] != PKT_VERSION)
        return;

    type = buf[5];
    seq = read_u16(buf + 6);

    /*
     * Same duplicate policy as UDP version:
     * drop exact duplicates, accept wraparound/out-of-order.
     */
    if (have_seq && seq == last_seq)
        return;

    have_seq = 1;
    last_seq = seq;

    packets_handled++;

    switch (type) {
        case PKT_KEY:
            if (len >= 10)
                handle_key((UWORD)buf[8], buf[9] ? 1 : 0);
            break;

        case PKT_MOUSE:
            if (len >= 12)
                send_mouse_move(read_s16(buf + 8), read_s16(buf + 10));
            break;

        case PKT_BUTTON:
            if (len >= 10)
                send_mouse_button((int)buf[8], buf[9] ? 1 : 0);
            break;

        case PKT_MOUSE2:
            if (len >= 13)
                handle_mouse2(read_s16(buf + 8), read_s16(buf + 10), buf[12]);
            break;

        case PKT_ALLUP:
            all_keys_up();
            break;

        case PKT_HEARTBEAT:
            break;

        case PKT_RESET:
            trigger_remote_reset();
            break;

        default:
            break;
    }
}

static int got_ctrl_c(void)
{
    return (SetSignal(0L, SIGBREAKF_CTRL_C) & SIGBREAKF_CTRL_C) != 0;
}


/* ---------------- UDP/IP side ---------------- */

static int open_udp_socket(UWORD port)
{
    struct sockaddr_in addr;
    ULONG nonblock = 1;

    SocketBase = OpenLibrary("bsdsocket.library", 4);
    if (!SocketBase) {
        Printf("OpenLibrary bsdsocket.library failed. Start Miami/Roadshow first.\n");
        return 0;
    }

    udp_socket = socket(AF_INET, SOCK_DGRAM, 0);
    if (udp_socket < 0) {
        Printf("socket(AF_INET, SOCK_DGRAM) failed, errno=%ld\n", (LONG)Errno());
        return 0;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);

    if (bind(udp_socket, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        Printf("bind UDP port %ld failed, errno=%ld\n", (LONG)port, (LONG)Errno());
        CloseSocket(udp_socket);
        udp_socket = -1;
        return 0;
    }

    if (IoctlSocket(udp_socket, FIONBIO, (char *)&nonblock) < 0) {
        Printf("IoctlSocket(FIONBIO) failed, errno=%ld\n", (LONG)Errno());
        CloseSocket(udp_socket);
        udp_socket = -1;
        return 0;
    }

    Printf("NetKeys-L3 KVM listening on UDP port %ld.\n", (LONG)port);
    Printf("TCP/IP stack must stay online. Ctrl-C exits.\n");
    return 1;
}

static void close_udp_socket(void)
{
    if (udp_socket >= 0) {
        CloseSocket(udp_socket);
        udp_socket = -1;
    }

    if (SocketBase) {
        CloseLibrary(SocketBase);
        SocketBase = NULL;
    }
}

static int read_one_udp_packet(void)
{
    LONG got;
    LONG e;

    got = recvfrom(udp_socket, (char *)rxbuf, sizeof(rxbuf), 0, NULL, NULL);
    if (got < 0) {
        e = Errno();
        if (e == EWOULDBLOCK || e == EAGAIN) {
            Delay(1);
            return 1;
        }
        Printf("recvfrom failed, errno=%ld\n", (LONG)e);
        Delay(5);
        return 0;
    }

    if (got == 0) {
        Delay(1);
        return 1;
    }

    packets_seen++;
    handle_packet(rxbuf, (int)got);
    return 1;
}

static UWORD parse_port(int argc, char **argv)
{
    LONG p;

    if (argc < 2)
        return DEFAULT_PORT;

    p = atol(argv[1]);
    if (p <= 0 || p > 65535) {
        Printf("Invalid port, using %ld.\n", (LONG)DEFAULT_PORT);
        return DEFAULT_PORT;
    }

    return (UWORD)p;
}

int main(int argc, char **argv)
{
    UWORD port;

    memset(key_down, 0, sizeof(key_down));
    mouse_buttons = 0;

    port = parse_port(argc, argv);

    if (!open_input_device()) {
        return 20;
    }

    if (!open_udp_socket(port)) {
        all_keys_up();
        close_input_device();
        close_udp_socket();
        return 20;
    }

    while (!got_ctrl_c()) {
        read_one_udp_packet();
    }

    Printf("\nCtrl-C received, exiting.\n");
    Printf("UDP packets seen: %ld\n", (LONG)packets_seen);
    Printf("AKVM packets handled: %ld\n", (LONG)packets_handled);

    all_keys_up();
    close_udp_socket();
    close_input_device();

    return 0;
}
