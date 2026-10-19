/* Host tests of Chrome Dino, on the game's own source: Chrome's numbers (the jump, the
 * speed, the obstacles' rules, the collision boxes, night, the score's flashes), the
 * calculator's keys and pause menu, and the drawing (sending only the tiles that changed
 * leaves the screen as a full redraw). Then long games with random keys and a player
 * that jumps, looking for anything out of bounds (built with the address and undefined
 * behaviour sanitizers).
 *
 *   make host-test */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define main dino_app_main
#include "../src/main.c"
#undef main

static uint16_t screen[W * H];
static uint64_t pixels_sent;
uint64_t eadk_keyboard_scan(void) { return 0; }
uint32_t eadk_timing_millis(void) { return 0; }
void eadk_timing_msleep(uint32_t ms) { (void)ms; }
void eadk_display_push_rect(eadk_rect_t r, const uint16_t *px) {
  assert(r.width > 0 && r.height > 0 && r.x + r.width <= W && r.y + r.height <= H);
  pixels_sent += (uint64_t)r.width * r.height;
  for (int y = 0; y < r.height; y++)
    for (int x = 0; x < r.width; x++) screen[(y + r.y) * W + x + r.x] = px[y * r.width + x];
}

/* ---- driving the game as main() does, a frame being one tick */
static uint64_t keys_now;
static void step(uint64_t keys) {
  uint64_t hit = keys & ~keys_now, released = keys_now & ~keys;
  keys_now = keys;
  input(keys, hit, released, (float)TICK_MS);
  if (!menu) tick();
}
static void fresh(void) {
  memset(&g, 0, sizeof g);
  memset(&trex, 0, sizeof trex);
  memset(&jump_key, 0, sizeof jump_key), memset(&duck_key, 0, sizeof duck_key);
  menu = MENU_NONE, leave = false, keys_now = 0;
  rng = 0x12345678u;
  runner_init();
}
static uint16_t screen_copy[W * H];
static void check_dirty_equals_full(void) {
  frame();
  memcpy(screen_copy, screen, sizeof screen);
  hashes_valid = false;
  frame();
  assert(!memcmp(screen_copy, screen, sizeof screen));
}

/* Chrome's Trex.updateJump, written again from offline.ts for the jump's heights */
static int chrome_apex(double speed, int hold_ticks) {
  double vel = INITIAL_JUMP_VELOCITY - speed / 10;
  int y = GROUND_Y, apex = y;
  bool min_reached = false;
  for (int t = 0; t < 200; t++) {
    if (t == hold_ticks && min_reached && vel < DROP_VELOCITY) vel = DROP_VELOCITY;  /* key up */
    y += (int)floor(vel + 0.5);
    vel += GRAVITY;
    if (y < GROUND_Y - MIN_JUMP_HEIGHT) min_reached = true;
    if (y < MAX_JUMP_HEIGHT && min_reached && vel < DROP_VELOCITY) vel = DROP_VELOCITY;
    if (y > GROUND_Y) break;
    if (y < apex) apex = y;
  }
  return apex;
}

