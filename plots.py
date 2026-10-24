import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ALGORITHMS = ("kovacevic", "cubic", "reno", "bbr")
COLORS = {"kovacevic": "#6b4f8c", "cubic": "#24476b", "reno": "#a34c32", "bbr": "#487454"}
METRICS = (
    ("snd_cwnd_segments", "Congestion window (segments)"),
    ("rtt_us", "RTT (ms)"),
    ("retransmits_interval", "Retransmissions / interval"),
    ("goodput_bps", "Goodput (Mbit/s)"),
)
SCATTER = (
    ("snd_cwnd_segments", "Congestion window (segments)"),
    ("rtt_us", "RTT (ms)"),
    ("retransmits_interval", "Retransmissions / interval"),
)


def value(sample, key):
    number = sample[key]
    if key == "rtt_us":
        return number / 1000
    if key == "goodput_bps":
        return number / 1_000_000
    return number


def runs_for(destination, algorithm):
    return [run for run in destination["runs"] if run["algorithm"] == algorithm]


def save_figure(fig, path):
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def shared_limits(destination):
    keys = [key for key, _ in METRICS] + ["rtt_us", "goodput_bps"]
    keys = set(keys)
    limits = {}
    for key in keys:
        values = [value(sample, key) for run in destination["runs"] for sample in run["samples"]]
        limits[key] = (min(values), max(values))
    return limits


def plot_throughput(complete, folder):
    files = []
    for first in range(0, len(complete), 5):
        group = complete[first:first + 5]
        fig, axes = plt.subplots(len(group), 1, figsize=(9, 2.15 * len(group)), squeeze=False)
        for axis, destination in zip(axes[:, 0], group):
            candidate = destination["candidate"]
            for algorithm in ALGORITHMS:
                for repetition, run in enumerate(runs_for(destination, algorithm)):
                    axis.plot(
                        [sample["time_seconds"] for sample in run["samples"]],
                        [value(sample, "goodput_bps") for sample in run["samples"]],
                        color=COLORS[algorithm], linewidth=1.1, alpha=0.8,
                        linestyle="-" if repetition == 0 else "--",
                        label=algorithm.upper() if repetition == 0 else None,
                    )
            axis.set_title(f"{candidate['host']}:{candidate['port']}", fontsize=9)
            axis.set_ylabel("Mbit/s")
            axis.grid(alpha=0.2)
        axes[0, 0].legend(ncol=4, fontsize=8)
        axes[-1, 0].set_xlabel("Time since transfer began (s)")
        filename = f"throughput-{first // 5 + 1}.pdf"
        save_figure(fig, folder / filename)
        files.append(filename)
    return files


def plot_algorithm_time_series(destination, algorithm, folder, limits):
    runs = runs_for(destination, algorithm)
    fig, axes = plt.subplots(2, 2, figsize=(9, 6), sharex=True)
    for axis, (key, label) in zip(axes.flat, METRICS):
        for repetition, run in enumerate(runs):
            axis.plot(
                [sample["time_seconds"] for sample in run["samples"]],
                [value(sample, key) for sample in run["samples"]],
                color=COLORS[algorithm], linewidth=1.1,
                linestyle="-" if repetition == 0 else "--",
                label=f"Run {repetition + 1}",
            )
        axis.set_ylabel(label)
        axis.set_ylim(limits[key])
        axis.grid(alpha=0.2)
    axes[0, 0].legend(fontsize=8)
    axes[1, 0].set_xlabel("Time (s)")
    axes[1, 1].set_xlabel("Time (s)")
    filename = f"{algorithm}-time-series.pdf"
    save_figure(fig, folder / filename)
    return filename


def plot_algorithm_scatter(destination, algorithm, folder, limits):
    runs = runs_for(destination, algorithm)
    fig, axes = plt.subplots(3, 1, figsize=(9, 9))
    for axis, (key, label) in zip(axes, SCATTER):
        for repetition, run in enumerate(runs):
            axis.scatter(
                [value(sample, key) for sample in run["samples"]],
                [value(sample, "goodput_bps") for sample in run["samples"]],
                color=COLORS[algorithm], s=13, alpha=0.6,
                marker="o" if repetition == 0 else "x",
                label=f"Run {repetition + 1}",
            )
        axis.set_xlabel(label)
        axis.set_xlim(limits[key])
        axis.set_ylabel("Goodput (Mbit/s)")
        axis.set_ylim(limits["goodput_bps"])
        axis.grid(alpha=0.2)
    axes[0].legend(fontsize=8)
    filename = f"{algorithm}-scatter.pdf"
    save_figure(fig, folder / filename)
    return filename


def make_plots(complete, representative, folder):
    limits = shared_limits(representative)
    throughput = plot_throughput(complete, folder)
    plots = {"throughput": throughput}
    for algorithm in ALGORITHMS:
        plots[f"{algorithm}_time_series"] = plot_algorithm_time_series(
            representative, algorithm, folder, limits
        )
        plots[f"{algorithm}_scatter"] = plot_algorithm_scatter(
            representative, algorithm, folder, limits
        )
    return plots
