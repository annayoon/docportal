from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def format_size(size: int | None) -> str:
    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


templates.env.filters["filesize"] = format_size

from .config import MAXKB_CHAT_URL  # noqa: E402

templates.env.globals["maxkb_chat_url"] = MAXKB_CHAT_URL
