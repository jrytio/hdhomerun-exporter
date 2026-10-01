"""Bytes and text captured from a real HDHR4-2US (fw 20260313), 2026-09-29.

The captured reply bytes are verbatim. In the text fixtures, the station call signs,
virtual channels, RF frequency, TSID and client address are replaced with fictional
values, so the fixtures do not identify a broadcast market or a home network.
"""

# Request for /sys/version, as sent by a working client.
REQ_SYS_VERSION = bytes.fromhex("0004000f030d2f7379732f76657273696f6e00170ebc4e")
# Reply: NAME + VALUE "20260313".
RPY_SYS_VERSION = bytes.fromhex(
    "0005001a030d2f7379732f76657273696f6e000409323032363033313300c0071ee4"
)
# Reply to any unknown variable (/sys/uptime, /tuner2/debug): ERROR tag only, no NAME.
RPY_UNKNOWN_VARIABLE = bytes.fromhex(
    "00050021051f4552524f523a20756e6b6e6f776e20676574736574207661726961626c6500a7b780f0"
)
# Reply to /tuner1/debug (idle): the VALUE uses the two-byte length form (0x82 0x01 = 130).
RPY_TUNER1_DEBUG = bytes.fromhex(
    "00050095030e2f74756e6572312f64656275670004820174756e3a2063683d6e6f6e65206c6f636b3d6e6f"
    "6e652073733d3020736e713d30207365713d30206462673d300a6465763a206270733d3020726573796e"
    "633d30206f766572666c6f773d300a74733a20206270733d302074653d30206372633d300a6e65743a20"
    "6270733d30207070733d30206572723d302073746f703d300a0089e38c80"
)

TUNER_DEBUG_STREAMING = (
    "tun: ch=8vsb:575000000 lock=8vsb:575000000 ss=100 snq=98 seq=100 dbg=-2875/9836\n"
    "dev: bps=19466272 resync=0 overflow=0\n"
    "ts:  bps=9366912 te=0 crc=0\n"
    "net: bps=9367360 pps=802 err=0 stop=0\n"
)
TUNER_DEBUG_IDLE = (
    "tun: ch=none lock=none ss=0 snq=0 seq=0 dbg=0\n"
    "dev: bps=0 resync=0 overflow=0\n"
    "ts:  bps=0 te=0 crc=0\n"
    "net: bps=0 pps=0 err=0 stop=0\n"
)
SYS_DEBUG = (
    "mem: nbk=5 dmk=210\nloop: pkt=0\nt0: pt=11 cal=-4990\nt1: pt=11 cal=-5015\neth: link=100f\n"
)
STREAMINFO = (
    "3: 5.1 DEMO\n4: 5.2 DEMO2\n5: 5.3 DEMO3\n6: 5.4 DEMO4\n"
    "7: 5.5 DEMO5\n8: 5.6 DEMO6\n9: 5.7 DEMO7\ntsid=0x1234\n"
)

# A whole device: tuner0 streaming 5.1 DEMO to 192.168.1.20, tuner1 idle, no tuner2.
DEVICE_VALUES = {
    "/sys/hwmodel": "HDHR4-2US",
    "/sys/model": "hdhomerun4_atsc",
    "/sys/version": "20260313",
    "/sys/debug": SYS_DEBUG,
    "/tuner0/debug": TUNER_DEBUG_STREAMING,
    "/tuner0/vchannel": "5.1",
    "/tuner0/program": "3",
    "/tuner0/streaminfo": STREAMINFO,
    "/tuner0/target": "http://192.168.1.20:60207",
    "/tuner1/debug": TUNER_DEBUG_IDLE,
}
