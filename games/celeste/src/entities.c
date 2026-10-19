#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize("Os")   /* not drawn every frame: smaller over faster */
#endif
/* The entities of Prologue and Forsaken City and the ones every chapter
 * uses: spikes, springs, refills, strawberries, jumpthrus, zip movers,
 * crumbling and falling blocks, dash blocks, fake walls, camera triggers. */
#include "entities.h"
#include "npc.h"

/* ---------------------------------------------------------------- records */
float ea_num(const EData *d, int off, char kind) {
  const uint8_t *p = d->attrs + off;
  switch (kind) {
    case 'b': return p[0] ? 1.f : 0.f;
    case 'i': return (float)rds16(p);
    case 'I': return (float)rds32(p);
    case 'f': return rdf(p);
  }
  return 0;
}
const char *ea_str(const EData *d, int off) { return str(rd16(d->attrs + off)); }
V2 ed_node(const EData *d, int i) {
  if (i >= d->nnodes) return v2(d->x, d->y);
  return v2(d->rx + rds16(d->nodes + 4 * i), d->ry + rds16(d->nodes + 4 * i + 2));
}
uint32_t level_entity_hash(const EData *d) { return level_entity_id(d->room, d->id) | (d->trig ? 1u << 31 : 0); }

const char *area_jumpthru(void) {
  /* AreaData.Jumpthru */
  static const char *J[] = {"wood", "wood", "wood", "wood", "cliffside", "temple", "reflection", "temple", "wood", "core", "wood"};
  int a = g_session.area;
  return a < 11 ? J[a] : "wood";
}
const char *area_crumble(void) {
  int a = g_session.area;
  return a == 4 ? "cliffside" : "default";
}
static int area_spike(void) {   /* AreaData.Spike: 0 default, 1 outline, 2 cliffside, 3 reflection */
  int a = g_session.area;
  return a == 4 ? 2 : a == 6 ? 3 : a == 7 ? 1 : 0;
}

static uint16_t tex_by_name(const char *path);

/* ---------------------------------------------------------------- Wiggler, SineWave */
void wiggler_init(Wiggler *w, float duration, float freq) {
  w->counter = w->sine_counter = 0;
  w->increment = 1 / duration;
  w->sine_add = PI_F * 2 * freq;
  w->active = false;
}
void wiggler_restart(Wiggler *w) {
  w->counter = 1;
  if (w->start_zero) w->sine_counter = PI_F / 2, w->value = 0;
  else w->sine_counter = 0, w->value = 1;
  w->active = true;
}
void wiggler_start(Wiggler *w, float duration, float freq) {
  w->increment = 1 / duration;
  w->sine_add = PI_F * 2 * freq;
  wiggler_restart(w);
}
void wiggler_update(Wiggler *w) {
  if (!w->active) return;
  w->sine_counter += w->sine_add * DT;
  w->counter -= w->increment * DT;
  if (w->counter <= 0) {
    w->counter = 0;
    w->active = false;
  }
  w->value = cosf(w->sine_counter) * w->counter;
}
void sine_set(SineWave *s, float counter) {
  s->counter = fmodf(counter + PI_F * 8, PI_F * 8);
  s->value = sinf(s->counter);
  s->value_over_two = sinf(s->counter / 2);
  s->two_value = sinf(s->counter * 2);
}
void sine_randomize(SineWave *s) { sine_set(s, rndf() * (PI_F * 2) * 2); }
void sine_update(SineWave *s) { sine_set(s, s->counter + PI_F * 2 * s->freq * DT); }
void tween_start(Tween *t, float duration) {
  t->duration = duration;
  t->time_left = duration;
  t->percent = 0;
  t->active = true;
}
bool tween_update(Tween *t) {
  if (!t->active) return false;
  t->time_left -= DT;
  t->percent = 1 - fmaxf(0, t->time_left) / t->duration;
  if (t->time_left <= 0) {
    t->time_left = 0;
    t->active = false;
    return true;
  }
  return false;
}

/* ---------------------------------------------------------------- spawn points */
#define MAX_SPAWNS 48
static V2 spawns[2][MAX_SPAWNS];
static int nspawns[2];

V2 level_closest_spawn(V2 at) {
  int s = g_level.room_slot;
  if (!nspawns[s]) return at;
  V2 best = spawns[s][0];
  float bd = 1e30f;
  for (int i = 0; i < nspawns[s]; i++) {
    float dx = spawns[s][i].x - at.x, dy = spawns[s][i].y - at.y, d = dx * dx + dy * dy;
    if (d < bd) bd = d, best = spawns[s][i];
  }
  return best;
}

/* DashListener.OnDash, for each that listens */
void level_dash_listeners(V2 dir) {
  for (int i = 0; i < g_nents; i++) {
    Ent *e = &g_ents[i];
    if (e->cls && e->dead != 1 && MORE(e->cls)->on_dash) MORE(e->cls)->on_dash(e, dir);
  }
}
WEAK void level_booster_boosted(Ent *b, V2 dir) { (void)b, (void)dir; }
/* Holdables (Theo's crystal, src/ch5.c): the player picks up, carries, throws */
WEAK Ent *level_holdable_check(void) { return NULL; }
WEAK void holdable_carry(Ent *h, V2 at) { (void)h, (void)at; }
WEAK void holdable_release(Ent *h, V2 force) { (void)h, (void)force; }
bool level_is_dash_block(Ent *e) { return e->cls && !strcmp(e->cls->name, "dashBlock"); }

/* ---------------------------------------------------------------- JumpthruPlatform */
typedef struct { uint16_t tex; uint8_t cols, n, first_row, last_row; uint32_t seed; } JumpThru;
/* the middle columns' pieces were Calc.Random picks: a hash of the place */
static void jt_piece(JumpThru *j, int i, int *col, int *row) {
  if (i == 0) *col = 0, *row = j->first_row;
  else if (i == j->cols - 1) *col = j->n - 1, *row = j->last_row;
  else {
    uint32_t h = (j->seed + (uint32_t)i * 2654435761u) ^ 0x9E3779B9u;
    h ^= h >> 15, h *= 0x2C1B3C6Du, h ^= h >> 12;
    *col = 1 + (int)(h % (uint32_t)(j->n > 2 ? j->n - 2 : 1));
    *row = (int)((h >> 8) & 1);
  }
}
static void jumpthru_awake(Ent *e) {
  JumpThru *j = ST(e, JumpThru);
  Tex t;
  j->n = tex_get(j->tex, &t) ? (uint8_t)(t.fw / 8) : 3;
  j->first_row = !collide_solid(e, e->x - 1, e->y);
  j->last_row = !collide_solid(e, e->x + 1, e->y);
  j->seed = (uint32_t)(int)e->x * 73856093u ^ (uint32_t)(int)e->y * 19349663u;
}
static void jumpthru_render(Ent *e) {
  JumpThru *j = ST(e, JumpThru);
  for (int i = 0; i < j->cols; i++) {
    int col, row;
    jt_piece(j, i, &col, &row);
    gfx_tex_part(j->tex, e->x + i * 8, e->y, col * 8, row * 8, 8, 8, 0, 0xFFFF, 255);
  }
}
static void jumpthru_update(Ent *e) { plat_update(e); }
static const EntClass JUMPTHRU = {.size = sizeof(JumpThru), .name = "jumpThru", .update = jumpthru_update, .render = jumpthru_render,
                                  .awake = jumpthru_awake, .kind = KIND_JUMPTHRU};

/* ---------------------------------------------------------------- Decal.MakeSolid */
/* the Resort's roofs and bridge columns, its broken elevator, the cliffside bridges: plain solids (safe) the converter
 * makes from those decals; a bg decal's lets waterfalls through (Solid.BlockWaterfalls: Depth != 9000) */
static const EntClass DSOLID = {.name = "decalSolid", .update = plat_update, .kind = KIND_SOLID};
static const EntClass DSOLID_OPEN = {.name = "decalSolid", .update = plat_update, .kind = KIND_SOLID};
bool solid_blocks_waterfalls(const Ent *e) { return e->cls != &DSOLID_OPEN; }
static void new_dsolid(const EData *d) {
  Ent *e = ent_new(EAB(d, decalSolid, blockWaterfalls) ? &DSOLID : &DSOLID_OPEN, d->x, d->y);
  if (!e) return;
  ent_box(e, EA(d, decalSolid, width), EA(d, decalSolid, height), 0, 0);
  e->safe = 1;
  e->depth = -9000;   /* (Platform's) */
  e->visible = 0;
}

static void new_jumpthru(const EData *d) {
  int w = (int)EA(d, jumpThru, width);
  Ent *e = ent_new(&JUMPTHRU, d->x, d->y);
  if (!e) return;
  ent_box(e, (float)w, 5, 0, 0);
  e->safe = 1;
  e->depth = -60;
  JumpThru *j = ST(e, JumpThru);
  j->cols = (uint8_t)(w / 8);
  const char *tx = EAS(d, jumpThru, texture);
  char path[48];
  path2(path, "objects/jumpthru/", (*tx && strcmp(tx, "default")) ? tx : area_jumpthru(), -1);
  j->tex = tex_by_name(path);
}

/* ---------------------------------------------------------------- Spikes */
enum { DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT };
typedef struct {
  uint8_t dir, n, cassette, on;   /* cassette: on a cassette block (its colors, enabled or disabled: cassette_tint) */
  uint16_t tex;
  int16_t gox, goy;               /* and its group's origin (SetOrigins), from the spikes */
  uint8_t tentacles, half;        /* the "tentacles" type: n tentacles of 16 pixels, and half: one more at the end */
} Spikes;
static void spikes_on_player(Ent *e, Player *p) {
  Spikes *s = ST(e, Spikes);
  switch (s->dir) {
    case DIR_UP: if (p->speed.y >= 0 && e_bottom(p->ent) <= e_bottom(e)) player_die(p, v2(0, -1), false); break;
    case DIR_DOWN: if (p->speed.y <= 0) player_die(p, v2(0, 1), false); break;
    case DIR_LEFT: if (p->speed.x >= 0) player_die(p, v2(-1, 0), false); break;
    case DIR_RIGHT: if (p->speed.x <= 0) player_die(p, v2(1, 0), false); break;
  }
}
static bool spikes_riding(Ent *e, Ent *p) {
  Spikes *s = ST(e, Spikes);
  if (p->kind & KIND_JUMPTHRU) return s->dir == DIR_UP && collide_ent_at(e, e->x, e->y + 1, p);
  float dx = 0, dy = 0;
  switch (s->dir) {
    case DIR_UP: dy = 1; break;
    case DIR_DOWN: dy = -1; break;
    case DIR_LEFT: dx = 1; break;
    case DIR_RIGHT: dx = -1; break;
  }
  return collide_ent_at(e, e->x + dx, e->y + dy, p) && !collide_ent_at(e, e->x, e->y, p);
}
/* Spikes.AddTentacle: the "tentacles" type (the Mirror Temple's other side) draws a tentacle sprite each 16 pixels, and
 * one more at the end of an odd number of 8; each plays one of the bank's three loops from its own frame, turned to face
 * out of the wall, mirrored or not (picked at random in the game: here from where it is, so nothing is kept) */
