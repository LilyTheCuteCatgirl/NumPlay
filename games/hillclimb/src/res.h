/* Hill Climb Racing: the pictures and fonts in data.bin (tools/pack.py). */
#ifndef HC_RES_H
#define HC_RES_H
#include <stdint.h>
#include "data.h"

typedef struct {
  uint16_t w, h, ncolors, flags;   /* flags 1: opaque */
  uint16_t ppm4;                   /* its pixels a meter x 4 (0: a picture of the menus) */
  int16_t px4, py4;                /* its pivot x 4 */
  uint16_t pad;         /* if cropped, original clear palette index */
} Img;
enum { IMG_OPAQUE = 1, IMG_CROPPED = 2 };

typedef struct {
  uint8_t w, h;
  int8_t x, y;        /* the bitmap's top-left from the pen, y from the baseline */
  uint8_t adv, pad[3];
  uint32_t off;
} Glyph;
typedef struct {
  uint8_t first, count, height, ascent;
} Font;

extern const uint8_t hc_data[];
const Img *img(int id);
static inline const uint32_t *img_pal(const Img *i) { return (const uint32_t *)(const void *)(i + 1); }
static inline const uint16_t *img_pal565(const Img *i) { return (const uint16_t *)(const void *)(img_pal(i) + i->ncolors); }
/* HC_LOSSLESS_CROP_V1: flags 2 marks an optional 4-byte x/y/w/h crop rectangle.
 * The original dimensions and pivot stay unchanged; pad stores a clear palette index. */
static inline const uint8_t *img_crop(const Img *i) {
  return (const uint8_t *)(img_pal(i) + i->ncolors) + (i->flags & IMG_OPAQUE ? 2 * i->ncolors : 0);
}
static inline const uint8_t *img_px(const Img *i) {
  return img_crop(i) + ((i->flags & IMG_CROPPED) ? 4 : 0);
}
/* Called only for cropped images. Uncropped sprites/terrain keep direct byte reads. */
static inline uint8_t img_crop_index(const Img *i, const uint8_t *px, int x, int y) {
  const uint8_t *c = img_crop(i);
  unsigned xx = (unsigned)(x - c[0]), yy = (unsigned)(y - c[1]);
  if (xx >= c[2] || yy >= c[3]) return (uint8_t)i->pad;
  return px[yy * c[2] + xx];
}
const Font *font(int id);
static inline const Glyph *font_glyphs(const Font *f) { return (const Glyph *)(const void *)(f + 1); }
static inline const uint8_t *font_bits(const Font *f) { return (const uint8_t *)(font_glyphs(f) + f->count); }
#endif
