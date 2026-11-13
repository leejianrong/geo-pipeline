def pixel_to_map(gt: tuple[float, ...], px: float, py: float) -> tuple[float, float]:
    """Apply a GDAL geotransform to a pixel coordinate."""
    return gt[0] + px * gt[1] + py * gt[2], gt[3] + px * gt[4] + py * gt[5]