static uint32_t tentacle_hash(uint32_t x) {
  x ^= x >> 16, x *= 0x7FEB352Du, x ^= x >> 15, x *= 0x846CA68Bu, x ^= x >> 16;
  return x;
}
static void tentacles_render(Ent *e, Spikes *s) {
  static const float rot[] = {[DIR_UP] = 0, [DIR_DOWN] = PI_F, [DIR_LEFT] = PI_F * 1.5f, [DIR_RIGHT] = PI_F * 0.5f};
  for (int k = 0; k < s->n + s->half; k++) {
    float i = k < s->n ? (float)k : s->n - 0.5f;
    uint32_t h = tentacle_hash((uint32_t)(int)e->x * 73856093u ^ (uint32_t)(int)e->y * 19349663u ^ (uint32_t)k * 83492791u);
    Sprite sp;
    spr_init(&sp, SB_tentacles);
    spr_play(&sp, A_tentacles_0 + (int)(h % 3), true);
    int nf = spr_frames(&sp);
    if (nf > 0) spr_play_offset(&sp, sp.anim, (int)((h >> 8) % (uint32_t)nf + (uint32_t)(g_level.time_active / 0.08f)) % nf);
    sp.flipx = (h >> 4) & 1;   /* Scale.X = -1 or 1 */
    sp.rot = rot[s->dir];
    if (s->cassette) sp.tint = cassette_tint(ent_platform(e), s->on);
    float x = e->x, y = e->y;
    switch (s->dir) {
      case DIR_UP: x += (i + .5f) * 16, y += 1; break;
      case DIR_DOWN: x += (i + .5f) * 16, y -= 1; break;
      case DIR_RIGHT: y += (i + .5f) * 16, x -= 1; break;
      case DIR_LEFT: y += (i + .5f) * 16, x += 1; break;
    }
    spr_draw(&sp, x + e->shakex, y + e->shakey);
  }
}

static void spikes_render(Ent *e) {
  Spikes *s = ST(e, Spikes);
  if (s->tentacles) {
    tentacles_render(e, s);
    return;
  }
  Tex t;
  if (!tex_get(s->tex, &t)) return;
  for (int j = 0; j < s->n; j++) {
    float x = e->x, y = e->y, ox = 0, oy = 0;
    switch (s->dir) {
      case DIR_UP: ox = t.fw * .5f, oy = (float)t.fh, x += (j + .5f) * 8, y += 1; break;
      case DIR_DOWN: ox = t.fw * .5f, oy = 0, x += (j + .5f) * 8, y -= 1; break;
      case DIR_RIGHT: ox = 0, oy = t.fh * .5f, y += (j + .5f) * 8, x -= 1; break;
      case DIR_LEFT: ox = (float)t.fw, oy = t.fh * .5f, y += (j + .5f) * 8, x += 1; break;
    }
    if (s->cassette) {   /* scaled with the block's group around its origin */
      V2 o, k;
      if (!cassette_block_scale(ent_platform(e), &o, &k)) k = v2(1, 1);
      float gx = e->x + s->gox, gy = e->y + s->goy;
      gfx_tex_ex(s->tex, gx + e->shakex, gy + e->shakey, ox + gx - x, oy + gy - y, k.x, k.y, 0, cassette_tint(ent_platform(e), s->on),
                 255, 0);
    } else
      gfx_tex_ex(s->tex, x + e->shakex, y + e->shakey, ox, oy, 1, 1, 0, 0xFFFF, 255, 0);
  }
}
/* StaticMover.OnEnable / OnDisable (VisibleWhenDisabled on cassette blocks) */
static void spikes_sm_enable(Ent *e, bool on) {
  Spikes *s = ST(e, Spikes);
  e->active = e->collidable = on;
  if (on) e->visible = 1;
  else if (!s->cassette) e->visible = 0;
  s->on = on;   /* the color: enabled or disabled */
}
void spikes_set_cassette(Ent *e, V2 origin) {
  Spikes *s = ST(e, Spikes);
  s->cassette = 1;
  s->on = e->collidable;
  s->gox = (int16_t)(origin.x - e->x), s->goy = (int16_t)(origin.y - e->y);
}
static void spikes_sm_shake(Ent *e, V2 a) { e->shakex = (int8_t)(e->shakex + a.x), e->shakey = (int8_t)(e->shakey + a.y); }
static const EntClass SPIKES = {.size = sizeof(Spikes), .name = "spikes", .render = spikes_render, .on_player = spikes_on_player,
                                .kind = KIND_PCOLLIDE | KIND_STATICMOVER | KIND_SPIKES,
                                .more = &(const EntMore){.sm_riding = spikes_riding,
                                                         .sm_shake = spikes_sm_shake, .sm_enable = spikes_sm_enable}};
bool spikes_ledge(const Ent *e) { return e->cls == &SPIKES && ST(e, Spikes)->dir != DIR_DOWN; }

static void new_spikes(const EData *d, int dir) {
  int size;
  const char *type;
  if (dir == DIR_UP) size = (int)EA(d, spikesUp, width), type = EAS(d, spikesUp, type);
  else if (dir == DIR_DOWN) size = (int)EA(d, spikesDown, width), type = EAS(d, spikesDown, type);
  else if (dir == DIR_LEFT) size = (int)EA(d, spikesLeft, height), type = EAS(d, spikesLeft, type);
  else size = (int)EA(d, spikesRight, height), type = EAS(d, spikesRight, type);
  Ent *e = ent_new(&SPIKES, d->x, d->y);
  if (!e) return;
  e->depth = -1;
  switch (dir) {
    case DIR_UP: ent_box(e, (float)size, 3, 0, -3); break;
    case DIR_DOWN: ent_box(e, (float)size, 3, 0, 0); break;
    case DIR_LEFT: ent_box(e, 3, (float)size, -3, 0); break;
    case DIR_RIGHT: ent_box(e, 3, (float)size, 0, 0); break;
  }
  Spikes *s = ST(e, Spikes);
  s->dir = (uint8_t)dir;
  s->n = (uint8_t)(size / 8);
  static const char *names[] = {"default", "outline", "cliffside", "reflection"};
  static const char *dirs[] = {"up", "down", "left", "right"};
  const char *t = (*type && strcmp(type, "default")) ? type : names[area_spike()];
  if (!strcmp(t, "tentacles")) {   /* (Spikes.Added) */
    s->tentacles = 1, s->n = (uint8_t)(size / 16), s->half = (size / 8) % 2;
    return;
  }
  char path[48], tmp[24];
  path2(tmp, t, "_", -1);
  path2(path, "danger/spikes/", path2(tmp + 12 - 12, tmp, dirs[dir], -1), 0);
  s->tex = tex_by_name(path);
}

/* ---------------------------------------------------------------- Spring */
typedef struct { uint8_t orient, can_use; Sprite spr; Wiggler wig; } Spring;
enum { SPRING_FLOOR, SPRING_WALLLEFT, SPRING_WALLRIGHT };
static uint16_t spring_tex[7];

static void spring_on_player(Ent *e, Player *p) {
  Spring *s = ST(e, Spring);
  if (p->state == ST_DREAMDASH || !s->can_use) return;
  bool bounce = false;
  if (s->orient == SPRING_FLOOR) {
    if (p->speed.y >= 0) bounce = true, player_super_bounce(p, e_top(e));
  } else if (s->orient == SPRING_WALLLEFT) {
    bounce = true;
    player_side_bounce(p, 1, e_right(e), e_cym(e));
  } else {
    bounce = true;
    player_side_bounce(p, -1, e_left(e), e_cym(e));
  }
  if (bounce) {
    if (ent_platform(e)) plat_static_movers_trigger(ent_platform(e));
    s->spr.frame = 0;
    s->spr.timer = 0;
    s->spr.playing = 1;
    wiggler_start(&s->wig, 1, 4);
  }
}
static const uint8_t SPRING_SEQ[] = {0, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 4, 5};
static void spring_update(Ent *e) {
  Spring *s = ST(e, Spring);
  if (s->spr.playing) {
    s->spr.timer += DT;
    while (s->spr.timer >= 0.07f && s->spr.playing) {
      s->spr.timer -= 0.07f;
      if (++s->spr.frame >= sizeof SPRING_SEQ) s->spr.frame = 0, s->spr.playing = 0;
    }
  }
  wiggler_update(&s->wig);
}
static void spring_render(Ent *e) {
  Spring *s = ST(e, Spring);
  int f = s->spr.playing ? SPRING_SEQ[s->spr.frame] : 0;
  uint16_t t = s->can_use ? spring_tex[f] : spring_tex[6];
  float sy = 1 + s->wig.value * 0.2f;
  float rot = s->orient == SPRING_WALLLEFT ? PI_F / 2 : s->orient == SPRING_WALLRIGHT ? -PI_F / 2 : 0;
  gfx_tex_ex(t, e->x, e->y, 8, 16, 1, sy, rot, 0xFFFF, 255, 0);
}
static bool spring_riding(Ent *e, Ent *p) {
  Spring *s = ST(e, Spring);
  float dx = s->orient == SPRING_WALLLEFT ? -1 : s->orient == SPRING_WALLRIGHT ? 1 : 0;
  float dy = s->orient == SPRING_FLOOR ? 1 : 0;
  return collide_ent_at(e, e->x + dx, e->y + dy, p);
}
static void spring_attach(Ent *e, Ent *p) {
  e->depth = (int16_t)(p->depth + 1);
  ents_mark_unsorted();
}
static const EntClass SPRING = {.size = sizeof(Spring), .name = "spring", .update = spring_update, .render = spring_render,
                                .on_player = spring_on_player, .kind = KIND_PCOLLIDE | KIND_STATICMOVER,
                                .more = &(const EntMore){.sm_riding = spring_riding, .sm_attach = spring_attach}};

