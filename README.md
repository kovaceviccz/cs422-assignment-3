# CS 422 Assignment 3

This repository contains the code and report for the third CS 422 assignment, a Linux kernel congestion control module named `kovacevic` and its evaluation against CUBIC, Reno, and BBR.

## Requirements

- Linux with kernel headers for the running kernel, gcc, make, and sudo.
- Python 3.12 or later and Typst.
- CUBIC, Reno, and BBR available to TCP sockets (`sudo modprobe tcp_bbr` if BBR is missing).
- Built and tested on kernel 6.17.0-35-generic.

## Build

```bash
make
```

This builds `tcp_kovacevic.ko`. The `linux-headers` package must match the running kernel.

## Load

```bash
sudo insmod tcp_kovacevic.ko
sysctl net.ipv4.tcp_available_congestion_control
```

The sysctl line ends with `kovacevic` once the module is loaded. Unload with `sudo rmmod tcp_kovacevic`.

## Quick check

Run one 10-second transfer from a terminal.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python demo.py 204.155.243.172 30005
```

Each printed line holds the congestion window in segments, the RTT in ms, the retransmission count, and the goodput in Mbit/s. The window doubles from its initial value and then stops growing once the RTT rises above its minimum. That stop is the delay pause from the design.

A second terminal shows the live socket while the transfer runs.

```bash
watch -n1 'ss -tin dport = :30005'
```

The `cubic` column of the output shows `kovacevic` and the `cwnd` value matches the demo output. Those two lines together show the module loaded, the data socket selected it, and the module is driving the window.

## Run all measurements

```bash
.venv/bin/python assignment3.py listed_iperf3_servers.json \
  --destinations 10 --duration 10 --interval 0.5 --repetitions 2
```

The four arguments set the destination count, the transfer duration in seconds, the sampling interval in seconds, and the number of repetitions per algorithm. A failed or busy server is recorded and the script tries another candidate up to a bounded number of attempts. Each run overwrites `results/` and finishes by compiling `results/assignment-3-report.pdf`.

## Files

```text
tcp_kovacevic.c                 congestion control module
Makefile                        module build
demo.py                         single-transfer demo
assignment3.py                  main script
iperf_client.py                 iperf3 client
tcp_stats.py                    TCP_INFO decoding
plots.py                        plot generation
listed_iperf3_servers.json      input downloaded from iperf3serverlist.net
report.typ                      report source
requirements.txt                Python dependencies
results/                        generated plots, raw measurements, and report
```

`report-data.json` supplies values to the [Typst report](results/assignment-3-report.pdf).
