"""Stands in for easyocr, which upstream's ``metrics/docs.py`` imports at the top for ``compare_image_text`` alone: the
real one pulls torch in and downloads its models when it runs, so the image leaves it out and that one metric fails."""


class Reader:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("compare_image_text needs easyocr, which the public OSWorld controller image leaves out")