static void new_spring(const EData *d, int orient, bool can_use) {
  if (!spring_tex[0]) {
    char p[32];
    for (int i = 0; i < 6; i++) path2(p, "objects/spring/", NULL, i), spring_tex[i] = tex_by_name(p);
    spring_tex[6] = tex_by_name("objects/spring/white");
  }
  Ent *e = ent_new(&SPRING, d->x, d->y);
  if (!e) return;
  e->depth = -8501;
  Spring *s = ST(e, Spring);
  s->orient = (uint8_t)orient;
  s->can_use = can_use;
  if (orient == SPRING_FLOOR) ent_box(e, 16, 6, -8, -6);
  else if (orient == SPRING_WALLLEFT) ent_box(e, 6, 16, 0, -8);
  else ent_box(e, 6, 16, -6, -8);
}

/* ---------------------------------------------------------------- Refill */
typedef struct {
  uint8_t two, one_use, visible, outline, flash_on, step;
  float respawn, flash_t, co_wait, frame_t;
  int frame, flash_frame;
  SineWave sine;
  Wiggler wig;
} Refill;
/* its sprite's frames (idle: all of them, 5 or 13; flash: 6) */
#define REFILL_IDLE(r) ((r)->two ? T_objects_refillTwo_idle00 : T_objects_refill_idle00)
#define REFILL_FLASH(r) ((r)->two ? T_objects_refillTwo_flash00 : T_objects_refill_flash00)

static void refill_on_player(Ent *e, Player *p) {
  Refill *r = ST(e, Refill);
  if (player_use_refill(p, r->two)) {
    e->collidable = 0;
    r->respawn = 2.5f;
    r->step = 1;   /* RefillRoutine */
    level_freeze(0.05f);
  }
}
static void refill_update(Ent *e) {
  Refill *r = ST(e, Refill);
  if (r->step == 1) {   /* yield null after the freeze */
    r->step = 2;
  } else if (r->step == 2) {
    level_shake(.3f);
    r->visible = 0;
    r->flash_on = 0;
    if (!r->one_use) r->outline = 1;
    e->depth = 8999;
    ents_mark_unsorted();
    r->co_wait = 0.05f;
    r->step = 3;
  } else if (r->step == 3) {
    if ((r->co_wait -= DT) <= 0) {
      Player *p = level_player();
      float a = p ? atan2f(p->speed.y, p->speed.x) : 0;
      particles_emit(PL_FG, (r->two ? &P_Refill_P_ShatterTwo : &P_Refill_P_Shatter), 5, v2(e->x, e->y), v2(4, 4), a - PI_F / 2);
      particles_emit(PL_FG, (r->two ? &P_Refill_P_ShatterTwo : &P_Refill_P_Shatter), 5, v2(e->x, e->y), v2(4, 4), a + PI_F / 2);
      r->step = 0;
      if (r->one_use) ent_remove(e);
    }
  }
  if (r->respawn > 0) {
    r->respawn -= DT;
    if (r->respawn <= 0 && !e->collidable) {
      e->collidable = 1;
      r->visible = 1;
      r->outline = 0;
      e->depth = -100;
      ents_mark_unsorted();
      wiggler_start(&r->wig, 1, 4);
      particles_emit(PL_FG, (r->two ? &P_Refill_P_RegenTwo : &P_Refill_P_Regen), 16, v2(e->x, e->y), v2(2, 2), 0);
    }
  } else if (level_on_interval(0.1f) && r->visible)
    particles_emit(PL_FG, (r->two ? &P_Refill_P_GlowTwo : &P_Refill_P_Glow), 1, v2(e->x, e->y), v2(5, 5), 0);
  sine_update(&r->sine);
  wiggler_update(&r->wig);
  r->frame_t += DT;
  if (r->frame_t >= 0.1f) r->frame_t -= 0.1f, r->frame = (r->frame + 1) % (r->two ? 13 : 5);
  if (r->flash_on) {
    r->flash_t += DT;
    if (r->flash_t >= 0.05f) {
      r->flash_t -= 0.05f;
      if (++r->flash_frame >= 6) r->flash_on = 0;
    }
  }
  if (level_on_interval(2) && r->visible) r->flash_on = 1, r->flash_frame = 0, r->flash_t = 0;
}
static void refill_render(Ent *e) {
  Refill *r = ST(e, Refill);
  float y = e->y + r->sine.value * 2, s = 1 + r->wig.value * 0.2f;
  if (r->outline) gfx_tex_ex(r->two ? T_objects_refillTwo_outline : T_objects_refill_outline, e->x, e->y, 8, 8, 1, 1, 0, 0xFFFF, 255, 0);
  if (r->visible) gfx_tex_ex((uint16_t)(REFILL_IDLE(r) + r->frame), e->x, y, 8, 8, s, s, 0, 0xFFFF, 255, 0);
  if (r->visible && r->flash_on) gfx_tex_ex((uint16_t)(REFILL_FLASH(r) + r->flash_frame), e->x, y, 8, 8, s, s, 0, 0xFFFF, 255, 0);
}
static const EntClass REFILL = {.size = sizeof(Refill), .name = "refill", .update = refill_update, .render = refill_render,
                                .on_player = refill_on_player, .kind = KIND_PCOLLIDE};
static void new_refill(const EData *d) {
  Ent *e = ent_new(&REFILL, d->x, d->y);
  if (!e) return;
  ent_box(e, 16, 16, -8, -8);
  e->depth = -100;
  Refill *r = ST(e, Refill);
  r->two = EAB(d, refill, twoDash);
  r->one_use = EAB(d, refill, oneUse);
  r->visible = 1;
  r->sine.freq = 0.6f;
  sine_randomize(&r->sine);
}

/* ---------------------------------------------------------------- level-wide entities */
/* effects that live as long as the level (SlashFx and the like) */
static void fx_entity_update(Ent *e) {
  (void)e;
  fx_update();
}
static const EntClass FX = {.name = "fx", .update = fx_entity_update};
/* the level's ParticlesBG, Particles and ParticlesFG (drawn at their depths) */
static void parts_render(Ent *e) { particles_render(e->depth == D_BGPARTICLES ? PL_BG : e->depth == D_PARTICLES ? PL_MID : PL_FG); }
static const EntClass PARTS = {.name = "particles", .render = parts_render};

/* FormationBackdrop: darkens the level behind hearts, cassettes, seeds being put together */
static void formation_update(Ent *e) {
  (void)e;
  g_level.formation_fade = approach(g_level.formation_fade, g_level.formation ? 1.f : 0.f, DT * 3);
}
static void formation_render(Ent *e) {
  (void)e;
  float a = g_level.formation_fade * g_level.formation_alpha * 0.85f;
  if (a > 0) gfx_rect(g_level.cam.x - 1, g_level.cam.y - 1, 322, 182, 0, (uint8_t)(a * 255));
}
static const EntClass FORMATION = {.name = "formationBackdrop", .update = formation_update, .render = formation_render};

/* ---------------------------------------------------------------- CrumblePlatform */
#define CRUMBLE_MAX 40
/* Image k's coroutine falls[k] (TileOut, later TileIn) moves image order[k] (fallOrder) after (k % 4) * 0.05 s:
 * four groups, each run like a Monocle Coroutine. Group phases: 0 just Replace'd, 1 waiting its delay (gt: the
 * waitTimer), 2 in its loop before the first body, 3 in its loop (gt: the loop's time, already advanced), 4 ended. */
typedef struct {
  uint8_t n, state, on_top, fell;   /* fell: 0 standing, 1 falling out (TileOut), 2 came back (TileIn) */
  uint8_t perm, shake_on, shook;    /* perm: TileIn put image order[k] at slot k (until the shaker moves them home);
                                       shook: the ShakerList moved the images this frame */
  uint8_t gph[4];
  float gt[4];
  float left;                       /* the images' scale TileOut left (TileIn shows it on its first frame) */
  float timer, outline_a, fade_from, fade_to, fade_t, wait;
  int step;
  uint16_t tex, outline, seed, seedq[4];   /* seed: the shaker's offsets (0: none); seedq: when each group's TileOut began */
  uint8_t order[CRUMBLE_MAX];
} Crumble;

