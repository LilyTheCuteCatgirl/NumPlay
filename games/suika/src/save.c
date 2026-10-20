/* SPDX-License-Identifier: GPL-3.0-only */
#include "suika.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

static void put16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static unsigned get16(const uint8_t *p) { return p[0] | (unsigned)p[1] << 8; }
static void put32(uint8_t *p, uint32_t v) { put16(p, v); put16(p + 2, v >> 16); }
static uint32_t get32(const uint8_t *p) { return get16(p) | (uint32_t)get16(p + 2) << 16; }
static uint32_t checksum(const uint8_t *p, size_t n) {
  uint32_t h = 2166136261u;
  while (n--) h = (h ^ *p++) * 16777619u;
  return h;
}
void sk_encode(const sk_game *g, uint8_t out[SK_SAVE_BYTES]) {
  memset(out, 0, SK_SAVE_BYTES);
  memcpy(out, "SKA1", 4);
  put32(out + 4, g->score);
  put32(out + 8, g->best);
  put32(out + 12, g->rng);
  put32(out + 16, g->merges);
  put16(out + 20, (int)(g->aim * 64));
  put16(out + 22, g->danger);
  out[24] = g->cooldown;
  out[25] = g->current;
  memcpy(out + 26, g->next, 3);
  out[29] = g->held;
  out[30] = g->hold_used;
  out[31] = g->highest;
  out[32] = g->over;
  out[33] = g->started;
  out[34] = g->bag_at;
  memcpy(out + 35, g->bag, 8);
  out[43] = g->combo;
  out[44] = (uint8_t)g->combo_timer;
  unsigned n = 0;
  for (unsigned i = 0; i < SK_CAPACITY; ++i) {
    const sk_fruit *b = &g->fruit[i];
    if (!b->kind) continue;
    uint8_t *p = out + 48 + n++ * 11;
    p[0] = b->kind;
    put16(p + 1, (int)(b->x * 64));
    put16(p + 3, (int)(b->y * 64));
    put16(p + 5, (int)(b->vx * 256));
    put16(p + 7, (int)(b->vy * 256));
    put16(p + 9, b->age);
  }
  out[45] = (uint8_t)n;
  put32(out + SK_SAVE_BYTES - 4, checksum(out, SK_SAVE_BYTES - 4));
}
bool sk_decode(sk_game *g, const uint8_t *p, size_t n) {
  if (!p || n != SK_SAVE_BYTES || memcmp(p, "SKA1", 4) ||
      get32(p + n - 4) != checksum(p, n - 4)) return false;
  if (!get32(p + 12) || get32(p + 4) > get32(p + 8) || get16(p + 20) > SK_WIDTH * 64 ||
      get16(p + 22) > 180 || p[24] > 24 || p[25] < 1 || p[25] > 4 ||
      p[29] > 4 || p[30] > 1 || p[31] < 1 || p[31] > SK_RANKS || p[32] > 1 ||
      p[33] > 1 || p[34] > 8 || p[43] > 99 || p[44] > 75 || p[45] > SK_CAPACITY) return false;
  for (unsigned i = 26; i < 29; ++i) if (p[i] < 1 || p[i] > 4) return false;
  unsigned bag_counts[5] = {0};
  for (unsigned i = 35; i < 43; ++i) {
    if (p[i] < 1 || p[i] > 4) return false;
    ++bag_counts[p[i]];
  }
  if (bag_counts[1] != 3 || bag_counts[2] != 2 || bag_counts[3] != 2 || bag_counts[4] != 1) return false;
  /* Validate the complete payload before touching the caller's live state. */
  for (unsigned i = 0; i < p[45]; ++i) {
    const uint8_t *b = p + 48 + i * 11;
    if (!b[0] || b[0] > SK_RANKS) return false;
    float x = (int16_t)get16(b + 1) / 64.0f, y = (int16_t)get16(b + 3) / 64.0f;
    if (x < sk_radius[b[0]] - 0.05f || x > SK_WIDTH - sk_radius[b[0]] + 0.05f ||
        y < -48 || y > SK_HEIGHT - sk_radius[b[0]] + 0.05f ||
        abs((int16_t)get16(b + 5)) > 2048 || abs((int16_t)get16(b + 7)) > 2048) return false;
  }
  memset(g, 0, sizeof *g);
  g->score = get32(p + 4);
  g->best = get32(p + 8);
  g->rng = get32(p + 12);
  g->merges = get32(p + 16);
  g->danger = get16(p + 22);
  g->cooldown = p[24];
  g->current = p[25];
  memcpy(g->next, p + 26, 3);
  g->held = p[29];
  g->hold_used = p[30];
  g->highest = p[31];
  g->over = p[32];
  g->started = p[33];
  g->bag_at = p[34];
  memcpy(g->bag, p + 35, 8);
  g->combo = p[43];
  g->combo_timer = p[44];
  g->count = p[45];
  sk_aim(g, get16(p + 20) / 64.0f);
  for (unsigned i = 0; i < g->count; ++i) {
    const uint8_t *b = p + 48 + i * 11;
    g->fruit[i] = (sk_fruit){(int16_t)get16(b + 1) / 64.0f, (int16_t)get16(b + 3) / 64.0f,
                           (int16_t)get16(b + 5) / 256.0f, (int16_t)get16(b + 7) / 256.0f,
                           (uint16_t)get16(b + 9), b[0], 0};
  }
  return true;
}
