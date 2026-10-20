/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef SK_RENDER_H
#define SK_RENDER_H
#include "suika.h"
enum { SK_TITLE, SK_PLAY, SK_PAUSE, SK_HELP, SK_CONFIRM, SK_RESULT, SK_SAVE_ERROR };
typedef void (*sk_push)(int x, int y, int w, int h, const uint16_t *pixels);
/* Rendering composes at most eight rows at a time (5120 bytes). */
void sk_render(const sk_game *g, unsigned view, unsigned selected, bool save_failed, sk_push push);
void sk_invalidate(void);
#endif
