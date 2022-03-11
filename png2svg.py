#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, operator
from collections import deque
from io import StringIO
from PIL import Image
from threading import Thread

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
    if not mag > 0:
        print("Cannot normalize a zero-length vector")
        sys.exit(1)
    return tuple(map(operator.truediv, a, [mag]*len(a)))

def svg_header(width, height):
    return '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n<svg width="%d" height="%d" xmlns="http://www.w3.org/2000/svg" version="1.1">' % (width, height)

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
                    piece[-1] = (piece[-1][0], next_edge[1])
                else:
                    piece.append(next_edge)
                if piece[0][0] == piece[-1][1]:
                    if not keep_every_point and normalize(direction(piece[0])) == normalize(direction(piece[-1])):
                        piece[-1] = (piece[-1][0], piece.pop(0)[1])
                    pieces.append(piece)
                    piece = []
                break
        else:
            raise Exception("Failed to find connecting edge")
    return pieces


def rgba_image_to_svg_contiguous(im, opaque=None, keep_every_point=False):
    adjacent = ((1, 0), (0, 1), (-1, 0), (0, -1))
    visited = Image.new('1', im.size, 0)
    color_pixel_lists = {}
    width, height = im.size
    for x in range(width):
        for y in range(height):
            here = (x, y)
            if visited.getpixel(here):
                continue
            rgba = im.getpixel((x, y))
            if opaque and not rgba[3]:
                continue
            piece = []
            queue = [here]
            visited.putpixel(here, 1)
            while queue:
                here = queue.pop()
                for offset in adjacent:
                    neighbour = add_tuple(here, offset)
                    if not (0 <= neighbour[0] < width) or not (0 <= neighbour[1] < height):
                        continue
                    if visited.getpixel(neighbour):
                        continue
                    neighbour_rgba = im.getpixel(neighbour)
                    if neighbour_rgba != rgba:
                        continue
                    queue.append(neighbour)
                    visited.putpixel(neighbour, 1)
                piece.append(here)

            if not rgba in color_pixel_lists:
                color_pixel_lists[rgba] = []
            color_pixel_lists[rgba].append(piece)

    del adjacent
    del visited
    edges = {
        (-1, 0):((0, 0), (0, 1)),
        (0, 1):((0, 1), (1, 1)),
        (1, 0):((1, 1), (1, 0)),
        (0, -1):((1, 0), (0, 0)),
        }
    color_edge_lists = {}
    for rgba, pieces in list(color_pixel_lists.items()):
        for piece_pixel_list in pieces:
            edge_set = set([])
            for coord in piece_pixel_list:
                for offset, (start_offset, end_offset) in list(edges.items()):
                    neighbour = add_tuple(coord, offset)
                    start = add_tuple(coord, start_offset)
                    end = add_tuple(coord, end_offset)
                    edge = (start, end)
                    if neighbour in piece_pixel_list:
                        continue
                    edge_set.add(edge)
            if not rgba in color_edge_lists:
                color_edge_lists[rgba] = []
            color_edge_lists[rgba].append(edge_set)

    del color_pixel_lists
    del edges

    color_joined_pieces = {}

    for color, pieces in list(color_edge_lists.items()):
        color_joined_pieces[color] = []
        for assorted_edges in pieces:
            color_joined_pieces[color].append(joined_edges(assorted_edges, keep_every_point))

    s = StringIO()
    s.write(svg_header(*im.size))

    for color, shapes in list(color_joined_pieces.items()):
        for shape in shapes:
            s.write(' <path d=" ')
            for sub_shape in shape:
                here = sub_shape.pop(0)[0]
                s.write(' M %d,%d ' % here)
                for edge in sub_shape:
                    here = edge[0]
                    s.write(' L %d,%d ' % here)
                s.write(' Z ')
            s.write(' " style="fill:rgb%s; fill-opacity:%.3f; stroke:none;" />\n""' % (color[0:3], float(color[3]) / 255))
            
    s.write('</svg>\n')
    return s.getvalue()

def png_to_svg(filename):
    try:
        im_rgba = Image.open(filename).convert('RGBA')
    except IOError as e:
        sys.stderr.write('%s: Could not open as image file\n' % filename)
        sys.exit(1)
    
    return rgba_image_to_svg_contiguous(im_rgba, None, None)

class SVG_Thread(Thread):
    def __init__(self, InputFileName, OutputFileName):
        Thread.__init__(self)
        self.InputFileName = InputFileName
        self.OutputFileName = OutputFileName
    def run(self):
        with open(self.OutputFileName, "w") as svg:
            svg.write(png_to_svg(self.InputFileName))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: %s [Input FILE] [OUT FILE]" % sys.argv[0].split('/')[-1])
        sys.exit(0)
    SVG_Writing = SVG_Thread(sys.argv[1], sys.argv[2])
    SVG_Writing.start()
    SVG_Writing.join()