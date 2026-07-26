"""
SD card formatter for the RAB Pi Pico HAT. DESTRUCTIVE -- it erases the card.

Two ways in:

  * The configurator's "Format SD card" button, which runs
    `import sd_format; sd_format.format_sd()` over the raw REPL. This module has to
    be uploaded for that -- it is deliberately NOT in micropico.pyIgnore. Nothing
    imports it at boot, so it costs only flash. format_sd() takes a `log` callable,
    which the page feeds into its console so a long job is not a silent wait, and
    returns True only if the card formatted AND passed its read-back self-test.

  * The bench: set I_UNDERSTAND_THIS_ERASES_THE_CARD below, then MicroPico -> "Run
    current file". That guard gates only the automatic run at the bottom of the file.

mkfs on a 7.5 GiB card writes several MiB of FAT tables -- measured at 20 s at
1.32 MHz, with the capacity probe on top, which is why configurator.html passes
replExec() a 5-minute timeout for this one call instead of its usual 6 s.

Why the capacity probe: a counterfeit card reports a size its flash cannot back, and
the usual behaviour is for high addresses to wrap onto low ones. Formatting one
succeeds and mounts cleanly -- it corrupts later, in flight, once the logs grow past
the real capacity. The probe writes a distinct pattern to points across the whole
card, then reads them ALL back afterwards. Writing and checking one spot at a time
would not catch a wrap, because you would read back the block you just wrote.

It is a fast screen, not a full verify. It catches wraps at power-of-two boundaries
-- which is what the fakes do, since the controller masks address bits -- and regions
that are dead or discard writes. A card aliasing at some arbitrary non-power-of-two
boundary would slip through, and no cheap probe can catch that: detection needs two
probe addresses congruent modulo a capacity you do not know. Only writing the whole
card proves the whole card, so h2testw or f3 on a computer is still the last word.
"""

import time
import vfs
from machine import SPI, Pin

import sdcard

# ---------------------------------------------------------------- safety catch
# Flip this to True to let the bench run actually format. Everything on the card
# is lost. format_sd() itself does not check it -- the configurator will have its
# own confirmation -- this only gates the automatic run at the bottom of the file.
I_UNDERSTAND_THIS_ERASES_THE_CARD = True

# Same pins as sd_write_task() in main.py.
SCK, MOSI, MISO, CS = 18, 19, 16, 17

# Conservative: mkfs is a long stream of writes, and a format that corrupts halfway
# is worse than a slow one. Raise it once sd_check.py has proven the card's ceiling.
BAUDRATE = 1_320_000

PROBE_SPOTS = 16
IO_RETRIES = 3
TEST_FILE = "/sd/wenet_format_test.txt"

try:
    from crc16 import crc16_viper as CRC
except ImportError:
    CRC = None


def open_card(baudrate=BAUDRATE):
    spi = SPI(0, sck=Pin(SCK), mosi=Pin(MOSI), miso=Pin(MISO), baudrate=baudrate)
    # With crc16_function set, sdcard.py verifies reads and sends real write CRCs,
    # so corruption raises EIO instead of being accepted silently.
    return sdcard.SDCard(spi=spi, cs=Pin(CS), baudrate=baudrate, crc16_function=CRC)


def _vfs_fat():
    fat = getattr(vfs, "VfsFat", None)
    if fat is None:                      # MicroPython older than 1.23
        import os
        fat = os.VfsFat
    return fat


def _fill(blk, buf):
    """Fill buf with a repeating tag naming the block it belongs to."""
    tag = ("WENET %d " % blk).encode()
    n = len(tag)
    for i in range(512):
        buf[i] = tag[i % n]


def _tag_of(buf):
    """Recover the block number a probe pattern was written for, or None."""
    try:
        s = bytes(buf[:64]).decode()
        i = s.index("WENET ") + 6
        return int(s[i:s.index(" ", i)])
    except Exception:
        return None


def _probe_spots(sectors):
    """Which blocks to probe.

    The powers of two are the ones that matter. A card that lies about its size
    almost always wraps at a power of two, and 2**n % 2**m == 0 for n >= m, so every
    probe above the real capacity lands back on block 0 -- block 0 then holds a tag
    naming a much higher block, which is proof. An evenly spaced probe would miss
    this entirely: a wrapped write lands on some low block that is not itself a probe
    point, so every spot still reads back its own data. The even spread is kept on
    top, because it catches the other failure mode -- a card that goes dead or reads
    back blank past a boundary that is not a power of two.
    """
    top = sectors - 3                    # the last sectors get their own check below
    spots = {0, top}
    b = 1
    while b <= top:
        spots.add(b)
        b *= 2
    step = top // (PROBE_SPOTS - 1)
    for i in range(PROBE_SPOTS):
        spots.add(min(i * step, top))
    return sorted(spots)                 # ascending, so a wrap overwrites the low block