static void test_start_and_intro(void) {
  fresh();
  assert(!g.playing && trex.x == 0 && trex.y == GROUND_Y && container_w() == TREX_W);
  frame();
  /* only the T-rex shows, in a window of its width */
  bool dark_left = false, dark_right = false;
  for (int y = CANVAS_Y; y < CANVAS_Y + CANVAS_H; y++)
    for (int x = 0; x < W; x++)
      if (screen[y * W + x] == rgb(83)) *(x < TREX_W ? &dark_left : &dark_right) = true;
  assert(dark_left && !dark_right);
  assert(fabs(g.current_speed - 3.84) < 1e-9);  /* 6 x 320 / 600 x 1.2: Chrome's speed on a narrow screen */
  step(KEY(eadk_key_ok));
  assert(g.playing && trex.jumping && !g.activated);
  assert(fabs(g.current_speed - 3.841) < 1e-9);  /* and 0.001 more each frame */
  int ticks = 0;
  while (trex.jumping) step(KEY(eadk_key_ok)), ticks++;
  assert(ticks > 20 && ticks < 60);
  /* landing starts the intro: the canvas widens in 0.4 s and the T-rex walks in */
  assert(g.playing_intro && g.activated);
  step(0);
  int w0 = container_w();
  while (g.playing_intro) step(0);
  assert(container_w() == WIDTH && w0 < WIDTH);
  assert(trex.x >= 22 && trex.x <= 25);  /* a pixel a frame for the intro's 0.4 s, as in Chrome (not its 50) */
  /* no obstacle for 3 seconds */
  int t = 0;
  while (!g.obstacle_count && t < 400) step(0), t++;
  assert(t == 180);  /* the first obstacle once the game has run 3 s (180 frames) */
}

static void test_jump_heights(void) {
  /* a held jump goes to Chrome's height; one let go early is lower */
  for (int hold = 1; hold <= 40; hold += 13) {
    fresh();
    step(KEY(eadk_key_ok));  /* start: the first jump */
    while (trex.jumping) step(KEY(eadk_key_ok));
    for (int i = 0; i < 300; i++) g.obstacle_count = 0, g.running_time = 0, step(0);  /* (no obstacles) */
    double speed = g.current_speed;
    assert(!trex.jumping && !g.crashed);
    step(KEY(eadk_key_up));
    int apex = trex.y, t = 1;
    while (trex.jumping) {
      g.obstacle_count = 0;
      step(t < hold ? KEY(eadk_key_up) : 0);
      if (trex.y < apex) apex = trex.y;
      t++;
    }
    int want = chrome_apex(speed, hold);
    if (abs(apex - want) > 1) printf("hold %d: apex %d, Chrome's %d\n", hold, apex, want);
    assert(abs(apex - want) <= 1);
  }
}

static void test_speed_and_spawns(void) {
  fresh();
  step(KEY(eadk_key_ok));
  bool ptero_slow = false, ptero_seen = false, small_group_slow = false, large_group_slow = false;
  int run = 0, last = -1;
  double prev = g.current_speed;
  for (int i = 0; i < 60 * 300; i++) {
    step(0);
    assert(g.current_speed >= prev && g.current_speed <= MAX_SPEED + ACCELERATION);
    prev = g.current_speed;
    const obstacle *o = g.obstacle_count ? &g.obstacles[g.obstacle_count - 1] : 0;
    if (o && o->x == WIDTH + o->type->width && !o->following_created) {  /* (one spawned this frame) */
      int type = (int)(o->type - TYPE);
      if (type == PTERODACTYL && g.current_speed < 8.5) ptero_slow = true;
      if (type == PTERODACTYL) ptero_seen = true;
      if (type == CACTUS_SMALL && o->size > 1 && g.current_speed < 4) small_group_slow = true;
      if (type == CACTUS_LARGE && o->size > 1 && g.current_speed < 7) large_group_slow = true;
      run = type == last ? run + 1 : 1;
      last = type;
      assert(run <= MAX_OBSTACLE_DUPLICATION);
    }
    assert(g.obstacle_count <= MAX_OBSTACLES);
    for (int k = 0; k < g.obstacle_count; k++) g.obstacles[k].y = -500;  /* (out of the way: they only go by) */
    assert(!g.crashed);
  }
  printf("spawns: pterodactyl before 8.5 %d, seen %d, groups too early %d %d\n", ptero_slow, ptero_seen, small_group_slow,
         large_group_slow);
  assert(!ptero_slow && ptero_seen && !small_group_slow && !large_group_slow);
  assert(fabs(g.current_speed - MAX_SPEED) < 0.01);
}