/* the ShakerList's offset of image k (axis 0: x, 1: y): Calc.Random.ShakeVector, kept between its 0.05 s intervals */
static int crumble_shake(uint16_t seed, int k, int axis) {
  static const int8_t off[5] = {-1, -1, 0, 1, 1};
  if (!seed) return 0;
  uint32_t h = (uint32_t)seed * 0x9E3779B1u ^ (uint32_t)(k * 2 + axis + 1) * 0x85EBCA77u;
  h ^= h >> 15, h *= 0x2C1B3C6Du, h ^= h >> 12;
  return off[h % 5];
}
static void crumble_emit(Ent *e, Crumble *c) {
  for (int i = 0; i < c->n; i++) particles_emit(PL_MID, &P_CrumblePlatform_P_Crumble, 2, v2(e->x + 4 + i * 8, e->y + 6), v2(3, 3), 0);
}
/* falls[k].Replace(TileOut or TileIn) for every k */
static void crumble_replace(Crumble *c, int fell) {
  c->fell = (uint8_t)fell;
  for (int q = 0; q < 4; q++) c->gph[q] = 0;
}
/* the falls coroutines (components before Sequence and the shaker) */
static void crumble_falls(Crumble *c) {
  if (!c->fell) return;
  for (int q = 0; q < 4 && q < c->n; q++) {
    float *t = &c->gt[q];
    switch (c->gph[q]) {
      case 0:   /* (TileOut: img.Color = Color.Gray) yield return delay */
        *t = 0.05f * (float)q, c->gph[q] = 1;
        break;
      case 1:   /* TileOut: from = img.Position; TileIn: Visible, at (index * 8 + 4, 4) */
        if (*t > 0) {
          *t -= DT;
          break;
        }
        c->seedq[q] = c->seed;
        *t = 0, c->gph[q] = 2;
        break;
      case 2:
      case 3: {   /* yield return null; the body; time += DT / duration */
        float body = *t;
        *t += DT / (c->fell == 1 ? 0.4f : 0.25f);
        c->gph[q] = 3;
        if (*t >= 1) {
          c->gph[q] = 4;
          if (c->fell == 1) c->left = 1 - body * 0.5f;   /* its last img.Scale */
        }
        break;
      }
    }
  }
}
/* the ShakerList (after Sequence): new offsets every 0.05 s, back home when its time is up */
static void crumble_shaker(Crumble *c) {
  c->shook = 0;
  if (!c->shake_on) return;
  if (c->timer > 0) {
    c->timer -= DT;
    if (c->timer <= 0) {
      c->shake_on = 0, c->seed = 0, c->shook = 1, c->perm = 0;
      return;
    }
  }
  if (level_on_interval(0.05f)) c->seed = (uint16_t)(1 + rndi(0xFFFF)), c->shook = 1, c->perm = 0;
}
static void crumble_fall(Ent *e, Crumble *c) {
  c->fade_from = 0, c->fade_to = 1, c->fade_t = 0;   /* OutlineFade(1) */
  e->collidable = 0;
  crumble_replace(c, 1);
  c->step = 3;
  c->wait = 2;
}
static void crumble_update(Ent *e) {
  Crumble *c = ST(e, Crumble);
  plat_update(e);
  if (c->fade_t < 1) {   /* outlineFader: for (t = 0; t < 1; t += DT * 2) { color; yield return null; } */
    c->outline_a = c->fade_from + (c->fade_to - c->fade_from) * ease_cube_inout(c->fade_t);
    c->fade_t += DT * 2;
  }
  crumble_falls(c);
  switch (c->step) {   /* Sequence */
    case 3:   /* yield return 2f; then while something is in the way, yield return null */
      if (c->wait > 0) {
        c->wait -= DT;
        break;
      }
      {
        bool blocked = false;
        e->collidable = 1;
        for (int i = 0; i < g_nents && !blocked; i++) {
          Ent *o = &g_ents[i];
          if (o != e && o->cls && o->dead != 1 && (o->kind & (KIND_ACTOR | KIND_SOLID)) && o->collidable && collide_ent_at(e, e->x, e->y, o))
            blocked = true;
        }
        e->collidable = 0;
        if (blocked) break;
      }
      c->fade_from = 1, c->fade_to = 0, c->fade_t = 0;   /* OutlineFade(0) */
      e->collidable = 1;
      crumble_replace(c, 2);
      c->perm = 1;
      c->step = 0;
      /* fall through - while (true) looks for the player at once */
    case 0: {   /* waiting for the player */
      bool top = solid_has_player_on_top(e);
      if (!top && !solid_has_player_climbing(e)) break;
      c->on_top = top;
      c->shake_on = 1, c->timer = top ? 0.6f : 1.f;   /* shaker.ShakeFor */
      crumble_emit(e, c);
      c->state = top ? 1 : 3;
      c->wait = 0.2f;
      c->step = 1;
      break;
    }
    case 1:   /* (1 or 3 times) yield return 0.2f; particles */
      if (c->wait > 0) {
        c->wait -= DT;
        break;
      }
      crumble_emit(e, c);
      if (--c->state) {
        c->wait = 0.2f;
        break;
      }
      c->wait = 0.4f, c->step = 2;
      if (c->on_top && !solid_has_player_on_top(e)) crumble_fall(e, c);   /* the while's first test comes before a yield */
      break;
    case 2:   /* while (timer > 0 (and the player on top)) { yield return null; timer -= DT; } */
      c->wait -= DT;
      if (c->wait <= 0 || (c->on_top && !solid_has_player_on_top(e))) crumble_fall(e, c);
      break;
  }
  crumble_shaker(c);
}
/* how image i is drawn: centered on (x, y) (world), scaled by sc, tinted, its texture column col; false: not drawn */
typedef struct {
  float x, y, sc;
  uint16_t tint;
  uint8_t a, col;
} CrumbleImg;
static bool crumble_img(const Ent *e, const Crumble *c, int i, CrumbleImg *m) {
  int k = 0;   /* falls[k] moves it */
  while (k < c->n - 1 && c->order[k] != i) k++;
  int q = k & 3, ph = c->gph[q];
  m->col = (uint8_t)((int)((fabsf(e->x) + i * 8) / 8) % 4);
  m->x = 4 + (c->perm ? k : i) * 8, m->y = 4;
  if (!c->perm) m->x += (float)crumble_shake(c->seed, i, 0), m->y += (float)crumble_shake(c->seed, i, 1);
  m->tint = 0xFFFF, m->a = 255, m->sc = 1;
  if (c->fell == 1) {   /* TileOut: grey, then falls (CubeIn) 12, 24 or 36 px, fading and shrinking to half, over 0.4 s */
    if (ph == 4) return false;
    if (ph) m->tint = 0x8410;   /* Color.Gray */
    if (ph == 3) {
      float t = c->gt[q] - DT / 0.4f;   /* the body's time */
      if (!c->shook) {   /* (on its intervals the shaker puts the image back home) */
        float fx = 4 + i * 8 + (float)crumble_shake(c->seedq[q], i, 0);
        m->x = fx;
        m->y = 4 + (float)crumble_shake(c->seedq[q], i, 1) + ease_cube_in(t) * (float)(((int)fx * 7 % 3 + 1) * 12);
      }
      m->a = (uint8_t)(255 * (1 - t));
      m->sc = 1 - t * 0.5f;
    }
  } else if (c->fell == 2) {   /* TileIn: TileOut's last scale for a frame, then 1.2 bouncing back to 1 over 0.25 s */
    if (ph < 2) return false;
    if (ph == 2) m->sc = c->left;
    else if (ph == 3) m->sc = 1 + ease_bounce_out(1 - (c->gt[q] - DT / 0.25f)) * 0.2f;
  }
  m->x += e->x, m->y += e->y;
  return m->a > 0;
}
/* the images while some are scaled: drawn here, in their order, so they take none of gfx.c's few affine slots */
static void crumble_strip(uint16_t *strip, int y0, int y1, void *ctx) {
  Ent *e = ctx;
  Crumble *c = ST(e, Crumble);
  Tex t;
  CrumbleImg m;
  if (!tex_get(c->tex, &t)) return;
  for (int i = 0; i < c->n; i++)
    if (crumble_img(e, c, i, &m)) {
      float x = m.x - g_camx, y = m.y - g_camy;   /* the 8x8 subtexture (column col), scaled around its center */
      blit_cells(strip, y0, y1, &t, x - 4, y - 4, 1, 1, &m.col, x, y, m.sc, m.sc, m.tint, m.a);
    }
}
static void crumble_render(Ent *e) {
  Crumble *c = ST(e, Crumble);
  if (c->outline_a > 0) {
    uint8_t a = (uint8_t)(c->outline_a * 255);
    for (int i = 0; i < c->n; i++) {
      int col = c->n == 1 ? 3 : i == 0 ? 0 : i < c->n - 1 ? 1 : 2;
      gfx_tex_part(c->outline, e->x + i * 8, e->y, col * 8, 0, 8, 8, 0, 0xFFFF, a);
    }
  }
  CrumbleImg m;
  float top = 1e9f, bot = -1e9f;
  bool scaled = false;
  for (int i = 0; i < c->n; i++)
    if (crumble_img(e, c, i, &m)) {
      float h = 4 * fmaxf(m.sc, 1);
      top = fminf(top, m.y - h), bot = fmaxf(bot, m.y + h);
      if (m.sc != 1) scaled = true;
    }
  if (scaled) {
    Tex t;   /* (loaded before the strips are drawn) */
    if (tex_get(c->tex, &t)) gfx_custom(crumble_strip, e, (int)floorf(top) - g_camy - 1, (int)ceilf(bot) - g_camy + 1);
    return;
  }
  for (int i = 0; i < c->n; i++)
    if (crumble_img(e, c, i, &m)) gfx_tex_part(c->tex, m.x - 4, m.y - 4, m.col * 8, 0, 8, 8, 0, m.tint, m.a);
}
static const EntClass CRUMBLE = {.size = sizeof(Crumble), .name = "crumbleBlock", .update = crumble_update, .render = crumble_render, .kind = KIND_SOLID};
static void new_crumble(const EData *d) {
  Ent *e = ent_new(&CRUMBLE, d->x, d->y);
  if (!e) return;
  int w = (int)EA(d, crumbleBlock, width);
  ent_box(e, (float)w, 8, 0, 0);
  Crumble *c = ST(e, Crumble);
  c->n = (uint8_t)(w / 8 > CRUMBLE_MAX ? CRUMBLE_MAX : w / 8);
  char p[48];
  path2(p, "objects/crumbleBlock/", area_crumble(), -1);
  c->tex = tex_by_name(p);
  c->outline = tex_by_name("objects/crumbleBlock/outline");
  c->fade_t = 1;
  c->left = 1;
  for (int i = 0; i < c->n; i++) c->order[i] = (uint8_t)i;
  for (int i = c->n - 1; i > 0; i--) {
    int j = rndi(i + 1);
    uint8_t t = c->order[i];
    c->order[i] = c->order[j], c->order[j] = t;
  }
}

/* ---------------------------------------------------------------- ZipMover */
typedef struct {
  V2 start, target;
  float percent, at, wait;
  int step, light;
  uint16_t cog, block, light_tex[4], inner[12];
  int ninner;
} Zip;

