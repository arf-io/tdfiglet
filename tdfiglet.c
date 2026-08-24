/* Copyright (c) 2018 Trollforge. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Trollforge's name may not be used to endorse or promote products
 *    derived from this software without specific prior written permission.
 */

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <iconv.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sysexits.h>
#include <unistd.h>

#include <sys/mman.h>
#include <sys/queue.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>

#ifdef DEBUG
#define DEBUG 1
#endif /* DEBUG */

#define OUTLN_FNT 0
#define BLOCK_FNT 1
#define COLOR_FNT 2

#define NUM_CHARS 94

/* 4 bytes + '\0' */
#define MAX_UTFSTR	5

#define LEFT_JUSTIFY	0
#define RIGHT_JUSTIFY	1
#define CENTER_JUSTIFY	2

#define DEFAULT_WIDTH	80

#define COLOR_ANSI	0
#define COLOR_MIRC	1

#define ENC_UNICODE	0
#define ENC_ANSI	1

/*
 * Font file layout.  A .tdf holds one or more sub-fonts.  Each sub-font
 * begins with a 55 AA 00 FF marker followed by a 213 byte header; the
 * glyph data block that follows is `blocksize` bytes long, and the next
 * sub-font's marker begins immediately after it.
 *
 *   +0    marker 55 AA 00 FF
 *   +4    name length (max 12)
 *   +5    name, 12 bytes, space/NUL padded
 *   +17   unused (4 bytes)
 *   +21   font type: 0 outline, 1 block, 2 color
 *   +22   letter spacing
 *   +23   block size (uint16, little endian)
 *   +25   glyph offset table, 94 * uint16 LE, 0xffff = no glyph
 *   +213  glyph data
 */
#define MAGIC_LEN	20
#define HDR_LEN		213
#define OFF_NAMELEN	4
#define OFF_NAME	5
#define OFF_FONTTYPE	21
#define OFF_SPACING	22
#define OFF_BLOCKSIZE	23
#define OFF_CHARLIST	25

#define NO_GLYPH	0xffff

/* sub-fonts we are willing to enumerate in one file */
#define MAX_SUBFONTS	64

/* attribute used for block and outline fonts, which carry no color data */
#define MONO_ATTR	0x0f

#ifndef FONT_DIR
#define FONT_DIR	"fonts"
#endif /* FONT_DIR */

#ifndef FONT_EXT
#define FONT_EXT	"tdf"
#endif /* FONT_EXT */

#ifndef DEFAULT_FONT
#define DEFAULT_FONT	"brndamgx" /* seems most complete */
#endif /* DEFAULT_FONT */

typedef struct opt_s {
	uint8_t justify;
	int width;
	uint8_t color;
	uint8_t encoding;
	bool random;
	bool info;
	bool list;
	bool verbose;
	long subfont;
} opt_t;

typedef struct cell_s {
	uint8_t color;
	char utfchar[MAX_UTFSTR];
} cell_t;

typedef struct glyph_s {
	uint8_t width;
	uint8_t height;
	cell_t *cell;
} glyph_t;

typedef struct font_s {
	uint8_t namelen;
	uint8_t *name;
	uint8_t fonttype;
	uint8_t spacing;
	uint16_t blocksize;
	/* Decoded copy of the glyph offset table.  It must not alias the
	 * mapping: the map is PROT_READ, and loadfont() rewrites entries to
	 * NO_GLYPH to retire offsets that fall outside the data window.  The
	 * on-disk table is also only byte-aligned, so casting it to
	 * uint16_t * would be unaligned as well as read-only. */
	uint16_t charlist[NUM_CHARS];
	uint8_t *data;
	uint8_t *dataend;
	glyph_t *glyphs[NUM_CHARS];
	uint8_t height;
	int index;
	int count;
} font_t;

struct dirname_s {
	char *str;
	SLIST_ENTRY(dirname_s) stuff;
};