static void place(int type, int y) {
  obstacle_init(&g.obstacles[0], &TYPE[type], g.current_speed);
  g.obstacles[0].size = 1, g.obstacles[0].width = TYPE[type].width;
  for (int i = 0; i < TYPE[type].nboxes; i++) g.obstacles[0].boxes[i] = TYPE[type].boxes[i];
  g.obstacles[0].x = trex.x + 10;
  g.obstacles[0].y = y;
  g.obstacle_count = 1;
  if (g.running_time <= CLEAR_TIME) g.running_time = CLEAR_TIME + 1;  /* (obstacles count from 3 s on) */
}
static void test_collisions(void) {
  fresh();
  step(KEY(eadk_key_ok));
  while (trex.jumping) step(0);
  for (int i = 0; i < 60; i++) step(0);
  /* the pterodactyl's three heights: the low one is jumped, the middle one ducked under, the high one flies over */
  place(PTERODACTYL, 100);
  assert(check_collision(&g.obstacles[0]));
  trex_set_duck(true);
  assert(check_collision(&g.obstacles[0]));
  trex_set_duck(false);
  place(PTERODACTYL, 75);
  assert(check_collision(&g.obstacles[0]));
  trex_set_duck(true);
  assert(!check_collision(&g.obstacles[0]));
  trex_set_duck(false);
  place(PTERODACTYL, 50);
  assert(!check_collision(&g.obstacles[0]));
  /* cacti are hit standing and ducking */
  place(CACTUS_SMALL, 105);
  assert(check_collision(&g.obstacles[0]));
  trex_set_duck(true);
  assert(check_collision(&g.obstacles[0]));
  trex_set_duck(false);
  /* ...but not from high above */
  trex.y = 20;
  assert(!check_collision(&g.obstacles[0]));
  trex.y = GROUND_Y;
}

static void test_night_and_flash(void) {
  fresh();
  step(KEY(eadk_key_ok));
  bool flashed = false;
  int night_ticks = 0;
  for (int i = 0; i < 60 * 200 && !g.crashed; i++) {
    g.obstacle_count = 0, g.running_time = 0;  /* (no obstacles) */
    step(0);
    if (g.achievement && !g.painted) flashed = true;
    uint32_t score = actual_distance((uint32_t)iceil(g.distance_ran));
    if (g.inverted) night_ticks++;
    if (score >= 700 && score < 710) assert(g.inverted);
    if (score > 1300) break;
  }
  assert(flashed);
  /* 12 seconds of night, the moon shown */
  assert(night_ticks >= 12000 / TICK_MS - 2 && night_ticks <= 12000 / TICK_MS + 3);
}

static void test_game_over_and_restart(void) {
  fresh();
  step(KEY(eadk_key_ok));
  while (trex.jumping) step(0);
  for (int i = 0; i < 60; i++) step(0);
  place(CACTUS_SMALL, 105);
  step(0);
  assert(g.crashed && !g.playing && trex.status == CRASHED);
  assert(g.highest_score == (uint32_t)iceil(g.distance_ran));
  /* OK let go before 1.2 s: nothing; after: a new game */
  step(KEY(eadk_key_ok)), step(0);
  assert(g.crashed);
  for (int i = 0; i < 75; i++) step(0);
  step(KEY(eadk_key_ok)), step(0);
  assert(!g.crashed && g.playing && g.distance_ran < 10);
  /* EXE (Chrome's Enter) at once */
  place(CACTUS_SMALL, 105);
  step(0);
  assert(g.crashed);
  step(KEY(eadk_key_exe)), step(0);
  assert(!g.crashed);
  check_dirty_equals_full();
}

