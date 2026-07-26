"""
SD card diagnostic for the RAB Pi Pico HAT. Read-only -- it never writes to the card.

Run it with MicroPico -> "Run current file". It does not need to be uploaded to the
board, and it is in micropico.pyIgnore so a project upload leaves it behind.

sd_write_task() fails at vfs.mount() with ENODEV. That does NOT mean "no card":
sdcard.py raises its own ENODEV from init_card() (lib/sdcard.py:124), which would
print before "sd init complete". ENODEV out of mount means MicroPython read sector 0
and found no filesystem it recognised. Exactly two things cause that, and this script
tells them apart:

  1. The SPI clock is too fast, so the sector arrives corrupted. init_card() does the
     whole handshake at 100 kHz and only switches to the requested baudrate on its
     last line, so mount() is the first read at full speed. main.py passes no
     crc16_function, so sdcard.py accepts bad data silently -- garbage in the buffer
     is indistinguishable from "not a filesystem". This script turns the CRC on, which
     turns silent corruption into a loud EIO.

  2. The card really is laid out in a way MicroPython cannot mount -- exFAT, GPT, or a
     sector size other than 512. This script decodes sector 0 and names it.
"""

import time
import vfs
from binascii import hexlify
from machine import SPI, Pin

import sdcard

# Same pins as sd_write_task() in main.py.
SCK, MOSI, MISO, CS = 18, 19, 16, 17

# 24 MHz is what main.py asks for today; the rest are the fallback ladder.
BAUDS = (1_320_000, 4_000_000, 8_000_000, 12_000_000, 24_000_000)

MBR_TYPES = {
    0x00: "empty",
    0x01: "FAT12",
    0x04: "FAT16 <32M",
    0x06: "FAT16",
    0x07: "exFAT or NTFS  <-- MicroPython CANNOT mount this",
    0x0B: "FAT32 (CHS)",
    0x0C: "FAT32 (LBA)",
    0x0E: "FAT16 (LBA)",
    0xEE: "GPT protective  <-- MicroPython CANNOT mount this",
}

try:
    from crc16 import crc16_viper as CRC
except ImportError:
    CRC = None
    print("! lib/crc16.py not found -- corrupt reads stay silent, exactly as in main.py")


def open_card(baud):
    spi = SPI(0, sck=Pin(SCK), mosi=Pin(MOSI), miso=Pin(MISO), baudrate=baud)
    return sdcard.SDCard(spi=spi, cs=Pin(CS), baudrate=baud, crc16_function=CRC)


def describe_vbr(buf, where):
    """Decode a FAT volume boot record -- sector 0 of a partition, or of the card."""
    # A never-written sector reads back as all 0x00 or all 0xFF. Say so plainly:
    # "0 bytes/sector" would be a nonsense reading of what is really a blank sector.
    if not any(buf) or all(b == 0xFF for b in buf):
        fill = "zeros" if not any(buf) else "0xFF"
        print(f"  {where}: all {fill} -- nothing has ever been written here.")
        print("   -> the card is partitioned but NOT formatted. The MBR points at a")
        print("      volume boot record that does not exist.")
        return False

    oem = bytes(buf[3:11])
    bps = buf[11] | (buf[12] << 8)
    print(f"  {where}: OEM {oem!r}, {bps} bytes/sector")
    if oem == b"EXFAT   ":
        print("   -> exFAT. MicroPython's FAT driver cannot mount it. Reformat as FAT32.")
        return False
    if bps != 512:
        print(f"   -> {bps}-byte sectors. MicroPython needs 512. Reformat.")
        return False
    fat32 = bytes(buf[82:90])
    fat16 = bytes(buf[54:62])
    if fat32.startswith(b"FAT32"):
        print("   -> FAT32. This is mountable.")
        return True
    if fat16.startswith(b"FAT"):
        print(f"   -> {fat16!r}. Mountable, but not FAT32.")
        return True
    print(f"   -> no FAT type string ({fat32!r} / {fat16!r}). Not a filesystem MicroPython knows.")
    return False


