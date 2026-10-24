import socket
import struct


TCP_INFO_SIZE = 248


def read_tcp_stats(data_socket):
    info = data_socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, TCP_INFO_SIZE)
    if len(info) < 136:
        raise RuntimeError(f"TCP_INFO returned only {len(info)} bytes")

    def value32(offset):
        return struct.unpack_from("=I", info, offset)[0]

    def value64(offset):
        return struct.unpack_from("=Q", info, offset)[0]

    return {
        "snd_mss_bytes": value32(16),
        "lost_packets": value32(32),
        "rtt_us": value32(68),
        "rttvar_us": value32(72),
        "snd_cwnd_segments": value32(80),
        "total_retrans_packets": value32(100),
        "pacing_rate_bytes_per_s": value64(104),
        "bytes_acked": value64(120),
    }