const char *charlist = "!\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNO"
		       "PQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~";

static const uint8_t seqmark[4] = { 0x55, 0xaa, 0x00, 0xff };

/*
 * Outline fonts store each cell as an index into a table of CP437 line
 * drawing characters rather than as a literal character.  Cell bytes run
 * '@' (0x40) through 'O' (0x4f); 'O' is a "hard space" marker that fills
 * a glyph interior and renders as blank, as does the descender mark '&'.
 *
 * Table from the TheDraw font format notes published at
 * http://www.roysac.com/blog/2014/04/thedraw-fonts-file-tdf-specifications/
 * and verified here by rendering unused-fonts/tdfonts_org.tdf.
 */
static const uint8_t outlinemap[16] = {
	0x20,	/* @  leading space filler	*/
	205,	/* A  horizontal beam, double	*/
	196,	/* B  horizontal beam, single	*/
	179,	/* C  vertical beam, single	*/
	186,	/* D  vertical beam, double	*/
	213,	/* E  upper left outer corner	*/
	187,	/* F  upper right outer corner	*/
	214,	/* G  up to right inner corner	*/
	191,	/* H  right to down inner corner*/
	200,	/* I  lower left inner corner	*/
	190,	/* J  lower right inner corner	*/
	192,	/* K  lower left outer corner	*/
	189,	/* L  lower right outer corner	*/
	181,	/* M  reserved			*/
	199,	/* N  reserved			*/
	0x20	/* O  hard space, renders blank	*/
};

#define OUTLINE_DESCENDER	0x26

opt_t opt;

void usage(void);
font_t *loadfont(char *fn);
void readchar(int i, glyph_t *glyph, font_t *font);

const char *fonttypename(uint8_t type);
int listfonts(const char *fn);

void ibmtoutf8(char *a, char *u);
void printcolor(uint8_t color);
int lookupchar(char c, const font_t *font);

void printcell(char *utfchar, uint8_t color);
void printrow(const glyph_t *glyph, int row);
void printstr(const char *str, font_t *font);

void
usage(void)
{
	fprintf(stderr, "usage: tdfiglet [options] input\n");
	fprintf(stderr, "\n");
	fprintf(stderr, "    -f [font] Specify font file used.  Append :n to pick a\n");
	fprintf(stderr, "              sub-font, e.g. -f tdfonts_org:3\n");
	fprintf(stderr, "    -n [n]    Select sub-font by index.  Default is 0.\n");
	fprintf(stderr, "    -L        List the sub-fonts in a font file and exit.\n");
	fprintf(stderr, "    -j l|r|c  Justify left, right, or center.  Default is left.\n");
	fprintf(stderr, "    -w n      Set screen width.  Default is 80.\n");
	fprintf(stderr, "    -c a|m    Color format ANSI or mirc.  Default is ANSI.\n");
	fprintf(stderr, "    -e u|a    Encode as unicode or ASCII.  Default is unicode.\n");
	fprintf(stderr, "    -i        Print font details.\n");
	fprintf(stderr, "    -r        Use random font.\n");
	fprintf(stderr, "    -v        Warn about characters the font has no glyph for.\n");
	fprintf(stderr, "    -h        Print usage.\n");
	fprintf(stderr, "\n");
	exit(EX_USAGE);
}

const char *
fonttypename(uint8_t type)
{
	switch (type) {
		case OUTLN_FNT:
			return "outline";
		case BLOCK_FNT:
			return "block";
		case COLOR_FNT:
			return "color";
		default:
			return "unknown";
	}
}

/*
 * Resolve a font argument to a path.  Accepts a bare name, a name with an
 * extension, or a path, and returns malloc'd storage the caller frees.
 */