def describe_sector0(sd):
    buf = bytearray(512)
    sd.readblocks(0, buf)          # raises EIO on a CRC failure, if crc16 is available

    print(f"  first 16 bytes : {hexlify(buf[:16]).decode()}")
    sig = bytes(buf[510:512])
    # No backslash escapes inside an f-string expression -- MicroPython rejects those.
    sig_ok = "ok" if sig == b"\x55\xaa" else "MISSING"
    print(f"  boot signature : {hexlify(sig).decode()} ({sig_ok})")
    if sig != b"\x55\xaa":
        print("   -> sector 0 is not a boot sector at all. Either the read is still")
        print("      corrupt, or the card was never formatted. Reformat it.")
        return False

    if buf[0] in (0xEB, 0xE9):
        # Jump instruction: a volume boot record sits right at sector 0, unpartitioned.
        return describe_vbr(buf, "VBR at sector 0")

    # Otherwise it is an MBR; the four partition entries start at offset 446.
    print("  sector 0 is an MBR:")
    first = None
    for i in range(4):
        o = 446 + 16 * i
        ptype = buf[o + 4]
        start = int.from_bytes(bytes(buf[o + 8:o + 12]), "little")
        count = int.from_bytes(bytes(buf[o + 12:o + 16]), "little")
        if not ptype and not count:
            continue
        name = MBR_TYPES.get(ptype, "unknown")
        gib = count / 2097152
        print(f"    part {i + 1}: type 0x{ptype:02x} {name}, LBA {start}, {count} sectors ({gib:.2f} GiB)")
        # FAT12/16 top out at 4 GiB even with 64 KiB clusters, so a small-FAT type
        # byte on a bigger span means the entry does not describe a real filesystem.
        if ptype in (0x01, 0x04, 0x06, 0x0E) and count > 8388608:
            print("      ^ impossible: that type byte cannot describe a volume this")
            print("        large. The partition entry is bogus or left over.")
        if first is None:
            first = (ptype, start)

    if first is None:
        print("   -> MBR with no partitions. Reformat the card.")
        return False
    ptype, start = first
    if ptype in (0x07, 0xEE):
        return False

    vbr = bytearray(512)
    sd.readblocks(start, vbr)
    return describe_vbr(vbr, f"VBR at LBA {start}")


def try_mount(sd):
    try:
        vfs.mount(sd, "/sd")
    except OSError as e:
        print(f"  vfs.mount FAILED: {e}")
        return False
    try:
        import os
        print(f"  mounted. /sd contains: {os.listdir('/sd')}")
    finally:
        vfs.umount("/sd")
    return True


