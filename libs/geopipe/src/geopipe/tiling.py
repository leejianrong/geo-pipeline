from .messages import Window


def _starts(size: int, tile: int, stride: int) -> list[int]:
    if size <= tile:
        return [0]
    return [*range(0, size - tile, stride), size - tile]


def windows(width: int, height: int, tile: int, overlap: float) -> list[Window]:
    """Row-major tile windows covering the raster. The last tile in each axis is shifted
    inward so every tile is full size (smaller only if the raster is smaller than a tile)."""
    stride = max(1, int(tile * (1 - overlap)))
    w, h = min(tile, width), min(tile, height)
    return [
        Window(x_off=x, y_off=y, w=w, h=h)
        for y in _starts(height, tile, stride)
        for x in _starts(width, tile, stride)
    ]