def _check_boundary(sd, log):
    """Read the last two sectors. Returns "ok", "edge" or "bad".

    sdcard.py issues CMD18 (READ_MULTIPLE_BLOCK) even for a single block, then CMD12
    to stop it. Starting that at the final sector makes the card prefetch past the end
    of its own media, and many cards then flag out-of-range -- which surfaces in the
    CMD12 response and comes back as a bare EIO. That is a driver/protocol edge, not a
    fault: FatFs will not put data in the last sector anyway. But if the second-to-last
    sector fails too, the top of the card really is unreadable, and that does matter.
    """
    buf = bytearray(512)
    results = {}
    for blk in (sd.sectors - 2, sd.sectors - 1):
        err = _io_retry(sd.readblocks, blk, buf, log, "read")
        results[blk] = err
        state = "refused (%s)" % err if err else "ok"
        log(f"  last sectors: block {blk} {state}")

    if not any(results.values()):
        return "ok"
    if results[sd.sectors - 2] is None:
        log("   -> only the very last sector. That is the CMD18 prefetch edge, and it")
        log("      is harmless -- no filesystem will store anything there.")
        return "edge"
    log("   -> both of the last sectors are unreadable. The top of the card is bad.")
    return "bad"


def _io_retry(op, blk, buf, log, what):
    """Run one block op, retrying a failure. Returns None on success, else the error.

    sdcard.py allows about 25 ms for a read data token: _CMD_TIMEOUT is 50, half of
    it spinning and half at 1 ms a turn. The SD spec permits a read access time up to
    100 ms, and a cheap card doing internal housekeeping after a burst of scattered
    writes will blow past 25 ms. So a single ETIMEDOUT means "slower than this driver
    waits", not "dead" -- retrying is what tells the two apart.
    """
    err = None
    for attempt in range(IO_RETRIES):
        try:
            op(blk, buf)
            if attempt:
                log(f"  block {blk} {what} OK on try {attempt + 1} -- slow, not dead")
            return None
        except OSError as e:
            err = e
            time.sleep_ms(100)
    return err


def probe_capacity(sd, log=print):
    """Write patterns across the whole card, then read them all back.

    Returns "ok", "io" if the card stopped responding, or "alias" if it returned
    the wrong data -- three different faults that need three different answers.
    """
    spots = _probe_spots(sd.sectors)
    buf = bytearray(512)

    log(f"  writing {len(spots)} probe blocks across {sd.sectors} sectors...")
    for blk in spots:
        _fill(blk, buf)
        err = _io_retry(sd.writeblocks, blk, buf, log, "write")
        if err:
            pct = 100 * blk // sd.sectors
            log(f"  WRITE FAILED at block {blk} ({pct}% in) -- {err}")
            return "io"

    # Let the card finish programming before reading any of it back.
    time.sleep_ms(250)

    log("  reading them back...")
    ok = True
    for blk in spots:
        err = _io_retry(sd.readblocks, blk, buf, log, "read")
        if err:
            pct = 100 * blk // sd.sectors
            log(f"  READ FAILED at block {blk} ({pct}% in) -- {err}")
            return "io"
        got = _tag_of(buf)
        if got == blk:
            continue
        ok = False
        pct = 100 * blk // sd.sectors
        if got is None:
            log(f"  MISMATCH at block {blk} ({pct}% in): the pattern is gone -- that")
            log("           part of the card discarded the write.")
        else:
            claimed = sd.sectors // 2048
            log(f"  MISMATCH at block {blk} ({pct}% in): holds the pattern written to")
            log(f"           block {got}. The card wraps high addresses onto low ones,")
            log(f"           so it really holds at most {got // 2048} MiB, not {claimed} MiB.")

    if not ok:
        return "alias"

    if _check_boundary(sd, log) == "bad":
        return "io"

    log("  all probe blocks returned their own data.")
    log("  (screens for power-of-two wraps and dead regions, not a full verify)")
    return "ok"


