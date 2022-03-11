#!/usr/bin/env python3
# -*- coding: utf-8 -*-
 
import sys, operator
from collections import deque
from io import StringIO
from PIL import Image

def add_tuple(a, b):
    return tuple(map(operator.add, a, b))

def sub_tuple(a, b):
    return tuple(map(operator.sub, a, b))

def neg_tuple(a):
    return tuple(map(operator.neg, a))

def direction(edge):
    return sub_tuple(edge[1], edge[0])

def magnitude(a):
    return int(pow(pow(a[0], 2) + pow(a[1], 2), .5))

def normalize(a):
    mag = magnitude(a)
    assert mag > 0, "Cannot normalize a zero-length vector"
    return tuple(map(operator.truediv, a, [mag]*len(a)))

def svg_header(width, height):
    return """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" 
  "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg width="%d" height="%d"
     xmlns="http://www.w3.org/2000/svg" version="1.1">
""" % (width, height)

def rgba_image_to_svg_pixels(im, opaque=None):
    s = StringIO()
    s.write(svg_header(*im.size))

    width, height = im.size
    for x in range(width):
        for y in range(height):
            here = (x, y)
            rgba = im.getpixel(here)
            if opaque and not rgba[3]:
                continue
            s.write("""  <rect x="%d" y="%d" width="1" height="1" style="fill:rgb%s; fill-opacity:%.3f; stroke:none;" />\n""" % (x, y, rgba[0:3], float(rgba[3]) / 255))
    s.write("""</svg>\n""")
    return s.getvalue()

def joined_edges(assorted_edges, keep_every_point=False):
    pieces = []
    piece = []
    directions = deque([
        (0, 1),
        (1, 0),
        (0, -1),
        (-1, 0),
        ])
    while assorted_edges:
        if not piece:
            piece.append(assorted_edges.pop())
        current_direction = normalize(direction(piece[-1]))
        while current_direction != directions[2]:
            directions.rotate()
        for i in range(1, 4):
            next_end = add_tuple(piece[-1][1], directions[i])
            next_edge = (piece[-1][1], next_end)
            if next_edge in assorted_edges:
                assorted_edges.remove(next_edge)
                if i == 2 and not keep_every_point:
                    # same direction
                    piece[-1] = (piece[-1][0], next_edge[1])
                else:
                    piece.append(next_edge)
                if piece[0][0] == piece[-1][1]:
                    if not keep_every_point and normalize(direction(piece[0])) == normalize(direction(piece[-1])):
                        piece[-1] = (piece[-1][0], piece.pop(0)[1])
                        # same direction
                    pieces.append(piece)
                    piece = []
                break
        else:
            raise Exception("Failed to find connecting edge")
    return pieces

def png_to_svg(filename):
    try:
        im_rgba = Image.open(filename).convert('RGBA')
    except IOError as e:
        sys.stderr.write('%s: Could not open as image file\n' % filename)
        sys.exit(1)

    return rgba_image_to_svg_pixels(im_rgba, None)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: %s [Input FILE] [OUT FILE]" % sys.argv[0].split('/')[-1])
        sys.exit(0)
    with open(sys.argv[2], "w") as svg:
        svg.write(png_to_svg(sys.argv[1]))