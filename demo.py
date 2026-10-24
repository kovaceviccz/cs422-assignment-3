import sys

from iperf_client import TransferError, run_transfer


def main():
    host, port = sys.argv[1], int(sys.argv[2])
    try:
        result = run_transfer(host, port, "kovacevic", 10, 0.5, "demo")
    except TransferError as error:
        print(f"transfer failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    for sample in result["samples"]:
        print(
            f"{sample['time_seconds']:5.1f}s cwnd={sample['snd_cwnd_segments']:>5} "
            f"rtt={sample['rtt_us'] / 1000:6.2f}ms "
            f"retrans={sample['retransmits_interval']:>3} "
            f"goodput={sample['goodput_bps'] / 1e6:7.2f}Mbit/s"
        )


if __name__ == "__main__":
    main()