static void zip_update(Ent *e) {
  Zip *z = ST(e, Zip);
  plat_update(e);
  switch (z->step) {
    case 0:
      if (!solid_has_player_rider(e)) break;
      plat_start_shaking(e, 0.1f);
      z->wait = 0.1f;
      z->step = 1;
      break;
    case 1:
      if ((z->wait -= DT) > 0) break;
      z->light = 3;
      z->at = 0;
      z->step = 2;
      break;
    case 2:   /* yield null, then move */
      z->at = approach(z->at, 1, 2 * DT);
      z->percent = ease_sine_in(z->at);
      plat_move_to(e, lerpf(z->start.x, z->target.x, z->percent), lerpf(z->start.y, z->target.y, z->percent));
      if (z->at >= 1) {
        plat_start_shaking(e, 0.2f);
        level_shake(.3f);
        z->wait = 0.5f;
        z->step = 3;
      }
      break;
    case 3:
      if ((z->wait -= DT) > 0) break;
      z->light = 2;
      z->at = 0;
      z->step = 4;
      break;
    case 4:
      z->at = approach(z->at, 1, 0.5f * DT);
      z->percent = 1 - ease_sine_in(z->at);
      plat_move_to(e, lerpf(z->target.x, z->start.x, ease_sine_in(z->at)), lerpf(z->target.y, z->start.y, ease_sine_in(z->at)));
      if (z->at >= 1) {
        plat_start_shaking(e, 0.2f);
        z->light = 1;
        z->wait = 0.5f;
        z->step = 5;
      }
      break;
    case 5:
      if ((z->wait -= DT) > 0) break;
      z->step = 0;
      break;
  }
}
static float fmod1(float x) { return x - floorf(x); }
static void zip_render(Ent *e) {
  Zip *z = ST(e, Zip);
  float x = e->x + e->shakex, y = e->y + e->shakey, w = e->cw, h = e->ch;
  gfx_rect(x + 1, y + 1, w - 2, h - 2, 0, 255);
  int num = 1;
  float num2 = 0;
  for (int i = 4; i <= h - 4; i += 8) {
    int num3 = num;
    for (int j = 4; j <= w - 4; j += 8) {
      int idx = (int)(fmod1((num2 + num * z->percent * PI_F * 4) / (PI_F / 2)) * z->ninner);
      if (idx >= z->ninner) idx = z->ninner - 1;
      uint16_t t = z->inner[idx];
      int sx = 0, sy = 0, sw = 12, sh = 12;
      float zx = 0, zy = 0;
      if (j <= 4) zx = 2, sx = 2, sw -= 2;
      else if (j >= w - 4) zx = -2, sw -= 2;
      if (i <= 4) zy = 2, sy = 2, sh -= 2;
      else if (i >= h - 4) zy = -2, sh -= 2;
      /* DrawCentered of the sub-rect */
      gfx_tex_part(t, x + j + zx - sw / 2.f, y + i + zy - sh / 2.f, sx, sy, sw, sh, 0, num < 0 ? 0x8410 : 0xFFFF, 255);
      num = -num;
      num2 += PI_F / 3;
    }
    if (num3 == num) num = -num;
  }
  int cw = (int)(w / 8), ch = (int)(h / 8);
  if (((int)w | (int)h) & 7)
    for (int k = 0; k < cw; k++)
      for (int l = 0; l < ch; l++) {
        int a = k == 0 ? 0 : k != cw - 1 ? 1 : 2, b = l == 0 ? 0 : l != ch - 1 ? 1 : 2;
        if (a != 1 || b != 1) gfx_tex_part(z->block, x + k * 8, y + l * 8, a * 8, b * 8, 8, 8, 0, 0xFFFF, 255);
      }
  else
    gfx_nine(z->block, x, y, (int)w, (int)h, true, 0xFFFF, 255);   /* the same edges, in one command */
  gfx_tex(z->light_tex[z->light], x + w / 2 - 4, y, 0, 0xFFFF, 255);
}

/* the path between the two places (ZipMoverPathRenderer, depth 5000) */
typedef struct { Ent *zip; } ZipPath;
/* the two ropes and their teeth (Draw.Line each): drawn into the strips by one command (a command a line filled the
 * frame's in 1A's rooms of zip movers, and what came after them was not drawn) */
static void zip_ropes(uint16_t *strip, int y0, int y1, Ent *zip, bool black) {
  Zip *z = ST(zip, Zip);
  V2 off = v2(0, black ? 1 : 0);
  V2 from = v2(z->start.x + zip->cw / 2, z->start.y + zip->ch / 2), to = v2(z->target.x + zip->cw / 2, z->target.y + zip->ch / 2);
  V2 d = v2norm(v2sub(to, from)), perp = v2(d.y, -d.x);
  V2 v1 = v2mul(perp, 3), v2_ = v2mul(perp, -4);
  uint16_t rope = black ? 0 : rgb(0x663931), light = black ? 0 : rgb(0x9B6157);
  blit_line(strip, y0, y1, from.x + v1.x + off.x, from.y + v1.y + off.y, to.x + v1.x + off.x, to.y + v1.y + off.y, rope, 255);
  blit_line(strip, y0, y1, from.x + v2_.x + off.x, from.y + v2_.y + off.y, to.x + v2_.x + off.x, to.y + v2_.y + off.y, rope, 255);
  float len = v2len(v2sub(to, from));
  for (float n = 4 - fmodf(z->percent * PI_F * 8, 4); n < len; n += 4) {
    V2 a = v2add(v2add(v2add(from, v1), perp), v2mul(d, n));
    V2 b = v2sub(v2add(to, v2_), v2mul(d, n));
    blit_line(strip, y0, y1, a.x + off.x, a.y + off.y, a.x + d.x * 2 + off.x, a.y + d.y * 2 + off.y, light, 255);
    blit_line(strip, y0, y1, b.x + off.x, b.y + off.y, b.x - d.x * 2 + off.x, b.y - d.y * 2 + off.y, light, 255);
  }
}
static void zip_ropes_black(uint16_t *strip, int y0, int y1, void *ctx) { zip_ropes(strip, y0, y1, ctx, true); }
static void zip_ropes_color(uint16_t *strip, int y0, int y1, void *ctx) { zip_ropes(strip, y0, y1, ctx, false); }
static void zip_cogs(Zip *z, Ent *zip, V2 off, bool black) {
  V2 from = v2(z->start.x + zip->cw / 2, z->start.y + zip->ch / 2), to = v2(z->target.x + zip->cw / 2, z->target.y + zip->ch / 2);
  float top = fminf(from.y, to.y) + off.y - 8, bot = fmaxf(from.y, to.y) + off.y + 8;   /* the ropes' rows, teeth and all */
  if (fmaxf(from.x, to.x) + 8 >= g_camx && fminf(from.x, to.x) - 8 < g_camx + VIEW_W)
    gfx_custom(black ? zip_ropes_black : zip_ropes_color, zip, (int)floorf(top) - g_camy, (int)ceilf(bot) - g_camy + 1);
  float rot = z->percent * PI_F * 2;
  uint8_t f = black ? GF_SILHOUETTE : 0;
  gfx_tex_ex(z->cog, from.x + off.x, from.y + off.y, 6, 6, 1, 1, rot, black ? 0 : 0xFFFF, 255, f);
  gfx_tex_ex(z->cog, to.x + off.x, to.y + off.y, 6, 6, 1, 1, rot, black ? 0 : 0xFFFF, 255, f);
}
static void zippath_render(Ent *e) {
  Ent *zip = ST(e, ZipPath)->zip;
  if (!zip->cls) return;
  Zip *z = ST(zip, Zip);
  zip_cogs(z, zip, v2(0, 1), true);
  zip_cogs(z, zip, v2(0, 0), false);
  gfx_rect(floorf(zip->x + zip->shakex - 1), floorf(zip->y + zip->shakey - 1), zip->cw + 2, zip->ch + 2, 0, 255);
}
static const EntClass ZIPPATH = {.size = sizeof(ZipPath), .name = "zipPath", .render = zippath_render};
static const EntClass ZIP = {.size = sizeof(Zip), .name = "zipMover", .update = zip_update, .render = zip_render, .kind = KIND_SOLID};
static void new_zip(const EData *d) {
  Ent *e = ent_new(&ZIP, d->x, d->y);
  if (!e) return;
  ent_box(e, EA(d, zipMover, width), EA(d, zipMover, height), 0, 0);
  e->depth = -9999;
  Zip *z = ST(e, Zip);
  z->start = v2(d->x, d->y);
  z->target = ed_node(d, 0);
  z->light = 1;
  z->cog = tex_by_name("objects/zipmover/cog");
  z->block = tex_by_name("objects/zipmover/block");
  char p[40];
  for (int i = 0; i < 4; i++) path2(p, "objects/zipmover/light", NULL, i), z->light_tex[i] = tex_by_name(p);
  for (int i = 0; i < 12; i++) path2(p, "objects/zipmover/innercog", NULL, i), z->inner[i] = tex_by_name(p);
  z->ninner = 12;
  Ent *path = ent_new(&ZIPPATH, 0, 0);
  if (path) {
    path->depth = 5000;
    path->collidable = 0;
    ST(path, ZipPath)->zip = e;
  }
}

/* ---------------------------------------------------------------- tile blocks */
/* the tiles of blocks come from one pool: the rooms in slot 0 take them from its start, in slot 1 from its end */
TileQ g_tile_pool[TILE_POOL];
static int tile_pool_n[2];
static TileQ *pool_tiles(int slot, int n) {
  if (tile_pool_n[0] + tile_pool_n[1] + n > TILE_POOL) return NULL;
  tile_pool_n[slot] += n;
  return slot ? g_tile_pool + TILE_POOL - tile_pool_n[1] : g_tile_pool + tile_pool_n[0] - n;
}
TileQ *blocks_new_tiles(int n) { return pool_tiles(g_level.room_slot, n); }
void blocks_free_tiles(int slot) { tile_pool_n[slot] = 0; }
enum { BF_OVERLAY = 0x80 };   /* Block.flags: the tiles blend into the room's (TileGrid of the level's grid) */
typedef struct {
  char type;
  uint8_t w, h, mode;
  uint8_t flags;      /* fallingBlock: climbFall; dashBlock: permanent, canDash; BF_OVERLAY */
  int step;
  float timer, speed, alpha;
  bool triggered;
  uint32_t hash;
  TileQ *q;
} Block;
/* its tiles from the pool; with too many in the two rooms, it tries again when drawn, once in the level's room */
static bool block_tiles(Ent *e) {
  Block *b = ST(e, Block);
  if (e->room != g_level.room_slot || !(b->q = pool_tiles(e->room, b->w * b->h))) return false;
  if (b->flags & BF_OVERLAY) tiles_overlay(b->type, (int)(e->x / 8), (int)(e->y / 8), b->w, b->h, b->q);
  else tiles_box(b->type, b->w, b->h, b->q);
  return true;
}

static void block_render(Ent *e) {
  Block *b = ST(e, Block);
  if (!b->q && !block_tiles(e)) return;
  tiles_draw(b->q, b->type, b->w, b->h, e->x + e->shakex, e->y + e->shakey, 0xFFFF, (uint8_t)(b->alpha * 255));
}

