// SPDX-License-Identifier: GPL-2.0
/*
 * CS 422 Assignment 3: Linux congestion control for the Assignment 2 design.
 */
#include <linux/module.h>
#include <net/tcp.h>

#define KOV_RTT_PAUSE_US		2000
#define KOV_DELAY_MARGIN_PERCENT	20
#define KOV_FLAT_GROWTH_PERCENT	5
#define KOV_DELAY_CUT_PERCENT		10
#define KOV_MIN_CWND			2
#define KOV_SMOOTH_WEIGHT		4

enum kov_mode {
	KOV_STARTUP = 0,
	KOV_AVOIDANCE = 1,
};

struct kov {
	u8 mode;
	u8 flat_rounds;
	u8 delay_cut_used;
	u8 initialized;
	u32 min_rtt_us;
	u32 smoothed_rtt_us;
	u64 smoothed_rate_bps;
	u64 prev_rate_bps;
	u64 round_start_usec;
	u64 round_acked;	/* segments */
};

static void kov_set_cwnd(struct tcp_sock *tp, u32 cwnd)
{
	if (tp->snd_cwnd_clamp)
		cwnd = min(cwnd, tp->snd_cwnd_clamp);
	tcp_snd_cwnd_set(tp, cwnd);
}

static u32 kov_delay_cut(u32 cwnd)
{
	return max_t(u32, KOV_MIN_CWND,
		     (u64)cwnd * (100 - KOV_DELAY_CUT_PERCENT) / 100);
}

static u32 kov_ssthresh(struct sock *sk)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct kov *ca = inet_csk_ca(sk);

	ca->mode = KOV_AVOIDANCE;
	ca->flat_rounds = 0;
	ca->delay_cut_used = 1;
	return max_t(u32, KOV_MIN_CWND, tp->snd_cwnd / 2);
}

static void kov_cong_avoid(struct sock *sk, u32 ack, u32 acked)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct kov *ca = inet_csk_ca(sk);
	u64 dt_us, bytes, rate_bps, smoothed_rate;
	u64 round_segments;
	u32 rtt_us, queue_delay, queue_limit;
	bool high_delay, flat_round;

	if (!ca->initialized) {
		ca->initialized = 1;
		ca->mode = KOV_STARTUP;
		ca->round_start_usec = ktime_get_ns() / NSEC_PER_USEC;
		tp->snd_ssthresh = TCP_INFINITE_SSTHRESH;
	}

	if (!tcp_is_cwnd_limited(sk))
		return;

	ca->round_acked += acked;
	if (ca->round_acked < tp->snd_cwnd)
		return;

	round_segments = ca->round_acked;
	ca->round_acked = 0;
	dt_us = ktime_get_ns() / NSEC_PER_USEC - ca->round_start_usec;
	ca->round_start_usec = ktime_get_ns() / NSEC_PER_USEC;

	rtt_us = tp->srtt_us >> 3;	/* srtt_us is 1/8 microseconds */
	if (!rtt_us || !dt_us) {
		ca->flat_rounds = 0;
		return;
	}

	if (!ca->min_rtt_us) {
		ca->min_rtt_us = rtt_us;
		ca->smoothed_rtt_us = rtt_us;
	} else {
		ca->min_rtt_us = min(ca->min_rtt_us, rtt_us);
		ca->smoothed_rtt_us =
			((KOV_SMOOTH_WEIGHT - 1) * ca->smoothed_rtt_us + rtt_us)
			/ KOV_SMOOTH_WEIGHT;
	}

	bytes = round_segments * tp->mss_cache;
	rate_bps = bytes * (8 * USEC_PER_SEC) / dt_us;
	ca->prev_rate_bps = ca->smoothed_rate_bps;
	if (ca->smoothed_rate_bps)
		smoothed_rate = ((KOV_SMOOTH_WEIGHT - 1) * ca->smoothed_rate_bps
				 + rate_bps) / KOV_SMOOTH_WEIGHT;
	else
		smoothed_rate = rate_bps;
	ca->smoothed_rate_bps = smoothed_rate;

	flat_round = ca->prev_rate_bps &&
		smoothed_rate < ca->prev_rate_bps
			+ ca->prev_rate_bps / (100 / KOV_FLAT_GROWTH_PERCENT);
	if (flat_round)
		ca->flat_rounds = min_t(u8, ca->flat_rounds + 1, 2);
	else
		ca->flat_rounds = 0;

	queue_delay = ca->smoothed_rtt_us - ca->min_rtt_us;
	queue_limit = max_t(u32, KOV_RTT_PAUSE_US,
			    ca->min_rtt_us / (100 / KOV_DELAY_MARGIN_PERCENT));
	high_delay = queue_delay > queue_limit;

	if (ca->mode == KOV_STARTUP && ca->flat_rounds == 2) {
		ca->mode = KOV_AVOIDANCE;
		if (high_delay) {
			kov_set_cwnd(tp, kov_delay_cut(tp->snd_cwnd));
			ca->delay_cut_used = 1;
		}
	} else if (ca->mode == KOV_STARTUP && high_delay) {
		/* hold */
	} else if (ca->mode == KOV_STARTUP) {
		kov_set_cwnd(tp, tp->snd_cwnd * 2);
	} else if (high_delay) {
		if (ca->flat_rounds == 2 && !ca->delay_cut_used) {
			kov_set_cwnd(tp, kov_delay_cut(tp->snd_cwnd));
			ca->delay_cut_used = 1;
		}
	} else {
		ca->delay_cut_used = 0;
		kov_set_cwnd(tp, tp->snd_cwnd + 1);
	}
}

static struct tcp_congestion_ops tcp_kov = {
	.flags		= TCP_CONG_NON_RESTRICTED,
	.ssthresh	= kov_ssthresh,
	.cong_avoid	= kov_cong_avoid,
	.undo_cwnd	= tcp_reno_undo_cwnd,
	.name		= "kovacevic",
	.owner		= THIS_MODULE,
};

static int __init kov_register(void)
{
	return tcp_register_congestion_control(&tcp_kov);
}

static void __exit kov_unregister(void)
{
	tcp_unregister_congestion_control(&tcp_kov);
}

module_init(kov_register);
module_exit(kov_unregister);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Marko Kovacevic");
MODULE_DESCRIPTION("CS 422 Assignment 3 congestion control");
