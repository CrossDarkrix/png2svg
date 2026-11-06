#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PNG画像を同色領域ごとのSVGパスへ変換するツール。

引数なしで起動するとGUI、引数付きではCLIとして動作します。
リサイズ、SVG表示サイズ指定、減色、SVG最小化、SVGZ圧縮に対応します。
"""

from __future__ import annotations

import argparse
import gzip
import html
import operator
import re
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from PIL import Image, ImageColor

Point = Tuple[int, int]
Edge = Tuple[Point, Point]
RGBA = Tuple[int, int, int, int]

RESAMPLING = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}


@dataclass(frozen=True)
class ConvertOptions:
    resize_width: Optional[int] = None
    resize_height: Optional[int] = None
    fit: str = "contain"
    resample: str = "nearest"
    output_width: Optional[str] = None
    output_height: Optional[str] = None
    colors: Optional[int] = None
    background: Optional[RGBA] = (255, 255, 255, 255)
    background_tolerance: int = 0
    alpha_threshold: int = 0
    keep_every_point: bool = False
    minify: bool = False
    gzip_level: Optional[int] = None


def add_tuple(a: Point, b: Point) -> Point:
    return tuple(map(operator.add, a, b))  # type: ignore[return-value]


def sub_tuple(a: Point, b: Point) -> Point:
    return tuple(map(operator.sub, a, b))  # type: ignore[return-value]


def direction(edge: Edge) -> Point:
    return sub_tuple(edge[1], edge[0])


def normalize_axis(a: Point) -> Point:
    """軸平行な輪郭辺を-1/0/1へ正規化する。"""
    x, y = a
    if x == 0 and y == 0:
        raise ValueError("長さ0の辺は正規化できません")
    return (0 if x == 0 else (1 if x > 0 else -1),
            0 if y == 0 else (1 if y > 0 else -1))


def parse_positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("1以上の整数を指定してください")
    return number


def parse_nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0以上の整数を指定してください")
    return number


def parse_css_size(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+)(?:px|pt|pc|mm|cm|in|em|rem|%|vw|vh)?", value):
        raise ValueError(f"不正な出力サイズです: {value}")
    number = float(re.match(r"(?:\d+(?:\.\d+)?|\.\d+)", value).group(0))  # type: ignore[union-attr]
    if number <= 0:
        raise ValueError("出力サイズは0より大きくしてください")
    return value


def parse_color(value: str) -> Optional[RGBA]:
    if value.lower() in {"none", "transparent", "off"}:
        return None
    try:
        rgba = ImageColor.getcolor(value, "RGBA")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"不正な色です: {value}") from exc
    return tuple(rgba)  # type: ignore[return-value]


def is_background(pixel: RGBA, background: Optional[RGBA], tolerance: int) -> bool:
    if background is None:
        return False
    return all(abs(pixel[i] - background[i]) <= tolerance for i in range(4))


def calculate_resize(source: Tuple[int, int], width: Optional[int], height: Optional[int], fit: str) -> Tuple[int, int]:
    source_width, source_height = source
    if width is None and height is None:
        return source
    if width is None:
        width = max(1, round(source_width * height / source_height))  # type: ignore[operator]
    if height is None:
        height = max(1, round(source_height * width / source_width))
    if fit == "stretch":
        return width, height
    ratio = min(width / source_width, height / source_height) if fit == "contain" else max(width / source_width, height / source_height)
    return max(1, round(source_width * ratio)), max(1, round(source_height * ratio))


def resize_image(image: Image.Image, options: ConvertOptions) -> Image.Image:
    target = calculate_resize(image.size, options.resize_width, options.resize_height, options.fit)
    if target == image.size:
        return image
    resized = image.resize(target, RESAMPLING[options.resample])
    if options.fit != "cover" or options.resize_width is None or options.resize_height is None:
        return resized
    left = max(0, (resized.width - options.resize_width) // 2)
    top = max(0, (resized.height - options.resize_height) // 2)
    return resized.crop((left, top, left + options.resize_width, top + options.resize_height))


def quantize_image(image: Image.Image, colors: Optional[int]) -> Image.Image:
    if colors is None:
        return image
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    quantized = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    quantized.putalpha(alpha)
    return quantized


def joined_edges(assorted_edges: set[Edge], keep_every_point: bool = False) -> list[list[Edge]]:
    pieces: list[list[Edge]] = []
    piece: list[Edge] = []
    directions: deque[Point] = deque([(0, 1), (1, 0), (0, -1), (-1, 0)])
    while assorted_edges:
        if not piece:
            piece.append(assorted_edges.pop())
        current_direction = normalize_axis(direction(piece[-1]))
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
                    if (not keep_every_point and
                            normalize_axis(direction(piece[0])) == normalize_axis(direction(piece[-1]))):
                        first = piece.pop(0)
                        piece[-1] = (piece[-1][0], first[1])
                    pieces.append(piece)
                    piece = []
                break
        else:
            raise RuntimeError("輪郭辺の接続に失敗しました")
    return pieces


def svg_header(view_width: int, view_height: int, output_width: Optional[str], output_height: Optional[str], minify: bool) -> str:
    width = html.escape(output_width or str(view_width), quote=True)
    height = html.escape(output_height or str(view_height), quote=True)
    newline = "" if minify else "\n"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>' + newline +
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{width}" height="{height}" viewBox="0 0 {view_width} {view_height}" '
        f'shape-rendering="crispEdges">' + newline
    )


def rgba_image_to_svg_contiguous(image: Image.Image, options: ConvertOptions) -> str:
    adjacent: Sequence[Point] = ((1, 0), (0, 1), (-1, 0), (0, -1))
    visited = Image.new("1", image.size, 0)
    color_pixel_lists: dict[RGBA, list[list[Point]]] = {}
    width, height = image.size

    pixels = image.load()
    visited_pixels = visited.load()
    for x in range(width):
        for y in range(height):
            here = (x, y)
            if visited_pixels[x, y]:
                continue
            rgba: RGBA = pixels[x, y]
            if rgba[3] <= options.alpha_threshold or is_background(rgba, options.background, options.background_tolerance):
                visited_pixels[x, y] = 1
                continue
            piece: list[Point] = []
            queue = [here]
            visited_pixels[x, y] = 1
            while queue:
                current = queue.pop()
                piece.append(current)
                for offset in adjacent:
                    neighbour = add_tuple(current, offset)
                    nx, ny = neighbour
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    if visited_pixels[nx, ny] or pixels[nx, ny] != rgba:
                        continue
                    queue.append(neighbour)
                    visited_pixels[nx, ny] = 1
            color_pixel_lists.setdefault(rgba, []).append(piece)

    edge_offsets = {
        (-1, 0): ((0, 0), (0, 1)),
        (0, 1): ((0, 1), (1, 1)),
        (1, 0): ((1, 1), (1, 0)),
        (0, -1): ((1, 0), (0, 0)),
    }
    color_joined_pieces: dict[RGBA, list[list[list[Edge]]]] = {}
    for rgba, pieces in color_pixel_lists.items():
        joined_for_color: list[list[list[Edge]]] = []
        for pixel_list in pieces:
            pixel_set = set(pixel_list)
            edge_set: set[Edge] = set()
            for coord in pixel_list:
                for offset, (start_offset, end_offset) in edge_offsets.items():
                    if add_tuple(coord, offset) in pixel_set:
                        continue
                    edge_set.add((add_tuple(coord, start_offset), add_tuple(coord, end_offset)))
            joined_for_color.append(joined_edges(edge_set, options.keep_every_point))
        color_joined_pieces[rgba] = joined_for_color

    newline = "" if options.minify else "\n"
    indent = "" if options.minify else "  "
    chunks = [svg_header(width, height, options.output_width, options.output_height, options.minify)]
    for color, shapes in color_joined_pieces.items():
        commands: list[str] = []
        for shape in shapes:
            for sub_shape in shape:
                start = sub_shape[0][0]
                commands.append(f"M{start[0]},{start[1]}")
                for edge in sub_shape:
                    end = edge[1]
                    commands.append(f"L{end[0]},{end[1]}")
                commands.append("Z")
        if commands:
            separator = "" if options.minify else " "
            path_data = separator.join(commands)
            opacity = color[3] / 255
            opacity_attr = "" if color[3] == 255 else f' fill-opacity="{opacity:.3f}"'
            chunks.append(f'{indent}<path fill="rgb({color[0]},{color[1]},{color[2]})"{opacity_attr} d="{path_data}"/>{newline}')
    chunks.append(f"</svg>{newline}")
    return "".join(chunks)


def convert_png_to_svg(input_path: Path, options: ConvertOptions) -> str:
    try:
        with Image.open(input_path) as source:
            image = source.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"画像を開けません: {input_path}") from exc
    image = resize_image(image, options)
    image = quantize_image(image, options.colors)
    return rgba_image_to_svg_contiguous(image, options)


def write_output(svg_text: str, output_path: Path, gzip_level: Optional[int]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    use_gzip = output_path.suffix.lower() == ".svgz" or gzip_level is not None
    if use_gzip:
        level = 9 if gzip_level is None else gzip_level
        with gzip.open(output_path, "wt", encoding="utf-8", compresslevel=level, newline="") as handle:
            handle.write(svg_text)
    else:
        output_path.write_text(svg_text, encoding="utf-8", newline="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PNGを同色領域ベースのSVGへ変換します。引数なしでGUIを開きます。")
    parser.add_argument("input", nargs="?", type=Path, help="入力PNGファイル")
    parser.add_argument("output", nargs="?", type=Path, help="出力SVG/SVGZファイル。省略時は入力名.svg")
    resize = parser.add_argument_group("リサイズ")
    resize.add_argument("--resize-width", type=parse_positive_int, help="ベクトル化前の幅（px）")
    resize.add_argument("--resize-height", type=parse_positive_int, help="ベクトル化前の高さ（px）")
    resize.add_argument("--fit", choices=("contain", "cover", "stretch"), default="contain", help="幅と高さを両方指定した場合の合わせ方")
    resize.add_argument("--resample", choices=tuple(RESAMPLING), default="nearest", help="リサイズ補間方式")
    display = parser.add_argument_group("SVG出力サイズ")
    display.add_argument("--output-width", help="SVGのwidth。例: 512, 100%%, 20cm")
    display.add_argument("--output-height", help="SVGのheight。例: 512, 100%%, 20cm")
    compression = parser.add_argument_group("圧縮・軽量化")
    compression.add_argument("--colors", type=parse_positive_int, help="減色数（1～256推奨）")
    compression.add_argument("--minify", action="store_true", help="改行と余分な空白を除去")
    compression.add_argument("--gzip-level", type=parse_nonnegative_int, choices=range(0, 10), metavar="0-9", help="SVGZ/Gzip圧縮レベル")
    tracing = parser.add_argument_group("輪郭・背景")
    tracing.add_argument("--background", type=parse_color, default=(255, 255, 255, 255), help="除外する背景色。例: white, '#ffffff', none")
    tracing.add_argument("--background-tolerance", type=parse_nonnegative_int, default=0, help="背景色判定の許容差（0～255）")
    tracing.add_argument("--alpha-threshold", type=parse_nonnegative_int, default=0, help="除外するアルファ値の上限（0～255）")
    tracing.add_argument("--keep-every-point", action="store_true", help="直線上の中間点を削除しない")
    return parser


def options_from_namespace(args: argparse.Namespace) -> ConvertOptions:
    if args.colors is not None and not 1 <= args.colors <= 256:
        raise ValueError("--colors は1～256で指定してください")
    if not 0 <= args.background_tolerance <= 255:
        raise ValueError("--background-tolerance は0～255で指定してください")
    if not 0 <= args.alpha_threshold <= 255:
        raise ValueError("--alpha-threshold は0～255で指定してください")
    return ConvertOptions(
        resize_width=args.resize_width,
        resize_height=args.resize_height,
        fit=args.fit,
        resample=args.resample,
        output_width=parse_css_size(args.output_width),
        output_height=parse_css_size(args.output_height),
        colors=args.colors,
        background=args.background,
        background_tolerance=args.background_tolerance,
        alpha_threshold=args.alpha_threshold,
        keep_every_point=args.keep_every_point,
        minify=args.minify or args.gzip_level is not None,
        gzip_level=args.gzip_level,
    )


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print("Tkinterが利用できません。CLI引数を指定して実行してください。", file=sys.stderr)
        return 2

    root = tk.Tk()
    root.title("PNG to SVG")
    root.minsize(620, 520)
    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    variables = {
        "input": tk.StringVar(), "output": tk.StringVar(),
        "resize_width": tk.StringVar(), "resize_height": tk.StringVar(),
        "fit": tk.StringVar(value="contain"), "resample": tk.StringVar(value="nearest"),
        "output_width": tk.StringVar(), "output_height": tk.StringVar(),
        "colors": tk.StringVar(), "minify": tk.BooleanVar(value=True),
        "svgz": tk.BooleanVar(value=False), "gzip_level": tk.IntVar(value=9),
        "background": tk.StringVar(value="#ffffff"),
        "background_tolerance": tk.StringVar(value="0"), "alpha_threshold": tk.StringVar(value="0"),
        "status": tk.StringVar(value="入力PNGと出力先を指定してください。"),
    }

    def select_input() -> None:
        selected = filedialog.askopenfilename(title="入力PNGを選択", filetypes=[("PNG画像", "*.png"), ("画像", "*.png *.webp *.bmp *.gif"), ("すべて", "*.*")])
        if selected:
            variables["input"].set(selected)
            if not variables["output"].get():
                variables["output"].set(str(Path(selected).with_suffix(".svg")))

    def select_output() -> None:
        ext = ".svgz" if variables["svgz"].get() else ".svg"
        selected = filedialog.asksaveasfilename(title="出力先を選択", defaultextension=ext, filetypes=[("SVG", "*.svg"), ("圧縮SVG", "*.svgz")])
        if selected:
            variables["output"].set(selected)

    def optional_int(key: str) -> Optional[int]:
        value = variables[key].get().strip()
        if not value:
            return None
        number = int(value)
        if number <= 0:
            raise ValueError(f"{key} は1以上で指定してください")
        return number

    def run_conversion() -> None:
        try:
            input_path = Path(variables["input"].get())
            output_path = Path(variables["output"].get())
            if not input_path.is_file():
                raise ValueError("入力画像を選択してください")
            if not str(output_path):
                raise ValueError("出力先を指定してください")
            colors = optional_int("colors")
            if colors is not None and not 1 <= colors <= 256:
                raise ValueError("減色数は1～256で指定してください")
            tolerance = int(variables["background_tolerance"].get() or "0")
            alpha = int(variables["alpha_threshold"].get() or "0")
            if not 0 <= tolerance <= 255 or not 0 <= alpha <= 255:
                raise ValueError("許容差とアルファ閾値は0～255で指定してください")
            use_svgz = bool(variables["svgz"].get()) or output_path.suffix.lower() == ".svgz"
            if use_svgz and output_path.suffix.lower() != ".svgz":
                output_path = output_path.with_suffix(".svgz")
                variables["output"].set(str(output_path))
            options = ConvertOptions(
                resize_width=optional_int("resize_width"), resize_height=optional_int("resize_height"),
                fit=variables["fit"].get(), resample=variables["resample"].get(),
                output_width=parse_css_size(variables["output_width"].get() or None),
                output_height=parse_css_size(variables["output_height"].get() or None),
                colors=colors, background=parse_color(variables["background"].get()),
                background_tolerance=tolerance, alpha_threshold=alpha,
                minify=bool(variables["minify"].get()) or use_svgz,
                gzip_level=int(variables["gzip_level"].get()) if use_svgz else None,
            )
            variables["status"].set("変換中...")
            root.update_idletasks()
            svg = convert_png_to_svg(input_path, options)
            write_output(svg, output_path, options.gzip_level)
            size = output_path.stat().st_size
            variables["status"].set(f"完了: {output_path.name} ({size:,} bytes)")
            messagebox.showinfo("完了", f"変換しました。\n{output_path}\n{size:,} bytes")
        except Exception as exc:
            variables["status"].set(f"エラー: {exc}")
            messagebox.showerror("変換エラー", str(exc))

    row = 0
    for label, key, command in (("入力画像", "input", select_input), ("出力先", "output", select_output)):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=variables[key]).grid(row=row, column=1, columnspan=3, sticky="ew", padx=6)
        ttk.Button(frame, text="参照...", command=command).grid(row=row, column=4, sticky="ew")
        row += 1

    ttk.Separator(frame).grid(row=row, column=0, columnspan=5, sticky="ew", pady=10); row += 1
    ttk.Label(frame, text="ベクトル化前リサイズ").grid(row=row, column=0, columnspan=5, sticky="w"); row += 1
    ttk.Label(frame, text="幅(px)").grid(row=row, column=0, sticky="w")
    ttk.Entry(frame, textvariable=variables["resize_width"], width=10).grid(row=row, column=1, sticky="w")
    ttk.Label(frame, text="高さ(px)").grid(row=row, column=2, sticky="e")
    ttk.Entry(frame, textvariable=variables["resize_height"], width=10).grid(row=row, column=3, sticky="w")
    row += 1
    ttk.Label(frame, text="合わせ方").grid(row=row, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=variables["fit"], values=("contain", "cover", "stretch"), state="readonly", width=12).grid(row=row, column=1, sticky="w")
    ttk.Label(frame, text="補間").grid(row=row, column=2, sticky="e")
    ttk.Combobox(frame, textvariable=variables["resample"], values=tuple(RESAMPLING), state="readonly", width=12).grid(row=row, column=3, sticky="w")
    row += 1

    ttk.Label(frame, text="SVG表示サイズ").grid(row=row, column=0, columnspan=5, sticky="w", pady=(10, 0)); row += 1
    ttk.Label(frame, text="width").grid(row=row, column=0, sticky="w")
    ttk.Entry(frame, textvariable=variables["output_width"], width=12).grid(row=row, column=1, sticky="w")
    ttk.Label(frame, text="height").grid(row=row, column=2, sticky="e")
    ttk.Entry(frame, textvariable=variables["output_height"], width=12).grid(row=row, column=3, sticky="w")
    ttk.Label(frame, text="例: 512 / 100% / 20cm").grid(row=row, column=4, sticky="w")
    row += 1

    ttk.Label(frame, text="圧縮・軽量化").grid(row=row, column=0, columnspan=5, sticky="w", pady=(10, 0)); row += 1
    ttk.Label(frame, text="減色数").grid(row=row, column=0, sticky="w")
    ttk.Entry(frame, textvariable=variables["colors"], width=10).grid(row=row, column=1, sticky="w")
    ttk.Checkbutton(frame, text="SVGを最小化", variable=variables["minify"]).grid(row=row, column=2, sticky="w")
    ttk.Checkbutton(frame, text="SVGZ圧縮", variable=variables["svgz"]).grid(row=row, column=3, sticky="w")
    ttk.Spinbox(frame, from_=0, to=9, textvariable=variables["gzip_level"], width=4).grid(row=row, column=4, sticky="w")
    row += 1

    ttk.Label(frame, text="背景除外色").grid(row=row, column=0, sticky="w", pady=(10, 0))
    ttk.Entry(frame, textvariable=variables["background"], width=14).grid(row=row, column=1, sticky="w", pady=(10, 0))
    ttk.Label(frame, text="許容差").grid(row=row, column=2, sticky="e", pady=(10, 0))
    ttk.Entry(frame, textvariable=variables["background_tolerance"], width=8).grid(row=row, column=3, sticky="w", pady=(10, 0))
    ttk.Label(frame, text="0～255").grid(row=row, column=4, sticky="w", pady=(10, 0)); row += 1
    ttk.Label(frame, text="アルファ除外閾値").grid(row=row, column=0, sticky="w")
    ttk.Entry(frame, textvariable=variables["alpha_threshold"], width=8).grid(row=row, column=1, sticky="w")
    row += 1

    ttk.Button(frame, text="変換", command=run_conversion).grid(row=row, column=0, columnspan=5, sticky="ew", pady=(16, 8)); row += 1
    ttk.Label(frame, textvariable=variables["status"], wraplength=580).grid(row=row, column=0, columnspan=5, sticky="w")
    frame.columnconfigure(1, weight=1)
    frame.columnconfigure(3, weight=1)
    root.mainloop()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return launch_gui()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.input is None:
        parser.error("入力ファイルを指定してください")
    output = args.output or args.input.with_suffix(".svg")
    try:
        options = options_from_namespace(args)
        if output.suffix.lower() == ".svgz" and options.gzip_level is None:
            options = ConvertOptions(**{**options.__dict__, "gzip_level": 9, "minify": True})
        svg_text = convert_png_to_svg(args.input, options)
        write_output(svg_text, output, options.gzip_level)
        print(f"出力完了: {output} ({output.stat().st_size:,} bytes)")
        return 0
    except Exception as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