/* FallingBlock */
static bool fall_player_check(Ent *e, Block *b) { return (b->flags & 1) ? solid_has_player_rider(e) : solid_has_player_on_top(e); }
static bool fall_wait_check(Ent *e, Block *b) {
  if (b->triggered || fall_player_check(e, b)) return true;
  if (b->flags & 1) {
    Ent *p = level_player_ent();
    return p && (collide_ent_at(e, e->x - 1, e->y, p) || collide_ent_at(e, e->x + 1, e->y, p));
  }
  return false;
}
static void falling_update(Ent *e) {
  Block *b = ST(e, Block);
  plat_update(e);
  switch (b->step) {
    case 0:
      if (b->triggered || fall_player_check(e, b)) b->step = 1;
      break;
    case 1:   /* shake 0.2 s */
      plat_start_shaking(e, 0);
      b->timer = 0.2f;
      b->step = 2;
      break;
    case 2:
      if ((b->timer -= DT) > 0) break;
      b->timer = 0.4f;
      b->step = 3;
      /* fall through */
    case 3:
      if (b->timer > 0 && fall_wait_check(e, b)) {
        b->timer -= DT;
        break;
      }
      plat_stop_shaking(e);
      for (int i = 2; i < e->cw; i += 4) particles_emit(PL_MID, &P_FallingBlock_P_FallDustB, 2, v2(e->x + i, e->y), v2(4, 4), PI_F / 2);
      b->speed = 0;
      b->step = 4;
      break;
    case 4: {
      Room *rm = g_level.room;
      b->speed = approach(b->speed, 160, 500 * DT);
      if (plat_move_v_collide_solids(e, b->speed * DT, true)) {
        level_dir_shake(v2(0, 1), 0.3f);
        plat_start_shaking(e, 0);
        b->timer = 0.2f;
        b->step = 5;
        break;
      }
      if (e_top(e) > rm->y + rm->h + 16 || (e_top(e) > rm->y + rm->h - 1 && collide_solid(e, e->x, e->y + 1))) {
        e->collidable = e->visible = 0;
        plat_static_movers_destroy(e);
        ent_remove(e);
      }
      break;
    }
    case 5:
      if ((b->timer -= DT) > 0) break;
      plat_stop_shaking(e);
      if (tiles_solid_rect(e_left(e), e_bottom(e), e_right(e), e_bottom(e) + 1)) {
        b->step = 7;
        e->safe = 1;
        break;
      }
      b->step = 6;
      b->timer = 0;
      /* fall through */
    case 6:
      if ((b->timer -= DT) > 0) break;
      if (collide_solid(e, e->x, e->y + 1) || collide_first_jumpthru_outside(e, e->x, e->y + 1)) {
        b->timer = 0.1f;
        break;
      }
      b->step = 1;
      break;
    default: break;
  }
}
static void falling_trigger(Ent *e, Ent *m) {
  (void)m;
  ST(e, Block)->triggered = true;
}
static void block_shake(Ent *e, V2 a) { plat_static_movers_shake(e, a); }
static const EntClass FALLING = {.size = sizeof(Block), .name = "fallingBlock", .update = falling_update, .render = block_render,
                                 .kind = KIND_SOLID,
                                 .more = &(const EntMore){.on_staticmover_trigger = falling_trigger, .on_shake = block_shake}};

static Ent *new_block(const EntClass *cls, const EData *d, float w, float h, char type, bool overlay) {
  Ent *e = ent_new(cls, d->x, d->y);
  if (!e) return NULL;
  ent_box(e, w, h, 0, 0);
  Block *b = ST(e, Block);
  b->type = type;
  b->w = (uint8_t)(w / 8), b->h = (uint8_t)(h / 8);
  b->alpha = 1;
  b->hash = level_entity_hash(d);
  b->flags = overlay ? BF_OVERLAY : 0;
  block_tiles(e);
  return e;
}
/* data.Char("tiletype", '3'): a number in some maps, a letter in others (data.bin keeps those as text) */
static char tile_attr(const EData *d, int off, char kind) {
  if (kind == 's') {
    const char *s = ea_str(d, off);
    return *s ? s[0] : '3';
  }
  return (char)('0' + (int)ea_num(d, off, kind));
}
#define TILE_ATTR(d, ent, attr) tile_attr(d, EA_##ent##_##attr, EK_##ent##_##attr)

/* DashBlock */
static int dash_block_hit(Ent *e, Player *p, V2 dir);
static const EntClass DASHBLOCK = {.size = sizeof(Block), .name = "dashBlock", .update = plat_update, .render = block_render,
                                   .kind = KIND_SOLID, .more = &(const EntMore){.on_dash_collide = dash_block_hit}};
void level_break_dash_block(Ent *e, V2 dir) {
  (void)dir;
  Block *b = ST(e, Block);
  for (int i = 0; i < b->w; i++)
    for (int j = 0; j < b->h; j++) level_debris(e->x + 4 + i * 8, e->y + 4 + j * 8, b->type, player_center(&g_player));
  e->collidable = 0;
  if (b->flags & 1) level_set_do_not_load(b->hash);
  ent_remove(e);
}
static int dash_block_hit(Ent *e, Player *p, V2 dir) {
  Block *b = ST(e, Block);
  if (!(b->flags & 2) && p->state != ST_REDDASH && p->state != ST_SUMMITLAUNCH) return DASH_NORMAL;
  level_break_dash_block(e, dir);
  return DASH_REBOUND;
}

/* FakeWall, CoverupWall, ExitBlock: tiles that fade */
static void fakewall_update(Ent *e) {
  Block *b = ST(e, Block);
  Ent *p = level_player_ent();
  if (b->mode == 1) {   /* fading */
    b->alpha = approach(b->alpha, 0, 2 * DT);
    if (b->alpha <= 0) ent_remove(e);
    return;
  }
  if (p && collide_ent_at(e, e->x, e->y, p) && g_player.state != ST_DREAMDASH) {
    level_set_do_not_load(b->hash);
    b->mode = 1;
  }
}
static const EntClass FAKEWALL = {.size = sizeof(Block), .name = "fakeWall", .update = fakewall_update, .render = block_render};
static void coverup_update(Ent *e) {
  Block *b = ST(e, Block);
  Ent *p = level_player_ent();
  bool in = p && collide_ent_at(e, e->x, e->y, p);
  b->alpha = approach(b->alpha, in ? 0 : 1, 4 * DT);
}
static const EntClass COVERUP = {.size = sizeof(Block), .name = "coverupWall", .update = coverup_update, .render = block_render};
static void exit_update(Ent *e) {
  Block *b = ST(e, Block);
  Ent *p = level_player_ent();
  if (!e->collidable) {
    e->collidable = 1;
    bool in = p && collide_ent_at(e, e->x, e->y, p);
    e->collidable = !in;
    if (!in) b->mode = 1;
  }
  if (b->mode == 1) b->alpha = approach(b->alpha, 1, 2 * DT);
  plat_update(e);
}
static const EntClass EXITBLOCK = {.size = sizeof(Block), .name = "exitBlock", .update = exit_update, .render = block_render, .kind = KIND_SOLID};

/* Debris: bits of broken blocks */
typedef struct { uint16_t tex; V2 speed; float life, rot, spin; } Debris;
static void debris_update(Ent *e) {
  Debris *d = ST(e, Debris);
  d->speed.y = approach(d->speed.y, 200, 600 * DT);
  d->speed.x = approach(d->speed.x, 0, 200 * DT);
  e->x += d->speed.x * DT, e->y += d->speed.y * DT;
  d->rot += d->spin * DT;
  if ((d->life -= DT) <= 0) ent_remove(e);
}
static void debris_render(Ent *e) {
  Debris *d = ST(e, Debris);
  gfx_tex_ex(d->tex, e->x, e->y, 4, 4, 1, 1, d->rot, 0xFFFF, (uint8_t)(255 * fminf(1, d->life * 2)), 0);
}
static const EntClass DEBRIS = {.size = sizeof(Debris), .name = "debris", .update = debris_update, .render = debris_render};
void level_debris(float x, float y, char type, V2 from) {
  Ent *e = ent_new(&DEBRIS, x, y);
  if (!e) return;
  e->collidable = 0;
  e->depth = -9000;
  Debris *d = ST(e, Debris);
  char p[16], t[2] = {type, 0};
  path2(p, "debris/", t, -1);
  d->tex = tex_by_name(p);
  if (d->tex == 0xFFFF) d->tex = tex_by_name("debris/1");
  V2 dir = v2norm(v2sub(v2(x, y), from));
  float sp = rnd_rangef(60, 120);
  d->speed = v2add(v2mul(dir, sp), v2(0, -60));
  d->life = rnd_rangef(1, 1.5f);
  d->spin = rnd_rangef(-6, 6);
}

/* ---------------------------------------------------------------- simple scenery */
typedef struct { Sprite spr; float t; uint16_t tex; uint8_t flag; } Simple;
static void simple_update(Ent *e) {
  Simple *s = ST(e, Simple);
  s->t += DT;
  spr_update(&s->spr);
}
static void simple_render(Ent *e) { spr_draw(&ST(e, Simple)->spr, e->x, e->y); }
static const EntClass SIMPLE_SPRITE = {.size = sizeof(Simple), .name = "sprite", .update = simple_update, .render = simple_render};
static Ent *new_sprite_ent(const EData *d, int bank, int anim, int depth) {
  Ent *e = ent_new(&SIMPLE_SPRITE, d->x, d->y);
  if (!e) return NULL;
  e->collidable = 0;
  e->depth = (int16_t)depth;
  spr_init(&ST(e, Simple)->spr, bank);
  if (anim >= 0) spr_play(&ST(e, Simple)->spr, anim, true);
  return e;
}

/* Checkpoint's background picture */
static void checkpoint_render(Ent *e) { gfx_tex_ex(ST(e, Simple)->tex, e->x, e->y, 12, 24, 1, 1, 0, 0xFFFF, 255, 0); }
static const EntClass CHECKPOINT = {.size = sizeof(Simple), .name = "checkpoint", .render = checkpoint_render};

/* LightBeam */
typedef struct { float w, h, rot, alpha, t; } Beam;
static void beam_update(Ent *e) {
  Beam *b = ST(e, Beam);
  b->t += DT;
  Ent *p = level_player_ent();
  float target = 1;
  if (p) {
    float d = fabsf(p->x - e->x);
    target = clampf((d - 8) / 32, 0, 1) * 0 + 1;
  }
  b->alpha = approach(b->alpha, target, DT);
}
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC push_options
#pragma GCC optimize("O2")   /* drawn every frame, over the whole view */
#endif
/* add565 (gfx.c): a saturating add */
static inline __attribute__((always_inline)) uint16_t beam_add(uint16_t d, uint16_t s) {
  uint32_t x = (d | (uint32_t)d << 16) & 0x07E0F81Fu, y = (s | (uint32_t)s << 16) & 0x07E0F81Fu;
  uint32_t t = x + y, ov = t & 0x08010020u;
  uint32_t lo = ov & 0x00010020u, hi = ov & 0x08000000u;
  t |= (lo - (lo >> 5)) | (hi - (hi >> 6));
  t &= 0x07E0F81Fu;
  return (uint16_t)(t | t >> 16);
}
/* stripes of util/lightbeam stretched along the beam, every 4 px across it, drawn by one layer, not one affine blit
 * each: those blits each worked out the texture's whole palette again for their alpha, and each pixel's place with
 * floats (the Golden Ridge's big beam: 40 stripes over the whole view, the frame took twice as long). Turned a
 * quarter turn at a time (every map's beams), a stripe is texels along one axis and 4 px across the other: the same
 * pixels, its texels' colors faded as the blits' palettes (GF_ADD: the premultiplied color tinted, times the alpha,
 * added). Other turns: the blits, from here. */
