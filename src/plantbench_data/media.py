"""Native media metadata; a Pillow display mode must not erase TIFF precision."""
from pathlib import Path


def fingerprint(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def inspect_media(path):
    from PIL import Image
    path = Path(path)
    with Image.open(path) as im:
        mode, width, height = im.mode, im.width, im.height
        channels, pages = len(im.getbands()), getattr(im, "n_frames", 1)
        fmt = im.format
        if fmt != "TIFF":
            for frame in range(pages):
                im.seek(frame)
                im.load()
    depth = 16 if mode.startswith("I;16") else 32 if mode in {"I", "F"} else 1 if mode == "1" else 8
    dtype = "float32" if mode == "F" else "int32" if mode == "I" else f"uint{depth}"
    page_metadata = []
    if fmt == "TIFF":
        import tifffile
        with tifffile.TiffFile(path) as tif:
            for page in tif.pages:
                array = page.asarray()
                if str(array.dtype) != str(page.dtype):
                    raise ValueError("Native TIFF decode dtype mismatch")
                page_metadata.append({"width": page.imagewidth, "height": page.imagelength,
                                      "channels": page.samplesperpixel, "dtype": str(array.dtype), "bit_depth": page.bitspersample})
            first = page_metadata[0]
            width, height, channels, dtype, depth = (first[k] for k in ("width", "height", "channels", "dtype", "bit_depth"))
            pages = len(tif.pages)
    if fmt == "PNG":
        with path.open("rb") as stream:
            header = stream.read(29)
        native_depth, color_type = header[24], header[25]
        if native_depth == 16 and color_type in {2, 4, 6} and depth != 16:
            raise ValueError("16-bit multichannel PNG needs a native decoder; cannot accept Pillow downconversion")
    return {"width": width, "height": height, "channels": channels, "dtype": dtype, "bit_depth": depth,
            "page_count": pages, "pillow_mode": mode, "native_page_metadata": page_metadata}
