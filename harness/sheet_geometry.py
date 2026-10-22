"""
Rectangle and segment tests shared by the sheet renderer and the annealer; a text is a rotated rectangle padded by PAD,
and the frame is the drawing area minus the title-block keep-out.
"""
import math

import numpy as np

AREA = (20.0, 10.0, 584.0, 358.0)
KEEP_OUT = (404.0, 360.0, 584.0, 410.0)
TEXT_MM = 3.5
CHAR_WIDTH = 0.6
PAD = 0.2


def text_rect(at, label, angle=0.0):
    half = len(label) * TEXT_MM * CHAR_WIDTH / 2
    corners = np.array([[-half, -TEXT_MM], [half, -TEXT_MM], [half, 0.3], [-half, 0.3]])
    turn = math.radians(angle)
    rotation = np.array([[math.cos(turn), -math.sin(turn)], [math.sin(turn), math.cos(turn)]])
    points = corners @ rotation.T + at
    return (points[:, 0].min() - PAD, points[:, 1].min() - PAD, points[:, 0].max() + PAD, points[:, 1].max() + PAD)


def rects_meet(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def inside(rect, frame):
    return rect[0] >= frame[0] and rect[1] >= frame[1] and rect[2] <= frame[2] and rect[3] <= frame[3]


def segments_meet_rect(segments, rect):
    if not len(segments):
        return np.zeros(0, dtype=bool)
    x0, y0, x1, y1 = segments.T
    dx, dy = x1 - x0, y1 - y0
    low, high = np.zeros(len(x0)), np.ones(len(x0))
    alive = np.ones(len(x0), dtype=bool)
    for p, q in ((-dx, x0 - rect[0]), (dx, rect[2] - x0), (-dy, y0 - rect[1]), (dy, rect[3] - y0)):
        parallel = p == 0
        alive &= ~(parallel & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(parallel, 0.0, q / np.where(parallel, 1.0, p))
        low = np.where(~parallel & (p < 0), np.maximum(low, ratio), low)
        high = np.where(~parallel & (p > 0), np.minimum(high, ratio), high)
    return alive & (low <= high)
