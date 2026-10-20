/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef SUIKA_H
#define SUIKA_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SK_CAPACITY 48
#define SK_RANKS 11
#define SK_WIDTH 194
#define SK_HEIGHT 178
#define SK_DANGER 24
#define SK_SAVE_BYTES 608
#define SK_TICK_MS 16 /* fixed simulation step; independent of render speed */

typedef struct {
  float x, y, vx, vy;
  uint16_t age;
  uint8_t kind, pop;
} sk_fruit;

typedef struct {
  sk_fruit fruit[SK_CAPACITY];
  uint32_t rng, score, best, merges;
  float aim;
  uint16_t danger, cooldown, combo_timer;
  uint8_t bag[8], bag_at, current, next[3], held, hold_used;
  uint8_t count, combo, highest, over, started, hint, notice;
} sk_game;

extern const uint8_t sk_radius[SK_RANKS + 1];
extern const char *const sk_names[SK_RANKS + 1];
void sk_new(sk_game *g, uint32_t seed, uint32_t best);
void sk_aim(sk_game *g, float x);
bool sk_drop(sk_game *g);
bool sk_hold(sk_game *g);
void sk_step(sk_game *g);
/* Exact first circle encountered by a vertical fall; -1 means the floor. */
float sk_landing(const sk_game *g, float x, int *target);
void sk_suggest(sk_game *g);
void sk_encode(const sk_game *g, uint8_t out[SK_SAVE_BYTES]);
/* Rejects truncated/corrupt saves without modifying g. */
bool sk_decode(sk_game *g, const uint8_t *data, size_t len);
#endif
