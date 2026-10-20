/* SPDX-License-Identifier: GPL-3.0-only */
#include <eadk.h>
#include "suika.h"
#include "render.h"
#include "../../common/epsilon_app.h"
#include "../../common/epsilon_files.h"

#ifdef __ELF__
const char eadk_app_name[] __attribute__((section(".rodata.eadk_app_name"))) = "Suika Game";
const uint32_t eadk_api_level __attribute__((section(".rodata.eadk_api_level"))) = 0;
#endif
#define KEY(k) (UINT64_C(1) << (k))
#define SAVE_NAME "suika.sav"
static sk_game game;
static uint8_t save_data[SK_SAVE_BYTES];
static uint32_t last_checksum;
static bool save_failed;
/* Instrumentation for the existing NumPlay ARM emulator. */
uint32_t perf_frames, perf_max, perf_slow;

static void push(int x,int y,int w,int h,const uint16_t *p) {
  eadk_display_push_rect((eadk_rect_t){x,y,w,h},p);
}
static uint32_t save_checksum(void) {
  unsigned n=SK_SAVE_BYTES-4;
  return save_data[n] | (uint32_t)save_data[n+1]<<8 | (uint32_t)save_data[n+2]<<16 | (uint32_t)save_data[n+3]<<24;
}
static bool save(void) {
  sk_encode(&game,save_data);
  uint32_t checksum=save_checksum();
  if(checksum==last_checksum&&!save_failed)return true;
  save_failed=!ef_write(SAVE_NAME,save_data,SK_SAVE_BYTES);
  if(!save_failed)last_checksum=checksum;
  return !save_failed;
}
static void load(void) {
  uint32_t len=0;
  const uint8_t *p=ef_read(SAVE_NAME,&len);
  if(!sk_decode(&game,p,len))sk_new(&game,eadk_random(),0);
}
int main(void) {
  np_app_begin();
  load();
  unsigned view=SK_TITLE,selected=0,help_return=SK_TITLE;
  uint64_t previous=eadk_keyboard_scan(),stale=previous;
  uint32_t now=(uint32_t)eadk_timing_millis(),tick_at=now,draw_at=now,save_at=now;
  sk_invalidate();
  for(;;) {
    now=(uint32_t)eadk_timing_millis();
    uint64_t keys=eadk_keyboard_scan();
    stale&=keys;
    uint64_t active=keys&~stale,hit=active&~previous;
    previous=active;
    bool ok=(hit&(KEY(eadk_key_ok)|KEY(eadk_key_exe)))!=0;
    bool back=(hit&KEY(eadk_key_back))!=0;
    if(view!=SK_SAVE_ERROR && keys&(KEY(eadk_key_home)|KEY(eadk_key_on_off))) {
      if(save())break;
      view=SK_SAVE_ERROR;selected=0;
      /* A failed exit remains visible until a fresh OK or Back press. */
      stale=keys;hit=0;ok=back=false;
    }
    if(view==SK_PLAY) {
      if(back) { view=SK_PAUSE;selected=0;save(); }
      else {
        if(hit&KEY(eadk_key_one))sk_hold(&game);
        if(hit&KEY(eadk_key_two))sk_suggest(&game);
        if(ok)sk_drop(&game);
        /* Bound catch-up after a slow frame; resuming never simulates the
         * time spent paused. Input is consumed once, not per catch-up tick. */
        if(now-tick_at>4*SK_TICK_MS)tick_at=now-4*SK_TICK_MS;
        while(now-tick_at>=SK_TICK_MS) {
          int dir=((active&KEY(eadk_key_right))!=0)-((active&KEY(eadk_key_left))!=0);
          if(dir)sk_aim(&game,game.aim+dir*2.0f);
          sk_step(&game);
          tick_at+=SK_TICK_MS;
        }
        if(game.over) { view=SK_RESULT;selected=0;save(); }
        if(now-save_at>=5000) { save();save_at=now; }
      }
    } else {
      tick_at=now;
      bool nav=(hit&(KEY(eadk_key_up)|KEY(eadk_key_down)))!=0;
      if(view==SK_TITLE) {
        if(nav)selected=!selected;
        if(back) { if(save())break;view=SK_SAVE_ERROR; }
        else if(ok&&selected) { help_return=SK_TITLE;view=SK_HELP; }
        else if(ok) {
          if(game.over)sk_new(&game,eadk_random()^now,game.best);
          view=SK_PLAY;selected=0;tick_at=now;
        }
      } else if(view==SK_HELP) {
        if(ok||back) { view=help_return;selected=0; }
      } else if(view==SK_PAUSE) {
        if(hit&KEY(eadk_key_down))selected=(selected+1)%4;
        if(hit&KEY(eadk_key_up))selected=(selected+3)%4;
        if(back||(ok&&selected==0))view=SK_PLAY;
        else if(ok&&selected==1)view=SK_CONFIRM,selected=0;
        else if(ok&&selected==2)help_return=SK_PAUSE,view=SK_HELP;
        else if(ok&&selected==3)view=SK_TITLE,selected=0;
      } else if(view==SK_CONFIRM) {
        if(nav)selected=!selected;
        if(back||(ok&&selected==0))view=SK_PAUSE,selected=0;
        else if(ok) { sk_new(&game,eadk_random()^now,game.best);view=SK_PLAY; }
      } else if(view==SK_RESULT) {
        if(nav)selected=!selected;
        if(back||(ok&&selected==1))view=SK_TITLE,selected=0;
        else if(ok) { sk_new(&game,eadk_random()^now,game.best);view=SK_PLAY; }
      } else if(view==SK_SAVE_ERROR) {
        if(back)break;
        if(ok&&save())break;
        if(hit&KEY(eadk_key_one))view=game.over?SK_RESULT:game.started?SK_PLAY:SK_TITLE,selected=0;
      }
    }
    /* Simulation at 62.5 Hz, screen at up to 31.25 Hz. Menus sleep too. */
    if(now-draw_at>=32||hit||perf_frames==0) {
      uint32_t begin=(uint32_t)eadk_timing_millis();
      sk_render(&game,view,selected,save_failed,push);
      uint32_t spent=(uint32_t)eadk_timing_millis()-begin;
      if(spent>perf_max)perf_max=spent;
      if(spent>32)++perf_slow;
      ++perf_frames;draw_at=now;
    }
    eadk_timing_msleep(4);
  }
  return np_app_end();
}