static void beam_layer(uint16_t *strip, int sy0, int sy1, void *ctx) {
  Ent *e = ctx;
  Beam *b = ST(e, Beam);
  Tex t;
  if (!tex_get(T_util_lightbeam, &t)) return;
  const float sxs = b->h / 80.f, sys = 4, oy = 0.5f, rot = b->rot + PI_F / 2;
  int q = (int)floorf(rot / (PI_F / 2) + 0.5f);
  bool axis = fabsf(rot - q * (PI_F / 2)) < 1e-3f && t.h == 1 && t.w <= 256 && t.scale <= 1 && sxs > 0;
  q &= 3;
  uint8_t idx[256];
  int n;
  const uint16_t *pal = pal_colors(t.pal, &n);
  if (axis) tex_row(&t, 0, idx);
  /* texture x (u) runs along view y for odd quarter turns, along x for even ones, its sign sp; texture y (v) across;
   * pixel (along p, across c) at p * pstride + c * cstride in the strip, rows from sy0 */
  bool along_y = q & 1;
  float sp = q <= 1 ? 1.f : -1.f, sc = q == 0 || q == 3 ? 1.f : -1.f, isx = 1 / sxs, isy = 1 / sys;
  int pstride = along_y ? VIEW_W : 1, cstride = along_y ? 1 : VIEW_W;
  int pclip_lo = along_y ? sy0 : 0, pclip_hi = along_y ? sy1 : VIEW_W, cclip_lo = along_y ? 0 : sy0, cclip_hi = along_y ? VIEW_W : sy1;
  float cr = cosf(b->rot), sr = sinf(b->rot);
  /* how far from the origin the texels can be along, and the row across (a trimmed texture's offsets too) */
  float reach_p = sxs * (fabsf((float)t.ox) + t.w + 1) + 1, reach_c = sys * (fabsf((float)t.oy) + 2) + 2;
  for (float x = 0; x < b->w; x += 4) {
    /* gfx_tex_ex's place (e + (cos, sin)(rot) * (x - w / 2)) */
    float px = e->x + cr * (x - b->w / 2), py = e->y + sr * (x - b->w / 2);
    float apx = floorf(px + 0.5f) - g_camx, apy = floorf(py + 0.5f) - g_camy;
    float a0 = along_y ? apy : apx, c0 = along_y ? apx : apy;   /* the origin along and across */
    /* along: the texels (u = floor(a * isx) - ox0, from 0 to w); across: the 4 px whose v is the texture's row
     * (floor(c * isy + oy) - oy0 == 0) */
    int p_lo = (int)floorf(a0 - reach_p), p_hi = (int)ceilf(a0 + reach_p);
    int c_lo = (int)floorf(c0 - reach_c), c_hi = (int)ceilf(c0 + reach_c);
    if (p_lo < pclip_lo) p_lo = pclip_lo;
    if (p_hi > pclip_hi) p_hi = pclip_hi;
    if (c_lo < cclip_lo) c_lo = cclip_lo;
    if (c_hi > cclip_hi) c_hi = cclip_hi;
    if (axis && (p_lo >= p_hi || c_lo >= c_hi)) continue;
    uint8_t al = (uint8_t)(b->alpha * 0.5f * (sinf(b->t * 2 + x * 0.2f) * 0.25f + 0.75f) * 120);
    if (!al) continue;
    if (!axis) {
      blit_tex_ex(strip, sy0, sy1, T_util_lightbeam, px, py, 0, oy, sxs, sys, rot, 0xFFF7, al, GF_ADD);
      continue;
    }
    int ga = al + (al >> 7), ca = c_hi, cb = c_lo;
    for (int c = c_lo; c < c_hi; c++)
      if ((int)floorf(sc * (c + 0.5f - c0) * isy + oy) - t.oy == 0) ca = c < ca ? c : ca, cb = c + 1;
    if (ca >= cb) continue;
    int last = -1;
    uint16_t col = 0;
    for (int p = p_lo; p < p_hi; p++) {
      int u = (int)floorf(sp * (p + 0.5f - a0) * isx) - t.ox;
      if ((unsigned)u >= t.w || !idx[u]) continue;
      if (idx[u] != last) {   /* prep's tables: tinted by 0xFFF7 (r, g as they are, b * 24 / 32), faded by ga */
        uint16_t v = idx[u] <= n ? pal[idx[u] - 1] : 0;
        last = idx[u];
        col = (uint16_t)(((v >> 11) * ga) >> 8 << 11 | (((v >> 5) & 63) * ga) >> 8 << 5 | ((((v & 31) * 24) >> 5) * ga) >> 8);
      }
      uint16_t *d = strip + (p * pstride + ca * cstride - sy0 * VIEW_W);
      for (int c = ca; c < cb; c++, d += cstride) *d = beam_add(*d, col);
    }
  }
}
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif
static void beam_render(Ent *e) {
  Beam *b = ST(e, Beam);
  Tex t;
  if (!tex_get(T_util_lightbeam, &t)) return;   /* (loaded now: nothing loads while strips are drawn) */
  /* the view rows the beam can cross: its corners, a stripe's 4 px around, and room for the camera's shake */
  float c = cosf(b->rot), s = sinf(b->rot), lo = 1e9f, hi = -1e9f;
  for (int i = 0; i < 4; i++) {
    float across = i & 1 ? b->w / 2 + 4 : -b->w / 2 - 4, along = i & 2 ? b->h + 4 : -4;
    float y = e->y + s * across + c * along;
    lo = fminf(lo, y), hi = fmaxf(hi, y);
  }
  gfx_custom(beam_layer, e, (int)floorf(lo - g_level.cam.y) - 8, (int)ceilf(hi - g_level.cam.y) + 8);
}
static const EntClass BEAM = {.size = sizeof(Beam), .name = "lightbeam", .update = beam_update, .render = beam_render};

/* ---------------------------------------------------------------- triggers */
/* (small: a room can have dozens, and two rooms are alive during a transition) */
typedef struct { V2 a; } Trig;   /* CameraOffsetTrigger's offset; ChangeRespawnTrigger's target */
typedef struct { V2 a; float b; uint8_t mode; bool xonly, yonly; } TrigTarget;
static void camoff_enter(Ent *e, Player *p) {
  (void)p;
  Trig *t = ST(e, Trig);
  g_level.cam_offset = v2(t->a.x * 48, t->a.y * 32);
}
static float pos_lerp(Ent *e, Player *p, int mode) {
  float px = e_cxm(p->ent), py = e_cym(p->ent);
  float l = e_left(e), r = e_right(e), t = e_top(e), b = e_bottom(e), cx = e_cxm(e), cy = e_cym(e);
#define CMAP(v, a, bb) clampf(((v) - (a)) / ((bb) - (a)), 0, 1)
  switch (mode) {
    case 1: return CMAP(px, l, r);
    case 2: return CMAP(px, r, l);
    case 3: return CMAP(py, t, b);
    case 4: return CMAP(py, b, t);
    case 5: return fminf(CMAP(px, l, cx), CMAP(px, r, cx));
    case 6: return fminf(CMAP(py, t, cy), CMAP(py, b, cy));
    default: return 1;
  }
#undef CMAP
}
static void camtarget_stay(Ent *e, Player *p) {
  TrigTarget *t = ST(e, TrigTarget);
  p->cam_anchor = v2sub(t->a, v2(160, 90));
  float l = clampf(t->b * pos_lerp(e, p, t->mode), 0, 1);
  p->cam_anchor_lerp = v2(l, l);
  p->cam_anchor_ignore_x = t->yonly;
  p->cam_anchor_ignore_y = t->xonly;
}
static void camtarget_leave(Ent *e, Player *p) {
  (void)e;
  bool other = false;
  for (int i = 0; i < g_nents; i++) {
    Ent *o = &g_ents[i];
    if (o != e && o->cls && MORE(o->cls)->on_stay == camtarget_stay && o->triggered) other = true;
  }
  if (!other) {
    p->cam_anchor_lerp = v2(0, 0);
  }
}
static void respawn_enter(Ent *e, Player *p) {
  (void)p;
  Trig *t = ST(e, Trig);
  if (!point_solid(t->a.x, t->a.y - 4)) {
    g_session.rx = (int16_t)t->a.x, g_session.ry = (int16_t)t->a.y;
  }
}
static const EntClass CAMOFF = {.size = sizeof(Trig), .name = "cameraOffsetTrigger", .kind = KIND_TRIGGER,
                                .more = &(const EntMore){.on_enter = camoff_enter}};
static const EntClass CAMTARGET = {.size = sizeof(TrigTarget), .name = "cameraTargetTrigger", .kind = KIND_TRIGGER,
                                   .more = &(const EntMore){.on_stay = camtarget_stay, .on_leave = camtarget_leave}};
static const EntClass RESPAWN = {.size = sizeof(Trig), .name = "changeRespawnTrigger", .kind = KIND_TRIGGER,
                                 .more = &(const EntMore){.on_enter = respawn_enter}};
static const EntClass KILLBOX = {.size = sizeof(int), .name = "killbox", .kind = KIND_KILLBOX};

/* HeartGem.removeCameraTriggers: every CameraOffsetTrigger goes */
void level_remove_camera_offset_triggers(void) {
  for (int i = 0; i < g_nents; i++)
    if (g_ents[i].cls == &CAMOFF && g_ents[i].dead != 1) ent_remove(&g_ents[i]);
}

static Ent *new_trigger(const EntClass *cls, const EData *d, float w, float h) {
  Ent *e = ent_new(cls, d->x, d->y);
  if (!e) return NULL;
  ent_box(e, w, h, 0, 0);
  e->visible = 0;
  return e;
}