static char *
fontpath(const char *fn_arg)
{
	char *fn;
	size_t len;

	if (!strchr(fn_arg, '/')) {
		if (strchr(fn_arg, '.')) {
			len = strlen(FONT_DIR) + strlen(fn_arg) + 2;
			fn = malloc(len);
			if (fn)
				snprintf(fn, len, "%s/%s", FONT_DIR, fn_arg);
		} else {
			len = strlen(FONT_DIR) + strlen(fn_arg) +
			      strlen(FONT_EXT) + 3;
			fn = malloc(len);
			if (fn)
				snprintf(fn, len, "%s/%s.%s", FONT_DIR, fn_arg,
					 FONT_EXT);
		}
	} else {
		len = strlen(fn_arg) + 1;
		fn = malloc(len);
		if (fn)
			snprintf(fn, len, "%s", fn_arg);
	}

	if (!fn) {
		perror(NULL);
		exit(EX_OSERR);
	}

	return fn;
}

static uint16_t
le16(const uint8_t *p)
{
	return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

/*
 * Walk the sub-font chain.  Each header declares the size of its own glyph
 * block, so the next marker sits at offset + HDR_LEN + blocksize.  Stops at
 * the first offset that is not a valid marker, which is what keeps trailing
 * garbage (fonts/guardf2.tdf has 129 such bytes) from being read as a font.
 */
static int
findsubfonts(const uint8_t *map, size_t len, size_t *offs, int max)
{
	size_t off = MAGIC_LEN;
	int n = 0;

	while (n < max && off + HDR_LEN <= len &&
	       !memcmp(map + off, seqmark, sizeof(seqmark))) {
		offs[n++] = off;
		off += HDR_LEN + le16(map + off + OFF_BLOCKSIZE);
	}

	return n;
}

static uint8_t *
mapfont(const char *fn, size_t *lenp)
{
	struct stat st;
	uint8_t *map;
	int fd;

	fd = open(fn, O_RDONLY);

	if (fd < 0) {
		perror(fn);
		exit(EX_NOINPUT);
	}

	if (fstat(fd, &st)) {
		perror(fn);
		close(fd);
		exit(EX_OSERR);
	}

	if ((size_t)st.st_size < MAGIC_LEN + HDR_LEN) {
		fprintf(stderr, "Invalid font file: %s (too short)\n", fn);
		close(fd);
		exit(EX_NOINPUT);
	}

	*lenp = st.st_size;
	map = mmap(0, *lenp, PROT_READ, MAP_PRIVATE, fd, 0);

	close(fd);

	if (map == MAP_FAILED) {
		perror(fn);
		exit(EX_OSERR);
	}

	if (memcmp(map, "\x13TheDraw FONTS file\x1a", MAGIC_LEN)) {
		fprintf(stderr, "Invalid font file: %s (bad magic)\n", fn);
		munmap(map, *lenp);
		exit(EX_NOINPUT);
	}

	return map;
}

int
listfonts(const char *fn_arg)
{
	char *fn = fontpath(fn_arg);
	size_t offs[MAX_SUBFONTS];
	size_t len;
	uint8_t *map;
	int n;

	map = mapfont(fn, &len);
	n = findsubfonts(map, len, offs, MAX_SUBFONTS);

	printf("file: %s\n", fn);
	printf("sub-fonts: %d\n", n);

	for (int i = 0; i < n; i++) {
		const uint8_t *h = map + offs[i];
		int namelen = h[OFF_NAMELEN];
		int glyphs = 0;

		if (namelen > 12)
			namelen = 12;

		for (int c = 0; c < NUM_CHARS; c++) {
			if (le16(h + OFF_CHARLIST + c * 2) != NO_GLYPH)
				glyphs++;
		}

		printf("  %2d  %-12.*s  %-7s  spacing %2d  %2d/%d glyphs\n",
		       i, namelen, (const char *)h + OFF_NAME,
		       fonttypename(h[OFF_FONTTYPE]), h[OFF_SPACING],
		       glyphs, NUM_CHARS);
	}

	munmap(map, len);
	free(fn);

	return n;
}

int
main(int argc, char *argv[])
{
	font_t *font = NULL;
	int o;

	opt.justify = LEFT_JUSTIFY;
	opt.width = DEFAULT_WIDTH;
	opt.info = false;
	opt.encoding = ENC_UNICODE;
	opt.random = false;
	opt.list = false;
	opt.verbose = false;
	opt.subfont = 0;
	char *fontfile = NULL;
	char *sep;

	struct timeval tv;

	DIR *d;
	struct dirent *dir;
	SLIST_HEAD(, dirname_s) head = SLIST_HEAD_INITIALIZER(dirname_s);
	SLIST_INIT(&head);
	struct dirname_s *dp;

	int r = 0;
	int dll = 0;

	while((o = getopt(argc, argv, "f:n:w:j:c:e:irLvh")) != -1) {
		switch (o) {
			case 'f':
				fontfile = optarg;
				break;
			case 'n':
				opt.subfont = strtol(optarg, NULL, 10);
				if (opt.subfont < 0)
					usage();
				break;
			case 'w':
				opt.width = atoi(optarg);
				if (opt.width < 1)
					usage();
				break;
			case 'j':
				switch (optarg[0]) {
					case 'l':
						opt.justify = LEFT_JUSTIFY;
						break;
					case 'r':
						opt.justify = RIGHT_JUSTIFY;
						break;
					case 'c':
						opt.justify = CENTER_JUSTIFY;
						break;
					default:
						usage();
				}
				break;
			case 'c':
				switch (optarg[0]) {
					case 'a':
						opt.color = COLOR_ANSI;
						break;
					case 'm':
						opt.color = COLOR_MIRC;
						break;
					default:
						usage();
				}
				break;
			case 'e':
				switch (optarg[0]) {
					case 'a':
						opt.encoding = ENC_ANSI;
						break;
					case 'u':
						opt.encoding = ENC_UNICODE;
						break;
					default:
						usage();
				}
				break;
			case 'i':
				opt.info = true;
				break;
			case 'r':
				opt.random = true;
				break;
			case 'L':
				opt.list = true;
				break;
			case 'v':
				opt.verbose = true;
				break;
			case 'h':
				/* fallthrough */
			default:
				usage();
		}
	}

	argc -= optind;
	argv += optind;

	/* -L takes the font file positionally so `tdfiglet -L foo.tdf` works */
	if (opt.list && !fontfile && argc > 0) {
		fontfile = argv[0];
		argc--;
		argv++;
	}

	if (!fontfile) {
		if (!opt.random) {
			fontfile = DEFAULT_FONT;
		} else {
			d = opendir(FONT_DIR);
			if (!d) {
				fprintf(stderr, "Error: unable to read %s\n",
					FONT_DIR);
				exit(1);
			}

			while ((dir = readdir(d))) {
				if (strstr(dir->d_name, FONT_EXT)) {
					dp = malloc(sizeof(struct dirname_s));
					if (!dp) {
						perror(NULL);
						exit(EX_OSERR);
					}
					dp->str = calloc(1, 1024);
					if (!dp->str) {
						perror(NULL);
						exit(EX_OSERR);
					}
					strncpy(dp->str, dir->d_name, 1023);
					SLIST_INSERT_HEAD(&head, dp, stuff);
					dll++;
				}
			}
			closedir(d);

			gettimeofday(&tv, NULL);

			srand(tv.tv_usec ^ (tv.tv_sec << 8) ^ getpid());
			r = dll ? rand() % dll : 0;

			dp = SLIST_FIRST(&head);
			for (int i = 0; i < r && dp; i++) {
				dp = SLIST_NEXT(dp, stuff);
			}

			if (dp && dp->str) {
				fontfile = dp->str;
			} else {
				fontfile = DEFAULT_FONT;
			}
		}
	}

	/* -f name:n selects a sub-font without needing -n */
	sep = strrchr(fontfile, ':');
	if (sep && sep[1] != '\0') {
		char *end = NULL;
		long n = strtol(sep + 1, &end, 10);

		if (end && *end == '\0' && n >= 0) {
			opt.subfont = n;
			*sep = '\0';
		}
	}

	if (opt.list) {
		listfonts(fontfile);
		return 0;
	}

	if (argc < 1) {
		usage();
	}

	font = loadfont(fontfile);

	printf("\n");

	for (int i = 0; i < argc; i++) {
		printstr(argv[i], font);
		printf("\n");
	}

	return(0);
}

font_t
*loadfont(char *fn_arg)
{
	font_t *font;
	uint8_t *map = NULL;
	uint8_t *hdr;
	size_t len;
	size_t offs[MAX_SUBFONTS];
	int nfonts;
	char *fn = fontpath(fn_arg);

	map = mapfont(fn, &len);

	nfonts = findsubfonts(map, len, offs, MAX_SUBFONTS);

	if (nfonts < 1) {
		fprintf(stderr, "Invalid font file: %s (no sub-fonts)\n", fn);
		exit(EX_NOINPUT);
	}

	if (opt.subfont >= nfonts) {
		fprintf(stderr,
			"%s: sub-font %ld requested but file has %d "
			"(0-%d).  Use -L to list them.\n",
			fn, opt.subfont, nfonts, nfonts - 1);
		exit(EX_USAGE);
	}

	font = calloc(1, sizeof(font_t));

	if (!font) {
		perror(NULL);
		exit(EX_OSERR);
	}

	hdr = map + offs[opt.subfont];

	font->namelen = hdr[OFF_NAMELEN] > 12 ? 12 : hdr[OFF_NAMELEN];
	font->name = hdr + OFF_NAME;
	font->fonttype = hdr[OFF_FONTTYPE];
	font->spacing = hdr[OFF_SPACING];
	font->blocksize = le16(hdr + OFF_BLOCKSIZE);
	for (int i = 0; i < NUM_CHARS; i++)
		font->charlist[i] = le16(hdr + OFF_CHARLIST + i * 2);

	font->data = hdr + HDR_LEN;
	font->height = 0;
	font->index = (int)opt.subfont;
	font->count = nfonts;

	/*
	 * Clamp the glyph data window to the declared block size and to the
	 * end of the mapping, whichever comes first.  Everything downstream
	 * bounds-checks against dataend, so a truncated or lying header can
	 * cost us glyphs but cannot walk off the map.
	 */
	font->dataend = font->data + font->blocksize;
	if (font->dataend > map + len)
		font->dataend = map + len;

	if (font->fonttype != OUTLN_FNT && font->fonttype != BLOCK_FNT &&
	    font->fonttype != COLOR_FNT) {
		fprintf(stderr, "%s: unknown font type %d\n", fn,
			font->fonttype);
		exit(EX_NOINPUT);
	}

	if (opt.info) {
		printf("file: %s\n", fn);
		printf("font: %.*s\n", font->namelen, (char *)font->name);
		printf("type: %s\n", fonttypename(font->fonttype));
		printf("sub-font: %d of %d\n", font->index, font->count);
		printf("spacing: %d\n", font->spacing);
		printf("char list: ");
	}

	/*
	 * First pass: establish the tallest glyph, which sets the cell grid
	 * height every glyph is padded to.  Offsets that do not leave room
	 * for a 2 byte glyph header inside the data window are dropped here
	 * so the second pass never sees them.
	 */
	for (int i = 0; i < NUM_CHARS; i++) {
		uint8_t *p;

		if (font->charlist[i] == NO_GLYPH)
			continue;

		p = font->data + font->charlist[i];

		if (p + 2 > font->dataend) {
			font->charlist[i] = NO_GLYPH;
			continue;
		}

		if (opt.info)
			printf("%c", charlist[i]);

		if (p[1] > font->height)
			font->height = p[1];
	}

	if (opt.info)
		printf("\n");

	if (font->height == 0)
		font->height = 1;

	for (int i = 0; i < NUM_CHARS; i++) {

		if (font->charlist[i] != NO_GLYPH) {

			font->glyphs[i] = calloc(1, sizeof(glyph_t));

			if (!font->glyphs[i]) {
				perror(NULL);
				exit(EX_OSERR);
			}

			readchar(i, font->glyphs[i], font);

		} else {
			font->glyphs[i] = NULL;
		}
	}

	free(fn);

	return font;
}

void
readchar(int i, glyph_t *glyph, font_t *font)
{
	uint8_t *p = font->data + font->charlist[i];
	uint8_t ch;
	uint8_t color;
	int row = 0;
	int col = 0;
	int width;

	glyph->width = *p;
	p++;
	glyph->height = *p;
	p++;

	width = glyph->width;

	if (width < 1) {
		/* zero width glyph: nothing to draw, but keep the cell array
		 * allocated so printrow() has something to walk */
		width = glyph->width = 1;
	}

	glyph->cell = calloc((size_t)width * font->height, sizeof(cell_t));
	if (!glyph->cell) {
		perror(NULL);
		exit(EX_OSERR);
	}

	/* Padding cells take the font's resting attribute: black on black for
	 * color fonts, which is how TheDraw stored them, and the mono
	 * attribute for block and outline fonts, which have no attribute data
	 * and would otherwise emit a pointless color change per pad cell. */
	for (int c = 0; c < width * font->height; c++) {
		glyph->cell[c].utfchar[0] = ' ';
		glyph->cell[c].color =
			font->fonttype == COLOR_FNT ? 0 : MONO_ATTR;
	}

	while (p < font->dataend && *p) {

		ch = *p;
		p++;

		if (ch == '\r') {
			row++;
			col = 0;
			continue;
		}

		if (font->fonttype == COLOR_FNT) {
			if (p >= font->dataend)
				break;
			color = *p;
			p++;
		} else {
			/* block and outline fonts carry no attribute byte */
			color = MONO_ATTR;
		}

		if (font->fonttype == OUTLN_FNT) {
			if (ch >= 0x40 && ch <= 0x4f)
				ch = outlinemap[ch - 0x40];
			else if (ch == OUTLINE_DESCENDER)
				ch = ' ';
		}

#ifdef DEBUG
		if (ch == 0x09)
			ch = 'T';
		if (ch < 0x20)
			ch = '?';
#else
		if (ch < 0x20)
			ch = ' ';
#endif /* DEBUG */

		/* Malformed glyph data can declare more rows or columns than
		 * the header allowed for.  Drop those cells rather than
		 * writing past the array. */
		if (row >= font->height || col >= width) {
			col++;
			continue;
		}

		if (opt.encoding == ENC_UNICODE) {
			ibmtoutf8((char *)&ch,
				  glyph->cell[row * width + col].utfchar);
		} else {
			glyph->cell[row * width + col].utfchar[0] = ch;
		}

		glyph->cell[row * width + col].color = color;

		col++;
	}
}

int
lookupchar(char c, const font_t *font)
{
	for (int i = 0; i < NUM_CHARS; i++) {
		if (charlist[i] == c && font->charlist[i] != NO_GLYPH)
			return i;
	}

	return -1;
}

void
ibmtoutf8(char *a, char *u)
{
	static iconv_t conv = (iconv_t)0;

	size_t inchsize = 1;
	size_t outchsize = MAX_UTFSTR;

	if (!conv) {
		conv = iconv_open("UTF-8", "CP437");
	}

	iconv(conv, &a, &inchsize, &u, &outchsize);

	return;
}

void
printcolor(uint8_t color)
{

	uint8_t fg = color & 0x0f;
	uint8_t bg = (color & 0xf0) >> 4;

	/* thedraw colors                                     BRT BRT BRT BRT BRT BRT BRT BRT */
	/* thedraw colors     BLK BLU GRN CYN RED MAG BRN GRY BLK BLU GRN CYN RED PNK YLW WHT */
	uint8_t fgacolors[] = {30, 34, 32, 36, 31, 35, 33, 37, 90, 94, 92, 96, 91, 95, 93, 97};

	/* The background nibble is four bits wide.  On DOS text hardware the
	 * top bit meant blink, but TheDraw and the ANSI art scene used it for
	 * "iCE color" bright backgrounds instead, which is how these fonts
	 * were drawn and how they are rendered here.  8552 cells across 12 of
	 * the bundled fonts set it.  The original table had only 8 entries
	 * and read out of bounds for every one of them. */
	uint8_t bgacolors[] = {40, 44, 42, 46, 41, 45, 43, 47,
			       100, 104, 102, 106, 101, 105, 103, 107};
	uint8_t fgmcolors[] = { 1,  2,  3, 10,  5,  6,  7, 15, 14,  12, 9, 11,  4, 13,  8,  0};
	uint8_t bgmcolors[] = { 1,  2,  3, 10,  5,  6,  7, 15, 14,  12, 9, 11,  4, 13,  8,  0};

	if (opt.color == COLOR_ANSI) {
		printf("\x1b[");
		printf("%d;", fgacolors[fg]);
		printf("%dm", bgacolors[bg]);
	} else {
		printf("\x03");
		printf("%d,", fgmcolors[fg]);
		printf("%d", bgmcolors[bg]);
	}
}

void
printrow(const glyph_t *glyph, int row)
{
	char *utfchar;
	uint8_t color;
	int i;
	uint8_t lastcolor = 0;

	for (i = 0; i < glyph->width; i++) {
		utfchar = glyph->cell[glyph->width * row + i].utfchar;
		color = glyph->cell[glyph->width * row + i].color;

		if (i == 0 || color != lastcolor) {
			printcolor(color);
			lastcolor = color;
		}

		printf("%s", utfchar);
	}

	if (opt.color == COLOR_ANSI) {
		printf("\x1b[0m");
	} else {
		printf("\x03");
	}
}

void
printstr(const char *str, font_t *font)
{
	int maxheight = 0;
	int linewidth = 0;
	int len = strlen(str);
	int padding = 0;
	int n = 0;

	for (int i = 0; i < len; i++) {
		glyph_t *g;

		n = lookupchar(str[i], font);

		if (n == -1) {
			if (opt.verbose && str[i] != ' ')
				fprintf(stderr,
					"warning: %.*s has no glyph for '%c'\n",
					font->namelen, (char *)font->name,
					str[i]);
			continue;
		}

		g = font->glyphs[n];

		if (g->height > maxheight) {
			maxheight = g->height;
		}

		linewidth += g->width;
		if (linewidth + 1 < len) {
			linewidth += font->spacing;
		}
	}

	if (maxheight > font->height)
		maxheight = font->height;

	if (opt.justify == CENTER_JUSTIFY) {
		padding = (opt.width - linewidth) / 2;
	} else if (opt.justify == RIGHT_JUSTIFY) {
		padding = (opt.width - linewidth);
	}

	if (padding < 0)
		padding = 0;

	for (int i = 0; i < maxheight; i++) {
		for (int j = 0; j < padding; ++j) {
			printf(" ");
		}

		for (int c = 0; c < len; c++) {
			n = lookupchar(str[c], font);

			if (n == -1) {
				continue;
			}

			glyph_t *g = font->glyphs[n];
			printrow(g, i);

			if (opt.color == COLOR_ANSI) {
				printf("\x1b[0m");
			} else {
				printf("\x03");
			}

			for (int s = 0; s < font->spacing; s++) {
				printf(" ");
			}
		}

		if (opt.color == COLOR_ANSI) {
			printf("\x1b[0m\n");
		} else {
			printf("\x03\r\n");
		}
	}
}
