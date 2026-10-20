/* SPDX-License-Identifier: GPL-3.0-only
 * Original procedural artwork. No sprites, framebuffer, or heap allocations.
 */
#include "render.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#define C(v) ((uint16_t)((((v) >> 8) & 0xF800) | (((v) >> 5) & 0x07E0) | (((v) >> 3) & 0x1F)))
#define PAPER C(0xFCF4DF)
#define INK C(0x354C34)
#define MUTED C(0x8D8867)
#define GREEN C(0x477842)
#define RED C(0xC64F49)
#define BX 12
#define BY 38
static uint16_t band[320 * 8];
static int x0, y0, x1, y1;
static int16_t dirty_lo[30], dirty_hi[30];
static bool initialized;
static const sk_game *world;
static unsigned page, selection;
static bool failed;
static bool basket_clip;

/* Five columns, seven rows. One tiny alphabet for every page of the game. */
static const uint8_t alphabet[][5] = {
 {0x7e,0x11,0x11,0x11,0x7e},{0x7f,0x49,0x49,0x49,0x36},{0x3e,0x41,0x41,0x41,0x22},
 {0x7f,0x41,0x41,0x22,0x1c},{0x7f,0x49,0x49,0x49,0x41},{0x7f,0x09,0x09,0x09,0x01},
 {0x3e,0x41,0x49,0x49,0x7a},{0x7f,0x08,0x08,0x08,0x7f},{0x00,0x41,0x7f,0x41,0x00},
 {0x20,0x40,0x41,0x3f,0x01},{0x7f,0x08,0x14,0x22,0x41},{0x7f,0x40,0x40,0x40,0x40},
 {0x7f,0x02,0x0c,0x02,0x7f},{0x7f,0x04,0x08,0x10,0x7f},{0x3e,0x41,0x41,0x41,0x3e},
 {0x7f,0x09,0x09,0x09,0x06},{0x3e,0x41,0x51,0x21,0x5e},{0x7f,0x09,0x19,0x29,0x46},
 {0x46,0x49,0x49,0x49,0x31},{0x01,0x01,0x7f,0x01,0x01},{0x3f,0x40,0x40,0x40,0x3f},
 {0x1f,0x20,0x40,0x20,0x1f},{0x3f,0x40,0x38,0x40,0x3f},{0x63,0x14,0x08,0x14,0x63},
 {0x07,0x08,0x70,0x08,0x07},{0x61,0x51,0x49,0x45,0x43},
 {0x3e,0x51,0x49,0x45,0x3e},{0x00,0x42,0x7f,0x40,0x00},{0x62,0x51,0x49,0x49,0x46},
 {0x22,0x41,0x49,0x49,0x36},{0x18,0x14,0x12,0x7f,0x10},{0x27,0x45,0x45,0x45,0x39},
 {0x3c,0x4a,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},{0x36,0x49,0x49,0x49,0x36},
 {0x06,0x49,0x49,0x29,0x1e},
 {0,0x08,0x3e,0x08,0},{0,0x08,0x08,0x08,0},{0,0x60,0x60,0,0},
 {0,0x36,0x36,0,0},{0x20,0x10,0x08,0x04,0x02},{0,0,0x5f,0,0},
 {0x08,0x14,0x22,0x41,0},{0x41,0x22,0x14,0x08,0},{0,0,0x7f,0,0}
};
static void mark(int x, int y, int w, int h) {
  if (x < 0) w += x, x = 0;
  if (y < 0) h += y, y = 0;
  if (x + w > 320) w = 320 - x;
  if (y + h > 240) h = 240 - y;
  if (w <= 0 || h <= 0) return;
  for (int row = y / 8; row <= (y + h - 1) / 8; ++row) {
    if (x < dirty_lo[row]) dirty_lo[row] = (int16_t)x;
    if (x + w > dirty_hi[row]) dirty_hi[row] = (int16_t)(x + w);
  }
}
void sk_invalidate(void) { mark(0, 0, 320, 240); initialized = false; }
static void pixel(int x, int y, uint16_t c) {
  if (basket_clip && (x < BX || x >= BX + SK_WIDTH || y < BY || y >= BY + SK_HEIGHT)) return;
  if (x >= x0 && x < x1 && y >= y0 && y < y1) band[(y - y0) * (x1 - x0) + x - x0] = c;
}
static void rect(int x, int y, int w, int h, uint16_t c) {
  int l = x < x0 ? x0 : x, t = y < y0 ? y0 : y;
  int r = x + w > x1 ? x1 : x + w, b = y + h > y1 ? y1 : y + h;
  if (basket_clip) {
    if (l < BX) l = BX;
    if (r > BX + SK_WIDTH) r = BX + SK_WIDTH;
    if (t < BY) t = BY;
    if (b > BY + SK_HEIGHT) b = BY + SK_HEIGHT;
  }
  if (l >= r || t >= b) return;
  /* Clip once per span instead of branching on every individual pixel. */
  for (int j = t; j < b; ++j) {
    uint16_t *p = band + (j-y0)*(x1-x0) + l-x0;
    for (int i = l; i < r; ++i) *p++ = c;
  }
}
static void roundbox(int x, int y, int w, int h, int radius, uint16_t c) {
  if (y >= y1 || y + h <= y0 || x >= x1 || x + w <= x0) return;
  for (int j = y > y0 ? y : y0; j < y + h && j < y1; ++j) {
    int dy = j < y + radius ? y + radius - j : j >= y + h - radius ? j - (y + h - radius - 1) : 0;
    int inset = dy ? radius - (int)sqrtf((float)(radius * radius - dy * dy)) : 0;
    rect(x + inset, j, w - 2 * inset, 1, c);
  }
}
static void line(int a, int b, int c, int d, uint16_t ink) {
  int dx = abs(c - a), sx = a < c ? 1 : -1, dy = -abs(d - b), sy = b < d ? 1 : -1, err = dx + dy;
  for (;;) {
    pixel(a, b, ink);
    if (a == c && b == d) break;
    int e = 2 * err;
    if (e >= dy) err += dy, a += sx;
    if (e <= dx) err += dx, b += sy;
  }
}
static void circle(int x, int y, int r, uint16_t c, bool outline) {
  if (y + r < y0 || y - r >= y1 || x + r < x0 || x - r >= x1) return;
  for (int j = y - r > y0 ? y - r : y0; j <= y + r && j < y1; ++j) {
    int half = (int)sqrtf((float)(r * r - (j - y) * (j - y)));
    if (outline) {
      pixel(x - half, j, c); pixel(x + half, j, c);
      if (abs(j - y) >= r - 1) rect(x - half, j, 2 * half + 1, 1, c);
    } else rect(x - half, j, 2 * half + 1, 1, c);
  }
}
static void text(int x, int y, const char *s, int scale, uint16_t ink) {
  if (y >= y1 || y + 7 * scale <= y0) return;
  for (; *s; ++s, x += 6 * scale) {
    unsigned ch = (unsigned char)*s, i;
    if (ch >= 'a' && ch <= 'z') ch -= 32;
    if (ch >= 'A' && ch <= 'Z') i = ch - 'A';
    else if (ch >= '0' && ch <= '9') i = ch - '0' + 26;
    else {
      static const char punctuation[] = "+-.:/!<>|";
      const char *p = strchr(punctuation, (int)ch);
      if (!p || ch == ' ') continue;
      i = 36 + (unsigned)(p - punctuation);
    }
    for (int col = 0; col < 5; ++col) for (int row = 0; row < 7; ++row)
      if (alphabet[i][col] & (1 << row)) rect(x + col * scale, y + row * scale, scale, scale, ink);
  }
}
static int width(const char *s, int scale) { return (int)strlen(s) * 6 * scale - scale; }
static void centered(int cx, int y, const char *s, int scale, uint16_t ink) {
  text(cx - width(s, scale) / 2, y, s, scale, ink);
}
static void number(int x, int y, uint32_t value, int scale, uint16_t ink) {
  char s[11]; int n = 0;
  do { s[n++] = (char)('0' + value % 10); value /= 10; } while (value);
  for (int i = 0; i < n / 2; ++i) { char c = s[i]; s[i] = s[n - i - 1]; s[n - i - 1] = c; }
  s[n] = 0;
  text(x, y, s, scale, ink);
}
static const uint16_t colors[] = {0,C(0xDB5561),C(0xF36E66),C(0xA480C4),C(0xFFB94F),C(0xF89A3D),
 C(0xEA6553),C(0xC8CF69),C(0xF4A1A0),C(0xEAC85A),C(0xB5CD80),C(0x64A66C)};