def describe_layout(sd, log=print):
    """Report what mkfs actually produced -- superfloppy VBR, or MBR + partition."""
    buf = bytearray(512)
    sd.readblocks(0, buf)
    if buf[0] in (0xEB, 0xE9):
        vbr, where = buf, "VBR at sector 0 (no partition table)"
    elif bytes(buf[510:512]) == b"\x55\xaa":
        start = int.from_bytes(bytes(buf[454:458]), "little")
        ptype = buf[450]
        where = f"MBR, partition 1 type 0x{ptype:02x} at LBA {start}"
        vbr = bytearray(512)
        sd.readblocks(start, vbr)
    else:
        log("  sector 0 is neither a VBR nor an MBR -- mkfs did not take.")
        return
    fat = bytes(vbr[82:90]).strip() or bytes(vbr[54:62]).strip()
    bps = vbr[11] | (vbr[12] << 8)
    log(f"  layout: {where}")
    log(f"  filesystem: {fat.decode()}, {bps} bytes/sector")


def selftest(log=print):
    """Mount the way main.py does, write a file, read it back, remove it."""
    import os
    stamp = str(time.time())
    with open(TEST_FILE, "w") as f:
        f.write("wenet sd_format selftest " + stamp + "\n")
    with open(TEST_FILE) as f:
        back = f.read()
    if stamp not in back:
        log(f"  SELF-TEST FAILED: read back {back!r}")
        return False
    os.remove(TEST_FILE)

    st = os.statvfs("/sd")
    total = st[2] * st[1] // 1048576
    free = st[3] * st[1] // 1048576
    log(f"  wrote, read back and removed {TEST_FILE}")
    log(f"  volume: {total} MiB total, {free} MiB free, {st[1]} byte clusters")
    return True


def format_sd(baudrate=BAUDRATE, probe=True, log=print):
    """Format the card as FAT and verify it. Returns True only if everything passed.

    ERASES THE CARD. Callers are responsible for confirming with the user first.
    """
    try:
        vfs.umount("/sd")
        log("unmounted a stale /sd")
    except Exception:
        pass

    log("opening card...")
    try:
        sd = open_card(baudrate)
    except OSError as e:
        log(f"  card init FAILED -- {e}")
        return False
    mb = sd.sectors // 2048
    log(f"  {sd.sectors} sectors, {mb} MiB ({mb / 1024:.2f} GiB) at {baudrate / 1e6:.2f} MHz")
    if CRC is None:
        log("  ! no lib/crc16.py -- reads and writes are unverified")

    if probe:
        log("checking the card really holds what it claims...")
        why = probe_capacity(sd, log)
        if why == "io":
            log("ABORTED: the card stopped responding mid-probe, even on retries.")
            log("That is NOT a capacity lie -- a card that lies still answers, it just")
            log("answers with the wrong data. Look at the block it died on: if it is a")
            log("low one it had already written happily, suspect the card itself or the")
            log("3V3 rail sagging during write bursts. sd_check.py reads without writing")
            log("at all -- run that to see whether reads alone are clean.")
            return False
        if why != "ok":
            log("ABORTED: the card does not hold the data it claims to. Formatting it")
            log("would produce a filesystem that corrupts once the logs pass the real")
            log("size. Replace it; confirm with h2testw or f3 on a computer if you want.")
            return False

    log("formatting (writing FAT tables -- this can take a minute or two)...")
    t0 = time.ticks_ms()
    try:
        _vfs_fat().mkfs(sd)
    except Exception as e:
        log(f"  mkfs FAILED -- {e}")
        return False
    log(f"  mkfs done in {time.ticks_diff(time.ticks_ms(), t0) / 1000:.1f} s")

    describe_layout(sd, log)

    log("mounting the way sd_write_task() does...")
    try:
        vfs.mount(sd, "/sd")           # autodetect, exactly as main.py calls it
    except OSError as e:
        log(f"  mount FAILED -- {e}")
        return False

    try:
        ok = selftest(log)
    except OSError as e:
        log(f"  SELF-TEST FAILED -- {e}")
        ok = False
    finally:
        vfs.umount("/sd")

    log("DONE -- card formatted and verified." if ok
        else "FAILED -- the card formatted but did not survive its self-test.")
    return ok


if __name__ == "__main__":
    if I_UNDERSTAND_THIS_ERASES_THE_CARD:
        format_sd()
    else:
        print("sd_format.py refuses to run: this ERASES the SD card.")
        print("Set I_UNDERSTAND_THIS_ERASES_THE_CARD = True at the top of the file.")
