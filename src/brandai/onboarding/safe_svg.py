from math import isfinite
from xml.etree import ElementTree


SVG_NAMESPACE = "http://www.w3.org/2000/svg"
TAGS = frozenset({"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "title", "desc"})
ATTRIBUTES = frozenset({
    "viewBox", "width", "height", "x", "y", "cx", "cy", "r", "rx", "ry", "d", "fill", "stroke", "stroke-width",
    "opacity", "fill-rule", "clip-rule", "fill-opacity", "stroke-opacity", "stroke-linejoin", "stroke-linecap",
    "x1", "x2", "y1", "y2", "points", "transform", "version", "preserveAspectRatio",
})


def static_svg(raw: bytes) -> tuple[bytes, float, float, str | None]:
    """Validate the static geometry subset used by local brand logos."""
    try:
        if b"<!" in raw or len(raw) > 500_000:
            raise ValueError("unsupported SVG document")
        root = ElementTree.fromstring(raw.decode("utf-8-sig"))
        if root.tag not in {"svg", f"{{{SVG_NAMESPACE}}}svg"}:
            raise ValueError("expected SVG")
        for node in root.iter():
            if (node.tag.rsplit("}", 1)[-1] not in TAGS
                    or (node.tag.startswith("{") and not node.tag.startswith(f"{{{SVG_NAMESPACE}}}"))
                    or any(key not in ATTRIBUTES or "url(" in value.lower() for key, value in node.attrib.items())):
                raise ValueError("unsupported SVG node")
        view_box = root.get("viewBox")
        if view_box:
            x, y, width, height = map(float, view_box.replace(",", " ").split())
            if not (isfinite(x) and isfinite(y)):
                raise ValueError("invalid viewBox origin")
        else:
            width, height = (float(root.attrib[key].removesuffix("px")) for key in ("width", "height"))
        if not (0 < width <= 100_000 and 0 < height <= 100_000 and .05 <= width / height <= 20):
            raise ValueError("invalid SVG dimensions")
        # A namespace is required when Chromium opens the geometry as an image document.
        if root.tag == "svg":
            root.set("xmlns", SVG_NAMESPACE)
        return ElementTree.tostring(root, encoding="utf-8"), width, height, view_box
    except (ElementTree.ParseError, KeyError, ValueError) as exc:
        raise ValueError("unsafe SVG or unsupported static geometry") from exc
