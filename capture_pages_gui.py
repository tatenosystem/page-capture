#!/usr/bin/env python3
"""
画面を連続キャプチャし、各キャプチャ後に矢印キーでページを送るGUI版。

- 「左キーで開始」: 指定秒後にキャプチャを開始し、左矢印キーでページを送る
- 「右キーで開始」: 指定秒後にキャプチャを開始し、右矢印キーでページを送る
- 「停止」: キャプチャを中断する

保存先:
    このプログラムと同じフォルダの images/

ファイル名:
    page-0001.jpg
    page-0002.jpg
    ...
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import flet as ft
import pyautogui
from PIL import Image


# =========================
# 設定
# =========================

# キャプチャ直前の待機時間（秒）
BEFORE_CAPTURE_WAIT_SECONDS = 0.3

# JPEG品質（1～100）
JPEG_QUALITY = 95

# =========================
# トリム設定
# =========================

# 余白とみなす色（黒）
TRIM_MARGIN_COLOR = (0, 0, 0)
# 各チャンネルの許容差
TRIM_RGB_DIFF = 50
# RGB合計の許容差
TRIM_SUM_RGB_DIFF = 70
# 同一サイズが連続一致した回数がこの値に達したらサイズ確定
TRIM_MATCH_REQUIRED = 2
# 縦ライン走査範囲（y=100..990 / 高さ2160 を基準に比率で算出）
TRIM_Y_START_RATIO = 100 / 2160
TRIM_Y_END_RATIO = 990 / 2160

# 全画面を撮る場合は None
# 範囲指定する場合は (左端X, 上端Y, 幅, 高さ)
# 例: CAPTURE_REGION = (100, 100, 1600, 900)
CAPTURE_REGION: tuple[int, int, int, int] | None = None

# 開始ページ番号
START_PAGE_NUMBER = 1


# =========================
# 左右トリム処理
# =========================


def _color_is_margin(pixel: tuple[int, int, int]) -> bool:
    """ピクセルが余白色（黒）とみなせるか。"""
    r, g, b = pixel[0], pixel[1], pixel[2]
    tr, tg, tb = TRIM_MARGIN_COLOR
    dr, dg, db = abs(r - tr), abs(g - tg), abs(b - tb)
    if dr > TRIM_RGB_DIFF or dg > TRIM_RGB_DIFF or db > TRIM_RGB_DIFF:
        return False
    if dr + dg + db > TRIM_SUM_RGB_DIFF:
        return False
    return True


def _is_margin_column(px, x: int, start_y: int, end_y: int) -> bool:
    """縦一列（start_y..end_y）がすべて余白色か。"""
    for y in range(start_y, end_y + 1):
        if not _color_is_margin(px[x, y]):
            return False
    return True


def _find_content_x(px, start_x: int, end_x: int, start_y: int, end_y: int):
    """start_x から end_x へ走査し、最初の非余白列（コンテンツ端）のXを返す。

    見つからなければ None。
    """
    step = 1 if end_x >= start_x else -1
    x = start_x
    while (step > 0 and x <= end_x) or (step < 0 and x >= end_x):
        if not _is_margin_column(px, x, start_y, end_y):
            return x
        x += step
    return None


def _percent_box(width_px: int, trim_percent: float) -> tuple[int, int]:
    """左右を trim_percent% ずつ削るトリム位置(left, width)を返す。"""
    margin = round(width_px * trim_percent / 100)
    return (margin, width_px - margin * 2)


def _measure_trim_box(image_paths: list[Path], trim_percent: float, set_file=None):
    """複数画像から左右トリム位置(left, width)を計測する。

    画像端から中央へ走査してコンテンツ端（＝黒余白の内側）を検出する。
    左右どちらにも黒余白がない場合は trim_percent% で左右をトリムする。
    同一の(left, width)が連続一致し、一致カウントが TRIM_MATCH_REQUIRED に
    達した時点で確定する。確定できなければ None。
    """
    match_count = 0
    last: tuple[int, int] | None = None

    for path in image_paths:
        if set_file is not None:
            set_file(path.name)
        with Image.open(path) as im:
            im = im.convert("RGB")
            width_px, height_px = im.size
            px = im.load()

            start_y = max(0, round(height_px * TRIM_Y_START_RATIO))
            end_y = min(height_px - 1, round(height_px * TRIM_Y_END_RATIO))
            half = width_px // 2

            # → 左端（左端0から中央へ）
            left = _find_content_x(px, 0, half, start_y, end_y)
            # ← 右端（右端width-1から中央へ）
            right = _find_content_x(px, width_px - 1, half, start_y, end_y)

        if left is None or right is None:
            continue

        # 左右どちらにも黒余白がない → 設定%でトリム
        if left == 0 and right == width_px - 1:
            box = _percent_box(width_px, trim_percent)
        else:
            box = (left, (right - left) + 1)

        if last is not None and last == box:
            match_count += 1
        last = box
        if match_count >= TRIM_MATCH_REQUIRED:
            return box

    return None


def _crop_image(path: Path, left: int, width: int) -> None:
    """画像を [left, 0, width, 高さ] で左右トリムして上書き保存。"""
    with Image.open(path) as im:
        im = im.convert("RGB")
        img_w, img_h = im.size
        right = min(left + width, img_w)
        cropped = im.crop((left, 0, right, img_h))
        cropped.save(path, format="JPEG", quality=JPEG_QUALITY, optimize=True)


def trim_images(output_dir: Path, set_status, trim_percent: float, set_file=None) -> None:
    """images フォルダ内の全画像を左右トリムする。"""
    def report_file(name: str) -> None:
        if set_file is not None:
            set_file(name)

    image_paths = sorted(output_dir.glob("page-*.jpg"))
    if not image_paths:
        set_status("トリム対象の画像がありません。")
        report_file("")
        return

    # 先頭画像の縦横比をチェックし、縦長ならトリムをスキップ
    with Image.open(image_paths[0]) as im:
        first_w, first_h = im.size
    if first_h > first_w:
        set_status(
            f"縦長画像（{first_w}x{first_h}）のためトリムをスキップしました。"
        )
        report_file("")
        return

    set_status("トリムサイズを計測中...")
    box = _measure_trim_box(image_paths, trim_percent, report_file)
    if box is None:
        set_status("トリム位置を特定できませんでした。トリムをスキップします。")
        report_file("")
        return

    left, width = box
    total = len(image_paths)
    for i, path in enumerate(image_paths, start=1):
        report_file(path.name)
        _crop_image(path, left, width)
        set_status(f"トリム中 [{i}/{total}] {path.name}")

    report_file("")
    set_status(f"トリム完了（{total}枚 / left={left}, width={width}）")


def main(page: ft.Page) -> None:
    page.title = "ページキャプチャ"
    page.window.width = 460
    page.window.height = 600
    # ウィンドウを固定サイズにし、最大化・リサイズを禁止する
    page.window.resizable = False
    page.window.maximizable = False
    page.window.min_width = 460
    page.window.max_width = 460
    page.window.min_height = 600
    page.window.max_height = 600

    # マウスを画面左上へ移動すると緊急停止できる
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1

    # PyInstaller等で凍結された場合は exe の場所を基準にする
    # （onefile では __file__ が一時展開先 _MEI... を指すため）
    if getattr(sys, "frozen", False):
        program_dir = Path(sys.executable).resolve().parent
    else:
        program_dir = Path(__file__).resolve().parent
    output_dir = program_dir / "images"

    stop_event = threading.Event()

    # =========================
    # コントロール
    # =========================

    delay_field = ft.TextField(
        label="開始までの待機（秒）",
        value="10",
        width=130,
    )
    count_field = ft.TextField(
        label="最大ページ数",
        value="500",
        width=130,
    )
    turn_wait_field = ft.TextField(
        label="ページ送り待機（秒）",
        value="1.5",
        width=130,
    )
    trim_percent_field = ft.TextField(
        label="黒余白なし時トリム（%）",
        value="28",
        width=130,
    )

    status_text = ft.Text(
        value=f"保存先: {output_dir}",
        size=14,
    )

    trim_file_text = ft.Text(
        value="",
        size=12,
        color=ft.Colors.BLUE,
    )

    left_button = ft.Button(
        content="← 左キーで開始",
        width=180,
        height=50,
        on_click=lambda _: start_capture("left"),
    )
    right_button = ft.Button(
        content="右キーで開始 →",
        width=180,
        height=50,
        on_click=lambda _: start_capture("right"),
    )
    stop_button = ft.Button(
        content="停止",
        width=180,
        height=50,
        bgcolor=ft.Colors.RED,
        color=ft.Colors.WHITE,
        disabled=True,
        on_click=lambda _: request_stop(),
    )
    trim_button = ft.Button(
        content="左右トリム",
        width=180,
        height=50,
        on_click=lambda _: start_trim(),
    )

    # =========================
    # UI更新ヘルパー
    # =========================

    def set_status(message: str) -> None:
        status_text.value = message
        page.update()

    def set_trim_file(name: str) -> None:
        trim_file_text.value = f"処理中: {name}" if name else ""
        page.update()

    def set_running(running: bool) -> None:
        left_button.disabled = running
        right_button.disabled = running
        delay_field.disabled = running
        count_field.disabled = running
        turn_wait_field.disabled = running
        trim_percent_field.disabled = running
        stop_button.disabled = not running
        trim_button.disabled = running
        page.update()

    # =========================
    # キャプチャ処理
    # =========================

    def capture_worker(
        key_name: str,
        start_delay: float,
        page_count: int,
        page_turn_wait: float,
    ) -> None:
        saved_count = 0
        try:
            # 開始前カウントダウン（1秒ごとに停止要求を確認）
            remaining = start_delay
            while remaining > 0:
                if stop_event.is_set():
                    set_status("開始前に停止しました。")
                    return
                set_status(
                    f"{remaining:.0f}秒後に開始します。"
                    "資料を前面に表示し、キー入力が届く状態にしてください。"
                )
                step = min(1.0, remaining)
                time.sleep(step)
                remaining -= step

            output_dir.mkdir(parents=True, exist_ok=True)

            previous_file_size: int | None = None

            for index in range(page_count):
                if stop_event.is_set():
                    set_status(f"停止しました。（{saved_count}ページ保存済み）")
                    return

                page_number = START_PAGE_NUMBER + index
                filename = output_dir / f"page-{page_number:04d}.jpg"

                time.sleep(BEFORE_CAPTURE_WAIT_SECONDS)

                screenshot = pyautogui.screenshot(region=CAPTURE_REGION)

                # JPEGはRGBで保存する
                if screenshot.mode != "RGB":
                    screenshot = screenshot.convert("RGB")

                screenshot.save(
                    filename,
                    format="JPEG",
                    quality=JPEG_QUALITY,
                    optimize=True,
                )
                saved_count += 1

                set_status(f"[{index + 1}/{page_count}] 保存: {filename.name}")

                # 前ページとファイルサイズが同じなら、ページが進んでいない＝最終ページ
                current_file_size = filename.stat().st_size
                if current_file_size == previous_file_size:
                    filename.unlink()
                    saved_count -= 1
                    set_status(
                        f"前ページと同じサイズのため最終ページと判定し終了しました。"
                        f"（{saved_count}ページ保存）"
                    )
                    return
                previous_file_size = current_file_size

                # 最終ページの保存後はページ送りしない
                if index < page_count - 1:
                    pyautogui.press(key_name)
                    time.sleep(page_turn_wait)

            set_status(f"完了しました。（{saved_count}ページ保存）")

        except pyautogui.FailSafeException:
            set_status(
                f"マウスが画面左上へ移動されたため停止しました。"
                f"（{saved_count}ページ保存済み）"
            )
        except Exception as exc:
            set_status(f"エラー: {exc}")
        finally:
            set_running(False)

    def start_capture(key_name: str) -> None:
        try:
            start_delay = float(delay_field.value)
            page_count = int(count_field.value)
            page_turn_wait = float(turn_wait_field.value)
            if start_delay < 0 or page_count < 1 or page_turn_wait < 0:
                raise ValueError
        except (TypeError, ValueError):
            set_status("設定値が不正です。数値を確認してください。")
            return

        # images フォルダに既存ファイルがあれば上書き防止のため中止
        if output_dir.exists():
            existing = [p for p in output_dir.iterdir() if p.is_file()]
            if existing:
                set_status(
                    f"images フォルダに既存ファイルが {len(existing)} 件あります。"
                    "退避または削除してから開始してください。"
                )
                return

        stop_event.clear()
        set_running(True)
        page.run_thread(
            capture_worker, key_name, start_delay, page_count, page_turn_wait
        )

    def request_stop() -> None:
        stop_event.set()
        stop_button.disabled = True
        set_status("停止しています...")

    def trim_worker(trim_percent: float) -> None:
        try:
            trim_images(output_dir, set_status, trim_percent, set_trim_file)
        except Exception as exc:
            set_status(f"トリムエラー: {exc}")
        finally:
            set_trim_file("")
            set_running(False)

    def start_trim() -> None:
        try:
            trim_percent = float(trim_percent_field.value)
            if not 0 <= trim_percent < 50:
                raise ValueError
        except (TypeError, ValueError):
            set_status("トリム％は 0 以上 50 未満で指定してください。")
            return

        set_running(True)
        page.run_thread(trim_worker, trim_percent)

    # =========================
    # レイアウト
    # =========================

    page.add(
        ft.Column(
            [
                ft.Text("ページキャプチャ", size=20, weight=ft.FontWeight.BOLD),
                ft.Row(
                    [delay_field, count_field, turn_wait_field],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                ),
                ft.Row(
                    [left_button, right_button],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=20,
                ),
                ft.Row(
                    [stop_button],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Row(
                    [trim_percent_field, trim_button],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=20,
                ),
                status_text,
                trim_file_text,
            ],
            spacing=20,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )


if __name__ == "__main__":
    ft.run(main)
