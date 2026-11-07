#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot RapidOCR runner: python ocr_rapid_once.py <image> -> JSON on stdout."""
import json
import sys

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception as error:  # pragma: no cover
    print(json.dumps({"ok": False, "error": "rapidocr unavailable: %s" % error}, ensure_ascii=False))
    sys.exit(2)


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "missing image path"}, ensure_ascii=False))
        return 2
    path = sys.argv[1]
    try:
        engine = RapidOCR()
        result, _ = engine(path)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    items = []
    for row in result or []:
        try:
            box, text, score = row[0], row[1], row[2]
        except Exception:
            continue
        try:
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
        except Exception:
            continue
        if not xs or not ys:
            continue
        items.append({
            "text": str(text),
            "x": int(min(xs)),
            "y": int(min(ys)),
            "w": max(1, int(max(xs) - min(xs))),
            "h": max(1, int(max(ys) - min(ys))),
            "score": float(score),
        })
    print(json.dumps({"ok": True, "items": items}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