bool trig_create(const EData *d) {
  Ent *e;
  if (berry_trigger(d)) return true;
  if (trigs_ch1(d) || trigs_ch2(d) || trigs_ch3(d) || trigs_ch4(d) || trigs_ch5(d) || trigs_ch6(d) || trigs_ch7(d) ||
      trigs_ch8(d) || trigs_ch9(d))
    return true;
  switch (d->type) {
    case TT_cameraOffsetTrigger:
      e = new_trigger(&CAMOFF, d, EA(d, cameraOffsetTrigger, width), EA(d, cameraOffsetTrigger, height));
      if (e) ST(e, Trig)->a = v2(EA(d, cameraOffsetTrigger, cameraX), EA(d, cameraOffsetTrigger, cameraY));
      return true;
    case TT_cameraTargetTrigger: {
      e = new_trigger(&CAMTARGET, d, EA(d, cameraTargetTrigger, width), EA(d, cameraTargetTrigger, height));
      if (!e) return true;
      TrigTarget *t = ST(e, TrigTarget);
      t->a = ed_node(d, 0);
      t->b = EA(d, cameraTargetTrigger, lerpStrength);
      const char *m = EAS(d, cameraTargetTrigger, positionMode);
      static const char *modes[] = {"NoEffect", "LeftToRight", "RightToLeft", "TopToBottom", "BottomToTop", "HorizontalCenter", "VerticalCenter"};
      t->mode = 0;
      for (int i = 0; i < 7; i++)
        if (!strcmp(m, modes[i])) t->mode = (uint8_t)i;
      t->xonly = EAB(d, cameraTargetTrigger, xOnly);
      t->yonly = EAB(d, cameraTargetTrigger, yOnly);
      return true;
    }
    case TT_changeRespawnTrigger: {
      e = new_trigger(&RESPAWN, d, EA(d, changeRespawnTrigger, width), EA(d, changeRespawnTrigger, height));
      if (!e) return true;
      V2 target = d->nnodes ? ed_node(d, 0) : e_center(e);
      ST(e, Trig)->a = level_closest_spawn(target);
      if (d->nnodes) ST(e, Trig)->a = target;
      return true;
    }
  }
  return false;
}

/* ---------------------------------------------------------------- the factory */
static uint16_t tex_by_name(const char *path) { return res_tex_by_name(path); }

/* each chapter's own entities (src/chN.c); absent files: none */
WEAK bool ents_ch0(const EData *d) { (void)d; return false; }
WEAK bool ents_ch1(const EData *d) { (void)d; return false; }
WEAK bool ents_ch8(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch1(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch8(const EData *d) { (void)d; return false; }
WEAK bool ents_ch2(const EData *d) { (void)d; return false; }
WEAK bool ents_ch3(const EData *d) { (void)d; return false; }
WEAK bool ents_ch4(const EData *d) { (void)d; return false; }
WEAK bool ents_ch5(const EData *d) { (void)d; return false; }
WEAK bool ents_ch6(const EData *d) { (void)d; return false; }
WEAK bool ents_ch7(const EData *d) { (void)d; return false; }
WEAK bool ents_ch9(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch2(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch3(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch4(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch5(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch6(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch7(const EData *d) { (void)d; return false; }
WEAK bool trigs_ch9(const EData *d) { (void)d; return false; }

bool ent_create(const EData *d) {
  if (berry_create(d) || props_create(d) || heart_create(d) || cassette_create(d) || dust_create(d) || spinner_create(d)) return true;
  if (ents_ch0(d) || ents_ch1(d) || ents_ch2(d) || ents_ch3(d) || ents_ch4(d) || ents_ch5(d) || ents_ch6(d) ||
      ents_ch7(d) || ents_ch8(d) || ents_ch9(d))
    return true;
  switch (d->type) {
    case ET_player: return true;   /* a spawn point: entities_load has it */
    case ET_jumpThru: new_jumpthru(d); return true;
    case ET_decalSolid: new_dsolid(d); return true;
    case ET_spikesUp: new_spikes(d, DIR_UP); return true;
    case ET_spikesDown: new_spikes(d, DIR_DOWN); return true;
    case ET_spikesLeft: new_spikes(d, DIR_LEFT); return true;
    case ET_spikesRight: new_spikes(d, DIR_RIGHT); return true;
    case ET_spring: new_spring(d, SPRING_FLOOR, EAB(d, spring, playerCanUse)); return true;
    case ET_wallSpringLeft: new_spring(d, SPRING_WALLLEFT, EAB(d, wallSpringLeft, playerCanUse)); return true;
    case ET_wallSpringRight: new_spring(d, SPRING_WALLRIGHT, EAB(d, wallSpringRight, playerCanUse)); return true;
    case ET_refill: new_refill(d); return true;
    case ET_crumbleBlock: new_crumble(d); return true;
    case ET_zipMover: new_zip(d); return true;
    case ET_fallingBlock: {
      Ent *e = new_block(&FALLING, d, EA(d, fallingBlock, width), EA(d, fallingBlock, height),
                         TILE_ATTR(d, fallingBlock, tiletype), false);
      if (!e) return true;
      if (EAB(d, fallingBlock, behind)) e->depth = 5000;
      ST(e, Block)->flags |= EAB(d, fallingBlock, climbFall) ? 1 : 0;
      return true;
    }
    case ET_dashBlock: {
      bool blend = EAB(d, dashBlock, blendin);
      Ent *e = new_block(&DASHBLOCK, d, EA(d, dashBlock, width), EA(d, dashBlock, height), TILE_ATTR(d, dashBlock, tiletype),
                         blend);
      if (!e) return true;
      e->depth = blend ? -10501 : -12999;
      e->safe = 1;
      ST(e, Block)->flags |= (EAB(d, dashBlock, permanent) ? 1 : 0) | (EAB(d, dashBlock, canDash) ? 2 : 0);
      return true;
    }
    case ET_fakeWall: {
      Ent *e = new_block(&FAKEWALL, d, EA(d, fakeWall, width), EA(d, fakeWall, height), TILE_ATTR(d, fakeWall, tiletype), true);
      if (e) e->depth = -13000;
      return true;
    }
    case ET_coverupWall: {
      Ent *e = new_block(&COVERUP, d, EA(d, coverupWall, width), EA(d, coverupWall, height), TILE_ATTR(d, coverupWall, tiletype),
                         true);
      if (e) e->depth = -13000;
      return true;
    }
    case ET_exitBlock: {
      Ent *e = new_block(&EXITBLOCK, d, EA(d, exitBlock, width), EA(d, exitBlock, height), TILE_ATTR(d, exitBlock, tileType), true);
      if (!e) return true;
      e->depth = -13000;
      e->collidable = 0;
      ST(e, Block)->alpha = 0;
      return true;
    }
    case ET_killbox: {
      Ent *e = ent_new(&KILLBOX, d->x, d->y);
      if (e) ent_box(e, EA(d, killbox, width), 32, 0, 0), e->visible = 0;
      return true;
    }
    case ET_checkpoint: {   /* Level.LoadLevel: not falling into a room (but the first) */
      if (g_level.last_intro == INTRO_FALL && strcmp(level_room_name(), "0")) return true;
      g_level.start_position = ed_node(d, 0), g_level.has_start_position = true;   /* + SpawnOffset */
      Ent *e = ent_new(&CHECKPOINT, d->x, d->y);
      if (!e) return true;
      e->depth = 9990;
      e->collidable = 0;
      const char *bg = EAS(d, checkpoint, bg);
      char p[48];
      char num[4] = {(char)('0' + g_session.area), 0};
      path2(p, "objects/checkpoint/bg/", *bg ? bg : num, -1);
      ST(e, Simple)->tex = tex_by_name(p);
      return true;
    }
    case ET_bonfire: {
      const char *mode = EAS(d, bonfire, mode);
      Ent *e = new_sprite_ent(d, SB_campfire, !strcmp(mode, "Lit") ? A_campfire_burn : !strcmp(mode, "Smoking") ? A_campfire_smoking : A_campfire_idle, -5);
      (void)e;
      return true;
    }
    case ET_lightbeam: {
      Ent *e = ent_new(&BEAM, d->x, d->y);
      if (!e) return true;
      e->collidable = 0;
      e->depth = -9998;
      Beam *b = ST(e, Beam);
      b->w = EA(d, lightbeam, width), b->h = EA(d, lightbeam, height);
      b->rot = EA(d, lightbeam, rotation) * PI_F / 180;
      b->alpha = 1;
      return true;
    }
  }
  return false;
}

void entities_load(Room *rm, const uint8_t *blob, int slot) {
  nspawns[slot] = 0;
  tile_pool_n[slot] = 0;
  EData d;
  d.room = rm->index;
  d.rx = (float)rm->x, d.ry = (float)rm->y;
  /* pass 0: the spawn points (LevelData.Spawns), all of them before an entity looks for the closest (a summit
     checkpoint is often listed before its own: 7B's g-00 and g-02); 1: the entities; 2: the triggers */
  for (int pass = 0; pass < 3; pass++) {
    bool trig = pass == 2;
    const uint8_t *p = blob + rd16(blob + 2 * (trig ? RB_TRIGS : RB_ENTS));
    int n = rd16(p);
    p += 2;
    for (int i = 0; i < n; i++) {
      d.type = p[0];
      d.nnodes = p[1];
      d.id = rd16(p + 2);
      d.x = rm->x + (float)rds16(p + 4);
      d.y = rm->y + (float)rds16(p + 6);
      d.attrs = p + 8;
      int size = trig ? trig_attr_size(d.type) : ent_attr_size(d.type);
      d.nodes = d.attrs + size;
      if (!pass) {
        if (d.type == ET_player && nspawns[slot] < MAX_SPAWNS) spawns[slot][nspawns[slot]++] = v2(d.x, d.y);
      } else {
        g_new_eid = (uint16_t)d.id;
        d.trig = trig;
        uint32_t id = level_entity_hash(&d);
        if (!level_do_not_load(id) && !leader_has_id(id)) {   /* what follows the player is there already */
          if (trig) trig_create(&d);
          else ent_create(&d);
        }
        g_new_eid = 0xFFFF;
      }
      p = d.nodes + 4 * d.nnodes;
      if ((uintptr_t)(p - blob) & 1) p++;
    }
  }
  /* what lives as long as the level */
  bool have = false;
  for (int i = 0; i < g_nents; i++)
    if (g_ents[i].cls == &FX) have = true;
  if (!have) {
    Ent *e = ent_new(&FX, 0, 0);
    if (e) e->tags = TAG_GLOBAL | TAG_TRANSITION_UPDATE, e->collidable = 0, e->visible = 0, e->dead = 0;
    static const int32_t depths[3] = {D_BGPARTICLES, D_PARTICLES, D_FGPARTICLES};
    for (int k = 0; k < 3; k++)
      if ((e = ent_new(&PARTS, 0, 0)) != NULL) e->tags = TAG_GLOBAL, e->depth = depths[k], e->collidable = 0, e->dead = 0;
    e = ent_new(&FORMATION, 0, 0);
    if (e) e->tags = TAG_GLOBAL | TAG_FROZEN_UPDATE, e->depth = -1999900, e->collidable = 0, e->dead = 0;
  }
}

int ent_attr_size(int type) { return type < (int)sizeof ENT_ATTR_SIZE ? ENT_ATTR_SIZE[type] : 0; }
int trig_attr_size(int type) { return type < (int)sizeof TRIG_ATTR_SIZE ? TRIG_ATTR_SIZE[type] : 0; }
