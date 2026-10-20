/* SPDX-License-Identifier: GPL-3.0-only */
#include "suika.h"
#include "render.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint16_t screen[320*240];
static unsigned pushes;
static void push(int x,int y,int w,int h,const uint16_t *p) {
  assert(x>=0&&y>=0&&w>0&&h>0&&x+w<=320&&y+h<=240&&w*h<=320*8);
  for(int j=0;j<h;++j)memcpy(screen+(y+j)*320+x,p+j*w,(size_t)w*2);
  ++pushes;
}
static void shot(const char *name) {
  FILE *f=fopen(name,"wb");assert(f);fprintf(f,"P6\n320 240\n255\n");
  for(unsigned i=0;i<320*240;++i) {
    unsigned c=screen[i];
    fputc(((c>>11)&31)*255/31,f);fputc(((c>>5)&63)*255/63,f);fputc((c&31)*255/31,f);
  }
  fclose(f);
}
static void body(sk_game *g,int slot,int kind,float x,float y) {
  g->fruit[slot]=(sk_fruit){x,y,0,0,200,(uint8_t)kind,0};++g->count;
}
static void valid(const sk_game *g) {
  unsigned count=0;
  for(unsigned i=0;i<SK_CAPACITY;++i) {
    const sk_fruit *b=&g->fruit[i];if(!b->kind)continue;++count;
    assert(b->kind<=SK_RANKS);
    assert(isfinite(b->x)&&isfinite(b->y)&&isfinite(b->vx)&&isfinite(b->vy));
    assert(b->x>=sk_radius[b->kind]-0.001f&&b->x<=SK_WIDTH-sk_radius[b->kind]+0.001f);
    assert(b->y>=-48&&b->y<=SK_HEIGHT-sk_radius[b->kind]+0.001f);
    assert(fabsf(b->vx)<=8.001f&&fabsf(b->vy)<=8.001f);
  }
  assert(count==g->count&&count<=SK_CAPACITY);
}
static void saves(void) {
  sk_game g,h;uint8_t data[SK_SAVE_BYTES],copy[SK_SAVE_BYTES];
  sk_new(&g,1234,99);body(&g,7,2,80,150);g.started=1;
  sk_encode(&g,data);assert(sk_decode(&h,data,sizeof data));
  assert(h.count==1&&h.fruit[0].kind==2&&h.best==99&&h.started);
  sk_encode(&h,copy);assert(!memcmp(data,copy,sizeof data));
  sk_game before=h;
  assert(!sk_decode(&h,data,sizeof data-1));assert(!memcmp(&before,&h,sizeof h));
  for(unsigned i=0;i<sizeof data;++i) {
    data[i]^=0x80;assert(!sk_decode(&h,data,sizeof data));data[i]^=0x80;
    assert(!memcmp(&before,&h,sizeof h));
  }
  assert(!sk_decode(&h,NULL,0));
  /* Deliberately valid checksum with a semantically invalid fruit kind. */
  data[48]=255;uint32_t hash=2166136261u;
  for(unsigned i=0;i<SK_SAVE_BYTES-4;++i)hash=(hash^data[i])*16777619u;
  for(int i=0;i<4;++i)data[SK_SAVE_BYTES-4+i]=(uint8_t)(hash>>(i*8));
  assert(!sk_decode(&h,data,sizeof data));assert(!memcmp(&before,&h,sizeof h));
}
static void physics(void) {
  sk_game g;sk_new(&g,1,0);
  assert(sk_hold(&g)&&!sk_hold(&g));sk_aim(&g,-500);assert(g.aim>=sk_radius[g.current]);
  assert(sk_drop(&g)&&!sk_drop(&g)&&g.count==1&&!g.hold_used);
  for(int t=0;t<200;++t)sk_step(&g);
  assert(!g.over);valid(&g);
  sk_new(&g,1,0);body(&g,0,3,80,140);body(&g,1,3,80,140);
  sk_step(&g);assert(g.count==1&&g.fruit[0].kind==4&&g.score==6&&g.merges==1);valid(&g);
  sk_new(&g,1,0);body(&g,0,11,78,140);body(&g,1,11,112,140);
  sk_step(&g);assert(g.count==0&&g.score==66&&g.merges==1);
  sk_new(&g,1,0);body(&g,0,2,80,140);body(&g,1,3,80,140);
  sk_step(&g);assert(fabsf(g.fruit[0].x-g.fruit[1].x)>15);valid(&g);
  /* Do not lose just because a newly dropped fruit crosses the line. */
  sk_new(&g,1,0);assert(sk_drop(&g));sk_step(&g);assert(!g.danger);
  /* Continuous danger, not a wrapping byte or a stale capacity flag. */
  sk_new(&g,1,0);body(&g,0,1,30,10);
  for(int t=0;t<179;++t) {g.fruit[0].y=10;g.fruit[0].vy=0;sk_step(&g);assert(!g.over);}
  g.fruit[0].y=10;g.fruit[0].vy=0;sk_step(&g);assert(g.over&&g.danger==180);
  sk_new(&g,1,0);body(&g,0,1,30,10);sk_step(&g);assert(g.danger==1);
  g.fruit[0].y=150;sk_step(&g);assert(g.danger==0);
  /* Exact circular preview: a diagonal edge is not a flat bounding box. */
  sk_new(&g,1,0);g.current=1;body(&g,0,2,80,120);int target;
  float y=sk_landing(&g,90,&target);assert(target==0&&y>108&&y<112);
  sk_new(&g,1,0);g.current=1;body(&g,0,1,140,160);sk_suggest(&g);
  assert(fabsf(g.aim-140)<13&&g.hint);sk_aim(&g,80);assert(!g.hint);
  /* A blocked spawn never adds an overlapping body. */
  sk_new(&g,1,0);body(&g,0,2,g.aim,14);assert(!sk_drop(&g)&&g.count==1&&g.notice);
  /* Bag determinism and exact weights over full bags. */
  sk_game h;sk_new(&g,4242,0);sk_new(&h,4242,0);
  for(int n=0;n<300;++n) {
    assert(g.current==h.current);
    memset(g.fruit,0,sizeof g.fruit);g.count=0;g.cooldown=0;
    memset(h.fruit,0,sizeof h.fruit);h.count=0;h.cooldown=0;
    assert(sk_drop(&g)&&sk_drop(&h));
  }
}
static void stress(void) {
  uint8_t data[SK_SAVE_BYTES];sk_game g,h;unsigned ticks=0;
  for(unsigned seed=1;seed<=12;++seed) {
    sk_new(&g,seed,0);
    for(unsigned t=0;t<2400;++t) {
      if(t%27==0) { sk_aim(&g,(float)((t*37+seed*13)%SK_WIDTH));sk_drop(&g); }
      if(t%191==0)sk_hold(&g);
      if(t%311==0)sk_suggest(&g);
      sk_step(&g);valid(&g);++ticks;
      if(t%17==0) { sk_encode(&g,data);assert(sk_decode(&h,data,sizeof data));valid(&h); }
      if(g.over)sk_new(&g,seed*131+t,g.best);
    }
  }
  printf("Physics stress: %u ticks, save round trips every 17 ticks.\n",ticks);
}
static void screenshots(void) {
  sk_game g;sk_new(&g,73,1284);
  sk_invalidate();sk_render(&g,SK_TITLE,0,false,push);shot("title.ppm");
  body(&g,0,11,49,140);body(&g,1,8,112,154);body(&g,2,7,163,158);
  body(&g,3,6,104,114);body(&g,4,5,151,118);body(&g,5,3,76,96);
  body(&g,6,2,130,95);body(&g,7,1,172,129);g.score=386;g.merges=24;g.highest=11;
  g.held=3;g.current=2;g.aim=77;g.started=1;
  sk_invalidate();sk_render(&g,SK_PLAY,0,false,push);shot("game.ppm");
  uint16_t previous[320*240];memcpy(previous,screen,sizeof screen);
  pushes=0;sk_render(&g,SK_PLAY,0,false,push);assert(pushes==0);
  for(int t=0;t<20;++t)sk_step(&g);
  sk_render(&g,SK_PLAY,0,false,push);memcpy(previous,screen,sizeof screen);
  sk_invalidate();sk_render(&g,SK_PLAY,0,false,push);
  assert(!memcmp(previous,screen,sizeof screen)); /* partial == full render */
  for(unsigned t=0;t<120;++t) {
    if(t%23==0)sk_suggest(&g);
    if(t%23==1)sk_aim(&g,g.aim); /* clearing HINT without moving invalidates it */
    if(t%41==0)sk_hold(&g);
    if(t%29==0)sk_drop(&g);
    if(t%3)sk_step(&g);
    sk_render(&g,SK_PLAY,0,false,push);memcpy(previous,screen,sizeof screen);
    sk_invalidate();sk_render(&g,SK_PLAY,0,false,push);
    assert(!memcmp(previous,screen,sizeof screen));
  }
  sk_render(&g,SK_PAUSE,0,false,push);shot("pause.ppm");
  sk_render(&g,SK_HELP,0,false,push);shot("help.ppm");
  g.over=1;sk_render(&g,SK_RESULT,0,false,push);shot("result.ppm");
  sk_render(&g,SK_SAVE_ERROR,0,true,push);shot("save-error.ppm");
}
int main(void) {
  physics();saves();stress();screenshots();
  printf("Suika: physics, save validation, deterministic generation and dirty rendering passed.\n");
  return 0;
}