static void test_menu(void) {
  fresh();
  step(KEY(eadk_key_ok));
  for (int i = 0; i < 100; i++) step(0);
  double d = g.distance_ran;
  step(KEY(eadk_key_back));
  assert(menu == MENU_PAUSE);
  for (int i = 0; i < 50; i++) step(0);
  assert(g.distance_ran == d);  /* paused */
  step(KEY(eadk_key_ok));       /* Resume */
  assert(!menu);
  step(KEY(eadk_key_back)), step(0);
  step(KEY(eadk_key_down)), step(0), step(KEY(eadk_key_down)), step(0);  /* Quit game */
  step(KEY(eadk_key_ok)), step(0);
  assert(menu == MENU_QUIT && !leave);
  frame();
  step(KEY(eadk_key_ok)), step(0);  /* No (chosen first) */
  assert(menu == MENU_PAUSE && !leave);
  step(KEY(eadk_key_ok)), step(0);
  step(KEY(eadk_key_left)), step(0);
  step(KEY(eadk_key_ok));
  assert(leave);
  /* Home quits at once; Back quits from the start and from game over */
  fresh();
  step(KEY(eadk_key_home));
  assert(leave);
  fresh();
  step(KEY(eadk_key_back));
  assert(leave);
}

/* a player that jumps when a cactus or a low pterodactyl comes near, and ducks under a middle one */
static uint64_t bot(void) {
  if (!g.obstacle_count) return 0;
  const obstacle *o = &g.obstacles[0];
  int d = o->x - (trex.x + TREX_W);
  int reach = (int)(g.current_speed * 9) + 10;
  if (d > reach || o->x + o->width < trex.x) return 0;
  if (o->type == &TYPE[PTERODACTYL] && o->y == 75) return KEY(eadk_key_down);
  if (o->type == &TYPE[PTERODACTYL] && o->y == 50) return 0;
  return KEY(eadk_key_ok);
}
static void test_long_games(void) {
  int best = 0;
  for (int game = 0; game < 30; game++) {
    fresh();
    rng = 0x9E3779B9u * (uint32_t)(game + 1);
    step(KEY(eadk_key_ok));
    uint32_t r = (uint32_t)game * 7919u + 1;
    for (int i = 0; i < 60 * 240; i++) {
      r = r * 1103515245u + 12345u;
      uint64_t k;
      if (game < 10) {  /* random keys */
        static const int KS[] = {eadk_key_ok, eadk_key_up, eadk_key_down, eadk_key_two, eadk_key_exe, eadk_key_eight, eadk_key_back};
        k = r >> 28 < 6 ? KEY(KS[(r >> 16) % 7]) : 0;
      } else {
        k = bot();
        if (g.crashed) k = (r >> 20) & 1 ? KEY(eadk_key_exe) : 0;
      }
      step(k);
      if (leave) break;
      if (menu && (r >> 24) % 3 == 0) step(KEY(eadk_key_back));
      assert(g.obstacle_count >= 0 && g.obstacle_count <= MAX_OBSTACLES && g.cloud_count <= MAX_CLOUDS);
      assert(trex.y <= GROUND_Y && trex.y > -60);
      if ((i & 255) == 0) frame();
      int s = (int)actual_distance(g.highest_score);
      if (s > best) best = s;
    }
    check_dirty_equals_full();
  }
  printf("long games: the jumping player's best %d\n", best);
  assert(best > 700);  /* (it saw a night) */
}

int main(void) {
  np_lz_unpack(SHEET_LZ, sizeof SHEET_LZ, sheet, sizeof sheet);
  test_start_and_intro();
  test_jump_heights();
  test_speed_and_spawns();
  test_collisions();
  test_night_and_flash();
  test_game_over_and_restart();
  test_menu();
  pixels_sent = 0;
  fresh();
  hashes_valid = false;
  frame();
  uint64_t full = pixels_sent;
  pixels_sent = 0;
  step(KEY(eadk_key_ok));
  frame();
  printf("drawing: %llu pixels for the first screen, %llu for the next frame\n", (unsigned long long)full,
         (unsigned long long)pixels_sent);
  test_long_games();
  printf("Chrome Dino: all tests passed\n");
  return 0;
}