def scan_extent(sd):
    """Single-block reads across the whole card.

    A card that lies about its capacity -- the usual counterfeit -- reads fine near
    the start and fails, or silently wraps, past its real flash. Reporting the block
    that fails is the whole point: failing at block 0 means the bus, failing at 60%
    means the card.
    """
    buf = bytearray(512)
    edge = None
    # Stop short of the final sector: sdcard.py reads with CMD18 even for one block,
    # and starting that at the end of the media makes many cards flag out-of-range in
    # the CMD12 response, which arrives as a bare EIO. That edge is tested separately
    # below, because reading it as "the card is broken" would be wrong.
    top = sd.sectors - 3
    for pct in range(0, 101, 10):
        blk = min(top, sd.sectors * pct // 100)
        try:
            sd.readblocks(blk, buf)
        except OSError as e:
            print(f"  {pct:3d}% (block {blk:>9}): FAILED -- {e}")
            if edge is None:
                edge = blk
            continue
        note = " (blank)" if not any(buf) else ""
        print(f"  {pct:3d}% (block {blk:>9}): ok{note}")

    for blk in (sd.sectors - 2, sd.sectors - 1):
        try:
            sd.readblocks(blk, buf)
            print(f"  last sectors: block {blk} ok")
        except OSError as e:
            last = blk == sd.sectors - 1
            print(f"  last sectors: block {blk} refused -- {e}")
            if last:
                print("   -> only the very last sector: that is the CMD18 prefetch edge,")
                print("      and it is harmless. No filesystem stores anything there.")
            else:
                print("   -> the top of the card is genuinely unreadable.")
                if edge is None:
                    edge = blk
    return edge


def speed_run(baud):
    """Read a spread of sectors at `baud` and report exactly what fails, where."""
    label = f"{baud / 1_000_000:.2f} MHz"
    try:
        sd = open_card(baud)
    except OSError as e:
        print(f"  {label:>9}: card init FAILED -- {e}")
        return False

    one = bytearray(512)
    multi = bytearray(512 * 8)
    # The top spot has to leave room for the whole 8-block read below the final
    # sector, or every run trips the CMD18 prefetch edge and no speed ever passes.
    spots = (0, 1, 64, sd.sectors // 2, sd.sectors - 16)

    # Single blocks first. If these pass and the 4 KiB read fails, the problem is
    # multi-block streaming, not signal integrity.
    for blk in spots:
        try:
            sd.readblocks(blk, one)
        except OSError as e:
            print(f"  {label:>9}: 1-block read FAILED at block {blk} -- {e}")
            return False

    t0 = time.ticks_ms()
    for blk in spots:
        try:
            sd.readblocks(blk, multi)
        except OSError as e:
            print(f"  {label:>9}: 4 KiB read FAILED at block {blk} -- {e}")
            print(f"  {' ':>9}  (single blocks at the same spots were fine)")
            return False
    dt = time.ticks_diff(time.ticks_ms(), t0)
    kib = len(multi) * len(spots) // 1024
    rate = kib * 1000 / dt if dt else 0
    print(f"  {label:>9}: {kib} KiB clean in {dt} ms ({rate:.0f} KiB/s)")
    return True


def main():
    try:
        vfs.umount("/sd")
        print("(unmounted a stale /sd)")
    except Exception:
        pass

    print("\n=== 1. card init and sector 0, at the driver's default 1.32 MHz ===")
    try:
        sd = open_card(BAUDS[0])
    except OSError as e:
        print(f"  card init FAILED: {e}")
        print("   -> the card is not responding at all. Check wiring, CS on GP17, and power.")
        return
    mb = sd.sectors // 2048
    print(f"  card ok: {sd.sectors} sectors, {mb} MiB ({mb / 1024:.2f} GiB)")
    if CRC is None:
        print("  (no CRC checking -- a clean-looking dump below may still be corrupt)")

    try:
        ok = describe_sector0(sd)
    except OSError as e:
        print(f"  sector 0 read FAILED: {e}")
        print("   -> corruption at 1.32 MHz is not a speed problem. Check grounding, the")
        print("      MISO line, and card seating. A HAT with a loose card does this.")
        return

    print("\n=== 2. mount, at 1.32 MHz ===")
    mounted = try_mount(sd)

    print("\n=== 3. is the whole card readable? (1.32 MHz, single blocks) ===")
    edge = scan_extent(sd)

    print("\n=== 4. how fast can this card actually go? ===")
    fastest = None
    for baud in BAUDS:
        if speed_run(baud):
            fastest = baud

    print("\n=== verdict ===")
    if edge is not None:
        pct = 100 * edge // sd.sectors
        print(f"  Reads fail from about {pct}% in (block {edge}) at the slowest clock this")
        print("  driver supports. That is not a speed or wiring fault -- block 0 reads")
        print("  fine. The card claims 7.5 GiB it cannot actually deliver.")
        print("  Verify it on a computer with h2testw or f3, and replace it. Reformatting")
        print("  a card that lies about its size gives you a filesystem that corrupts as")
        print("  soon as the logs pass the real capacity.")
    elif not ok:
        print("  Every sector is readable, but there is no filesystem to mount.")
        print("  Reformat as FAT32 with an MBR -- the SD Association's SD Card Formatter")
        print("  does this correctly; Windows will hand you exFAT on some paths.")
    elif mounted:
        print("  The card mounts fine at 1.32 MHz, so the filesystem is not the problem.")
        if fastest is None:
            print("  No clock passed the read test, yet the card mounted -- that is a")
            print("  contradiction, so suspect the test rather than the card.")
        elif fastest < 24_000_000:
            print(f"  It went bad above {fastest / 1_000_000:.2f} MHz -- that is why main.py's")
            print("  24 MHz fails. Set sd_write_task() to one step below that.")
        elif fastest == 24_000_000:
            print("  It also read cleanly at 24 MHz here, which is odd given the failure.")
            print("  Suspect something intermittent: card seating, supply sag under the")
            print("  radio's TX current, or a marginal MISO line.")
    else:
        print("  Reads are clean but mount still failed. Re-check the sector 0 decode above.")


main()