static const uint16_t rims[] = {0,C(0xA3334D),C(0xC34F4B),C(0x795A97),C(0xD08A30),C(0xD77A2C),
 C(0xB74B39),C(0x8D9B45),C(0xC97D81),C(0xB59B35),C(0x849D52),C(0x3C784B)};
static void fruit(int k, int x, int y, int r, bool happy) {
  if (y + r + 2 < y0 || y - r - 6 >= y1 || x + r + 2 < x0 || x - r - 2 >= x1) return;
  circle(x + 1, y + 2, r, C(0xE6D4AA), false);
  circle(x, y, r, rims[k], false);
  circle(x, y - 1, r - 1, colors[k], false);
  /* Small soft highlight, drawn inside the fruit's circular silhouette. */
  circle(x - r / 3, y - r / 3, r / 4, C(0xFFE4BF), false);
  if (k == 3) { /* a bunch of grapes */
    circle(x-r/3,y-r/4,r/3,rims[k],true); circle(x+r/3,y-r/4,r/3,rims[k],true);
    circle(x,y+r/3,r/3,rims[k],true);
  }
  if (k == 2 || k == 9) {
    for (int j = -r / 2; j <= r / 2; j += 5) for (int i = -r / 2; i <= r / 2; i += 5)
      if (i*i+j*j < r*r*3/4) pixel(x+i+(j&1), y+j, k==2?C(0xFFE9A3):rims[k]);
  }
  if (k >= 10) { /* curved rind stripes */
    for (int j = -r + 3; j <= r - 3; ++j) {
      float span = sqrtf((float)(r*r-j*j));
      for (int i = -2; i <= 2; ++i) if (i) pixel(x+(int)(span*i/3),y+j,rims[k]);
    }
  }
  line(x, y-r+2, x+1, y-r-4, C(0x796043));
  line(x+1,y-r-2,x+5,y-r-4,GREEN); line(x+2,y-r+1,x+7,y-r-2,GREEN);
  if (k == 9) { line(x-3,y-r+1,x-7,y-r-6,GREEN); line(x,y-r,x,y-r-8,GREEN); }
  if (k == 2) { line(x-r/2,y-r+2,x+r/2,y-r+2,GREEN); }
  int ex = r > 15 ? r / 4 : 2, ey = y + r / 8, eye = r >= 20 ? 2 : 1;
  uint16_t face = C(0x4C403B);
  if (happy) {
    line(x-ex-2,ey,x-ex,ey-2,face); line(x-ex,ey-2,x-ex+2,ey,face);
    line(x+ex-2,ey,x+ex,ey-2,face); line(x+ex,ey-2,x+ex+2,ey,face);
  } else {
    roundbox(x-ex,ey,eye,eye+2,1,face); roundbox(x+ex,ey,eye,eye+2,1,face);
  }
  line(x-1,ey+4,x,ey+5,face); line(x,ey+5,x+2,ey+4,face);
  if (r >= 12) {
    rect(x-ex-4,ey+3,3,1,C(0xE88D83)); rect(x+ex+3,ey+3,3,1,C(0xE88D83));
  }
}
static void button(int x, int y, int w, const char *label, bool active) {
  roundbox(x,y+2,w,23,6,C(0xDDD2AB));
  roundbox(x,y,w,23,6,active?GREEN:C(0xF4E9CE));
  centered(x+w/2,y+8,label,1,active?C(0xFFF9E9):INK);
}
static void card(int x, int y, int w, int h) {
  roundbox(x,y+3,w,h,7,C(0xE5D7B5)); roundbox(x,y,w,h,7,C(0xFFFBEF));
}
static void board(void) {
  const sk_game *g = world;
  roundbox(BX-3,BY-3,SK_WIDTH+6,SK_HEIGHT+7,7,C(0xBF9663));
  roundbox(BX-1,BY-2,SK_WIDTH+2,SK_HEIGHT+3,5,C(0xEACB91));
  rect(BX,BY,SK_WIDTH,SK_HEIGHT,C(0xFFFBEF));
  for (int y=BY+13;y<BY+SK_HEIGHT;y+=16) for(int x=BX+9;x<BX+SK_WIDTH;x+=16)
    pixel(x,y,C(0xF4EDD7));
  /* Draw the exact landing preview before fruit. It is hidden under them. */
  int target;
  float landing = sk_landing(g,g->aim,&target);
  int ax=BX+(int)g->aim, ay=BY+(int)landing, r=sk_radius[g->current];
  uint16_t guide = target>=0 && g->fruit[target].kind==g->current ? GREEN : C(0xC9B58C);
  basket_clip = true;
  for (int y=BY+14;y<ay-r;y+=5) rect(ax,y,1,2,guide);
  circle(ax,ay,r,guide,true);
  /* Fruit drawing is clipped to the inside of the basket. */
  for(unsigned i=0;i<SK_CAPACITY;++i) {
    const sk_fruit *b=&g->fruit[i];
    if(!b->kind)continue;
    fruit(b->kind,BX+(int)b->x,BY+(int)b->y,sk_radius[b->kind],b->pop>0);
    if(b->pop>4) circle(BX+(int)b->x,BY+(int)b->y,sk_radius[b->kind]+(16-b->pop)/2,C(0xD0AE67),true);
  }
  for(int x=BX+3;x<BX+SK_WIDTH-3;x+=7)rect(x,BY+SK_DANGER,4,1,g->danger?RED:C(0xD8BDA0));
  if(g->danger) {
    rect(BX,BY+SK_HEIGHT-2,SK_WIDTH*g->danger/180,2,RED);
    centered(BX+SK_WIDTH/2,BY+SK_DANGER+5,"LET IT SETTLE!",1,RED);
  }
  basket_clip = false;
  if(!g->over) {
    fruit(g->current,ax,BY+8,r,false);
    if(g->hint) centered(ax,BY-11,"HINT",1,GREEN);
  }
}
static void gameplay(void) {
  const sk_game *g=world;
  text(13,12,"SUIKA",2,INK);
  text(214,8,"BEST",1,MUTED); number(214,20,g->best,1,INK);
  board();
  card(214,38,94,39);
  text(223,45,"SCORE",1,MUTED); number(223,57,g->score,g->score<1000000?2:1,INK);
  card(214,85,94,45); text(223,93,"NEXT",1,MUTED);
  for(int i=0;i<3;++i)fruit(g->next[i],231+i*29,115,sk_radius[g->next[i]]>10?10:sk_radius[g->next[i]],false);
  card(214,138,94,43);
  text(223,145,g->hold_used?"HOLD - USED":"1 HOLD",1,g->hold_used?MUTED:GREEN);
  if(g->held)fruit(g->held,262,166,sk_radius[g->held]>10?10:sk_radius[g->held],false);
  else centered(261,165,"EMPTY",1,MUTED);
  text(217,192,g->notice?"NO ROOM":g->combo>1&&g->combo_timer?"COMBO":"2 HINT",1,g->notice?RED:GREEN);
  if(g->combo>1&&g->combo_timer&&!g->notice) { text(266,192,"X",1,GREEN);number(278,192,g->combo,1,GREEN); }
  text(217,207,g->cooldown?"SETTLING...":"OK DROP",1,MUTED);
  text(12,230,"< > AIM",1,INK); text(113,230,"BACK PAUSE",1,MUTED); text(237,230,"HOME EXIT",1,MUTED);
}
static void title(void) {
  text(22,18,"NUMPLAY / SUIKA",1,MUTED);
  text(21,42,"SUIKA",4,INK);
  text(23,82,"A LITTLE FRUIT. A BIG MERGE.",1,GREEN);
  fruit(11,272,52,28,true);
  roundbox(16,112,143,105,10,C(0xEAD5AD));
  roundbox(20,113,135,98,8,C(0xFFFBEF));
  fruit(11,67,169,34,false); fruit(8,119,187,20,false);
  fruit(5,111,151,14,false); fruit(3,124,125,10,false); fruit(1,88,128,6,false);
  text(177,111,"BEST SCORE",1,MUTED); number(177,124,world->best,2,INK);
  button(176,151,128,world->started&&!world->over?"CONTINUE":"PLAY",selection==0);
  button(176,181,128,"HOW TO PLAY",selection==1);
  text(178,218,"OK SELECT",1,MUTED);
  text(22,230,"MERGE YOUR WAY TO A WATERMELON",1,GREEN);
}
static void modal(void) {
  /* Deterministic stipple dims without a second framebuffer. */
  for(int y=y0;y<y1;++y)for(int x=x0;x<x1;++x)if((x+y)&1)pixel(x,y,C(0xD5C9A6));
  card(40,35,240,174);
  if(page==SK_HELP) {
    centered(160,48,"HOW TO PLAY",2,INK);
    text(54,72,"MATCH TWO FRUITS TO GROW.",1,INK);
    text(54,86,"KEEP BELOW THE DOTTED LINE.",1,INK);
    text(54,107,"< >   MOVE THE DROPPER",1,MUTED);
    text(54,121,"OK    DROP ONE FRUIT",1,MUTED);
    text(54,135,"1     HOLD / SWAP ONCE",1,MUTED);
    text(54,149,"2     SUGGEST A MERGE",1,MUTED);
    text(54,163,"BACK  PAUSE / RESUME",1,MUTED);
    button(91,181,138,"OK GOT IT",true);
  } else if(page==SK_CONFIRM) {
    centered(160,50,"START AGAIN?",2,INK);
    centered(160,86,"YOUR CURRENT RUN WILL BE LOST.",1,MUTED);
    centered(160,102,"YOUR BEST SCORE WILL STAY.",1,MUTED);
    button(66,133,188,"KEEP PLAYING",selection==0);
    button(66,166,188,"NEW GAME",selection==1);
  } else if(page==SK_RESULT) {
    centered(160,49,"BASKET FULL",2,INK);
    centered(160,78,world->score && world->score==world->best?"A NEW PERSONAL BEST!":"A FRESH BASKET AWAITS.",1,GREEN);
    text(67,103,"SCORE",1,MUTED); number(149,99,world->score,2,INK);
    text(67,122,"MERGES",1,MUTED); number(149,122,world->merges,1,INK);
    button(66,145,188,"PLAY AGAIN",selection==0);
    button(66,175,188,"TITLE SCREEN",selection==1);
  } else if(page==SK_SAVE_ERROR) {
    centered(160,52,"SAVE FAILED",2,RED);
    centered(160,85,"CALCULATOR STORAGE IS FULL",1,INK);
    centered(160,102,"OR THE SAVE CANNOT BE WRITTEN.",1,MUTED);
    centered(160,124,"YOUR RUN IS STILL IN MEMORY.",1,MUTED);
    centered(160,138,"1 RETURN TO THE GAME",1,GREEN);
    button(66,158,188,"OK RETRY SAVE",true);
    centered(160,193,"BACK EXIT WITHOUT SAVING",1,RED);
  } else {
    centered(160,49,"TAKE A BREATHER",2,INK);
    centered(160,77,failed?"STORAGE FULL - SAVE FAILED":"YOUR BASKET IS SAVED.",1,failed?RED:MUTED);
    button(66,90,188,"RESUME",selection==0);
    button(66,119,188,"NEW GAME",selection==1);
    button(66,148,188,"HOW TO PLAY",selection==2);
    button(66,177,188,"TITLE SCREEN",selection==3);
  }
}
typedef struct {int16_t x,y;uint8_t kind,pop;} projection;
static projection previous[SK_CAPACITY];
static int old_aim,old_landing,old_current,old_danger;
static uint32_t old_score,old_best,old_panel;
static unsigned old_page=99,old_selection;
static bool old_failed,old_hint;
static uint32_t panel_hash(const sk_game *g) {
  return g->next[0] | (uint32_t)g->next[1]<<4 | (uint32_t)g->next[2]<<8 |
    (uint32_t)g->held<<12 | (uint32_t)g->hold_used<<16 | (uint32_t)(g->cooldown>0)<<17 |
    (uint32_t)(g->notice>0)<<18 | (uint32_t)(g->combo_timer?g->combo:0)<<20;
}
void sk_render(const sk_game *g,unsigned view,unsigned selected,bool save_failed,sk_push push) {
  world=g;page=view;selection=selected;failed=save_failed;
  bool full=!initialized||page!=old_page||selection!=old_selection||failed!=old_failed;
  if(full) mark(0,0,320,240);
  int target;
  int aim=(int)g->aim,landing=(int)sk_landing(g,g->aim,&target);
  bool changed=false;
  for(unsigned i=0;i<SK_CAPACITY;++i) {
    const sk_fruit *b=&g->fruit[i]; projection p={(int16_t)b->x,(int16_t)b->y,b->kind,b->pop};
    if(memcmp(&p,&previous[i],sizeof p)) {
      projection q=previous[i];
      if(q.kind)mark(BX+q.x-sk_radius[q.kind]-9,BY+q.y-sk_radius[q.kind]-12,2*sk_radius[q.kind]+22,2*sk_radius[q.kind]+24);
      if(p.kind)mark(BX+p.x-sk_radius[p.kind]-9,BY+p.y-sk_radius[p.kind]-12,2*sk_radius[p.kind]+22,2*sk_radius[p.kind]+24);
      previous[i]=p;changed=true;
    }
  }
  if(changed||aim!=old_aim||landing!=old_landing||g->current!=old_current||g->hint!=old_hint) {
    int r=old_current?sk_radius[old_current]+2:16;
    mark(BX+old_aim-r-4,BY-16,2*r+9,old_landing+r+24);
    r=sk_radius[g->current]+2;
    mark(BX+aim-r-4,BY-16,2*r+9,landing+r+24);
  }
  if(g->danger!=old_danger) { mark(BX,BY+SK_DANGER-1,SK_WIDTH,16);mark(BX,BY+SK_HEIGHT-3,SK_WIDTH,3); }
  if(g->score!=old_score||g->best!=old_best||panel_hash(g)!=old_panel)mark(212,0,108,221);
  old_aim=aim;old_landing=landing;old_current=g->current;old_danger=g->danger;
  old_score=g->score;old_best=g->best;old_panel=panel_hash(g);
  old_page=page;old_selection=selection;old_failed=failed;
  old_hint=g->hint;
  for(int row=0;row<30;++row) {
    if(dirty_hi[row]<=dirty_lo[row])continue;
    x0=dirty_lo[row];x1=dirty_hi[row];y0=row*8;y1=y0+8;
    rect(x0,y0,x1-x0,8,PAPER);
    if(page==SK_TITLE)title();
    else { gameplay();if(page!=SK_PLAY)modal(); }
    push(x0,y0,x1-x0,8,band);
    dirty_lo[row]=320;dirty_hi[row]=0;
  }
  initialized=true;
}
